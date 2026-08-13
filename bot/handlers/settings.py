"""/settings — pick active search site (owner only)."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..config import Config
from ..settings_store import SEARCH_SITES, get_active_site, set_active_site, site_label


def _is_owner(config: Config, update: Update) -> bool:
    return (
        config.telegram_owner_id is not None
        and update.effective_user is not None
        and update.effective_user.id == config.telegram_owner_id
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    config: Config = context.bot_data["config"]
    if not _is_owner(config, update):
        return

    active = get_active_site()
    rows: list[list[InlineKeyboardButton]] = []
    for sid, meta in SEARCH_SITES.items():
        mark = "✅ " if sid == active else ""
        rows.append(
            [
                InlineKeyboardButton(
                    f"{mark}{meta['label']}",
                    callback_data=f"setsite:{sid}",
                )
            ]
        )

    await update.message.reply_text(
        f"⚙️ <b>إعدادات البحث</b>\n\n"
        f"الموقع النشط دلوقتي: <b>{site_label(active)}</b>\n\n"
        "اختار موقع البحث للـ inline و <code>/secret search</code>:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="HTML",
    )


async def handle_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    if query is None or query.data is None:
        return False
    if not query.data.startswith("setsite:"):
        return False

    config: Config = context.bot_data["config"]
    if not _is_owner(config, update):
        await query.answer("مش مسموح.", show_alert=True)
        return True

    site = query.data.split(":", 1)[1].strip().lower()
    if site not in SEARCH_SITES:
        await query.answer("موقع غير معروف.", show_alert=True)
        return True

    set_active_site(site)
    await query.answer(f"تم: {SEARCH_SITES[site]['label']}")
    if query.message is not None:
        rows: list[list[InlineKeyboardButton]] = []
        for sid, meta in SEARCH_SITES.items():
            mark = "✅ " if sid == site else ""
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{mark}{meta['label']}",
                        callback_data=f"setsite:{sid}",
                    )
                ]
            )
        await query.message.edit_text(
            f"⚙️ <b>إعدادات البحث</b>\n\n"
            f"الموقع النشط: <b>{SEARCH_SITES[site]['label']}</b>\n\n"
            "الـ inline و /secret search هيستخدموا الموقع ده.",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode="HTML",
        )
    return True
