"""Block every update that is not from TELEGRAM_OWNER_ID — silent for others."""

from __future__ import annotations

import contextlib
import logging

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from .config import Config
from .terms import TERMS_CALLBACK, is_terms_accepted

logger = logging.getLogger(__name__)


async def owner_only_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs in group -1 before all other handlers.

    Non-owners get *no* reply at all (including /start).
    """
    config: Config | None = context.bot_data.get("config")
    if config is None:
        return

    user = update.effective_user
    uid = user.id if user else None

    if config.telegram_owner_id is None:
        # Misconfigured bot: only warn the first person who messages, then stop.
        if update.effective_message is not None:
            with contextlib.suppress(Exception):
                await update.effective_message.reply_text(
                    "⚠️ البوت مقفول لحد ما يتظبط <code>TELEGRAM_OWNER_ID</code> في .env",
                    parse_mode="HTML",
                )
        raise ApplicationHandlerStop

    if uid != config.telegram_owner_id:
        # Completely silent — no advertise, no /start reply.
        if update.inline_query is not None:
            with contextlib.suppress(Exception):
                await update.inline_query.answer([], cache_time=30, is_personal=True)
        raise ApplicationHandlerStop

    # The owner can always read /start and /terms. Every other feature waits
    # for the one-time acknowledgement stored in settings_store.
    if uid is not None and not is_terms_accepted(uid):
        message = update.effective_message
        text = (message.text or "").strip() if message is not None else ""
        callback = update.callback_query.data if update.callback_query is not None else ""
        if text.startswith(("/start", "/terms")) or callback == TERMS_CALLBACK:
            return
        if update.inline_query is not None:
            with contextlib.suppress(Exception):
                await update.inline_query.answer([], cache_time=5, is_personal=True)
        elif message is not None:
            with contextlib.suppress(Exception):
                await message.reply_text("استخدم /start واقبل شروط الاستخدام أولًا.")
        raise ApplicationHandlerStop
