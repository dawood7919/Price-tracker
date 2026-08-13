from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import Config
from bot.downloader import DownloadError, MediaInfo
from bot.handlers.scan import handle_scan_callback, scan_command
from bot.pagescan import PageScanError


def make_config(**overrides) -> Config:
    defaults = dict(telegram_bot_token="123456:dummy-token", max_scan_probe_links=30, max_scan_items=10)
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]


def make_command_update(url_arg, config, downloader):
    message = MagicMock()
    message.reply_text = AsyncMock()

    update = MagicMock()
    update.message = message
    update.effective_user = MagicMock(id=1)

    context = MagicMock()
    context.args = [url_arg] if url_arg else []
    context.bot_data = {"config": config, "downloader": downloader}
    context.user_data = {}

    return update, context, message


def media(title: str, is_playlist: bool = False) -> MediaInfo:
    return MediaInfo(url="x", title=title, duration=10, is_playlist=is_playlist)


@pytest.mark.asyncio
async def test_scan_without_url_shows_usage():
    config = make_config()
    downloader = MagicMock()
    update, context, message = make_command_update(None, config, downloader)

    await scan_command(update, context)

    assert "استخدم: /scan" in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_scan_rejects_invalid_url():
    config = make_config()
    downloader = MagicMock()
    update, context, message = make_command_update("not-a-url", config, downloader)

    await scan_command(update, context)

    assert "⛔" in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_scan_reports_page_scan_error(monkeypatch):
    import bot.handlers.scan as scan_module

    config = make_config()
    downloader = MagicMock()
    update, context, message = make_command_update("https://example.com/list", config, downloader)
    status = message.reply_text.return_value
    status.edit_text = AsyncMock()

    async def boom(url, timeout):
        raise PageScanError("مقدرتش أفتح الصفحة")

    monkeypatch.setattr(scan_module, "extract_page_links", boom)

    await scan_command(update, context)

    assert "مقدرتش أفتح الصفحة" in status.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_scan_probes_links_and_offers_confirmation(monkeypatch):
    import bot.handlers.scan as scan_module

    config = make_config()
    downloader = MagicMock()

    async def fake_links(url, timeout):
        return ["https://example.com/a", "https://example.com/b", "https://example.com/notvideo"]

    def fake_extract_info(url):
        if url.endswith("/a"):
            return media("Video A")
        if url.endswith("/b"):
            return media("Video B")
        raise DownloadError("Unsupported URL")

    downloader.extract_info = fake_extract_info
    monkeypatch.setattr(scan_module, "extract_page_links", fake_links)

    update, context, message = make_command_update("https://example.com/list", config, downloader)
    status = message.reply_text.return_value
    status.edit_text = AsyncMock()

    await scan_command(update, context)

    text, kwargs = status.edit_text.await_args.args, status.edit_text.await_args.kwargs
    assert "Video A" in text[0]
    assert "Video B" in text[0]
    assert "notvideo" not in text[0]
    assert "لقيت 2 فيديو" in text[0]
    assert kwargs["reply_markup"] is not None
    assert len(context.user_data["pending_scans"]) == 1


@pytest.mark.asyncio
async def test_scan_stops_probing_at_max_scan_items(monkeypatch):
    import bot.handlers.scan as scan_module

    config = make_config(max_scan_items=1, max_scan_probe_links=10)
    downloader = MagicMock()
    calls = []

    async def fake_links(url, timeout):
        return ["https://example.com/1", "https://example.com/2", "https://example.com/3"]

    def fake_extract_info(url):
        calls.append(url)
        return media(f"Video {url[-1]}")

    downloader.extract_info = fake_extract_info
    monkeypatch.setattr(scan_module, "extract_page_links", fake_links)

    update, context, message = make_command_update("https://example.com/list", config, downloader)
    status = message.reply_text.return_value
    status.edit_text = AsyncMock()

    await scan_command(update, context)

    assert len(calls) == 1  # stopped as soon as max_scan_items (1) was reached


@pytest.mark.asyncio
async def test_scan_no_videos_found_reports_failure(monkeypatch):
    import bot.handlers.scan as scan_module

    config = make_config()
    downloader = MagicMock()

    async def fake_links(url, timeout):
        return ["https://example.com/a"]

    downloader.extract_info = MagicMock(side_effect=DownloadError("Unsupported URL"))
    monkeypatch.setattr(scan_module, "extract_page_links", fake_links)

    update, context, message = make_command_update("https://example.com/list", config, downloader)
    status = message.reply_text.return_value
    status.edit_text = AsyncMock()

    await scan_command(update, context)

    assert "مالقتش" in status.edit_text.await_args.args[0]


def make_callback_update(callback_data, pending_scans=None):
    query = MagicMock()
    query.data = callback_data
    query.message = MagicMock()
    query.message.edit_text = AsyncMock()
    query.message.chat_id = 555

    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock(id=1)

    context = MagicMock()
    context.user_data = {"pending_scans": pending_scans if pending_scans is not None else {}}
    context.bot_data = {
        "config": make_config(),
        "manager": MagicMock(),
        "downloader": MagicMock(),
        "uploader": MagicMock(),
    }
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    context.application = MagicMock()
    context.application.create_task = MagicMock()

    return update, context, query


@pytest.mark.asyncio
async def test_scandrop_clears_pending_and_confirms():
    update, context, query = make_callback_update(
        "scandrop:abc123", pending_scans={"abc123": [{"url": "x", "title": "t"}]}
    )

    consumed = await handle_scan_callback(update, context)

    assert consumed is True
    assert "abc123" not in context.user_data["pending_scans"]
    query.message.edit_text.assert_awaited_once()
    assert "اتلغى" in query.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_scanrun_starts_background_task_with_queued_items():
    items = [{"url": "https://x/1", "title": "A"}, {"url": "https://x/2", "title": "B"}]
    update, context, query = make_callback_update("scanrun:abc123", pending_scans={"abc123": items})

    consumed = await handle_scan_callback(update, context)

    assert consumed is True
    assert "abc123" not in context.user_data["pending_scans"]
    query.message.edit_text.assert_awaited_once()
    assert "2 فيديو" in query.message.edit_text.await_args.args[0]
    context.application.create_task.assert_called_once()
    # create_task is a plain MagicMock here (not real asyncio), so the
    # coroutine it was given never actually runs — close it explicitly to
    # avoid a "coroutine was never awaited" warning bleeding into other tests.
    context.application.create_task.call_args.args[0].close()


@pytest.mark.asyncio
async def test_scanrun_with_unknown_key_reports_expired():
    update, context, query = make_callback_update("scanrun:missing", pending_scans={})

    consumed = await handle_scan_callback(update, context)

    assert consumed is True
    assert "قديمة" in query.message.edit_text.await_args.args[0]
    context.application.create_task.assert_not_called()


@pytest.mark.asyncio
async def test_ignores_unrelated_callback_data():
    update, context, query = make_callback_update("cancel:jobid")

    consumed = await handle_scan_callback(update, context)

    assert consumed is False
    query.message.edit_text.assert_not_awaited()
