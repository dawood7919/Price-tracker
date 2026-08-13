"""/secret + inline — search on the active site from /settings."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from urllib.parse import urlparse

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.error import BadRequest, TimedOut
from telegram.ext import ContextTypes

from ..config import Config
from ..jobs import StatusReporter, run_download_job
from ..settings_store import get_active_site, site_label
from ..sitesearch import search_videos
from ..utils import truncate_text

logger = logging.getLogger(__name__)

INLINE_RESULT_TTL_SECONDS = 15 * 60
INLINE_MAX_RESULTS = 40


def _is_owner(config: Config, update: Update) -> bool:
    return (
        config.telegram_owner_id is not None
        and update.effective_user is not None
        and update.effective_user.id == config.telegram_owner_id
    )


def _safe_title(raw: str, fallback: str = "فيديو") -> str:
    text = re.sub(r"[\r\n\t]+", " ", raw or "").strip()
    return (truncate_text(text, 64) or fallback)[:64]


def _safe_thumb(raw: str | None) -> str | None:
    if not raw:
        return None
    u = str(raw).strip()
    if u.startswith("//"):
        u = "https:" + u
    if not u.startswith("https://") or len(u) > 300:
        return None
    path = urlparse(u).path.lower()
    if any(x in path for x in (".gif", ".svg", ".mp4", ".webm")):
        return None
    return u


def _page_keyboard(
    key: str, page_items: list[dict], offset: int, total: int, page_size: int
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for i, item in enumerate(page_items):
        idx = offset + i
        label = f"⬇️ {idx + 1}. {truncate_text(item['title'], 40)}"
        rows.append([InlineKeyboardButton(label, callback_data=f"secretdl:{key}:{idx}")])
    nav: list[InlineKeyboardButton] = []
    if offset > 0:
        nav.append(
            InlineKeyboardButton(
                "⬅️ السابق", callback_data=f"secretpage:{key}:{max(0, offset - page_size)}"
            )
        )
    nxt = offset + page_size
    if nxt < total:
        nav.append(
            InlineKeyboardButton(
                f"المزيد ➡️ ({nxt + 1}-{min(nxt + page_size, total)}/{total})",
                callback_data=f"secretpage:{key}:{nxt}",
            )
        )
    if nav:
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(
                f"⬇️ حمّل الصفحة ({len(page_items)})", callback_data=f"secretrun:{key}:{offset}"
            )
        ]
    )
    rows.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"secretdrop:{key}")])
    return InlineKeyboardMarkup(rows)


async def _render_page(message, key: str, pool: list[dict], offset: int, page_size: int) -> None:
    total = len(pool)
    offset = max(0, min(offset, max(0, total - 1)))
    page = pool[offset : offset + page_size]
    if not page and pool:
        offset = 0
        page = pool[:page_size]
    end = offset + len(page)
    label = site_label()
    lines = "\n".join(
        f"{offset + i + 1}. {truncate_text(it['title'], 50)}" for i, it in enumerate(page)
    )
    await message.edit_text(
        f"🔐 <b>{label}</b> — <b>{total}</b> نتيجة ({offset + 1}–{end})\n\n{lines}\n\n"
        f"اضغط فيديو أو <b>المزيد</b>.\n(غيّر الموقع من /settings)",
        reply_markup=_page_keyboard(key, page, offset, total, page_size),
        parse_mode="HTML",
    )


async def _show_results(update, context, status_message, found) -> None:
    config: Config = context.bot_data["config"]
    page_size = max(5, min(config.secret_page_size, 25))
    key = uuid.uuid4().hex[:8]
    context.user_data.setdefault("pending_secret", {})[key] = found
    await _render_page(status_message, key, found, 0, page_size)


async def secret_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    config: Config = context.bot_data["config"]
    # owner gate already applied globally; keep soft checks
    args = list(context.args or [])
    label = site_label()
    if not args or args[0].lower() not in ("search", "s") or len(args) < 2:
        await update.message.reply_text(
            f"🔐 بحث على <b>{label}</b>\n\n"
            f"<code>/secret search كلمة</code>\n"
            f"أو inline: <code>@البوت كلمة</code>\n\n"
            f"غيّر الموقع: /settings",
            parse_mode="HTML",
        )
        return

    query = " ".join(a.strip() for a in args[1:] if a.strip())
    status = await update.message.reply_text(
        f"🔐 ببحث على <b>{label}</b> عن <code>{query}</code>...", parse_mode="HTML"
    )
    found = await search_videos(query, config)
    if not found:
        await status.edit_text(
            f"❌ مفيش نتائج على {label}.\nجرّب كلمة تانية أو غيّر الموقع من /settings"
        )
        return
    await _show_results(update, context, status, found)


async def secret_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    iq = update.inline_query
    if iq is None:
        return True
    config: Config = context.bot_data["config"]
    # non-owners blocked by gate; still safe-guard
    if not _is_owner(config, update):
        with contextlib_suppress_answer(iq):
            await iq.answer([], cache_time=5, is_personal=True)
        return True

    q = (iq.query or "").strip()
    lower = q.lower()
    for p in ("search ", "s ", "search:", "s:"):
        if lower.startswith(p):
            q = q[len(p) :].strip()
            break

    label = site_label()
    if len(q) < 2:
        try:
            await iq.answer(
                [
                    InlineQueryResultArticle(
                        id="help",
                        title=f"اكتب كلمة بحث — {label}",
                        description="غيّر الموقع من /settings",
                        input_message_content=InputTextMessageContent(
                            f"اكتب بعد اسم البوت كلمة البحث ({label})"
                        ),
                    )
                ],
                cache_time=5,
                is_personal=True,
            )
        except Exception:
            pass
        return True

    results = (await search_videos(q, config))[:INLINE_MAX_RESULTS]
    pending = context.bot_data.setdefault("inline_secret_results", {})
    now = time.monotonic()
    for sk, (_it, created) in list(pending.items()):
        if now - created > INLINE_RESULT_TTL_SECONDS:
            del pending[sk]

    if not results:
        try:
            await iq.answer(
                [
                    InlineQueryResultArticle(
                        id=uuid.uuid4().hex[:8],
                        title=f"مفيش نتائج على {label}",
                        description=q,
                        input_message_content=InputTextMessageContent(
                            f"مفيش نتائج على {label}: {q}"
                        ),
                    )
                ],
                cache_time=5,
                is_personal=True,
            )
        except Exception:
            pass
        return True

    def build(with_thumbs: bool):
        out = []
        for item in results:
            key = uuid.uuid4().hex[:10]
            pending[key] = (item, now)
            title = _safe_title(item.get("title") or "")
            kw = dict(
                id=key,
                title=title,
                description=f"{label} · {title}",
                input_message_content=InputTextMessageContent(f"🔐 {title}"),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬇️ تحميل", callback_data=f"secretdl_i:{key}")]]
                ),
            )
            if with_thumbs:
                t = _safe_thumb(item.get("thumb"))
                if t:
                    kw["thumbnail_url"] = t
            out.append(InlineQueryResultArticle(**kw))
        return out

    answers = build(True)
    try:
        await iq.answer(answers, cache_time=5, is_personal=True)
    except (BadRequest, TimedOut):
        try:
            await iq.answer(build(False), cache_time=5, is_personal=True)
        except Exception:
            try:
                await iq.answer([], cache_time=1, is_personal=True)
            except Exception:
                pass
    except Exception:
        logger.exception("inline answer failed")
    return True


class contextlib_suppress_answer:
    def __init__(self, iq):
        self.iq = iq

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return True


async def handle_secret_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    if query is None or query.data is None:
        return False
    parts = query.data.split(":")
    action = parts[0]
    if action not in (
        "secretdl",
        "secretrun",
        "secretrunall",
        "secretdrop",
        "secretdl_i",
        "secretpage",
    ):
        return False

    config: Config = context.bot_data["config"]
    if not _is_owner(config, update):
        await query.answer("مش مسموح.", show_alert=True)
        return True
    if len(parts) < 2:
        return True

    key = parts[1]
    bot = context.bot
    inline_id = query.inline_message_id
    message = query.message
    page_size = max(5, min(config.secret_page_size, 25))

    if action == "secretpage":
        if message is None or len(parts) < 3:
            return True
        try:
            offset = int(parts[2])
        except ValueError:
            return True
        pool = context.user_data.get("pending_secret", {}).get(key)
        if not pool:
            await query.answer("انتهت القائمة.", show_alert=True)
            return True
        await query.answer()
        await _render_page(message, key, pool, offset, page_size)
        return True

    if action == "secretdl_i":
        await query.answer()
        entry = context.bot_data.get("inline_secret_results", {}).pop(key, None)
        item = None
        if entry:
            item, created = entry
            if time.monotonic() - created > INLINE_RESULT_TTL_SECONDS:
                item = None
        if item is None:
            text = "انتهت الصلاحية — دوّر تاني."
            if message:
                await message.edit_text(text)
            elif inline_id:
                await bot.edit_message_text(text, inline_message_id=inline_id)
            return True
        manager = context.bot_data["manager"]
        downloader = context.bot_data["downloader"]
        uploader = context.bot_data["uploader"]
        user_id = update.effective_user.id if update.effective_user else 0
        chat_id = message.chat_id if message else user_id
        status = StatusReporter(
            message, config.edit_throttle_seconds, inline_message_id=inline_id, bot=bot
        )
        await status.update(f"🕐 {truncate_text(item['title'], 60)}", force=True)

        async def run_one():
            try:
                await run_download_job(
                    config=config,
                    manager=manager,
                    downloader=downloader,
                    uploader=uploader,
                    status=status,
                    chat_id=chat_id,
                    user_id=user_id,
                    url=item["url"],
                    format_key="best",
                    title_hint=item["title"],
                    job_id=uuid.uuid4().hex[:8],
                )
            except Exception:
                logger.exception("inline dl fail")

        context.application.create_task(run_one())
        return True

    pending = context.user_data.get("pending_secret", {})
    if message is None:
        return True
    if action == "secretdrop":
        pending.pop(key, None)
        await message.edit_text("👌 اتلغى.")
        return True
    items = pending.get(key)
    if items is None:
        await message.edit_text("القائمة قديمة.")
        return True

    manager = context.bot_data["manager"]
    downloader = context.bot_data["downloader"]
    uploader = context.bot_data["uploader"]
    chat_id = message.chat_id
    user_id = update.effective_user.id if update.effective_user else 0

    if action == "secretdl":
        if len(parts) < 3:
            return True
        try:
            idx = int(parts[2])
        except ValueError:
            return True
        if idx < 0 or idx >= len(items):
            await query.answer("مش موجود.", show_alert=True)
            return True
        item = items[idx]
        await query.answer()
        msg = await context.bot.send_message(
            chat_id, f"🕐 {truncate_text(item['title'], 60)}"
        )
        st = StatusReporter(msg, config.edit_throttle_seconds)

        async def run_one():
            try:
                await run_download_job(
                    config=config,
                    manager=manager,
                    downloader=downloader,
                    uploader=uploader,
                    status=st,
                    chat_id=chat_id,
                    user_id=user_id,
                    url=item["url"],
                    format_key="best",
                    title_hint=item["title"],
                    job_id=uuid.uuid4().hex[:8],
                )
            except Exception:
                logger.exception("dl fail")

        context.application.create_task(run_one())
        return True

    if action == "secretrun":
        off = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 0
        batch = items[off : off + page_size]
    else:
        batch = list(items)

    await query.answer()
    await message.edit_text(f"📃 بدء تحميل {len(batch)}...")

    async def run_all():
        ok = 0
        for i, item in enumerate(batch, 1):
            msg = await context.bot.send_message(
                chat_id, f"🕐 ({i}/{len(batch)}) {truncate_text(item['title'], 50)}"
            )
            st = StatusReporter(msg, config.edit_throttle_seconds)
            try:
                if await run_download_job(
                    config=config,
                    manager=manager,
                    downloader=downloader,
                    uploader=uploader,
                    status=st,
                    chat_id=chat_id,
                    user_id=user_id,
                    url=item["url"],
                    format_key="best",
                    title_hint=item["title"],
                    job_id=uuid.uuid4().hex[:8],
                ):
                    ok += 1
            except asyncio.CancelledError:
                await context.bot.send_message(chat_id, "🚫 اتلغى.")
                return
            except Exception:
                logger.exception("batch fail")
        await context.bot.send_message(chat_id, f"📃 خلص — {ok}/{len(batch)}")

    context.application.create_task(run_all())
    return True
