"""Application configuration via pydantic-settings.

Every knob is an environment variable so the bot can run on Render,
locally, or with a Local Bot API Server without code changes
(feature flags instead of hard assumptions).
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MB = 1024 * 1024

# Telegram Cloud Bot API upload ceiling for bots is a hard 50MB, enforced by
# Telegram's servers — no client-side change can raise this without routing
# through a Local Bot API Server (see Config.uses_local_api below).
CLOUD_SAFE_UPLOAD_BYTES = 49 * MB
# Local Bot API Server ceiling is documented at ~2000MB. Set to exactly 2GB
# per requirement; note this removes the previous safety margin, so a file
# landing within a few MB of this exact boundary may occasionally fail on
# container overhead — that risk is accepted here per explicit request.
LOCAL_SAFE_UPLOAD_BYTES = 2000 * MB


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Telegram ---
    telegram_bot_token: str = Field(..., description="Bot token from @BotFather")
    telegram_base_url: str | None = Field(
        default=None,
        description="Base URL of a Local Bot API Server, e.g. http://localhost:8081. "
        "When set, the safe upload limit rises to 2GB. Normally left unset and "
        "filled in automatically by start.sh once it launches the local server "
        "(see telegram_api_id/telegram_api_hash below).",
    )
    telegram_api_id: str | None = Field(
        default=None,
        description="From https://my.telegram.org (API development tools). "
        "Together with telegram_api_hash, start.sh uses this to launch a Local "
        "Bot API Server automatically. Leave both unset to stay on the 50MB "
        "Cloud Bot API.",
    )
    telegram_api_hash: str | None = Field(default=None, description="See telegram_api_id.")
    telegram_owner_id: int | None = Field(
        default=None,
        description="Your own numeric Telegram user ID (e.g. from @userinfobot). "
        "Required for /setcookies — without it the command is disabled for "
        "everyone, since accepting a cookies file from any random user would "
        "let a stranger overwrite the bot's shared login session.",
    )

    # --- Webhook / server ---
    webhook_url: str | None = Field(
        default=None,
        description="Public base URL (e.g. https://myapp.onrender.com). Falls back to "
        "RENDER_EXTERNAL_URL, then to https://<RAILWAY_PUBLIC_DOMAIN>. If none of "
        "these are set, the bot runs in polling mode.",
    )
    render_external_url: str | None = None  # Render injects this automatically
    # Railway injects this automatically too, but as a bare domain (no scheme) —
    # unlike Render's RENDER_EXTERNAL_URL, which is already a full https:// URL.
    railway_public_domain: str | None = None
    webhook_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    port: int = Field(default=8080, alias="PORT")

    # --- Limits ---
    safe_upload_limit: int | None = Field(
        default=None,
        description="Override the max bytes per uploaded part. "
        "Defaults to 49MB (cloud) or 2000MB (local server).",
    )
    # No per-user quota or per-user concurrency cap (removed on request). This
    # global cap is the only safety valve protecting the server itself from
    # unbounded simultaneous yt-dlp/ffmpeg processes; set high, not removed.
    max_concurrent_downloads: int = 20
    max_playlist_items: int = 10
    # /scan: crawling an index page for sub-page links. max_scan_items caps
    # how many *confirmed* videos get queued; max_scan_probe_links caps how
    # many raw links get test-probed with yt-dlp before giving up (most
    # links on a page won't be videos, so this is a separate, looser cap).
    max_scan_items: int = 10
    max_scan_probe_links: int = 30
    scan_probe_timeout_seconds: int = 20
    pdf_timeout_seconds: int = 60
    min_free_disk_mb: int = 500
    download_timeout_seconds: int = 1800
    extract_timeout_seconds: int = 90
    # 1s matches "update every second"; Telegram allows roughly this rate per
    # chat. Under many *simultaneous* jobs this adds more edit calls overall
    # (global bot-wide rate limits, not per-chat), which is why this remains
    # configurable instead of hardcoded.
    edit_throttle_seconds: float = 1.0
    upload_retries: int = 3
    # How long to wait for Telegram's response after an upload finishes
    # sending. With a Local Bot API Server, that server relays the file to
    # Telegram's real servers *synchronously* — our request doesn't get a
    # response until that real (possibly slow) internet upload completes, not
    # just until we're done handing bytes to the local server over loopback.
    # The general per-request read_timeout (60s) is deliberately left short
    # for responsiveness elsewhere (status edits, health checks); only the
    # actual upload calls use this much longer one.
    upload_call_timeout_seconds: int = 3600

    # --- Downloads ---
    download_dir: Path = Path("/tmp/video-bot-downloads")
    ytdlp_cookies_file: str | None = None
    ytdlp_cookies_content: str | None = Field(
        default=None,
        description="Raw contents of a Netscape-format cookies.txt exported from your "
        "own logged-in browser session (needed for sensitive/protected tweets etc.). "
        "Written to disk once at startup and used as ytdlp_cookies_file — set this as "
        "a private (sync: false) Render env var, never commit a cookies file to git.",
    )

    # --- AI (OpenAI-compatible; default targets Groq free tier) ---
    ai_api_key: str | None = Field(
        default=None,
        description="API key for an OpenAI-compatible chat API (e.g. Groq free key).",
    )
    ai_base_url: str = Field(
        default="https://api.groq.com/openai/v1",
        description="Base URL ending before /chat/completions.",
    )
    ai_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Model id on that provider.",
    )
    ai_timeout_seconds: float = 60.0

    @field_validator("telegram_bot_token")
    @classmethod
    def _token_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("TELEGRAM_BOT_TOKEN must not be empty")
        return v.strip()

    @property
    def effective_webhook_url(self) -> str | None:
        url = self.webhook_url or self.render_external_url
        if not url and self.railway_public_domain:
            url = f"https://{self.railway_public_domain}"
        return url.rstrip("/") if url else None

    @property
    def uses_local_api(self) -> bool:
        return bool(self.telegram_base_url)

    @property
    def upload_limit_bytes(self) -> int:
        if self.safe_upload_limit:
            return self.safe_upload_limit
        return LOCAL_SAFE_UPLOAD_BYTES if self.uses_local_api else CLOUD_SAFE_UPLOAD_BYTES


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config()  # type: ignore[call-arg]
