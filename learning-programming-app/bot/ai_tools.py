"""Tools the /ai agent can call to control the bot."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from telegram.ext import ContextTypes

from .config import Config
from .handlers.torrent import search_torrents
from .jobs import StatusReporter, run_download_job
from .manager import DownloadManager
from .utils import disk_usage_mb, free_disk_mb, system_stats, truncate_text

logger = logging.getLogger(__name__)

MAX_SHELL_SECONDS = 20
MAX_SHELL_OUTPUT = 4000
MAX_LOG_CHARS = 3500

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_server_stats",
            "description": "حالة السيرفر: CPU, RAM, disk, عدد التحميلات الشغالة",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_logs",
            "description": "آخر سطور من لوج البوت",
            "parameters": {
                "type": "object",
                "properties": {
                    "lines": {
                        "type": "integer",
                        "description": "عدد السطور (افتراضي 40، أقصى 120)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kill_all_jobs",
            "description": "إلغاء كل التحميلات الشغالة ومسح ملفات التحميل المؤقتة",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_url",
            "description": (
                "بدء تحميل فيديو/ميديا من رابط يدعمه yt-dlp "
                "(يوتيوب، تويتر، فيسبوك، تيك توك...). يتم الرفع لتيليجرام تلقائيًا."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "رابط الفيديو"},
                    "title": {"type": "string", "description": "عنوان اختياري"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "torrent_ubuntu_search",
            "description": "بحث تورينت في نسخ Ubuntu الرسمية فقط من releases.ubuntu.com",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "مثل ubuntu أو desktop أو server أو 24.04",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (
                "تنفيذ أمر شل قصير على السيرفر (timeout 20 ثانية). "
                "استخدمه فقط لما يطلب صاحب البوت صراحة فحص أو أمر بسيط."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "الأمر"},
                },
                "required": ["command"],
            },
        },
    },
]


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    user_id: int,
) -> str:
    config: Config = context.bot_data["config"]
    manager: DownloadManager = context.bot_data["manager"]

    try:
        if name == "get_server_stats":
            stats = system_stats()
            total, used, free = disk_usage_mb(config.download_dir)
            by_state = manager.active_by_state()
            return json.dumps(
                {
                    "cpu_percent": stats["cpu_percent"],
                    "memory_percent": stats["memory_percent"],
                    "memory_used_mb": stats["memory_used_mb"],
                    "memory_total_mb": stats["memory_total_mb"],
                    "disk_total_mb": total,
                    "disk_used_mb": used,
                    "disk_free_mb": free,
                    "active_jobs": manager.active_jobs(),
                    "jobs_by_state": {str(k): v for k, v in by_state.items()},
                    "min_free_disk_mb": config.min_free_disk_mb,
                },
                ensure_ascii=False,
            )

        if name == "get_logs":
            lines = int(arguments.get("lines") or 40)
            lines = max(5, min(lines, 120))
            log_path = Path("/tmp/bot-logs/bot.log")
            # also try common path inside container
            candidates = [
                log_path,
                Path("/tmp/video-bot-downloads/bot.log"),
                Path("bot.log"),
            ]
            text = None
            for p in candidates:
                if p.is_file():
                    raw = p.read_text(errors="ignore").splitlines()
                    text = "\n".join(raw[-lines:])
                    break
            if text is None:
                return "مفيش ملف لوج واضح. جرب docker logs من السيرفر."
            return text[-MAX_LOG_CHARS:]

        if name == "kill_all_jobs":
            n = manager.cancel_all()
            # wipe download dir contents
            d = config.download_dir
            removed = 0
            if d.is_dir():
                for child in d.iterdir():
                    try:
                        if child.is_file():
                            child.unlink(missing_ok=True)
                            removed += 1
                        elif child.is_dir():
                            import shutil

                            shutil.rmtree(child, ignore_errors=True)
                            removed += 1
                    except Exception:
                        pass
            return json.dumps(
                {"cancelled_jobs": n, "cleaned_items": removed, "free_disk_mb": free_disk_mb(d)},
                ensure_ascii=False,
            )

        if name == "download_url":
            url = str(arguments.get("url") or "").strip()
            if not url.startswith("http://") and not url.startswith("https://"):
                return "رابط غير صالح — لازم يبدأ بـ http/https"
            title = str(arguments.get("title") or url).strip()
            job_id = uuid.uuid4().hex[:8]
            msg = await context.bot.send_message(
                chat_id, f"🕐 (AI) بدء تحميل\n{truncate_text(title, 60)}"
            )
            status = StatusReporter(msg, config.edit_throttle_seconds)
            downloader = context.bot_data["downloader"]
            uploader = context.bot_data["uploader"]

            async def _run() -> None:
                try:
                    await run_download_job(
                        config=config,
                        manager=manager,
                        downloader=downloader,
                        uploader=uploader,
                        status=status,
                        chat_id=chat_id,
                        user_id=user_id,
                        url=url,
                        format_key="best",
                        title_hint=title,
                        job_id=job_id,
                    )
                except Exception:
                    logger.exception("AI download_url failed")

            context.application.create_task(_run())
            return json.dumps(
                {
                    "status": "started",
                    "job_id": job_id,
                    "url": url,
                    "note": "التحميل شغال في الخلفية وهيوصل على تيليجرام عند الانتهاء",
                },
                ensure_ascii=False,
            )

        if name == "torrent_ubuntu_search":
            query = str(arguments.get("query") or "ubuntu").strip()
            results = await search_torrents(query)
            slim = [
                {"name": r["name"], "size": r["size"], "torrent": r["torrent"]}
                for r in results[:15]
            ]
            return json.dumps({"count": len(slim), "results": slim}, ensure_ascii=False)

        if name == "run_shell":
            command = str(arguments.get("command") or "").strip()
            if not command:
                return "أمر فاضي"
            # block some obviously catastrophic patterns
            lowered = command.lower()
            for bad in ("rm -rf /", "mkfs", ":(){", "dd if=", ">/dev/sd"):
                if bad in lowered:
                    return "الأمر مرفوض لأسباب أمان."
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=MAX_SHELL_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
                return f"انتهى الوقت ({MAX_SHELL_SECONDS}s) — تم إيقاف الأمر."
            out = (out_b or b"").decode(errors="ignore")
            if len(out) > MAX_SHELL_OUTPUT:
                out = out[:MAX_SHELL_OUTPUT] + "\n…(مقصوص)"
            return f"exit={proc.returncode}\n{out or '(no output)'}"

        return f"أداة غير معروفة: {name}"
    except Exception as exc:
        logger.exception("tool %s failed", name)
        return f"خطأ أثناء تنفيذ {name}: {exc}"
