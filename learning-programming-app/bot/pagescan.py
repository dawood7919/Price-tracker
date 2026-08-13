"""Extract candidate video-page links from a listing/index page.

Used by /scan: given a page that links out to individual video pages (a
gallery/listing style page), collect same-domain sub-links. This module
does no site-specific scraping or video detection at all — it just finds
links; the downloader layer (yt-dlp's own extractors) is what actually
decides which of those links are real, downloadable videos by trying each
one. The probe *is* the filter, which is what makes this work generically
across arbitrary sites instead of only ones we specifically coded for.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import aiohttp

# A page can link to thousands of things; this bounds how many distinct
# sub-links we even keep before handing them off to be probed one by one.
MAX_LINKS_COLLECTED = 200

_ASSET_EXTS = (
    ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".pdf", ".zip", ".woff", ".woff2", ".ttf", ".json", ".xml",
)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class PageScanError(RuntimeError):
    pass


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)


async def extract_page_links(url: str, timeout_seconds: float) -> list[str]:
    """Fetch *url* and return deduped, same-domain sub-page links (in the
    order they appear on the page)."""
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            headers={"User-Agent": _USER_AGENT},
        ) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status >= 400:
                    raise PageScanError(f"الصفحة رجّعت خطأ {resp.status}")
                html_text = await resp.text(errors="ignore")
    except aiohttp.ClientError as exc:
        raise PageScanError(f"مقدرتش أفتح الصفحة: {exc}") from exc

    parser = _LinkCollector()
    parser.feed(html_text)

    base_domain = urlparse(url).netloc
    seen: set[str] = {url}
    links: list[str] = []
    for href in parser.hrefs:
        if len(links) >= MAX_LINKS_COLLECTED:
            break
        absolute = urljoin(url, href).split("#", 1)[0]
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc != base_domain:
            continue
        if absolute.lower().endswith(_ASSET_EXTS):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append(absolute)
    return links
