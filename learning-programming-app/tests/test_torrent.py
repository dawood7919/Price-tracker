import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import Config
from bot.handlers.torrent import (
    _parse_release_listing,
    search_torrents,
    torrent_document_handler,
    torrent_download_button,
    torrent_inline_query,
    torrent_search_command,
)
from bot.manager import DownloadManager, JobState
from bot.torrentdl import TorrentDownloadError
from bot.uploader import UploadManager


def make_config(**overrides) -> Config:
    defaults = dict(telegram_bot_token="123456:dummy-token")
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]


# A trimmed copy of a real releases.ubuntu.com listing: two point releases of
# the same flavour (so "newest wins" is exercised) plus non-torrent rows.
UBUNTU_LISTING_HTML = """
<tr><td><a href="ubuntu-24.04.3-desktop-amd64.iso.torrent">..</a></td><td>473K</td></tr>
<tr><td><a href="ubuntu-24.04.3-desktop-amd64.iso.zsync">..</a></td><td>13M</td></tr>
<tr><td><a href="ubuntu-24.04.4-desktop-amd64.iso.torrent">..</a></td><td>496K</td></tr>
<tr><td><a href="ubuntu-24.04.4-live-server-amd64.iso.torrent">..</a></td><td>310K</td></tr>
<tr><td><a href="SHA256SUMS">..</a></td><td>-</td></tr>
"""


@pytest.fixture(autouse=True)
def _clear_listing_cache():
    """search_torrents memoizes the listing; isolate tests from each other."""
    import bot.handlers.torrent as torrent_module

    torrent_module._listing_cache = None
    yield
    torrent_module._listing_cache = None


@pytest.fixture
def stub_search(monkeypatch):
    """Handler tests must not touch the network — search_torrents does HTTP."""
    import bot.handlers.torrent as torrent_module

    items = [
        {
            "name": "Ubuntu 24.04.4 Desktop (amd64)",
            "size": "6.2 GB",
            "seeders": "official",
            "torrent": f"{torrent_module.UBUNTU_BASE}/24.04/a.iso.torrent",
        },
        {
            "name": "Ubuntu 24.04.4 Server (amd64)",
            "size": "3.0 GB",
            "seeders": "official",
            "torrent": f"{torrent_module.UBUNTU_BASE}/24.04/b.iso.torrent",
        },
    ]

    async def fake_search(query: str):
        return list(items)

    monkeypatch.setattr(torrent_module, "search_torrents", fake_search)
    return items


def make_command_update(args):
    message = MagicMock()
    message.reply_text = AsyncMock()

    update = MagicMock()
    update.message = message

    context = MagicMock()
    context.args = args
    context.user_data = {}

    return update, context, message


def make_inline_update(query_text):
    inline_query = MagicMock()
    inline_query.query = query_text
    inline_query.answer = AsyncMock()

    update = MagicMock()
    update.inline_query = inline_query

    context = MagicMock()
    context.user_data = {}

    return update, context, inline_query


def make_callback_update(callback_data, config, torrent_results=None, user_id=1):
    query = MagicMock()
    query.data = callback_data
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.chat_id = 555
    query.message.edit_text = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock(id=user_id)

    manager = DownloadManager(config)
    uploader = MagicMock(spec=UploadManager)
    uploader.upload = AsyncMock()

    context = MagicMock()
    context.user_data = {"torrent_results": torrent_results if torrent_results is not None else {}}
    context.bot_data = {"config": config, "manager": manager, "uploader": uploader}

    return update, context, query, uploader


