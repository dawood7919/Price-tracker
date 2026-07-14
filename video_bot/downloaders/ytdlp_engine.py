import logging
from pathlib import Path
from typing import Callable, Optional

import yt_dlp

logger = logging.getLogger(__name__)

AUDIO_FORMAT_CODE = "audio"


def extract_info(url: str) -> dict:
    """Fetches metadata only (no download) — used to show the user title/duration/
    thumbnail and to run authorization checks before committing to a download."""
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def download(
    url: str,
    format_selector: str,
    output_dir: Path,
    progress_hook: Optional[Callable[[dict], None]] = None,
) -> Path:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
    }
    if progress_hook is not None:
        ydl_opts["progress_hooks"] = [progress_hook]

    if format_selector == AUDIO_FORMAT_CODE:
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
    else:
        ydl_opts["format"] = format_selector
        ydl_opts["merge_output_format"] = "mp4"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    return _find_output_file(output_dir, str(info["id"]))


def _find_output_file(output_dir: Path, video_id: str) -> Path:
    matches = [p for p in output_dir.glob(f"{video_id}.*") if p.suffix != ".part"]
    if not matches:
        raise FileNotFoundError(f"No downloaded file found for id {video_id}.")
    return max(matches, key=lambda item: item.stat().st_mtime)
