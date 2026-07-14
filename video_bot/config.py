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


# Defaults are hardcoded so the bot runs with zero external configuration.
# An env var, if set, still overrides its matching default.
_DEFAULT_BOT_TOKEN = "1427827588:AAE9hCOIBp3l-2-pWNFf8nveQCzt-Nh3D7Y"
_DEFAULT_OWNER_TELEGRAM_ID = "1096429310"

BOT_TOKEN = os.getenv("BOT_TOKEN", _DEFAULT_BOT_TOKEN)
OWNER_TELEGRAM_ID = int(os.getenv("OWNER_TELEGRAM_ID", _DEFAULT_OWNER_TELEGRAM_ID))

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
