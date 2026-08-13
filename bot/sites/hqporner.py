"""HQPorner search + resolve embed (mydaddy/bigdaddy) for yt-dlp."""

from __future__ import annotations

import logging
import re
from html import unescape
from urllib.parse import quote_plus, unquote, urljoin

import aiohttp

logger = logging.getLogger(__name__)

HOME = "https://hqporner.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_VIDEO_ANY = re.compile(
    r"/hdporn/(?P<id>\d+)-(?P<slug>[a-zA-Z0-9_\-.]+?)\.html",
    re.I,
)
_IMG_NEAR = re.compile(
    r'(?:src|data-src|data-original)=["\']([^"\']+\.(?:jpg|jpeg|png|webp)[^"\']*)["\']',
    re.I,
)

# iframe / data-src / JS string embeds
_IFRAME_SRC = re.compile(
    r'(?:iframe[^>]+(?:src|data-src)|["\'](?:src|data-src)["\']\s*:)\s*["\']([^"\']+)["\']',
    re.I,
)
_EMBED_URL = re.compile(
    r"https?://(?:www\.)?(?:mydaddy\.cc|bigdaddy\.cc|flyflv\.com|hqwo\.cc)/[^\s\"'<>]+",
    re.I,
)
_EMBED_PROTOREL = re.compile(
    r"//(?:www\.)?(?:mydaddy\.cc|bigdaddy\.cc|flyflv\.com|hqwo\.cc)/[^\s\"'<>]+",
    re.I,
)
_MP4_URL = re.compile(
    r"https?://[^\s\"'<>]+\.mp4(?:\?[^\s\"'<>]*)?",
    re.I,
)
# JS: video source lists often look like file:"https://...mp4" or file:'...'
_JS_FILE = re.compile(
    r"(?:file|src|videoUrl|url)\s*[:=]\s*[\"'](https?://[^\"']+\.mp4[^\"']*)[\"']",
    re.I,
)


def _title_from_slug(slug: str) -> str:
    text = unquote(slug).replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return unescape(text).title() or "Video"


def _headers(referer: str = HOME + "/") -> dict[str, str]:
    return {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
    }


def _abs(url: str, base: str = HOME) -> str:
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return urljoin(base, url)
    return url


async def search(query: str, *, timeout: float = 20.0, limit: int = 40) -> list[dict]:
    q = query.strip()
    if len(q) < 2:
        return []

    urls = [
        f"{HOME}/?q={quote_plus(q)}",
        f"{HOME}/?q={quote_plus(q)}&p=2",
    ]
    seen: set[str] = set()
    out: list[dict] = []
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)

    async with aiohttp.ClientSession(timeout=timeout_cfg, headers=_headers()) as session:
        for page_url in urls:
            try:
                async with session.get(page_url, allow_redirects=True) as resp:
                    logger.info("hqporner GET %s -> %s", page_url, resp.status)
                    if resp.status >= 400:
                        continue
                    html = await resp.text(errors="ignore")
            except Exception as exc:
                logger.warning("hqporner fetch failed %s: %s", page_url, exc)
                continue

            for m in _VIDEO_ANY.finditer(html):
                path = m.group(0)
                if not path.startswith("/hdporn/"):
                    continue
                url = HOME + path.split("?")[0]
                if url in seen:
                    continue
                seen.add(url)
                slug = m.group("slug")
                thumb = None
                window = html[max(0, m.start() - 400) : m.end() + 200]
                im = _IMG_NEAR.search(window)
                if im:
                    thumb = _abs(im.group(1).strip(), page_url)
                out.append(
                    {
                        "url": url,
                        "title": _title_from_slug(slug),
                        "thumb": thumb,
                    }
                )
                if len(out) >= limit:
                    return out

    logger.info("hqporner search %r -> %d results", q, len(out))
    return out


def _find_embed(html: str) -> str | None:
    for m in _IFRAME_SRC.finditer(html):
        src = _abs(m.group(1))
        if re.search(r"mydaddy|bigdaddy|flyflv|hqwo|embed|player", src, re.I):
            return src
    m = _EMBED_URL.search(html)
    if m:
        return m.group(0)
    m = _EMBED_PROTOREL.search(html)
    if m:
        return "https:" + m.group(0)
    return None


def _pick_best_mp4(candidates: list[str]) -> str | None:
    if not candidates:
        return None

    def score(u: str) -> int:
        u_l = u.lower()
        for i, tag in enumerate(("2160", "4k", "1440", "1080", "720", "480", "360")):
            if tag in u_l:
                return 100 - i
        return 0

    return sorted(set(candidates), key=score, reverse=True)[0]


async def resolve_playable_url(page_url: str, *, timeout: float = 25.0) -> str:
    """HQPorner page → embed → mp4 if possible."""
    if "hqporner.com" not in page_url.lower():
        return page_url

    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=timeout_cfg, headers=_headers()) as session:
        try:
            async with session.get(page_url, allow_redirects=True) as resp:
                logger.info("hqporner resolve page %s -> %s", page_url, resp.status)
                if resp.status >= 400:
                    return page_url
                html = await resp.text(errors="ignore")
        except Exception as exc:
            logger.warning("hqporner page fetch failed: %s", exc)
            return page_url

        # direct mp4 on page?
        mp4 = _pick_best_mp4(_MP4_URL.findall(html) + _JS_FILE.findall(html))
        if mp4:
            logger.info("hqporner page mp4: %s", mp4[:120])
            return mp4

        embed = _find_embed(html)
        if not embed:
            logger.warning(
                "hqporner: no embed (html_len=%d sample=%r)",
                len(html),
                html[html.lower().find("iframe") : html.lower().find("iframe") + 200]
                if "iframe" in html.lower()
                else html[:200],
            )
            return page_url

        logger.info("hqporner embed: %s", embed)

        try:
            async with session.get(
                embed, allow_redirects=True, headers=_headers(referer=page_url)
            ) as resp:
                logger.info("hqporner embed HTTP %s", resp.status)
                if resp.status >= 400:
                    return embed
                emb_html = await resp.text(errors="ignore")
        except Exception as exc:
            logger.warning("embed fetch failed: %s", exc)
            return embed

        mp4 = _pick_best_mp4(_MP4_URL.findall(emb_html) + _JS_FILE.findall(emb_html))
        if mp4:
            logger.info("hqporner embed mp4: %s", mp4[:120])
            return mp4

        # nested iframe inside player page
        nested = _find_embed(emb_html)
        if nested and nested != embed:
            logger.info("hqporner nested embed: %s", nested)
            try:
                async with session.get(
                    nested, allow_redirects=True, headers=_headers(referer=embed)
                ) as resp:
                    if resp.status < 400:
                        nested_html = await resp.text(errors="ignore")
                        mp4 = _pick_best_mp4(
                            _MP4_URL.findall(nested_html) + _JS_FILE.findall(nested_html)
                        )
                        if mp4:
                            return mp4
            except Exception:
                pass
            return nested

        return embed


def resolve_playable_url_sync(page_url: str, timeout: float = 25.0) -> str:
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return asyncio.run(resolve_playable_url(page_url, timeout=timeout))
    except RuntimeError:
        pass
    return asyncio.run(resolve_playable_url(page_url, timeout=timeout))