class TestParseReleaseListing:
    """Offline tests for the listing parser.

    Regression context: the .torrent URLs used to be hardcoded, which 404'd
    as soon as Ubuntu published a point release and removed the plain
    "24.04" file — so filenames are now discovered from the listing.
    """

    def test_picks_newest_point_release_per_flavour(self):
        items = _parse_release_listing(UBUNTU_LISTING_HTML, "24.04")
        names = [i["name"] for i in items]
        assert names == [
            "Ubuntu 24.04.4 Desktop (amd64)",
            "Ubuntu 24.04.4 Server (amd64)",
        ]
        # 24.04.3 was superseded and must not be offered
        assert not any("24.04.3" in n for n in names)

    def test_builds_absolute_urls_on_the_official_host_only(self):
        items = _parse_release_listing(UBUNTU_LISTING_HTML, "24.04")
        for item in items:
            assert item["torrent"].startswith("https://releases.ubuntu.com/24.04/")
            assert item["torrent"].endswith(".iso.torrent")
            # the .iso alongside it, used only to read a real size via HEAD
            assert item["iso"] == item["torrent"][: -len(".torrent")]

    def test_ignores_non_torrent_rows(self):
        items = _parse_release_listing(UBUNTU_LISTING_HTML, "24.04")
        assert all(i["torrent"].endswith(".torrent") for i in items)
        assert not any("zsync" in i["torrent"] or "SHA256" in i["torrent"] for i in items)

    def test_numeric_version_ordering_not_string_ordering(self):
        """A plain string sort would rank "24.04.9" above "24.04.10"."""
        html = (
            '<a href="ubuntu-24.04.9-desktop-amd64.iso.torrent">x</a>'
            '<a href="ubuntu-24.04.10-desktop-amd64.iso.torrent">x</a>'
        )
        items = _parse_release_listing(html, "24.04")
        assert [i["name"] for i in items] == ["Ubuntu 24.04.10 Desktop (amd64)"]

    def test_empty_listing_yields_nothing(self):
        assert _parse_release_listing("<html>nothing here</html>", "24.04") == []


@pytest.mark.asyncio
async def test_search_filters_by_query_tokens(monkeypatch):
    import bot.handlers.torrent as torrent_module

    parsed = _parse_release_listing(UBUNTU_LISTING_HTML, "24.04")
    monkeypatch.setattr(torrent_module, "_listing_cache", (parsed, time.monotonic()))

    assert len(await search_torrents("")) == 2  # no filter
    assert [i["name"] for i in await search_torrents("server")] == [
        "Ubuntu 24.04.4 Server (amd64)"
    ]
    assert [i["name"] for i in await search_torrents("desktop")] == [
        "Ubuntu 24.04.4 Desktop (amd64)"
    ]
    # a query the fixed source simply cannot satisfy returns nothing, rather
    # than silently handing back unrelated results
    assert await search_torrents("some movie") == []


@pytest.mark.asyncio
async def test_command_without_query_shows_usage(stub_search):
    update, context, message = make_command_update([])

    await torrent_search_command(update, context)

    assert "اكتب كلمة البحث" in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_command_stores_results_with_torrentdl_callback_data(stub_search):
    update, context, message = make_command_update(["ubuntu"])

    await torrent_search_command(update, context)

    message.reply_text.assert_awaited_once()
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    keys = list(context.user_data["torrent_results"])
    assert len(keys) == 2
    all_buttons = [btn for row in markup.inline_keyboard for btn in row]
    assert all(btn.callback_data.startswith("torrentdl:") for btn in all_buttons)


@pytest.mark.asyncio
async def test_inline_query_offers_download_button(stub_search):
    update, context, inline_query = make_inline_update("ubuntu")

    await torrent_inline_query(update, context)

    inline_query.answer.assert_awaited_once()
    answers = inline_query.answer.await_args.args[0]
    assert len(answers) == 2
    assert len(context.user_data["torrent_results"]) == 2


