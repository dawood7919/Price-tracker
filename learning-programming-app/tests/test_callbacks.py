from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.handlers.callbacks import handle_callback


def make_update_and_context(callback_data: str, pending: dict | None = None):
    query = MagicMock()
    query.data = callback_data
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.edit_text = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock(id=1)

    context = MagicMock()
    context.user_data = {"pending": pending or {}}
    return update, context, query


@pytest.mark.asyncio
async def test_exception_in_start_job_shows_error_instead_of_silence(monkeypatch):
    """Regression test: an exception raised before the first status message
    edit (e.g. inside _start_single) must never fail completely silently —
    that's exactly the "nothing happens" symptom this was covering up.
    """
    import bot.handlers.callbacks as callbacks_module

    async def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(callbacks_module, "_start_single", boom)

    update, context, query = make_update_and_context(
        "dl:key1:best", pending={"key1": {"url": "https://x", "title": "t", "is_playlist": False}}
    )

    await handle_callback(update, context)

    query.message.edit_text.assert_awaited_once()
    text = query.message.edit_text.await_args.args[0]
    assert "❌" in text
    assert "kaboom" in text


@pytest.mark.asyncio
async def test_successful_start_does_not_show_error(monkeypatch):
    import bot.handlers.callbacks as callbacks_module

    called = {}

    async def fake_start_single(update, context, url, format_key, title):
        called["ran"] = True

    monkeypatch.setattr(callbacks_module, "_start_single", fake_start_single)

    update, context, query = make_update_and_context(
        "dl:key1:best", pending={"key1": {"url": "https://x", "title": "t", "is_playlist": False}}
    )

    await handle_callback(update, context)

    assert called.get("ran") is True
    query.message.edit_text.assert_not_awaited()
