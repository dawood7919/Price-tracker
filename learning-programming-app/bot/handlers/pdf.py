"""/pdf <url>: render a webpage to PDF (headless Chromium) and send it."""

from __future__ import annotations

import logging
import shutil
import uuid

from telegram import Update
from telegram.ext import ContextTypes

from ..config import Config
from ..pdfconvert import PdfConvertError, render_url_to_pdf
from ..utils import error_message
from ..validators import URLValidator

logger = logging.getLogger(__name__)


async def pdf_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    if not context.args:
        await update.message.reply_text("استخدم: /pdf <لينك الصفحة>")
        return

    url = context.args[0]
    ok, err = URLValidator.validate(url)
    if not ok:
        await update.message.reply_text(f"⛔ {err}")
        return

    config: Config = context.bot_data["config"]
    status = await update.message.reply_text("📄 جاري تحويل الصفحة لـ PDF...")

    job_dir = config.download_dir / f"pdf_{uuid.uuid4().hex[:8]}"
    pdf_path = job_dir / "page.pdf"
    try:
        await render_url_to_pdf(url, pdf_path, config.pdf_timeout_seconds)
        with pdf_path.open("rb") as fh:
            await update.message.reply_document(document=fh, filename="page.pdf")
        await status.edit_text("✅ اتبعت PDF الصفحة.")
    except PdfConvertError as exc:
        await status.edit_text(f"❌ {exc}")
    except Exception as exc:
        logger.exception("PDF conversion failed for %s", url)
        await status.edit_text(f"❌ حصل خطأ: {error_message(exc)}")
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
