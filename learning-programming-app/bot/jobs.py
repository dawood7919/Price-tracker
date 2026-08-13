"""Job orchestration: the full download → (split) → upload pipeline.

The Telegram handlers only *enqueue* jobs; everything heavy happens here,
off the event loop, with cleanup guaranteed in ``finally``.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import shutil
import time
from pathlib import Path

from telegram import InlineKeyboardMarkup, Message
from telegram.error import BadRequest

from .config import Config
from .downloader import DownloadCancelledError, DownloadError, DownloadProgress, YTDLPDownloader
from .manager import DownloadManager, JobState
from .uploader import UploadManager
from .utils import error_message, format_progress_panel, format_size, free_disk_mb, truncate_text

logger = logging.getLogger(__name__)


class StatusReporter:
    """Edits one status message, throttled (Telegram rate-limits edits).

    Keeps the cancel button attached on progress updates and removes it
    on final updates.
    """

    def __init__(
        self,
        message: Message | None,
        throttle_seconds: float,
        markup: InlineKeyboardMarkup | None = None,
        inline_message_id: str | None = None,
        bot=None,
    ) -> None:
        self._message = message
        self._throttle = throttle_seconds
        self._markup = markup
        self._inline_message_id = inline_message_id
        self._bot = bot
        self._last_edit = 0.0
        self._last_text = ""

    async def update(self, text: str, force: bool = False, final: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_edit) < self._throttle:
            return
        if text == self._last_text:
            return
        markup = None if final else self._markup
        try:
            if self._message:
                await self._message.edit_text(text, reply_markup=markup, parse_mode="HTML")
            elif self._inline_message_id and self._bot:
                await self._bot.edit_message_text(
                    text,
                    inline_message_id=self._inline_message_id,
                    reply_markup=markup,
                    parse_mode="HTML",
                )
            self._last_edit = now
            self._last_text = text
        except BadRequest as exc:
            # "message is not modified" and similar are harmless
            logger.debug("Status edit skipped: %s", exc)
        except Exception:
            logger.exception("Failed to edit status message")


async def run_download_job(
    *,
    config: Config,
    manager: DownloadManager,
    downloader: YTDLPDownloader,
    uploader: UploadManager,
    status: StatusReporter,
    chat_id: int,
    user_id: int,
    url: str,
    format_key: str,
    title_hint: str,
    job_id: str,
) -> bool:
    """Run one complete job for one URL. Reports every outcome via *status*.

    Returns True on success, False on any reported failure.
    """
    job_dir = config.download_dir / f"job_{job_id}"

    # 1) global concurrency slot (no per-user limit — see manager.py)
    await manager.acquire_slot(user_id)

    # Everything from here on must be inside try/finally: if anything
    # between acquire_slot() and the main try block below were to raise
    # (bad job_id, a broken task reference, etc.), the slot would never be
    # released and the leak would compound with every such failure until
    # the global semaphore is exhausted and *all* downloads hang forever
    # waiting for acquire_slot().
    try:
        task = asyncio.current_task()
        cancel_event = manager.start_job(job_id, task) if task is not None else None

        # 2) disk space guard — before starting, not after failing mid-way
        free = free_disk_mb(config.download_dir)
        if free < config.min_free_disk_mb:
            await status.update(
                f"⛔ <b>مفيش مساحة كافية</b> على السيرفر دلوقتي ({free}MB). جرب بعد شوية.",
                force=True,
                final=True,
            )
            return False

        # 3) download (blocking work in a thread, with explicit timeout)
        manager.set_state(job_id, JobState.DOWNLOADING)
        latest = DownloadProgress()

        def on_progress(p: DownloadProgress) -> None:
            nonlocal latest
            latest = p  # written from yt-dlp's thread; read by the poller below

        safe_title = html.escape(truncate_text(title_hint, 50))
        await status.update(f"⏳ <b>بدء التحميل</b>\n<i>{safe_title}</i>", force=True)

        download_task = asyncio.ensure_future(
            asyncio.to_thread(downloader.download, url, format_key, job_dir, on_progress, cancel_event)
        )
        # if we abandon the task (cancel/timeout) its exception must not leak
        download_task.add_done_callback(_consume_exception)

        deadline = time.monotonic() + config.download_timeout_seconds
        while not download_task.done():
            if time.monotonic() > deadline:
                # Setting the flag (not just cancelling the task) is what
                # actually stops the download thread — see manager.py.
                if cancel_event is not None:
                    cancel_event.set()
                download_task.cancel()
                await status.update(
                    "⛔ <b>التحميل خد وقت أطول من المسموح واتلغى.</b>", force=True, final=True
                )
                return False
            await asyncio.sleep(1.0)
            if latest.status == "downloading":
                total = latest.total_bytes
                percent = (latest.downloaded_bytes * 100 / total) if total else 0.0
                eta = (
                    (total - latest.downloaded_bytes) / latest.speed
                    if total and latest.speed
                    else None
                )
                panel = format_progress_panel(
                    phase="download",
                    percent=percent,
                    speed_bytes_per_sec=latest.speed,
                    done_bytes=latest.downloaded_bytes,
                    total_bytes=total,
                    eta_seconds=eta,
                    title=title_hint,
                )
                await status.update(panel)
            elif latest.status == "processing":
                await status.update("⚙️ <b>جاري المعالجة</b> (دمج/تحويل)...")

        dl_result = download_task.result()  # re-raises DownloadError if any
        file_path: Path = dl_result.file_path

        # 4) upload (splitting handled inside the uploader)
        manager.set_state(job_id, JobState.UPLOADING)
        size = file_path.stat().st_size
        await status.update(f"📤 <b>جاري تجهيز الرفع</b> ({format_size(size)})...", force=True)
        caption = truncate_text(title_hint, 200)

        async def notify(text: str) -> None:
            await status.update(text, force=True)

        await uploader.upload(
            chat_id,
            file_path,
            caption,
            notify=notify,
            thumbnail_path=dl_result.thumbnail_path,
            cancel_event=cancel_event,
        )

        manager.set_state(job_id, JobState.COMPLETED)
        await status.update("✅ <b>تم!</b> استلم ملفك فوق 👆", force=True, final=True)
        return True

    except asyncio.CancelledError:
        manager.set_state(job_id, JobState.CANCELLED)
        with contextlib.suppress(Exception):
            await asyncio.shield(status.update("🚫 <b>اتلغى الطلب.</b>", force=True, final=True))
        raise
    except DownloadCancelledError:
        manager.set_state(job_id, JobState.CANCELLED)
        await status.update("🚫 <b>اتلغى الطلب.</b>", force=True, final=True)
        return False
    except DownloadError as exc:
        manager.set_state(job_id, JobState.FAILED)
        logger.warning("Job %s download error: %s", job_id, exc)
        safe_err = html.escape(error_message(exc))
        await status.update(f"❌ <b>فشل التحميل</b>\n{safe_err}", force=True, final=True)
        return False
    except Exception as exc:
        manager.set_state(job_id, JobState.FAILED)
        logger.exception("Job %s failed", job_id)
        safe_err = html.escape(error_message(exc))
        await status.update(
            f"❌ <b>حصل خطأ غير متوقع</b>\n{safe_err}", force=True, final=True
        )
        return False
    finally:
        # 5) cleanup — always, success or failure (design doc rule)
        shutil.rmtree(job_dir, ignore_errors=True)
        await manager.release_slot(user_id)


def _consume_exception(task: "asyncio.Future") -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.debug("Abandoned download task raised: %s", exc)
