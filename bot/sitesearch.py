"""Dispatch search to the active site (AdultColony when configured)."""

from __future__ import annotations

import logging
import re
import time
from html import unescape
from urllib.parse import quote_plus, urljoin, urlparse, unquote_plus

import aiohttp

from . import adultcolony
from .config import Config
from .settings_store import SEARCH_SITES, get_active_site
from .sites import eporner as eporner_mod
from .sites import hqporner as hqporner_mod
from .sites import kporno4k as kporno4k_mod
from .sites import noodlemagazine as noodlemagazine_mod
from .sites import perverzija as perverzija_mod

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_CACHE: dict[str, tuple[list[dict], float]] = {}
_CACHE_TTL = 45.0
_HREF_ONLY_RE = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.I)


def _headers(referer: str) -> dict[str, str]:
    return {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
    }


def _slug_title(slug: str) -> str:
    s = unquote_plus(slug).replace("-", " ").replace("_", " ").replace("+", " ")
    s = re.sub(r"\.html?$", "", s, flags=re.I)
    s = re.sub(r"^\d+-", "", s)
    return unescape(s).strip().title() or "Video"


def _search_urls(site: str, query: str) -> list[str]:
    q = query.strip()
    if site == "wow":
        slug = "-".join(q.split())
        base = SEARCH_SITES["wow"]["home"]
        return [urljoin(base, f"search/{slug}/"), urljoin(base, f"search/{slug}/relevance/")]
    if site == "pornhub":
        return [
            f"https://www.pornhub.com/video/search?search={quote_plus(q)}",
            f"https://www.pornhub.com/video/search?search={quote_plus(q)}&page=2",
        ]
    if site == "perverzija":
        return [
            f"https://tube.perverzija.com/search/{quote_plus(q)}/",
            f"https://tube.perverzija.com/search/{quote_plus(q)}/page/2/",
        ]
    return []


async def _fetch(url: str, timeout: float, referer: str) -> tuple[str, str]:
    timeout_cfg = aiohttp.ClientTimeout(total=min(timeout, 25))
    async with aiohttp.ClientSession(timeout=timeout_cfg, headers=_headers(referer)) as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status}")
            text = await resp.text(errors="ignore")
            if not text or len(text) < 200:
                raise RuntimeError("empty page")
            return str(resp.url), text


def _same_site(home_host: str, host: str) -> bool:
    if not host:
        return True
    h = host.lower().removeprefix("www.")
    home = home_host.lower().removeprefix("www.")
    if home in h or h in home:
        return True
    return home.split(".")[-2:] == h.split(".")[-2:]


def _normalize(site: str, absolute: str) -> tuple[str, str] | None:
    parsed = urlparse(absolute)
    path = parsed.path or ""

    if site == "pornhub":
        m = re.search(r"viewkey=([a-zA-Z0-9]+)", absolute, re.I)
        if not m:
            return None
        return f"https://www.pornhub.com/view_video.php?viewkey={m.group(1)}", m.group(1)

    if site == "wow":
        m = re.search(r"/videos/([^/?#]+)/?", path, re.I)
        if not m:
            return None
        return f"https://www.wow.xxx/videos/{m.group(1)}/", m.group(1)

    if site == "perverzija":
        m = re.search(r"/([^/?#]+)/?$", path, re.I)
        if not m or path in ("/", ""):
            return None
        slug = m.group(1)
        if slug in ("search", "page", "category", "tag"):
            return None
        return f"https://tube.perverzija.com/{slug}/", slug

    return None


def _extract_generic(site: str, page_url: str, html: str) -> list[dict]:
    home = SEARCH_SITES[site]["home"]
    home_host = urlparse(home).netloc.removeprefix("www.")
    seen: set[str] = set()
    out: list[dict] = []

    for href in _HREF_ONLY_RE.findall(html):
        if href.startswith("//"):
            href = "https:" + href
        absolute = urljoin(page_url, href).split("#", 1)[0]
        parsed = urlparse(absolute)
        host = (parsed.netloc or urlparse(page_url).netloc).removeprefix("www.")
        if parsed.netloc and not _same_site(home_host, host):
            continue
        normed = _normalize(site, absolute)
        if not normed and href.startswith("/"):
            normed = _normalize(site, urljoin(home, href))
        if not normed:
            continue
        norm, slug = normed
        if norm in seen:
            continue
        seen.add(norm)
        out.append({"url": norm, "title": _slug_title(slug), "thumb": None})
    return out


async def search_videos(query: str, config: Config, site: str | None = None) -> list[dict]:
    site = (site or get_active_site()).lower()
    if site not in SEARCH_SITES:
        site = "wow"
    q = query.strip()
    if len(q) < 2:
        return []

    cache_key = f"{site}:{q.lower()}"
    now = time.monotonic()
    cached = _CACHE.get(cache_key)
    if cached and now - cached[1] < _CACHE_TTL:
        return list(cached[0])

    limit = max(getattr(config, "secret_max_results", 40), 20)
    timeout = float(config.extract_timeout_seconds)

    # Prefer AdultColony when configured and site is supported by it
    ac_base = (config.adultcolony_base_url or "").strip()
    if ac_base and site in adultcolony.AC_SITES:
        merged = await adultcolony.search(
            ac_base, site, q, limit=limit, timeout=timeout
        )
        if merged:
            _CACHE[cache_key] = (list(merged), now)
            return merged
        logger.info("adultcolony empty for %s — falling back to local scraper", site)

    if site == "hqporner":
        merged = await hqporner_mod.search(q, timeout=timeout, limit=limit)
        if merged:
            _CACHE[cache_key] = (list(merged), now)
        return merged

    if site == "eporner":
        merged = await eporner_mod.search(q, timeout=timeout, limit=limit)
        if merged:
            _CACHE[cache_key] = (list(merged), now)
        return merged

    if site == "4kporno":
        merged = await kporno4k_mod.search(q, timeout=timeout, limit=limit)
        if merged:
            _CACHE[cache_key] = (list(merged), now)
        return merged

    if site == "noodlemagazine":
        merged = await noodlemagazine_mod.search(q, timeout=timeout, limit=limit)
        if merged:
            _CACHE[cache_key] = (list(merged), now)
        return merged

    if site == "perverzija":
        merged = await perverzija_mod.search(q, timeout=timeout, limit=limit)
        if merged:
            _CACHE[cache_key] = (list(merged), now)
        return merged

    home = SEARCH_SITES[site]["home"]
    seen: set[str] = set()
    merged: list[dict] = []

    for page_url in _search_urls(site, q):
        try:
            final, html = await _fetch(page_url, timeout, home)
        except Exception as exc:
            logger.warning("sitesearch %s failed %s: %s", site, page_url, exc)
            continue
        for item in _extract_generic(site, final, html):
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            merged.append(item)
        if len(merged) >= limit:
            break

    merged = merged[:limit]
    if merged:
        _CACHE[cache_key] = (list(merged), now)
    else:
        logger.info("sitesearch %s query=%r -> 0", site, q)
    return merged
