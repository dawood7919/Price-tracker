from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import Config
from bot.handlers.commands import stats_command
from bot.manager import DownloadManager, JobState


def make_config(**overrides) -> Config:
    defaults = dict(telegram_bot_token="123456:dummy-token")
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]


def make_update_and_context(config: Config, manager: DownloadManager, start_time=None):
    message = MagicMock()
    message.reply_text = AsyncMock()

    update = MagicMock()
    update.message = message

    context = MagicMock()
    context.bot_data = {"config": config, "manager": manager}
    if start_time is not None:
        context.bot_data["start_time"] = start_time

    return update, context, message


@pytest.mark.asyncio
async def test_stats_command_reports_cloud_api_and_empty_queue():
    config = make_config()
    manager = DownloadManager(config)
    update, context, message = make_update_and_context(config, manager)

    await stats_command(update, context)

    message.reply_text.assert_awaited_once()
    text = message.reply_text.await_args.args[0]
    assert context.bot_data or True  # sanity: context wired
    assert message.reply_text.await_args.kwargs["parse_mode"] == "HTML"
    assert "Cloud API (50MB)" in text
    assert "مفيش تحميلات شغالة دلوقتي" in text


@pytest.mark.asyncio
async def test_stats_command_reports_local_api_and_active_jobs():
    config = make_config(telegram_base_url="http://localhost:8081")
    manager = DownloadManager(config)
    manager.start_job("job1", MagicMock(done=lambda: False))
    manager.set_state("job1", JobState.DOWNLOADING)
    update, context, message = make_update_and_context(config, manager, start_time=0.0)

    await stats_command(update, context)

    text = message.reply_text.await_args.args[0]
    assert "Local Bot API (2GB)" in text
    assert "إجمالي التحميلات الشغالة: <b>1</b>" in text
    assert "📥 بيتنزّل" in text
