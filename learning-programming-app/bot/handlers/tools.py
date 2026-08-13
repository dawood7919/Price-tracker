"""Misc bot-management commands:
- /killall: owner-only emergency stop (cancel every active job) + wipe the
  download directory. Restricted like /setcookies — this bot has no other
  per-user auth, and letting any random user nuke everyone's active
  downloads and storage would be a real abuse vector.
- /speedtest: network diagnostics, open to everyone (read-only, same risk
  profile as /stats).
- /logs: owner-only, sends the bot's own rotating log file. Restricted
  because logs can contain internal file paths, stack traces, and other
  operational detail that shouldn't be handed to arbitrary users.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from ..config import Config
from ..manager import DownloadManager
from ..netspeed import SpeedTestError, run_speedtest
from ..utils import error_message

logger = logging.getLogger(__name__)

# A fixed path independent of Config — set up once, at import time, before
# any handler (or Config itself) exists, so every log line from process
# startup onward ends up in the file /logs sends.
LOG_FILE_PATH = Path("/tmp/bot-logs/bot.log")


def make_log_file_handler() -> RotatingFileHandler:
    LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return RotatingFileHandler(
        LOG_FILE_PATH, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )


def _is_owner(config: Config, update: Update) -> bool:
    return (
        config.telegram_owner_id is not None
        and update.effective_user is not None
        and update.effective_user.id == config.telegram_owner_id
    )


async def killall_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    config: Config = context.bot_data["config"]
    if not _is_owner(config, update):
        return  # silently ignore — don't advertise a destructive command to non-owners

    manager: DownloadManager = context.bot_data["manager"]
    cancelled = manager.cancel_all()

    shutil.rmtree(config.download_dir, ignore_errors=True)
    config.download_dir.mkdir(parents=True, exist_ok=True)

    await update.message.reply_text(
        f"🛑 اتلغى {cancelled} طلب شغال، ومسحت كل الملفات المؤقتة على السيرفر.\n"
        "⚠️ لو كنت مضيف كوكيز، هتحتاج تبعتها تاني بـ /setcookies."
    )


async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    config: Config = context.bot_data["config"]
    if not _is_owner(config, update):
        return  # silently ignore — logs can reveal internal details

    if not LOG_FILE_PATH.exists() or LOG_FILE_PATH.stat().st_size == 0:
        await update.message.reply_text("مفيش لوج لسه.")
        return

    with LOG_FILE_PATH.open("rb") as fh:
        await update.message.reply_document(document=fh, filename="bot.log")


async def speedtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    status = await update.message.reply_text("📶 جاري قياس سرعة السيرفر... (بتاخد شوية وقت)")
    try:
        result = await asyncio.to_thread(run_speedtest)
    except SpeedTestError as exc:
        await status.edit_text(f"❌ فشل قياس السرعة: {error_message(exc)}")
        return
    except Exception as exc:
        logger.exception("Speedtest failed")
        await status.edit_text(f"❌ حصل خطأ: {error_message(exc)}")
        return

    await status.edit_text(
        "📶 <b>نتيجة قياس السرعة</b>\n"
        "――――――――――――――――\n"
        f"⬇️ التنزيل: <b>{result['download_mbps']:.1f} Mbps</b>\n"
        f"⬆️ الرفع: <b>{result['upload_mbps']:.1f} Mbps</b>\n"
        f"🏓 Ping: <b>{result['ping_ms']:.0f} ms</b>\n"
        f"🌍 السيرفر: {result['server_name']}, {result['server_country']}",
        parse_mode="HTML",
    )
