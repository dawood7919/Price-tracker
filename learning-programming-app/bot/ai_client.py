"""OpenAI-compatible chat client (Groq free tier, etc.) with optional tools."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM = (
    "أنت وكيل ذكي داخل بوت تيليجرام تعليمي. تتكلم العربية باختصار.\n"
    "عندك أدوات للتحكم في البوت والسيرفر — استخدمها عندما يطلب المستخدم تنفيذ شيء.\n"
    "قواعد:\n"
    "- نفّذ الطلب عبر الأدوات المتاحة بدل ما تدّعي إنك نفّذت.\n"
    "- التحميل بالفيديو عبر download_url (يوتيوب وغيرها مما يدعمه yt-dlp).\n"
    "- بحث التورينت عبر torrent_ubuntu_search فقط (نسخ Ubuntu الرسمية).\n"
    "- لا تبحث في فهارس تورينت عامة للأفلام/المسلسلات.\n"
    "- أوامر الشل عبر run_shell بحذر ولما يطلب صاحب البوت صراحة.\n"
    "- بعد استخدام الأدوات، لخّص النتيجة للمستخدم بوضوح.\n"
)


class AIError(RuntimeError):
    pass


async def chat_completion(
    *,
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    timeout_seconds: float = 90.0,
) -> dict[str, Any]:
    """POST /chat/completions. Returns the assistant message dict
    (may include content and/or tool_calls)."""
    url = base_url.rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.4,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                body = await resp.text()
                if resp.status >= 400:
                    logger.warning("AI API %s: %s", resp.status, body[:400])
                    raise AIError(f"الـ API رجّع خطأ {resp.status}: {body[:250]}")
                data = await resp.json(content_type=None)
    except aiohttp.ClientError as exc:
        raise AIError(f"مقدرتش أوصل للـ API: {exc}") from exc

    try:
        return data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError(f"رد الـ API مش مفهوم: {str(data)[:250]}") from exc
