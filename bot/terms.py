"""Usage-terms acknowledgement enforced before the bot's features are used."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import ContextTypes

from .settings_store import _load, _save

TERMS_VERSION = "2026-08-13"
TERMS_CALLBACK = "termsaccept:legal-torrent-v1"

TERMS_TEXT = (
    "<b>شروط استخدام البوت</b>\n\n"
    "هذا البوت أداة تقنية للبحث والتنزيل من مصادر التورنت الرسمية المفعّلة فقط. "
    "باستمرارك، أنت تؤكد أن لديك الحق في تنزيل أو توزيع أي ملف تختاره، وأنك ستلتزم "
    "بقوانين بلدك وشروط المصدر.\n\n"
    "أنت مسؤول عن طريقة استخدامك للبوت وعن الحصول على الأذونات اللازمة. "
    "لا يقدّم مشغّل البوت أي محتوى ولا يتحمل مسؤولية الاستخدام المخالف للقانون.\n\n"
    "اضغط «أوافق وأتابع» لتفعيل الأوامر."
)


def is_terms_accepted(user_id: int) -> bool:
    data = _load()
    accepted = data.get("terms_acceptance", {})
    return isinstance(accepted, dict) and accepted.get(str(user_id)) == TERMS_VERSION


def accept_terms(user_id: int) -> None:
    data = _load()
    accepted = data.get("terms_acceptance")
    if not isinstance(accepted, dict):
        accepted = {}
    accepted[str(user_id)] = TERMS_VERSION
    data["terms_acceptance"] = accepted
    _save(data)


def terms_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ أوافق وأتابع", callback_data=TERMS_CALLBACK)]]
    )


async def send_terms(message: Message) -> None:
    await message.reply_text(TERMS_TEXT, reply_markup=terms_markup(), parse_mode="HTML")


async def terms_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is not None:
        await send_terms(update.message)


async def handle_terms_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return
    await query.answer("تم تسجيل موافقتك.")
    accept_terms(user.id)
    if query.message is not None:
        await query.message.edit_text(
            "✅ <b>تم تفعيل البوت.</b>\n\n"
            "استخدم <code>/torrent ubuntu</code> أو افتح <code>/settings</code> لإدارة مصادر التورنت.",
            parse_mode="HTML",
        )
