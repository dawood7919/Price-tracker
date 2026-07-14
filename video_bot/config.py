import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _get_id_set(name: str) -> set[str]:
    value = os.getenv(name, "")
    return {item.strip() for item in value.split(",") if item.strip()}


BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Set it in your .env file or environment variables.")

_owner_id = os.getenv("OWNER_TELEGRAM_ID")
if not _owner_id:
    raise RuntimeError(
        "OWNER_TELEGRAM_ID is missing. This bot only serves its configured owner; "
        "get your numeric Telegram ID from @userinfobot and set it in .env."
    )
OWNER_TELEGRAM_ID = int(_owner_id)

# Optional: enables the Creative Commons license check for YouTube videos
# that aren't on the owner's own channel. Without it, such videos are rejected.
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY") or None

# YouTube channel IDs the owner controls; videos from these channels are
# always authorized regardless of license.
OWNED_YOUTUBE_CHANNEL_IDS = _get_id_set("OWNED_YOUTUBE_CHANNEL_IDS")

# Domains explicitly permitted for downloading (their ToS allows it).
ALLOWED_DOMAINS = _get_id_set("ALLOWED_DOMAINS") or {"archive.org"}

MAX_VIDEO_SIZE_MB = _get_int("MAX_VIDEO_SIZE_MB", 50)
MAX_VIDEO_SIZE_BYTES = MAX_VIDEO_SIZE_MB * 1024 * 1024

TEMP_FOLDER = Path(os.getenv("TEMP_FOLDER", "temp_downloads"))
TEMP_FOLDER.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = os.getenv("DATABASE_PATH", "video_bot.db")