@pytest.mark.asyncio
async def test_download_button_with_unknown_key_reports_expired():
    config = make_config()
    update, context, query, uploader = make_callback_update(
        "torrentdl:missing", config, torrent_results={}
    )

    await torrent_download_button(update, context)

    query.message.edit_text.assert_awaited_with("انتهت صلاحية النتيجة دي — دوّر تاني.")
    uploader.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_button_full_flow_success(monkeypatch, tmp_path):
    import bot.handlers.torrent as torrent_module

    config = make_config(download_dir=tmp_path)
    item = {"name": "Ubuntu 24.04 Desktop ISO", "torrent": "https://example.com/x.torrent"}
    update, context, query, uploader = make_callback_update(
        "torrentdl:key1", config, torrent_results={"key1": item}
    )

    class FakeResponse:
        status = 200

        async def read(self):
            return b"fake torrent descriptor"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class FakeSession:
        def get(self, url):
            return FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(torrent_module.aiohttp, "ClientSession", lambda **kwargs: FakeSession())

    fake_result = tmp_path / "ubuntu.iso"

    def fake_download_torrent(torrent_path, out_dir, progress_callback=None, cancel_event=None):
        fake_result.write_bytes(b"x" * 100)
        if progress_callback is not None:
            progress_callback(100, 50.0)
        return fake_result

    monkeypatch.setattr(torrent_module, "download_torrent", fake_download_torrent)

    await torrent_download_button(update, context)

    uploader.upload.assert_awaited_once()
    assert uploader.upload.await_args.args[0] == 555
    assert uploader.upload.await_args.args[1] == fake_result
    assert "key1" not in context.user_data["torrent_results"]
    query.message.edit_text.assert_awaited()
    assert "تم!" in query.message.edit_text.await_args.args[0]
    assert slots_held(context, user_id=1) == 0


def slots_held(context, user_id: int) -> int:
    """Global concurrency slots still held. This is the invariant that
    actually matters: a leaked slot permanently shrinks the pool, and
    enough leaks wedge every future download. (The job *record* itself is
    dropped by start_job's done-callback when the surrounding task ends,
    which in these tests is after the test function returns.)"""
    return context.bot_data["manager"].active_for_user(user_id)


@pytest.mark.asyncio
async def test_download_button_reports_descriptor_http_error(monkeypatch, tmp_path):
    import bot.handlers.torrent as torrent_module

    config = make_config(download_dir=tmp_path)
    item = {"name": "Ubuntu 24.04 Desktop ISO", "torrent": "https://example.com/x.torrent"}
    update, context, query, uploader = make_callback_update(
        "torrentdl:key1", config, torrent_results={"key1": item}
    )

    class FakeResponse:
        status = 404

        async def read(self):
            return b"not found"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class FakeSession:
        def get(self, url):
            return FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(torrent_module.aiohttp, "ClientSession", lambda **kwargs: FakeSession())

    await torrent_download_button(update, context)

    uploader.upload.assert_not_awaited()
    assert "404" in query.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_download_button_reports_torrentdownloaderror(monkeypatch, tmp_path):
    import bot.handlers.torrent as torrent_module

    config = make_config(download_dir=tmp_path)
    item = {"name": "Ubuntu 24.04 Desktop ISO", "torrent": "https://example.com/x.torrent"}
    update, context, query, uploader = make_callback_update(
        "torrentdl:key1", config, torrent_results={"key1": item}
    )

    class FakeResponse:
        status = 200

        async def read(self):
            return b"fake torrent descriptor"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class FakeSession:
        def get(self, url):
            return FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(torrent_module.aiohttp, "ClientSession", lambda **kwargs: FakeSession())

    def fake_download_torrent(torrent_path, out_dir, progress_callback=None, cancel_event=None):
        raise TorrentDownloadError("aria2c مش متثبت على السيرفر.")

    monkeypatch.setattr(torrent_module, "download_torrent", fake_download_torrent)

    await torrent_download_button(update, context)

    uploader.upload.assert_not_awaited()
    assert "aria2c مش متثبت" in query.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_download_button_reports_upload_failure(monkeypatch, tmp_path):
    import bot.handlers.torrent as torrent_module

    config = make_config(download_dir=tmp_path)
    item = {"name": "Ubuntu 24.04 Desktop ISO", "torrent": "https://example.com/x.torrent"}
    update, context, query, uploader = make_callback_update(
        "torrentdl:key1", config, torrent_results={"key1": item}
    )
    uploader.upload.side_effect = RuntimeError("upload exploded")

    class FakeResponse:
        status = 200

        async def read(self):
            return b"fake torrent descriptor"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class FakeSession:
        def get(self, url):
            return FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(torrent_module.aiohttp, "ClientSession", lambda **kwargs: FakeSession())

    fake_result = tmp_path / "ubuntu.iso"

    def fake_download_torrent(torrent_path, out_dir, progress_callback=None, cancel_event=None):
        fake_result.write_bytes(b"x" * 100)
        return fake_result

    monkeypatch.setattr(torrent_module, "download_torrent", fake_download_torrent)

    await torrent_download_button(update, context)

    assert "فشل الرفع" in query.message.edit_text.await_args.args[0]
    assert slots_held(context, user_id=1) == 0


