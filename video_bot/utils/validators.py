import re
from urllib.parse import urlparse

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def is_valid_url(url: str) -> bool:
    """Basic structural check: has an http(s) scheme and a network location."""
    stripped = url.strip()
    if not _URL_RE.match(stripped):
        return False
    return bool(urlparse(stripped).netloc)


def extract_domain(url: str) -> str:
    netloc = urlparse(url.strip()).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def human_readable_size(num_bytes: int | None) -> str:
    if not num_bytes:
        return "Unknown"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def human_readable_duration(seconds: int | None) -> str:
    if not seconds:
        return "Unknown"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
