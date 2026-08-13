"""Upload manager: single-file upload, splitting for oversized files, retries.

Rules from the design doc:
- files over the safe limit are split with ffmpeg into playable parts
- each part is deleted immediately after a successful upload
- every external call has an explicit timeout (set on the PTB request objects)
- files are streamed to Telegram, never fully buffered in RAM (see _ProgressFile)
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from typing import Awaitable, Callable

from telegram import Bot, InputFile, Message
from telegram.error import RetryAfter, TimedOut

from .config import Config
from .splitter import split_media
from .utils import format_progress_panel, truncate_text

logger = logging.getLogger(__name__)

AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wav"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi"}

ProgressCallback = Callable[[int, int], None]  # (bytes_sent, total_bytes)


class UploadError(RuntimeError):
    pass


class _ProgressFile:
    """Wraps a binary file handle and reports bytes as they're actually read.

    Passed to InputFile(..., read_file_handle=False) so PTB's networking
    backend streams reads from this object instead of buffering the whole
    file in memory — this callback then fires in lockstep with the real
    upload, not a simulated one.
    """

    def __init__(self, fh, total_size: int, on_progress: ProgressCallback | None) -> None:
        self._fh = fh
        self._total = total_size
        self._sent = 0
        self._on_progress = on_progress

    def read(self, size: int = -1) -> bytes:
        chunk = self._fh.read(size)
        if chunk:
            self._sent += len(chunk)
            if self._on_progress is not None:
                self._on_progress(self._sent, self._total)
        return chunk

    def __getattr__(self, item):
        return getattr(self._fh, item)


class _SpeedTracker:
    """Turns raw (sent, total) byte callbacks into speed + ETA for the UI."""

    def __init__(self) -> None:
        self.start = time.monotonic()
        self.sent = 0
        self.total = 0

    def update(self, sent: int, total: int) -> None:
        self.sent = sent
        self.total = total

    @property
    def speed(self) -> float | None:
        elapsed = time.monotonic() - self.start
        return self.sent / elapsed if elapsed > 0 else None

    @property
    def eta(self) -> float | None:
        speed = self.speed
        if not speed:
            return None
        return max(0.0, (self.total - self.sent) / speed)

    @property
    def percent(self) -> float:
        return (self.sent * 100 / self.total) if self.total else 0.0


class UploadManager:
    def __init__(self, config: Config, bot: Bot) -> None:
        self._config = config
        self._bot = bot

    async def upload(
        self,
        chat_id: int,
        file_path: Path,
        caption: str,
        notify: Callable[[str], Awaitable[None]],
        thumbnail_path: Path | None = None,
        cancel_event: "threading.Event | None" = None,
    ) -> None:
        """Upload *file_path*, splitting first when it exceeds the safe limit."""
        limit = self._config.upload_limit_bytes
        size = file_path.stat().st_size
        title = caption

        if size <= limit:
            await self._send_with_retry(chat_id, file_path, caption, notify, thumbnail_path, title)
            return

        await notify("📦 <b>الملف أكبر من حد الرفع</b>، جاري تقسيمه لأجزاء قابلة للتشغيل...")
        parts = await asyncio.to_thread(split_media, file_path, limit, file_path.parent, cancel_event)

        total = len(parts)
        if total > 30:
            raise UploadError(
                f"الملف هيتقسم لـ {total} جزء — كتير جدًا. "
                "جرب جودة أقل، أو شغّل Local Bot API Server لرفع لغاية 2GB للجزء."
            )

        # The original is no longer needed once parts exist
        if total > 1 and parts[0] != file_path:
            self._safe_remove(file_path)

        for i, part in enumerate(parts, start=1):
            part_caption = f"{caption}\n(جزء {i} من {total})"
            try:
                # Only the first part carries the real thumbnail — attaching
                # the same preview image to every part would be misleading.
                part_thumb = thumbnail_path if i == 1 else None
                await self._send_with_retry(
                    chat_id, part, part_caption, notify, part_thumb, f"{title} (جزء {i}/{total})"
                )
            finally:
                # delete each part as soon as it's handled — never accumulate
                self._safe_remove(part)

    # ---- internals ----

    async def _send_with_retry(
        self,
        chat_id: int,
        path: Path,
        caption: str,
        notify: Callable[[str], Awaitable[None]],
        thumbnail_path: Path | None = None,
        title: str | None = None,
    ) -> None:
        caption = truncate_text(caption, 1000)
        last_error: Exception | None = None
        for attempt in range(1, self._config.upload_retries + 1):
            try:
                await self._send_once(chat_id, path, caption, notify, thumbnail_path, title)
                return
            except RetryAfter as exc:
                wait = float(exc.retry_after) + 1.0
                logger.warning("Rate limited on upload, waiting %.1fs", wait)
                await asyncio.sleep(wait)
                last_error = exc
            except TimedOut as exc:
                logger.warning("Upload attempt %d timed out for %s", attempt, path.name)
                last_error = exc
                await asyncio.sleep(2 * attempt)
            except Exception as exc:
                logger.exception("Upload attempt %d failed for %s", attempt, path.name)
                last_error = exc
                await asyncio.sleep(2 * attempt)
        raise UploadError(f"فشل الرفع بعد {self._config.upload_retries} محاولات") from last_error

    async def _send_once(
        self,
        chat_id: int,
        path: Path,
        caption: str,
        notify: Callable[[str], Awaitable[None]],
        thumbnail_path: Path | None = None,
        title: str | None = None,
    ) -> Message:
        ext = path.suffix.lower()
        size = path.stat().st_size
        tracker = _SpeedTracker()
        loop = asyncio.get_running_loop()
        last_render = 0.0

        def on_progress(sent: int, total: int) -> None:
            nonlocal last_render
            tracker.update(sent, total)
            now = time.monotonic()
            if now - last_render < self._config.edit_throttle_seconds:
                return
            last_render = now
            panel = format_progress_panel(
                phase="upload",
                percent=tracker.percent,
                speed_bytes_per_sec=tracker.speed,
                done_bytes=tracker.sent,
                total_bytes=tracker.total,
                eta_seconds=tracker.eta,
                title=title,
            )
            # on_progress runs on the networking backend's thread/loop context;
            # schedule the coroutine safely back onto the bot's event loop.
            asyncio.run_coroutine_threadsafe(notify(panel), loop)

        # Overrides the general (short, 60s) per-request read_timeout for
        # this call only — see the comment on Config.upload_call_timeout_seconds.
        call_timeout = self._config.upload_call_timeout_seconds

        with path.open("rb") as raw_fh:
            # read_file_handle=False: stream from disk instead of buffering the
            # whole file in RAM (default PTB behavior reads it all upfront).
            wrapped = _ProgressFile(raw_fh, size, on_progress)
            input_file = InputFile(wrapped, filename=path.name, read_file_handle=False)

            if ext in AUDIO_EXTS:
                return await self._bot.send_audio(
                    chat_id=chat_id,
                    audio=input_file,
                    caption=caption,
                    filename=path.name,
                    read_timeout=call_timeout,
                    write_timeout=call_timeout,
                )
            if ext in VIDEO_EXTS:
                if thumbnail_path is not None and thumbnail_path.exists():
                    with thumbnail_path.open("rb") as thumb_fh:
                        return await self._bot.send_video(
                            chat_id=chat_id,
                            video=input_file,
                            caption=caption,
                            filename=path.name,
                            supports_streaming=True,
                            thumbnail=thumb_fh,
                            read_timeout=call_timeout,
                            write_timeout=call_timeout,
                        )
                return await self._bot.send_video(
                    chat_id=chat_id,
                    video=input_file,
                    caption=caption,
                    filename=path.name,
                    supports_streaming=True,
                    read_timeout=call_timeout,
                    write_timeout=call_timeout,
                )
            return await self._bot.send_document(
                chat_id=chat_id,
                document=input_file,
                caption=caption,
                filename=path.name,
                read_timeout=call_timeout,
                write_timeout=call_timeout,
            )

    @staticmethod
    def _safe_remove(path: Path) -> None:
        try:
            os.remove(path)
        except OSError:
            pass
