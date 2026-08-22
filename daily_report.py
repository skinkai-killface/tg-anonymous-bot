# --------------------------------------------------------
# Telegram Anonymous Suggestion Bot
# Author: @NAWMBAD (2026)
# Licensed under the MIT License
# --------------------------------------------------------

import html
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from aiogram import Bot

from config import ADMIN_CHAT_ID
from database import get_daily_moderator_stats

logger = logging.getLogger(__name__)

CHISINAU_TZ = ZoneInfo("Europe/Chisinau")


def get_seconds_until_chisinau_midnight() -> float:
    """Calculate exact seconds until next 00:00:00 in Europe/Chisinau timezone."""
    now = datetime.now(CHISINAU_TZ)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1.0, (tomorrow - now).total_seconds())


def format_daily_report(stats: list[dict], date_title: str) -> str:
    """Format daily moderation leaderboard message."""
    total_processed = sum(s["total"] for s in stats)
    if not stats or total_processed == 0:
        return (
            f"🏆 <b>Ежедневные итоги модерации</b>\n"
            f"📅 <i>Дата: {date_title} (Кишинёв)</i>\n\n"
            f"😴 За данный период предложения не обрабатывались."
        )

    medals = ["🥇", "🥈", "🥉"]
    lines = [
        f"🏆 <b>Ежедневные итоги модерации</b>\n"
        f"📅 <i>Дата: {date_title} (Кишинёв)</i>\n",
        f"📊 <b>Всего обработано:</b> <code>{total_processed}</code>\n",
        "👥 <b>Топ лидеров модерации:</b>\n"
    ]

    for idx, s in enumerate(stats):
        rank_icon = medals[idx] if idx < len(medals) else f"<b>{idx + 1}.</b>"
        mod_name = html.escape(s["moderator_name"] or f"ID {s['moderator_id']}")
        lines.append(
            f"{rank_icon} <b>{mod_name}</b> [ID: <code>{s['moderator_id']}</code>]\n"
            f"   ├ ✅ Одобрено: <b>{s['approved']}</b>\n"
            f"   ├ ❌ Отклонено: <b>{s['rejected']}</b>\n"
            f"   └ 🚫 Заблокировано: <b>{s['blocked']}</b> (всего: <b>{s['total']}</b>)\n"
        )

    lines.append("🔥 <i>Отличная работа, команда! Всем спасибо за модерацию!</i>")
    return "\n".join(lines)


async def send_daily_report(bot: Bot, date_str: str | None = None) -> None:
    """Send daily leaderboard report to ADMIN_CHAT_ID for the given date (or yesterday)."""
    if not date_str:
        now = datetime.now(CHISINAU_TZ)
        yesterday = now - timedelta(days=1)
        date_str = yesterday.strftime("%Y-%m-%d")
        display_date = yesterday.strftime("%d.%m.%Y")
    else:
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            display_date = dt.strftime("%d.%m.%Y")
        except Exception:
            display_date = date_str

    stats = await get_daily_moderator_stats(date_str)
    report_text = format_daily_report(stats, display_date)

    try:
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=report_text,
            parse_mode="HTML",
        )
        logger.info("Daily report for %s sent to admin chat.", date_str)
    except Exception as e:
        logger.error("Could not send daily report to admin chat: %s", e)


async def daily_report_loop(bot: Bot) -> None:
    """Background task running continuously, triggering report at 00:00 Europe/Chisinau time."""
    logger.info("Daily report loop started for Europe/Chisinau timezone.")
    while True:
        try:
            delay = get_seconds_until_chisinau_midnight()
            logger.info("Next Chisinau midnight report in %.1f seconds (~%.1f hours).", delay, delay / 3600)
            await asyncio.sleep(delay)

            # Fire the report for the day that just ended
            await send_daily_report(bot)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Error in daily_report_loop: %s", e)
            await asyncio.sleep(60)
