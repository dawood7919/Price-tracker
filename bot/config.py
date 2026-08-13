"""Application configuration via pydantic-settings."""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MB = 1024 * 1024

CLOUD_SAFE_UPLOAD_BYTES = 49 * MB
LOCAL_SAFE_UPLOAD_BYTES = 2000 * MB


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = Field(..., description="Bot token from @BotFather")
    telegram_base_url: str | None = Field(default=None)
    telegram_api_id: str | None = Field(default=None)
    telegram_api_hash: str | None = Field(default=None)
    telegram_owner_id: int | None = Field(default=None)

    webhook_url: str | None = Field(default=None)
    render_external_url: str | None = None
    railway_public_domain: str | None = None
    webhook_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    port: int = Field(default=8080, alias="PORT")

    safe_upload_limit: int | None = Field(default=None)
    max_concurrent_downloads: int = 20
    max_playlist_items: int = 10
    max_scan_items: int = 10
    max_scan_probe_links: int = 30
    scan_probe_timeout_seconds: int = 20
    pdf_timeout_seconds: int = 60
    min_free_disk_mb: int = 500
    download_timeout_seconds: int = 1800
    extract_timeout_seconds: int = 60
    edit_throttle_seconds: float = 1.0
    upload_retries: int = 3
    upload_call_timeout_seconds: int = 3600

    download_dir: Path = Path("/tmp/video-bot-downloads")
    ytdlp_cookies_file: str | None = None
    ytdlp_cookies_content: str | None = Field(default=None)

    secret_base_url: str = Field(default="https://www.wow.xxx/")
    secret_page_size: int = Field(default=15)
    secret_max_results: int = Field(default=60)

    # AdultColony-API (optional search backend)
    adultcolony_base_url: str | None = Field(
        default=None,
        description="Base URL of AdultColony API, e.g. http://127.0.0.1:3000",
    )

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
