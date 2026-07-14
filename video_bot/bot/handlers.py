import logging
import uuid

from telegram import Update
from telegram.ext import ContextTypes

import database.repository as db
from bot import messages
from bot.keyboards import quality_keyboard, resolve_format
from config import OWNER_TELEGRAM_ID
from downloaders import manager
from downloaders.manager import UnauthorizedError, UnsupportedSourceError
from services import downloader_service, telegram_upload
from services.downloader_service import FileTooLargeError
from utils.validators import extract_domain

logger = logging.getLogger(__name__)

# request_id -> pending download context. In-memory is fine: this is a
# single-owner bot and requests only need to survive one interactive session.
_pending_requests: dict[str, dict] = {}


def _is_owner(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id == OWNER_TELEGRAM_ID


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        await update.message.reply_text(messages.owner_only_message())
        return
    db.upsert_user(update.effective_user.id, update.effective_user.username)
    await update.message.reply_text(messages.WELCOME_MESSAGE)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    await update.message.reply_text(messages.HELP_MESSAGE)


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    await update.message.reply_text(messages.ABOUT_MESSAGE)


async def url_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        await update.message.reply_text(messages.owner_only_message())
        return

    url = update.message.text.strip()
    status_message = await update.message.reply_text(messages.URL_RECEIVED)
    await status_message.edit_text(messages.FETCHING_INFO)

    try:
        info = manager.get_video_info(url)
    except UnsupportedSourceError as exc:
        await status_message.edit_text(messages.unsupported_source_message(str(exc)))
        return
    except UnauthorizedError as exc:
        await status_message.edit_text(messages.unauthorized_message(str(exc)))
        return
    except Exception as exc:
        logger.exception("Failed to fetch video info for %s", url)
        await status_message.edit_text(messages.error_message(str(exc)))
        return

    request_id = uuid.uuid4().hex[:8]
    _pending_requests[request_id] = {
        "url": url,
        "title": info.title,
        "thumbnail": info.thumbnail,
    }

    await status_message.edit_text(
        messages.video_info_message(info.title, info.duration),
        reply_markup=quality_keyboard(request_id),
    )


async def download_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if update.effective_user is None or update.effective_user.id != OWNER_TELEGRAM_ID:
        await query.answer("Not authorized.", show_alert=True)
        return

    await query.answer()
    action, request_id, format_code = query.data.split(":")

    pending = _pending_requests.get(request_id)
    if pending is None:
        await query.edit_message_text(messages.expired_request_message())
        return

    if action == "cancel":
        _pending_requests.pop(request_id, None)
        await query.edit_message_text(messages.CANCELLED_MESSAGE)
        return

    format_selector = resolve_format(format_code)
    await query.edit_message_text(messages.DOWNLOADING_MESSAGE)

    async def on_progress(percent: float) -> None:
        try:
            await query.edit_message_text(messages.downloading_progress_message(percent))
        except Exception:
            pass  # transient edit conflicts are harmless; the next update will land

    history_id = db.start_download(
        user_id=update.effective_user.id,
        url=pending["url"],
        domain=extract_domain(pending["url"]),
        title=pending["title"],
        resolution=format_code,
    )

    file_path = None
    try:
        file_path = await downloader_service.download_with_progress(
            pending["url"], format_selector, on_progress
        )
        await query.edit_message_text(messages.UPLOADING_MESSAGE)
        await telegram_upload.send_video_result(
            context=context,
            chat_id=query.message.chat_id,
            file_path=file_path,
            title=pending["title"],
            resolution=format_code,
            original_url=pending["url"],
            thumbnail_url=pending["thumbnail"],
        )
        await query.edit_message_text(messages.DONE_MESSAGE)
        db.finish_download(history_id, "completed", file_path.stat().st_size)
    except FileTooLargeError as exc:
        await query.edit_message_text(messages.error_message(str(exc)))
        db.finish_download(history_id, "failed_too_large", None)
    except Exception as exc:
        logger.exception("Download/upload failed for %s", pending["url"])
        await query.edit_message_text(messages.error_message(str(exc)))
        db.finish_download(history_id, "failed", None)
    finally:
        if file_path is not None:
            downloader_service.cleanup(file_path)
        _pending_requests.pop(request_id, None)
