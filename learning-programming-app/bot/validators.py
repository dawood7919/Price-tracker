"""URL validation — fast, no network. Runs in the bot process before queueing."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


class URLValidator:
    @staticmethod
    def extract_url(text: str) -> str | None:
        """Pull the first http(s) URL out of a message, if any."""
        if not text:
            return None
        match = _URL_RE.search(text)
        return match.group(0).rstrip(").,>»\"'") if match else None

    @staticmethod
    def validate(url: str) -> tuple[bool, str]:
        """Return (ok, error_message). Rejects malformed and private-network URLs."""
        if not url or not url.strip():
            return False, "الرسالة مفيهاش لينك."
        try:
            parsed = urlparse(url.strip())
        except ValueError:
            return False, "اللينك مش مفهوم."
        if parsed.scheme not in ("http", "https"):
            return False, "اللينك لازم يبدأ بـ http أو https."
        if not parsed.netloc:
            return False, "اللينك ناقص اسم الموقع."
        host = parsed.hostname or ""
        if host in ("localhost",):
            return False, "اللينك ده مش مسموح."
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return False, "اللينك ده مش مسموح."
        except ValueError:
            pass  # a normal hostname, not an IP
        return True, ""
