import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable, Optional

from config import MAX_VIDEO_SIZE_BYTES, TEMP_FOLDER
from downloaders import manager

logger = logging.getLogger(__name__)


class FileTooLargeError(Exception):
    pass


async def download_with_progress(
    url: str,
    format_selector: str,
    on_progress: Optional[Callable[[float], Awaitable[None]]] = None,
) -> Path:
    """Runs the blocking yt-dlp download in a worker thread so it doesn't block the
    bot's event loop, while still reporting progress back onto that loop."""
    loop = asyncio.get_running_loop()
    last_reported = {"percent": -100.0}

    def _hook(status: dict) -> None:
        if status.get("status") != "downloading" or on_progress is None:
            return
        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        downloaded = status.get("downloaded_bytes", 0)
        if not total:
            return
        percent = downloaded / total * 100
        if percent - last_reported["percent"] >= 5:
            last_reported["percent"] = percent
            asyncio.run_coroutine_threadsafe(on_progress(percent), loop)

    output_path = await loop.run_in_executor(
        None, manager.download_video, url, format_selector, TEMP_FOLDER, _hook
    )

    if output_path.stat().st_size > MAX_VIDEO_SIZE_BYTES:
        output_path.unlink(missing_ok=True)
        raise FileTooLargeError(
            f"The downloaded file exceeds the {MAX_VIDEO_SIZE_BYTES // (1024 * 1024)}MB limit."
        )

    return output_path


def cleanup(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.exception("Failed to delete temp file %s", path)
