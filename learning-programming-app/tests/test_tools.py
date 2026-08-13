from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import Config
from bot.handlers.tools import killall_command, logs_command, speedtest_command
from bot.manager import DownloadManager
from bot.netspeed import SpeedTestError

OWNER_ID = 999


def make_config(**overrides) -> Config:
    defaults = dict(telegram_bot_token="123456:dummy-token", telegram_owner_id=OWNER_ID)
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]


def make_update(user_id, config, manager=None):
    message = MagicMock()
    message.reply_text = AsyncMock()

    update = MagicMock()
    update.message = message
    update.effective_user = MagicMock(id=user_id)

    context = MagicMock()
    context.bot_data = {"config": config, "manager": manager or MagicMock()}

    return update, context, message


@pytest.mark.asyncio
async def test_killall_ignored_for_non_owner(tmp_path):
    config = make_config(download_dir=tmp_path)
    manager = MagicMock()
    (tmp_path / "leftover.txt").write_text("x")
    update, context, message = make_update(1, config, manager)

    await killall_command(update, context)

    message.reply_text.assert_not_awaited()
    manager.cancel_all.assert_not_called()
    assert (tmp_path / "leftover.txt").exists()


@pytest.mark.asyncio
async def test_killall_disabled_when_owner_id_unset(tmp_path):
    config = make_config(telegram_owner_id=None, download_dir=tmp_path)
    manager = MagicMock()
    update, context, message = make_update(OWNER_ID, config, manager)

    await killall_command(update, context)

    message.reply_text.assert_not_awaited()
    manager.cancel_all.assert_not_called()


@pytest.mark.asyncio
async def test_killall_cancels_jobs_and_wipes_storage(tmp_path):
    config = make_config(download_dir=tmp_path)
    manager = DownloadManager(config)
    (tmp_path / "job_abc").mkdir()
    (tmp_path / "job_abc" / "video.mp4").write_bytes(b"x" * 100)
    (tmp_path / "cookies.txt").write_text("secret")

    update, context, message = make_update(OWNER_ID, config, manager)

    await killall_command(update, context)

    assert not (tmp_path / "job_abc").exists()
    assert not (tmp_path / "cookies.txt").exists()
    assert tmp_path.exists()  # recreated, not left missing
    message.reply_text.assert_awaited_once()
    assert "🛑" in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_logs_ignored_for_non_owner(monkeypatch, tmp_path):
    import bot.handlers.tools as tools_module

    log_path = tmp_path / "bot.log"
    log_path.write_text("some log line\n")
    monkeypatch.setattr(tools_module, "LOG_FILE_PATH", log_path)

    config = make_config()
    update, context, message = make_update(1, config)
    message.reply_document = AsyncMock()

    await logs_command(update, context)

    message.reply_text.assert_not_awaited()
    message.reply_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_logs_reports_when_missing(monkeypatch, tmp_path):
    import bot.handlers.tools as tools_module

    monkeypatch.setattr(tools_module, "LOG_FILE_PATH", tmp_path / "does-not-exist.log")

    config = make_config()
    update, context, message = make_update(OWNER_ID, config)
    message.reply_document = AsyncMock()

    await logs_command(update, context)

    message.reply_document.assert_not_awaited()
    assert "مفيش لوج" in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_logs_sends_file_for_owner(monkeypatch, tmp_path):
    import bot.handlers.tools as tools_module

    log_path = tmp_path / "bot.log"
    log_path.write_text("2026-01-01 INFO main started\n")
    monkeypatch.setattr(tools_module, "LOG_FILE_PATH", log_path)

    config = make_config()
    update, context, message = make_update(OWNER_ID, config)
    message.reply_document = AsyncMock()

    await logs_command(update, context)

    message.reply_document.assert_awaited_once()
    assert message.reply_document.await_args.kwargs["filename"] == "bot.log"


@pytest.mark.asyncio
async def test_speedtest_reports_success(monkeypatch):
    import bot.handlers.tools as tools_module

    async def fake_run(*a, **k):
        return {
            "download_mbps": 157.0,
            "upload_mbps": 29.0,
            "ping_ms": 12.0,
            "server_name": "Some ISP",
            "server_country": "Egypt",
        }

    monkeypatch.setattr(tools_module.asyncio, "to_thread", fake_run)

    config = make_config()
    update, context, message = make_update(1, config)
    status = message.reply_text.return_value
    status.edit_text = AsyncMock()

    await speedtest_command(update, context)

    text = status.edit_text.await_args.args[0]
    assert "157.0 Mbps" in text
    assert "29.0 Mbps" in text
    assert "Some ISP" in text


@pytest.mark.asyncio
async def test_speedtest_reports_failure(monkeypatch):
    import bot.handlers.tools as tools_module

    async def fake_run_raises(*a, **k):
        raise SpeedTestError("no internet connection")

    monkeypatch.setattr(tools_module.asyncio, "to_thread", fake_run_raises)

    config = make_config()
    update, context, message = make_update(1, config)
    status = message.reply_text.return_value
    status.edit_text = AsyncMock()

    await speedtest_command(update, context)

    assert "no internet connection" in status.edit_text.await_args.args[0]
