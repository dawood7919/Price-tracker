import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import requests

from config import ALLOWED_DOMAINS, OWNED_YOUTUBE_CHANNEL_IDS, YOUTUBE_API_KEY
from utils.validators import extract_domain

logger = logging.getLogger(__name__)

YOUTUBE_DOMAINS = {"youtube.com", "youtu.be", "m.youtube.com"}


@dataclass
class AuthorizationResult:
    allowed: bool
    reason: str


def _extract_youtube_video_id(url: str) -> Optional[str]:
    domain = extract_domain(url)
    if domain not in YOUTUBE_DOMAINS:
        return None

    parsed = urlparse(url)
    if domain == "youtu.be":
        return parsed.path.lstrip("/") or None

    match = re.search(r"[?&]v=([^&]+)", url)
    if match:
        return match.group(1)

    if "/shorts/" in parsed.path:
        return parsed.path.split("/shorts/")[-1].split("/")[0]

    return None


def _is_creative_commons(video_id: str) -> bool:
    """Checks the YouTube Data API's status.license field. Requires YOUTUBE_API_KEY;
    without it we can't verify licensing, so this always reports False (not authorized)."""
    if not YOUTUBE_API_KEY:
        return False

    try:
        response = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "status", "id": video_id, "key": YOUTUBE_API_KEY},
            timeout=10,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
    except requests.RequestException:
        logger.exception("YouTube license check failed for video %s", video_id)
        return False

    if not items:
        return False
    return items[0]["status"].get("license") == "creativeCommon"


def authorize(url: str, channel_id: Optional[str] = None) -> AuthorizationResult:
    """The single point of truth for whether a download may proceed.

    Three independently checkable authorization paths:
      1. Domain is on the explicit allowlist (its ToS permits downloading).
      2. The video's channel_id matches one of the owner's configured channels.
      3. The YouTube video is licensed Creative Commons per the Data API.
    Everything else is rejected.
    """
    domain = extract_domain(url)

    if domain in ALLOWED_DOMAINS:
        return AuthorizationResult(True, f"'{domain}' is on the authorized domain allowlist.")

    video_id = _extract_youtube_video_id(url)
    if video_id:
        if channel_id and channel_id in OWNED_YOUTUBE_CHANNEL_IDS:
            return AuthorizationResult(True, "Video belongs to your configured YouTube channel.")
        if _is_creative_commons(video_id):
            return AuthorizationResult(True, "Video is licensed Creative Commons on YouTube.")
        return AuthorizationResult(
            False,
            "This YouTube video isn't from your own channel and isn't marked Creative Commons.",
        )

    return AuthorizationResult(False, f"'{domain}' isn't an authorized source.")
