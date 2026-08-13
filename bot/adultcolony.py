"""Client for AdultColony-API.

Setup (on the Ubuntu server):

  docker pull snowball60/adultcolony-api:latest
  docker rm -f adultcolony
  docker run -d --name adultcolony --restart unless-stopped -p 3000:3000 \
    snowball60/adultcolony-api:latest

  # In learning-programming-app/.env:
  ADULTCOLONY_BASE_URL=http://172.17.0.1:3000

  # When starting video-bot, also pass host-gateway if needed:
  docker run ... --add-host=host.docker.internal:host-gateway \
    -e ADULTCOLONY_BASE_URL=http://host.docker.internal:3000 ...
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus, urljoin

import aiohttp

logger = logging.getLogger(__name__)

# API path prefix per site (must match AdultColony-API routes)
AC_SITES: dict[str, str] = {
    "pornhub": "pornhub",
    "xvideos": "xvideos",
    "xnxx": "xnxx",
    "xhamster": "xhamster",
    "spankbang": "spankbang",
    "eporner": "eporner",
    "hentaifox": "hentaifox",
    "hentaicity": "hentaicity",
    "xasiat": "xasiat",
    "javhdtoday": "javhdtoday",
    "javtsunami": "javtsunami",
    "javgiga": "javgiga",
    "missav": "missav",
}

SITE_HOME: dict[str, str] = {
    "pornhub": "https://www.pornhub.com",
    "xvideos": "https://www.xvideos.com",
    "xnxx": "https://www.xnxx.com",
    "xhamster": "https://xhamster.com",
    "spankbang": "https://spankbang.com",
    "eporner": "https://www.eporner.com",
    "hentaifox": "https://hentaifox.com",
    "hentaicity": "https://www.hentaicity.com",
    "xasiat": "https://www.xasiat.com",
    "javhdtoday": "https://javhdtoday.com",
    "javtsunami": "https://javtsunami.com",
    "javgiga": "https://javgiga.com",
    "missav": "https://missav.com",
}


def _pick_url(item: dict[str, Any], site: str) -> str | None:
    home = SITE_HOME.get(site, "")
    for key in ("link", "url", "video", "href", "page"):
        val = item.get(key)
        if not isinstance(val, str) or not val.strip():
            continue
        val = val.strip()
        if val.startswith("javascript"):
            continue
        if val.startswith("http://") or val.startswith("https://"):
            return val
        if val.startswith("//"):
            return "https:" + val
        if val.startswith("/") and home:
            return urljoin(home, val)
    # rebuild from id for pornhub
    vid = item.get("id")
    if site == "pornhub" and isinstance(vid, str) and vid:
        return f"https://www.pornhub.com/view_video.php?viewkey={vid}"
    return None


def _pick_title(item: dict[str, Any]) -> str:
    for key in ("title", "name", "text"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return "Video"


def _pick_thumb(item: dict[str, Any]) -> str | None:
    for key in ("image", "thumb", "thumbnail", "preview"):
        val = item.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
        if isinstance(val, str) and val.startswith("//"):
            return "https:" + val
    return None


def _normalize_list(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("results", "videos", "items", "search"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
    for key in ("results", "videos", "items", "search"):
        inner = payload.get(key)
        if isinstance(inner, list):
            return [x for x in inner if isinstance(x, dict)]
    return []


async def probe(base_url: str, timeout: float = 8.0) -> str:
    """Return a short status string for diagnostics."""
    root = base_url.rstrip("/")
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    try:
        async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
            async with session.get(f"{root}/api/stats") as resp:
                return f"stats HTTP {resp.status}"
    except Exception as exc:
        return f"unreachable: {exc}"


async def search(
    base_url: str,
    site: str,
    query: str,
    *,
    page: int = 1,
    limit: int = 40,
    timeout: float = 25.0,
) -> list[dict]:
    prefix = AC_SITES.get(site)
    if not prefix:
        return []
    q = query.strip()
    if len(q) < 2:
        return []

    root = base_url.rstrip("/")
    url = f"{root}/{prefix}/search?query={quote_plus(q)}&page={page}"
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)

    try:
        async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
            async with session.get(url) as resp:
                text = await resp.text()
                logger.info(
                    "adultcolony %s -> %s bytes=%d",
                    url,
                    resp.status,
                    len(text),
                )
                if resp.status >= 400:
                    logger.warning("adultcolony error: %s", text[:400])
                    return []
                try:
                    payload = await resp.json(content_type=None)
                except Exception:
                    # retry parse from text
                    import json

                    payload = json.loads(text)
    except Exception as exc:
        logger.warning("adultcolony request failed (%s): %s", url, exc)
        return []

    raw_items = _normalize_list(payload)
    if not raw_items and isinstance(payload, dict):
        logger.warning(
            "adultcolony empty parse keys=%s success=%s",
            list(payload.keys())[:10],
            payload.get("success"),
        )

    out: list[dict] = []
    seen: set[str] = set()
    for item in raw_items:
        link = _pick_url(item, site)
        if not link or link in seen:
            continue
        seen.add(link)
        out.append(
            {
                "url": link,
                "title": _pick_title(item),
                "thumb": _pick_thumb(item),
            }
        )
        if len(out) >= limit:
            break

    logger.info("adultcolony %s %r -> %d", site, q, len(out))
    return out
