from unittest.mock import AsyncMock

import pytest

from bot.handlers.messages import _send_preview


@pytest.mark.asyncio
async def test_sends_thumbnail_as_separate_message_then_text_keyboard():
    """Regression test: the keyboard message must stay plain text.

    Attaching the caption+keyboard directly to the photo (the old
    behavior) made every later status.edit_text() call crash with
    "BadRequest: There is no text in the message to edit", since Telegram
    only allows editing a photo message's caption, never converting it to
    text. The thumbnail must be its own separate, caption-less photo.
    """
    message = AsyncMock()
    status = AsyncMock()
    keyboard = object()

    await _send_preview(message, status, "https://example.com/thumb.jpg", "caption", keyboard)

    message.reply_photo.assert_awaited_once_with(photo="https://example.com/thumb.jpg")
    status.edit_text.assert_awaited_once_with("caption", reply_markup=keyboard)
    status.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_falls_back_to_text_when_no_thumbnail():
    message = AsyncMock()
    status = AsyncMock()
    keyboard = object()

    await _send_preview(message, status, None, "caption", keyboard)

    message.reply_photo.assert_not_awaited()
    status.edit_text.assert_awaited_once_with("caption", reply_markup=keyboard)


@pytest.mark.asyncio
async def test_keyboard_still_shown_when_photo_send_fails():
    """A dead thumbnail URL must not break showing the quality keyboard."""
    message = AsyncMock()
    message.reply_photo.side_effect = RuntimeError("boom")
    status = AsyncMock()
    keyboard = object()

    await _send_preview(message, status, "https://example.com/thumb.jpg", "caption", keyboard)

    status.edit_text.assert_awaited_once_with("caption", reply_markup=keyboard)
