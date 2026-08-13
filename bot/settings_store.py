"""Persistent bot settings (active search site, etc.)."""

from __future__ import annotations

import json
import logging
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
_SETTINGS_PATH = Path("/tmp/video-bot-settings.json")


def _load() -> dict[str, Any]:
    try:
        if _SETTINGS_PATH.is_file():
            return json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
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