def make_document_update(config, document, user_id=1):
    message = MagicMock()
    message.document = document
    message.chat_id = 777
    reply = MagicMock()
    reply.edit_text = AsyncMock()
    message.reply_text = AsyncMock(return_value=reply)

    update = MagicMock()
    update.message = message
    update.effective_user = MagicMock(id=user_id)

    manager = DownloadManager(config)
    uploader = MagicMock(spec=UploadManager)
    uploader.upload = AsyncMock()

    context = MagicMock()
    context.bot_data = {"config": config, "manager": manager, "uploader": uploader}

    return update, context, message, reply, uploader


@pytest.mark.asyncio
async def test_document_rejects_oversized_file(tmp_path):
    config = make_config(download_dir=tmp_path)
    document = MagicMock()
    document.file_size = 10 * 1024 * 1024
    document.get_file = AsyncMock()

    update, context, message, reply, uploader = make_document_update(config, document)

    await torrent_document_handler(update, context)

    document.get_file.assert_not_awaited()
    message.reply_text.assert_awaited_once()
    assert "⛔" in message.reply_text.await_args.args[0]
    uploader.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_document_full_flow_success(monkeypatch, tmp_path):
    import bot.handlers.torrent as torrent_module

    config = make_config(download_dir=tmp_path)
    document = MagicMock()
    document.file_size = 5000
    document.file_name = "my-backup.torrent"
    tg_file = MagicMock()
    tg_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"fake torrent bytes"))
    document.get_file = AsyncMock(return_value=tg_file)

    update, context, message, reply, uploader = make_document_update(config, document)

    fake_result = tmp_path / "result.bin"

    def fake_download_torrent(torrent_path, out_dir, progress_callback=None, cancel_event=None):
        assert torrent_path.read_bytes() == b"fake torrent bytes"
        fake_result.write_bytes(b"x" * 100)
        return fake_result

    monkeypatch.setattr(torrent_module, "download_torrent", fake_download_torrent)

    await torrent_document_handler(update, context)

    message.reply_text.assert_awaited_once()
    assert "my-backup" in message.reply_text.await_args.args[0]
    uploader.upload.assert_awaited_once()
    assert uploader.upload.await_args.args[0] == 777
    assert uploader.upload.await_args.args[1] == fake_result
    assert uploader.upload.await_args.args[2] == "my-backup"
    assert "تم!" in reply.edit_text.await_args.args[0]
    assert slots_held(context, user_id=1) == 0


@pytest.mark.asyncio
async def test_torrent_job_is_registered_and_cancellable(monkeypatch, tmp_path):
    """Regression test: the torrent path used to take a global concurrency
    slot without ever calling start_job, so the job was invisible to /stats
    and untouchable by both the cancel button and /killall — a batch of
    stuck torrents could exhaust the semaphore while reporting "0 running",
    wedging every future download with no recovery short of a restart.
    """
    import bot.handlers.torrent as torrent_module

    config = make_config(download_dir=tmp_path)
    document = MagicMock()
    document.file_size = 5000
    document.file_name = "backup.torrent"
    tg_file = MagicMock()
    tg_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"t"))
    document.get_file = AsyncMock(return_value=tg_file)

    update, context, message, reply, uploader = make_document_update(config, document)
    manager = context.bot_data["manager"]
    observed = {}

    def fake_download_torrent(torrent_path, out_dir, progress_callback=None, cancel_event=None):
        # Mid-download the job must be visible to /stats and present in the
        # registry that cancel_all() (i.e. /killall) iterates over.
        observed["states"] = manager.active_by_state()
        observed["registered"] = manager.active_jobs()
        observed["cancel_event"] = cancel_event
        # Simulate the cancel flag being set (what the button and /killall
        # both do); the flag is what actually stops the worker thread.
        if cancel_event is not None:
            cancel_event.set()
        raise TorrentDownloadError("اتلغى التحميل.")

    monkeypatch.setattr(torrent_module, "download_torrent", fake_download_torrent)

    await torrent_document_handler(update, context)

    assert observed["states"] == {JobState.DOWNLOADING: 1}  # visible to /stats
    assert observed["registered"] == 1  # visible to /killall's cancel_all()
    assert observed["cancel_event"] is not None  # passed through to the downloader
    # a cancel must read as cancelled, not as a generic failure
    assert "اتلغى" in reply.edit_text.await_args.args[0]
    assert slots_held(context, user_id=1) == 0


