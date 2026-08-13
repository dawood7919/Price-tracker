from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import Config
from bot.handlers.pdf import pdf_command
from bot.pdfconvert import PdfConvertError


def make_config(**overrides) -> Config:
    defaults = dict(telegram_bot_token="123456:dummy-token")
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]


def make_update(url_arg, config, tmp_path):
    message = MagicMock()
    message.reply_text = AsyncMock()
    message.reply_document = AsyncMock()

    update = MagicMock()
    update.message = message

    context = MagicMock()
    context.args = [url_arg] if url_arg else []
    context.bot_data = {"config": config}

    return update, context, message


@pytest.mark.asyncio
async def test_pdf_without_url_shows_usage(tmp_path):
    config = make_config(download_dir=tmp_path)
    update, context, message = make_update(None, config, tmp_path)

    await pdf_command(update, context)

    assert "استخدم: /pdf" in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_pdf_rejects_invalid_url(tmp_path):
    config = make_config(download_dir=tmp_path)
    update, context, message = make_update("not-a-url", config, tmp_path)

    await pdf_command(update, context)

    assert "⛔" in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_pdf_sends_document_on_success(monkeypatch, tmp_path):
    import bot.handlers.pdf as pdf_module

    config = make_config(download_dir=tmp_path)
    update, context, message = make_update("https://example.com", config, tmp_path)
    status = message.reply_text.return_value
    status.edit_text = AsyncMock()

    async def fake_render(url, out_path, timeout_seconds):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(pdf_module, "render_url_to_pdf", fake_render)

    await pdf_command(update, context)

    message.reply_document.assert_awaited_once()
    assert message.reply_document.await_args.kwargs["filename"] == "page.pdf"
    assert "✅" in status.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_pdf_cleans_up_job_dir_on_success(monkeypatch, tmp_path):
    import bot.handlers.pdf as pdf_module

    config = make_config(download_dir=tmp_path)
    update, context, message = make_update("https://example.com", config, tmp_path)
    status = message.reply_text.return_value
    status.edit_text = AsyncMock()

    captured_dir = {}

    async def fake_render(url, out_path, timeout_seconds):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"%PDF-1.4 fake")
        captured_dir["dir"] = out_path.parent

    monkeypatch.setattr(pdf_module, "render_url_to_pdf", fake_render)

    await pdf_command(update, context)

    assert not captured_dir["dir"].exists()


@pytest.mark.asyncio
async def test_pdf_reports_conversion_error(monkeypatch, tmp_path):
    import bot.handlers.pdf as pdf_module

    config = make_config(download_dir=tmp_path)
    update, context, message = make_update("https://example.com", config, tmp_path)
    status = message.reply_text.return_value
    status.edit_text = AsyncMock()

    async def fake_render(url, out_path, timeout_seconds):
        raise PdfConvertError("الصفحة خدت وقت أطول من المسموح.")

    monkeypatch.setattr(pdf_module, "render_url_to_pdf", fake_render)

    await pdf_command(update, context)

    message.reply_document.assert_not_awaited()
    assert "الصفحة خدت وقت أطول من المسموح" in status.edit_text.await_args.args[0]
