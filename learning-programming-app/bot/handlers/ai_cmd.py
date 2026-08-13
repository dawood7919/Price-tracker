"""/ai — owner-only agent that can call bot tools."""

from __future__ import annotations

import json
import logging
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from ..ai_client import DEFAULT_SYSTEM, AIError, chat_completion
from ..ai_tools import TOOL_SCHEMAS, execute_tool
from ..config import Config
from ..utils import error_message

logger = logging.getLogger(__name__)

MAX_HISTORY = 16
MAX_REPLY_CHARS = 4000
MAX_TOOL_ROUNDS = 6


async def ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return

    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id
    chat_id = update.message.chat_id

    if config.telegram_owner_id is None:
        await update.message.reply_text(
            "⚠️ /ai محتاج <code>TELEGRAM_OWNER_ID</code> في .env",
            parse_mode="HTML",
        )
        return

    if user_id != config.telegram_owner_id:
        await update.message.reply_text("⛔ الأمر ده لصاحب البوت بس.")
        return

    if not config.ai_api_key:
        await update.message.reply_text(
            "⚠️ مفيش AI_API_KEY في .env — أضفه من Groq وأعد التشغيل.",
            parse_mode="HTML",
        )
        return

    prompt = " ".join(context.args or []).strip()
    if not prompt:
        await update.message.reply_text(
            "🤖 <b>وكيل البوت</b>\n\n"
            "أمثلة:\n"
            "• <code>/ai حالة السيرفر إيه؟</code>\n"
            "• <code>/ai حمّل الرابط https://...</code>\n"
            "• <code>/ai دور على ubuntu desktop</code>\n"
            "• <code>/ai أوقف كل التحميلات</code>\n"
            "• <code>/ai ورّيني آخر اللوج</code>\n"
            "• <code>/ai reset</code> مسح السياق\n",
            parse_mode="HTML",
        )
        return

    if prompt.lower() in ("reset", "clear", "جديد"):
        context.user_data.pop("ai_history", None)
        await update.message.reply_text("✅ تم مسح سياق المحادثة.")
        return

    history: list[dict[str, Any]] = context.user_data.setdefault("ai_history", [])
    history.append({"role": "user", "content": prompt})
    if len(history) > MAX_HISTORY:
        del history[: len(history) - MAX_HISTORY]

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": DEFAULT_SYSTEM},
        *history,
    ]
    status = await update.message.reply_text("🤖 بفكر / بنفّذ...")

    final_text = ""
    try:
        for _round in range(MAX_TOOL_ROUNDS):
            msg = await chat_completion(
                api_key=config.ai_api_key,
                base_url=config.ai_base_url,
                model=config.ai_model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                timeout_seconds=config.ai_timeout_seconds,
            )
            tool_calls = msg.get("tool_calls") or []
            content = (msg.get("content") or "").strip()

            # Append assistant message as returned (may include tool_calls)
            messages.append(msg)

            if not tool_calls:
                final_text = content or "✅ تم."
                break

            # status hint
            names = ", ".join(
                (tc.get("function") or {}).get("name", "?") for tc in tool_calls
            )
            with_contextlib_suppress = True
            try:
                await status.edit_text(f"🛠 بستخدم: {names}")
            except Exception:
                pass

            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except json.JSONDecodeError:
                    args = {}
                result = await execute_tool(
                    name, args, context, chat_id=chat_id, user_id=user_id
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id") or name,
                        "content": result,
                    }
                )
        else:
            final_text = content or "وصلت لحد أقصى من خطوات الأدوات. جرب تطلب خطوة أصغر."

    except AIError as exc:
        if history and history[-1].get("role") == "user":
            history.pop()
        await status.edit_text(f"❌ {exc}")
        return
    except Exception as exc:
        if history and history[-1].get("role") == "user":
            history.pop()
        logger.exception("AI agent failed")
        await status.edit_text(f"❌ {error_message(exc)}")
        return

    # Store only plain text turns in long-term history (drop tool noise)
    history.append({"role": "assistant", "content": final_text})
    if len(history) > MAX_HISTORY:
        del history[: len(history) - MAX_HISTORY]

    if len(final_text) > MAX_REPLY_CHARS:
        final_text = final_text[: MAX_REPLY_CHARS - 20] + "\n\n…(اختُصر)"

    try:
        await status.edit_text(final_text)
    except Exception:
        await status.edit_text(final_text[:MAX_REPLY_CHARS])
