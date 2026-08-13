from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import Config
from bot.handlers.cookies import handle_cookies_upload, handle_document, setcookies_command

OWNER_ID = 999


def make_config(**overrides) -> Config:
    defaults = dict(telegram_bot_token="123456:dummy-token", telegram_owner_id=OWNER_ID)
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]


def make_update_and_context(config: Config, user_id: int, text=None, document=None, user_data=None):
    message = MagicMock()
    message.text = text
    message.document = document
    message.reply_text = AsyncMock()

    update = MagicMock()
    update.message = message
    update.effective_user = MagicMock(id=user_id)

    context = MagicMock()
    context.bot_data = {"config": config}
    context.user_data = user_data if user_data is not None else {}

    return update, context, message


@pytest.mark.asyncio
async def test_setcookies_ignored_for_non_owner():
    config = make_config()
    update, context, message = make_update_and_context(config, user_id=1)

    await setcookies_command(update, context)

    message.reply_text.assert_not_awaited()
    assert "awaiting_cookies" not in context.user_data


@pytest.mark.asyncio
async def test_setcookies_prompts_owner_and_sets_flag():
    config = make_config()
    update, context, message = make_update_and_context(config, user_id=OWNER_ID)

    await setcookies_command(update, context)

    message.reply_text.assert_awaited_once()
    assert context.user_data["awaiting_cookies"] is True


@pytest.mark.asyncio
async def test_setcookies_disabled_when_owner_id_unset():
    config = make_config(telegram_owner_id=None)
    update, context, message = make_update_and_context(config, user_id=OWNER_ID)

    await setcookies_command(update, context)

    message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_ignored_when_not_awaiting(tmp_path):
    config = make_config(download_dir=tmp_path)
    update, context, message = make_update_and_context(
        config, user_id=OWNER_ID, text="# Netscape HTTP Cookie File\n"
    )

    consumed = await handle_cookies_upload(update, context)

    assert consumed is False
    assert not (tmp_path / "cookies.txt").exists()


@pytest.mark.asyncio
async def test_upload_ignored_for_non_owner_even_if_awaiting(tmp_path):
    config = make_config(download_dir=tmp_path)
    update, context, message = make_update_and_context(
        config, user_id=1, text="stolen cookies", user_data={"awaiting_cookies": True}
    )

    consumed = await handle_cookies_upload(update, context)

    assert consumed is False
    assert not (tmp_path / "cookies.txt").exists()


@pytest.mark.asyncio
async def test_upload_saves_text_content_and_updates_config(tmp_path):
    config = make_config(download_dir=tmp_path)
    update, context, message = make_update_and_context(
        config,
        user_id=OWNER_ID,
        text="# Netscape HTTP Cookie File\nfoo\tbar\n",
        user_data={"awaiting_cookies": True},
    )

    consumed = await handle_cookies_upload(update, context)

    assert consumed is True
    assert context.user_data["awaiting_cookies"] is False
    saved = tmp_path / "cookies.txt"
    assert saved.read_text() == "# Netscape HTTP Cookie File\nfoo\tbar\n"
    assert config.ytdlp_cookies_file == str(saved)
    message.reply_text.assert_awaited_once()
    assert "✅" in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_upload_saves_document_content(tmp_path):
    config = make_config(download_dir=tmp_path)
    document = MagicMock()
    document.file_size = 100
    tg_file = MagicMock()
    tg_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"cookie-bytes"))
    document.get_file = AsyncMock(return_value=tg_file)

    update, context, message = make_update_and_context(
        config, user_id=OWNER_ID, document=document, user_data={"awaiting_cookies": True}
    )

    consumed = await handle_cookies_upload(update, context)

    assert consumed is True
    assert (tmp_path / "cookies.txt").read_text() == "cookie-bytes"


@pytest.mark.asyncio
async def test_upload_rejects_oversized_document(tmp_path):
    config = make_config(download_dir=tmp_path)
    document = MagicMock()
    document.file_size = 10 * 1024 * 1024
    document.get_file = AsyncMock()

    update, context, message = make_update_and_context(
        config, user_id=OWNER_ID, document=document, user_data={"awaiting_cookies": True}
    )

    consumed = await handle_cookies_upload(update, context)

    assert consumed is True
    document.get_file.assert_not_awaited()
    assert not (tmp_path / "cookies.txt").exists()
    assert "⛔" in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_handle_document_falls_back_when_not_awaiting():
    config = make_config()
    document = MagicMock()
    update, context, message = make_update_and_context(config, user_id=OWNER_ID, document=document)

    await handle_document(update, context)

    message.reply_text.assert_awaited_once()
    assert "لينك فيديو" in message.reply_text.await_args.args[0]
