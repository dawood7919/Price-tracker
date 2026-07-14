import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from config import ALLOWED_DOMAINS
from downloaders import authorization, ytdlp_engine
from utils.validators import extract_domain, is_valid_url

logger = logging.getLogger(__name__)

YOUTUBE_DOMAINS = {"youtube.com", "youtu.be", "m.youtube.com"}


class UnsupportedSourceError(Exception):
    pass


class UnauthorizedError(Exception):
    pass


@dataclass
class VideoInfo:
    url: str
    title: str
    duration: Optional[int]
    thumbnail: Optional[str]
    channel_id: Optional[str]


def _is_supported_domain(domain: str) -> bool:
    return domain in YOUTUBE_DOMAINS or domain in ALLOWED_DOMAINS


def get_video_info(url: str) -> VideoInfo:
    """Validates, fetches metadata, and authorizes a URL. Raises UnsupportedSourceError
    for URLs we don't even try, or UnauthorizedError for ones we can't verify authorization for."""
    if not is_valid_url(url):
        raise UnsupportedSourceError("That doesn't look like a valid URL.")

    domain = extract_domain(url)
    if not _is_supported_domain(domain):
        raise UnsupportedSourceError(f"'{domain}' isn't a supported or authorized source.")

    info = ytdlp_engine.extract_info(url)

    auth = authorization.authorize(url, channel_id=info.get("channel_id"))
    if not auth.allowed:
        raise UnauthorizedError(auth.reason)

    logger.info("Authorized download: %s (%s)", url, auth.reason)

    return VideoInfo(
        url=url,
        title=info.get("title") or "Unknown title",
        duration=info.get("duration"),
        thumbnail=info.get("thumbnail"),
        channel_id=info.get("channel_id"),
    )


def download_video(
    url: str,
    format_selector: str,
    output_dir: Path,
    progress_hook: Optional[Callable[[dict], None]] = None,
) -> Path:
    """Re-checks domain support before downloading. Does not re-run authorize() here —
    get_video_info() already gated the request; this is called only after that gate passed."""
    domain = extract_domain(url)
    if not _is_supported_domain(domain):
        raise UnsupportedSourceError(f"'{domain}' isn't a supported source.")
    return ytdlp_engine.download(url, format_selector, output_dir, progress_hook)
