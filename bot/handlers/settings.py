"""Owner-only /settings panel for video and torrent search sources."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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


def _is_owner(config: Config, update: Update) -> bool:
    return (
        config.telegram_owner_id is not None
        and update.effective_user is not None
        and update.effective_user.id == config.telegram_owner_id
    )


def _markup(config: Config) -> InlineKeyboardMarkup:
    active_video = get_active_site()
    active_torrent = set(get_active_torrent_sources(config.torrent_sources))
    rows: list[list[InlineKeyboardButton]] = []

    for site_id, meta in SEARCH_SITES.items():
        mark = "✅ " if site_id == active_video else ""
        rows.append([InlineKeyboardButton(f"{mark}{meta['label']}", callback_data=f"setsite:{site_id}")])

    for source_id in TORRENT_SITES:
        mark = "✅ " if source_id in active_torrent else "⬜ "
        rows.append(
            [
                InlineKeyboardButton(
                    f"{mark}{torrent_site_label(source_id)}",
                    callback_data=f"torrentsite:{source_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


def _text(config: Config) -> str:
    active_torrent = get_active_torrent_sources(config.torrent_sources)
    labels = "، ".join(torrent_site_label(source_id) for source_id in active_torrent)
    return (
        "⚙️ <b>إعدادات البحث</b>\n\n"
        f"🔍 موقع البحث الحالي: <b>{site_label()}</b>\n"
        "اختر موقع البحث للفيديو من الأزرار الأولى.\n\n"
        f"🧲 مصادر التورنت المفعّلة: <b>{labels}</b>\n"
        "اضغط أزرار مصادر التورنت لتفعيلها أو تعطيلها. يجب أن يبقى مصدر واحد مفعّل على الأقل."
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    config: Config = context.bot_data["config"]
    if not _is_owner(config, update):
        return
    await update.message.reply_text(_text(config), reply_markup=_markup(config), parse_mode="HTML")


async def handle_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    if query is None or query.data is None:
        return False
    if not (query.data.startswith("setsite:") or query.data.startswith("torrentsite:")):
        return False

    config: Config = context.bot_data["config"]
    if not _is_owner(config, update):
        await query.answer("مش مسموح.", show_alert=True)
        return True

    action, source_id = query.data.split(":", 1)
    source_id = source_id.strip().lower()
    if action == "setsite":
        if source_id not in SEARCH_SITES:
            await query.answer("موقع غير معروف.", show_alert=True)
            return True
        set_active_site(source_id)
        await query.answer(f"تم اختيار {SEARCH_SITES[source_id]['label']}")
    else:
        if source_id not in TORRENT_SITES:
            await query.answer("مصدر تورنت غير معروف.", show_alert=True)
            return True
        before = get_active_torrent_sources(config.torrent_sources)
        after = toggle_torrent_source(source_id, config.torrent_sources)
        if before == after and source_id in before:
            await query.answer("لازم يفضل مصدر تورنت واحد مفعّل على الأقل.", show_alert=True)
        else:
            await query.answer("تم تحديث مصادر التورنت.")

    if query.message is not None:
        await query.message.edit_text(
            _text(config), reply_markup=_markup(config), parse_mode="HTML"
        )
    return True
