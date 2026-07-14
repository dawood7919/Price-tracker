from typing import Optional

from utils.validators import human_readable_duration

WELCOME_MESSAGE = (
    "👋 Personal video download bot.\n\n"
    "Send a URL and I'll check if it's from an authorized source "
    "(your own YouTube channel, Creative Commons YouTube videos, or "
    "allowlisted domains like archive.org).\n\n"
    "Commands:\n"
    "/help - How this bot works\n"
    "/about - Authorization policy"
)

HELP_MESSAGE = (
    "Send me a video URL.\n\n"
    "I only download content you're authorized to download:\n"
    "• Videos from your own YouTube channel\n"
    "• Creative Commons licensed YouTube videos\n"
    "• Domains you've explicitly allowlisted (e.g. archive.org)\n\n"
    "Anything else is rejected automatically."
)

ABOUT_MESSAGE = (
    "This is a personal-use bot restricted to its owner.\n\n"
    "Authorization is enforced automatically, not just requested:\n"
    "1. Domain allowlist check\n"
    "2. Your configured YouTube channel ID match\n"
    "3. YouTube Data API Creative Commons license check\n\n"
    "It will not download arbitrary copyrighted content."
)

URL_RECEIVED = "✅ URL received.\n🔍 Detecting website..."
FETCHING_INFO = "📥 Getting video information..."
DOWNLOADING_MESSAGE = "⬇️ Downloading... 0%"
UPLOADING_MESSAGE = "📤 Uploading to Telegram..."
DONE_MESSAGE = "✅ Done."
CANCELLED_MESSAGE = "❌ Cancelled."


def video_info_message(title: str, duration: Optional[int]) -> str:
    return (
        f"📺 {title}\n"
        f"⏱ Duration: {human_readable_duration(duration)}\n\n"
        "Download?"
    )


def unsupported_source_message(reason: str) -> str:
    return f"⚠️ Can't process this link.\n{reason}"


def unauthorized_message(reason: str) -> str:
    return f"🚫 Not authorized.\n{reason}"


def downloading_progress_message(percent: float) -> str:
    return f"⬇️ Downloading... {percent:.0f}%"


def error_message(reason: str) -> str:
    return f"❌ Something went wrong.\n{reason}"


def owner_only_message() -> str:
    return "This is a private bot."


def expired_request_message() -> str:
    return "This request has expired. Send the URL again."
