"""Persistent bot settings (active search site, etc.)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SEARCH_SITES: dict[str, dict[str, str]] = {
    "wow": {"label": "WOW.XXX", "home": "https://www.wow.xxx/"},
    "pornhub": {"label": "Pornhub", "home": "https://www.pornhub.com/"},
    "xvideos": {"label": "XVideos", "home": "https://www.xvideos.com/"},
    "xnxx": {"label": "XNXX", "home": "https://www.xnxx.com/"},
    "xhamster": {"label": "XHamster", "home": "https://xhamster.com/"},
    "spankbang": {"label": "SpankBang", "home": "https://spankbang.com/"},
    "eporner": {"label": "Eporner", "home": "https://www.eporner.com/"},
    "4kporno": {"label": "4KPorno", "home": "https://www.4kporno.xxx/"},
    "hqporner": {"label": "HQ Porner", "home": "https://hqporner.com/"},
    "noodlemagazine": {"label": "NoodleMagazine", "home": "https://noodlemagazine.com/"},
    "perverzija": {"label": "Perverzija", "home": "https://tube.perverzija.com/"},
    # AdultColony-only extras (search via API when ADULTCOLONY_BASE_URL is set)
    "hentaifox": {"label": "HentaiFox", "home": "https://hentaifox.com/"},
    "hentaicity": {"label": "HentaiCity", "home": "https://www.hentaicity.com/"},
    "xasiat": {"label": "XAsiat", "home": "https://www.xasiat.com/"},
    "javhdtoday": {"label": "JavHDToday", "home": "https://javhdtoday.com/"},
    "javtsunami": {"label": "JavTsunami", "home": "https://javtsunami.com/"},
    "javgiga": {"label": "JavGiga", "home": "https://javgiga.com/"},
    "missav": {"label": "MissAV", "home": "https://missav.com/"},
}

DEFAULT_SITE = "wow"

# Torrent search providers. Official ISOs are ON by default.
# Public indexes are OFF by default — enable them from /settings.
TORRENT_SITES: dict[str, dict[str, str]] = {
    # --- official ---
    "ubuntu": {"label": "Ubuntu الرسمي", "home": "https://releases.ubuntu.com/"},
    "debian": {"label": "Debian الرسمي", "home": "https://www.debian.org/CD/torrent-cd/"},
    "fedora": {"label": "Fedora الرسمي", "home": "https://fedoraproject.org/torrents/"},
    # --- public (optional, enable in /settings) ---
    "piratebay": {"label": "Pirate Bay", "home": "https://thepiratebay.org/"},
    "yts": {"label": "YTS (أفلام)", "home": "https://yts.mx/"},
    "eztv": {"label": "EZTV (مسلسلات)", "home": "https://eztv.re/"},
    "solid": {"label": "SolidTorrents", "home": "https://solidtorrents.to/"},
    "torrcsv": {"label": "Torrents-CSV", "home": "https://torrents-csv.com/"},
    "nyaa": {"label": "Nyaa (أنمي)", "home": "https://nyaa.si/"},
    "x1337": {"label": "1337x", "home": "https://www.1377x.to/"},
}
DEFAULT_TORRENT_SOURCES = ("ubuntu", "debian", "fedora")

_SETTINGS_PATH = Path(os.environ.get("BOT_SETTINGS_PATH", "/tmp/video-bot-settings.json"))


def _load() -> dict[str, Any]:
    try:
        if _SETTINGS_PATH.is_file():
            return json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to load settings from %s", _SETTINGS_PATH)
    return {}


def _save(data: dict[str, Any]) -> None:
    try:
        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logger.exception("Failed to save settings")


def get_active_site() -> str:
    data = _load()
    site = str(data.get("search_site") or DEFAULT_SITE).lower()
    if site not in SEARCH_SITES:
        return DEFAULT_SITE
    return site


def set_active_site(site: str) -> str:
    site = site.lower().strip()
    if site not in SEARCH_SITES:
        raise ValueError(f"unknown site: {site}")
    data = _load()
    data["search_site"] = site
    _save(data)
    return site


def site_label(site: str | None = None) -> str:
    s = site or get_active_site()
    return SEARCH_SITES.get(s, SEARCH_SITES[DEFAULT_SITE])["label"]


def _normalize_torrent_sources(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        candidates = value.split(",")
    elif isinstance(value, (list, tuple)):
        candidates = [str(item) for item in value]
    else:
        candidates = []
    selected = tuple(
        dict.fromkeys(item.strip().lower() for item in candidates if item.strip().lower() in TORRENT_SITES)
    )
    return selected


def get_active_torrent_sources(configured_sources: str | None = None) -> tuple[str, ...]:
    """Return persistent user choices, falling back to the env configuration."""
    saved = _load().get("torrent_sources")
    selected = _normalize_torrent_sources(saved)
    if selected:
        return selected
    selected = _normalize_torrent_sources(configured_sources)
    return selected or DEFAULT_TORRENT_SOURCES


def toggle_torrent_source(source_id: str, configured_sources: str | None = None) -> tuple[str, ...]:
    """Toggle one provider while always preserving at least one active source."""
    source_id = source_id.strip().lower()
    if source_id not in TORRENT_SITES:
        raise ValueError(f"unknown torrent source: {source_id}")
    current = list(get_active_torrent_sources(configured_sources))
    if source_id in current:
        if len(current) == 1:
            return tuple(current)
        current.remove(source_id)
    else:
        current.append(source_id)
    data = _load()
    data["torrent_sources"] = current
    _save(data)
    return tuple(current)


def torrent_site_label(source_id: str) -> str:
    return TORRENT_SITES[source_id]["label"]
