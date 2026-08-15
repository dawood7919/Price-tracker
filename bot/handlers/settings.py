"""Owner-only /settings panel for video and torrent search sources."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from ..config import Config
from ..settings_store import (
    SEARCH_SITES,
    TORRENT_SITES,
    get_active_site,
    get_active_torrent_sources,
    set_active_site,
    site_label,
    toggle_torrent_source,
    torrent_site_label,
)

logger = logging.getLogger(__name__)

# Keep the keyboard compact — Telegram rejects oversized reply markups.
_VIDEO_PER_PAGE = 8


def _is_owner(config: Config, update: Update) -> bool:
    return (
        config.telegram_owner_id is not None
        and update.effective_user is not None
        and update.effective_user.id == config.telegram_owner_id
    )


def _chunk_rows(
    buttons: list[InlineKeyboardButton], per_row: int = 2
) -> list[list[InlineKeyboardButton]]:
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


def _video_page_count() -> int:
    n = len(SEARCH_SITES)
    return max(1, (n + _VIDEO_PER_PAGE - 1) // _VIDEO_PER_PAGE)


def _markup(config: Config, video_page: int = 0) -> InlineKeyboardMarkup:
    active_video = get_active_site()
    active_torrent = set(get_active_torrent_sources(config.torrent_sources))
    rows: list[list[InlineKeyboardButton]] = []

    site_ids = list(SEARCH_SITES.keys())
    pages = _video_page_count()
    video_page = max(0, min(video_page, pages - 1))
    start = video_page * _VIDEO_PER_PAGE
    chunk_ids = site_ids[start : start + _VIDEO_PER_PAGE]

    rows.append(
        [
            InlineKeyboardButton(
                f"—— 🔍 فيديو ({video_page + 1}/{pages}) ——",
                callback_data="settings:noop",
            )
        ]
    )
    video_btns = [
        InlineKeyboardButton(
            f"{'✅ ' if site_id == active_video else ''}{SEARCH_SITES[site_id]['label']}",
            callback_data=f"setsite:{site_id}",
        )
        for site_id in chunk_ids
    ]
    rows.extend(_chunk_rows(video_btns, 2))

    if pages > 1:
        nav: list[InlineKeyboardButton] = []
        if video_page > 0:
            nav.append(
                InlineKeyboardButton("⬅️ السابق", callback_data=f"settings:vpage:{video_page - 1}")
            )
        if video_page < pages - 1:
            nav.append(
                InlineKeyboardButton("التالي ➡️", callback_data=f"settings:vpage:{video_page + 1}")
            )
        if nav:
            rows.append(nav)

    rows.append([InlineKeyboardButton("—— 🧲 تورنت رسمي ——", callback_data="settings:noop")])
    official: list[InlineKeyboardButton] = []
    for source_id in ("ubuntu", "debian", "fedora"):
        if source_id not in TORRENT_SITES:
            continue
        mark = "✅ " if source_id in active_torrent else "⬜ "
        official.append(
            InlineKeyboardButton(
                f"{mark}{torrent_site_label(source_id)}",
                callback_data=f"torrentsite:{source_id}",
            )
        )
    rows.extend(_chunk_rows(official, 3))

    rows.append([InlineKeyboardButton("—— 🌐 تورنت عام ——", callback_data="settings:noop")])
    public: list[InlineKeyboardButton] = []
    for source_id in TORRENT_SITES:
        if source_id in ("ubuntu", "debian", "fedora"):
            continue
        mark = "✅ " if source_id in active_torrent else "⬜ "
        public.append(
            InlineKeyboardButton(
                f"{mark}{torrent_site_label(source_id)}",
                callback_data=f"torrentsite:{source_id}",
            )
        )
    rows.extend(_chunk_rows(public, 2))

    return InlineKeyboardMarkup(rows)


def _text(config: Config) -> str:
    active_torrent = get_active_torrent_sources(config.torrent_sources)
    labels = "، ".join(torrent_site_label(source_id) for source_id in active_torrent) or "—"
    return (
        "⚙️ <b>إعدادات البحث</b>\n\n"
        f"🔍 موقع الفيديو: <b>{site_label()}</b>\n"
        f"🧲 مصادر التورنت: <b>{labels}</b>\n\n"
        "اضغط موقع فيديو لاختياره، أو مصدر تورنت لتفعيله/تعطيله.\n"
        "لازم يفضل مصدر تورنت واحد مفعّل على الأقل."
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    config: Config = context.bot_data["config"]
    if not _is_owner(config, update):
        return
    context.user_data["settings_vpage"] = 0
    await update.message.reply_text(
        _text(config), reply_markup=_markup(config, 0), parse_mode="HTML"
    )


async def _safe_edit(query, config: Config, video_page: int = 0) -> None:
    """Update the settings panel without crashing on Telegram BadRequest."""
    text = _text(config)
    markup = _markup(config, video_page)

    try:
        await query.edit_message_text(text=text, reply_markup=markup, parse_mode="HTML")
        return
    except BadRequest as exc:
        err = str(exc).lower()
        if "not modified" in err:
            return
        logger.warning("settings edit_message_text: %s", exc)
    except Exception:
        logger.exception("settings edit_message_text unexpected")

    try:
        await query.edit_message_reply_markup(reply_markup=markup)
        return
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            return
        logger.warning("settings edit_reply_markup: %s", exc)
    except Exception:
        logger.warning("settings edit_reply_markup failed", exc_info=True)

    try:
        chat = query.message.chat if query.message else None
        if chat is not None:
            await chat.send_message(text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        logger.exception("settings fallback send_message failed")


async def handle_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    if query is None or query.data is None:
        return False

    data = query.data
    config: Config = context.bot_data["config"]

    async def _ack(text: str | None = None, alert: bool = False) -> None:
        try:
            if text:
                await query.answer(text, show_alert=alert)
            else:
                await query.answer()
        except Exception:
            pass

    if data == "settings:noop":
        await _ack()
        return True

    if data.startswith("settings:vpage:"):
        if not _is_owner(config, update):
            await _ack("مش مسموح.", alert=True)
            return True
        try:
            page = int(data.rsplit(":", 1)[-1])
        except ValueError:
            page = 0
        page = max(0, min(page, _video_page_count() - 1))
        context.user_data["settings_vpage"] = page
        await _ack()
        await _safe_edit(query, config, page)
        return True

    if not (data.startswith("setsite:") or data.startswith("torrentsite:")):
        return False

    if not _is_owner(config, update):
        await _ack("مش مسموح.", alert=True)
        return True

    parts = data.split(":", 1)
    if len(parts) != 2:
        await _ack("طلب غير صالح.", alert=True)
        return True
    action, source_id = parts[0], parts[1].strip().lower()
    page = int(context.user_data.get("settings_vpage") or 0)

    if action == "setsite":
        if source_id not in SEARCH_SITES:
            await _ack("موقع غير معروف.", alert=True)
            return True
        set_active_site(source_id)
        await _ack(f"تم: {SEARCH_SITES[source_id]['label']}")
    elif action == "torrentsite":
        if source_id not in TORRENT_SITES:
            await _ack("مصدر تورنت غير معروف.", alert=True)
            return True
        before = get_active_torrent_sources(config.torrent_sources)
        after = toggle_torrent_source(source_id, config.torrent_sources)
        if before == after and source_id in before:
            await _ack("لازم يفضل مصدر واحد مفعّل.", alert=True)
        else:
            state = "مفعّل" if source_id in after else "معطّل"
            await _ack(f"{torrent_site_label(source_id)}: {state}")
    else:
        await _ack()
        return True

    await _safe_edit(query, config, page)
    return True
