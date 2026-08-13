"""Small pure helpers. Every function here is unit-tested (see tests/)."""

from __future__ import annotations

import contextlib
import html
import logging
import re
import shutil
import time
import traceback
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING

import psutil

if TYPE_CHECKING:
    from telegram import Bot

    from .config import Config

logger = logging.getLogger(__name__)

_FILENAME_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_SPACE = re.compile(r"\s+")


def format_duration(seconds: float | int | None) -> str:
    """Format a duration as M:SS or H:MM:SS. Handles None and floats safely."""
    if seconds is None:
        return "0:00"
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return "0:00"
    if total < 0:
        total = 0
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_size(num_bytes: float | int | None) -> str:
    """Human-readable file size."""
    if not num_bytes or num_bytes < 0:
        return "غير معروف"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def truncate_text(text: str, max_len: int = 60, suffix: str = "…") -> str:
    """Truncate text safely for captions/status messages."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[: max(0, max_len - len(suffix))].rstrip() + suffix


def sanitize_filename(name: str, max_len: int = 80) -> str:
    """Make a video title safe to use as a filename on any filesystem."""
    if not name:
        return "video"
    name = unicodedata.normalize("NFKC", name)
    name = _FILENAME_BAD_CHARS.sub("_", name)
    name = _MULTI_SPACE.sub(" ", name).strip(" ._")
    if not name:
        return "video"
    if len(name) > max_len:
        name = name[:max_len].rstrip(" ._")
    return name or "video"


def error_message(exc: BaseException) -> str:
    """Never return an empty error string (gotcha #1 in the design doc)."""
    msg = str(exc).strip()
    if msg:
        return truncate_text(msg, 200)
    return type(exc).__name__


def free_disk_mb(path: Path) -> int:
    """Free disk space in MB for the filesystem containing *path*."""
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    usage = shutil.disk_usage(target)
    return int(usage.free // (1024 * 1024))


def system_stats() -> dict[str, float | int]:
    """CPU/RAM snapshot for the health check and /stats command.

    Deliberately does not attempt a live internet-speed test or ping: those
    are slow, unreliable across restrictive hosts, and would block a
    request/command for seconds just to produce a number. Actual observed
    download/upload throughput is already shown in the progress panel while
    a job is running, which is more useful than a synthetic benchmark.
    """
    mem = psutil.virtual_memory()
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_percent": mem.percent,
        "memory_used_mb": int(mem.used / (1024 * 1024)),
        "memory_total_mb": int(mem.total / (1024 * 1024)),
    }


def write_cookies_file(download_dir: Path, content: str) -> Path:
    """Write cookies.txt content to disk with restrictive permissions.

    Shared by the startup env-var path (YTDLP_COOKIES_CONTENT) and the
    Telegram-upload path (/setcookies) — a cookies file is a live login
    session, so it's written 0o600 regardless of where it came from.
    """
    download_dir.mkdir(parents=True, exist_ok=True)
    path = download_dir / "cookies.txt"
    path.write_text(content)
    path.chmod(0o600)
    return path


def disk_usage_mb(path: Path) -> tuple[int, int, int]:
    """(total_mb, used_mb, free_mb) for the filesystem containing *path*."""
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    usage = shutil.disk_usage(target)
    mb = 1024 * 1024
    return int(usage.total // mb), int(usage.used // mb), int(usage.free // mb)


def build_progress_bar(percent: float, length: int = 12) -> str:
    """Render a filled/empty block bar for a 0-100 percent value."""
    percent = max(0.0, min(100.0, percent))
    filled = int(round(length * percent / 100))
    return "█" * filled + "░" * (length - filled)


def format_speed(bytes_per_sec: float | int | None) -> str:
    """Human-readable transfer speed, e.g. '12.8 MB/s'."""
    if not bytes_per_sec or bytes_per_sec <= 0:
        return "-- MB/s"
    return f"{format_size(bytes_per_sec)}/s"


def format_eta(seconds: float | int | None) -> str:
    """Human-readable ETA as MM:SS, or '--:--' when unknown."""
    if seconds is None or seconds < 0:
        return "--:--"
    try:
        total = int(seconds)
    except (TypeError, ValueError, OverflowError):
        return "--:--"
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _spinner_frame() -> str:
    """A frame derived from wall-clock time (changes every ~0.5s).

    This makes the panel visibly animate across successive throttled edits
    without any caller needing to thread a tick counter through — the same
    call made a moment later just naturally picks a different frame.
    """
    return _SPINNER_FRAMES[int(time.monotonic() * 2) % len(_SPINNER_FRAMES)]


def format_progress_panel(
    *,
    phase: str,
    percent: float,
    speed_bytes_per_sec: float | None,
    done_bytes: int,
    total_bytes: int | None,
    eta_seconds: float | None,
    title: str | None = None,
) -> str:
    """Render the Download/Upload progress panel shown to the user.

    HTML-formatted (bold labels, monospace bar + spinner) — callers must
    send/edit the message with parse_mode="HTML". *title* is escaped here,
    so callers may pass the raw (unescaped) video title straight through.
    """
    is_download = phase == "download"
    icon = "📥" if is_download else "📤"
    label = "Download" if is_download else "Upload"
    speed_label = "Speed" if is_download else "Upload Speed"
    size_label = "الحجم" if is_download else "Uploaded"
    eta_label = "الوقت المتبقي" if is_download else "ETA"

    bar = build_progress_bar(percent)
    total_str = format_size(total_bytes) if total_bytes else "؟"

    header = f"{icon} <b>{label}</b>  {_spinner_frame()}"
    if title:
        header += f"\n<i>{html.escape(truncate_text(title, 45))}</i>"

    return (
        f"{header}\n"
        f"――――――――――――――――\n"
        f"<code>[{bar}] {percent:.0f}%</code>\n\n"
        f"⚡ {speed_label}: {format_speed(speed_bytes_per_sec)}\n"
        f"📦 {size_label}: {format_size(done_bytes)} / {total_str}\n"
        f"⏱ {eta_label}: {format_eta(eta_seconds)}"
    )


async def notify_owner(bot: "Bot", config: "Config", text: str) -> None:
    """Send an error/alert message to TELEGRAM_OWNER_ID if configured.

    Silently no-ops when the owner id is unset or the send fails — never
    let a notification failure cascade into the user-facing error path.
    """
    if not config.telegram_owner_id:
        return
    with contextlib.suppress(Exception):
        await bot.send_message(
            chat_id=config.telegram_owner_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


def format_exception_for_owner(exc: BaseException, *, context: str | None = None) -> str:
    """Build a short HTML message with exception type, message, and tail of traceback."""
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    # Keep the last ~12 lines so the message stays under Telegram's limit.
    tb_tail = "".join(tb[-12:]).strip()
    header = "🚨 <b>خطأ في البوت</b>"
    if context:
        header += f"\n📍 {html.escape(context)}"
    return (
        f"{header}\n"
        f"――――――――――――――――\n"
        f"<b>{html.escape(type(exc).__name__)}</b>: {html.escape(error_message(exc))}\n\n"
        f"<pre>{html.escape(tb_tail[:1500])}</pre>"
    )
