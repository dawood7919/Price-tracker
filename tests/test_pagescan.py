"""Integration tests for page-link extraction (real local aiohttp server)."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bot.pagescan import PageScanError, extract_page_links

SAMPLE_HTML = """
<html><body>
<a href="/video/1">Video 1</a>
<a href="/video/2">Video 2</a>
<a href="https://other-domain.example/x">external</a>
<a href="/style.css">asset</a>
<a href="javascript:void(0)">js</a>
<a href="mailto:test@example.com">mail</a>
<a href="/video/1#comments">dup with fragment</a>
</body></html>
"""


@pytest.fixture
async def server():
    async def handler(request):
        return web.Response(text=SAMPLE_HTML, content_type="text/html")

    app = web.Application()
    app.router.add_get("/", handler)
    srv = TestServer(app)
    await srv.start_server()
    yield srv
    await srv.close()


async def test_extracts_same_domain_links_deduped(server):
    base_url = str(server.make_url("/"))
    links = await extract_page_links(base_url, timeout_seconds=5)
    assert len(links) == 2
    assert any(link.endswith("/video/1") for link in links)
    assert any(link.endswith("/video/2") for link in links)


async def test_excludes_external_asset_and_non_http_links(server):
    base_url = str(server.make_url("/"))
    links = await extract_page_links(base_url, timeout_seconds=5)
    assert not any("other-domain" in link for link in links)
    assert not any(link.endswith(".css") for link in links)
    assert not any(link.startswith("javascript:") for link in links)
    assert not any(link.startswith("mailto:") for link in links)


async def test_raises_pagescanerror_on_http_error():
    async def handler(request):
        return web.Response(status=404)

    app = web.Application()
    app.router.add_get("/", handler)
    srv = TestServer(app)
    await srv.start_server()
    try:
        base_url = str(srv.make_url("/"))
        with pytest.raises(PageScanError):
            await extract_page_links(base_url, timeout_seconds=5)
    finally:
        await srv.close()


async def test_empty_page_returns_no_links():
    async def handler(request):
        return web.Response(text="<html><body>no links here</body></html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/", handler)
    srv = TestServer(app)
    await srv.start_server()
    try:
        base_url = str(srv.make_url("/"))
        links = await extract_page_links(base_url, timeout_seconds=5)
        assert links == []
    finally:
        await srv.close()