@pytest.mark.asyncio
async def test_inline_pick_downloads_via_inline_message_id(monkeypatch, tmp_path):
    """Regression test: a result chosen through inline mode (@BotName ...)
    arrives with query.message == None and query.inline_message_id set.
    Bailing out on `message is None` made the inline download button do
    nothing at all; status updates must go through bot.edit_message_text.
    """
    import bot.handlers.torrent as torrent_module

    config = make_config(download_dir=tmp_path)
    item = {"name": "Ubuntu 24.04 Desktop ISO", "torrent": "magnet:?xt=urn:btih:abc"}

    query = MagicMock()
    query.data = "torrentdl:key1"
    query.answer = AsyncMock()
    query.message = None  # inline mode: no real message object
    query.inline_message_id = "inline-42"

    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock(id=1234)

    manager = DownloadManager(config)
    uploader = MagicMock(spec=UploadManager)
    uploader.upload = AsyncMock()

    context = MagicMock()
    context.user_data = {"torrent_results": {"key1": item}}
    context.bot_data = {"config": config, "manager": manager, "uploader": uploader}
    context.bot = MagicMock()
    context.bot.edit_message_text = AsyncMock()

    fake_result = tmp_path / "ubuntu.iso"

    def fake_download_torrent(torrent_path, out_dir, progress_callback=None, cancel_event=None):
        fake_result.write_bytes(b"x" * 10)
        return fake_result

    monkeypatch.setattr(torrent_module, "download_torrent", fake_download_torrent)

    await torrent_download_button(update, context)

    # status went out over the inline message, not a chat message
    assert context.bot.edit_message_text.await_count > 0
    assert all(
        c.kwargs.get("inline_message_id") == "inline-42"
        for c in context.bot.edit_message_text.await_args_list
    )
    # the file itself goes to the user's own chat, since inline mode has none
    uploader.upload.assert_awaited_once()
    assert uploader.upload.await_args.args[0] == 1234
    assert manager.active_for_user(1234) == 0


@pytest.mark.asyncio
async def test_torrent_rejects_when_disk_is_full(monkeypatch, tmp_path):
    """The video path guards free disk before downloading; the torrent path
    did not, so a multi-GB torrent could fill the disk."""
    import bot.handlers.torrent as torrent_module

    config = make_config(download_dir=tmp_path, min_free_disk_mb=500)
    document = MagicMock()
    document.file_size = 5000
    document.file_name = "backup.torrent"
    tg_file = MagicMock()
    tg_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"t"))
    document.get_file = AsyncMock(return_value=tg_file)

    update, context, message, reply, uploader = make_document_update(config, document)

    monkeypatch.setattr(torrent_module, "free_disk_mb", lambda _p: 10)
    called = {}
    monkeypatch.setattr(
        torrent_module, "download_torrent", lambda *a, **k: called.setdefault("ran", True)
    )

    await torrent_document_handler(update, context)

    assert "ran" not in called  # never started the download
    assert "مساحة" in reply.edit_text.await_args.args[0]
    assert slots_held(context, user_id=1) == 0
