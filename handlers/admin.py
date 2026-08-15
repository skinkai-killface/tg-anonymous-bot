import re
import os
import sys
import time
import asyncio
import logging
from aiogram import Router, types, Bot, F
from aiogram.filters import Command

from database import block_user, unblock_user, get_blocked_list, get_stats, get_moderator_stats
from config import ADMIN_CHAT_ID

router = Router()
logger = logging.getLogger(__name__)


def extract_user_id(message: types.Message) -> int | None:
    """
    Extract author's user ID from the replied message:
    1. From message entities (text_mention or text_link with tg://user?id=)
    2. From inline keyboard callback_data: e.g. "approve:12345678:..."
    3. From text/caption regex (tg://user?id= or ID: 12345678)
    """
    # 1. Check message entities
    entities = (message.entities or []) + (message.caption_entities or [])
    for entity in entities:
        if entity.type == "text_mention" and entity.user:
            return entity.user.id
        if entity.type == "text_link" and entity.url:
            match = re.search(r"tg://user\?id=(\d+)", entity.url)
            if match:
                return int(match.group(1))

    # 2. Check inline keyboard callback_data
    if message.reply_markup and message.reply_markup.inline_keyboard:
        for row in message.reply_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data:
                    parts = btn.callback_data.split(":")
                    if len(parts) >= 2 and parts[1].isdigit():
                        return int(parts[1])

    # 3. Check raw text / caption
    target_text = message.text or message.caption or ""

    match = re.search(r"tg://user\?id=(\d+)", target_text)
    if match:
        return int(match.group(1))

    match = re.search(r"ID[:\s]+(?:<code>)?(\d+)(?:</code>)?", target_text, re.IGNORECASE)
    if match:
        return int(match.group(1))

    match = re.search(r"ID.*?(\d{5,15})", target_text, re.IGNORECASE)
    if match:
        return int(match.group(1))

    return None


@router.message(Command("ping"))
async def cmd_ping(message: types.Message):
    """
    /ping — check bot latency and responsiveness.
    Only works in the admin chat.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return

    start_time = time.monotonic()
    msg = await message.answer("🏓 Понг...")
    end_time = time.monotonic()
    latency = round((end_time - start_time) * 1000)
    await msg.edit_text(f"🏓 <b>Понг!</b>\n⏱ Задержка: <code>{latency} ms</code>", parse_mode="HTML")


@router.message(Command("restart"))
async def cmd_restart(message: types.Message):
    """
    /restart — gracefully restart the bot process.
    Only works in the admin chat.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return

    await message.answer("🔄 <b>Перезапуск бота...</b>", parse_mode="HTML")
    logger.info(f"Restart initiated by admin {message.from_user.id} ({message.from_user.full_name})")
    await asyncio.sleep(1)

    # Re-execute the current python process
    os.execv(sys.executable, [sys.executable] + sys.argv)


@router.message(Command("ban"))
async def cmd_ban(message: types.Message):
    """
    /ban <user_id> — block a user by their Telegram ID.
    Only works in the admin chat.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("Использование: /ban <user_id>")
        return

    user_id = int(args[1].strip())
    await block_user(user_id, reason=f"Banned by {message.from_user.full_name}")
    await message.answer(f"🚫 Пользователь <code>{user_id}</code> заблокирован.", parse_mode="HTML")


@router.message(Command("unban"))
async def cmd_unban(message: types.Message):
    """
    /unban <user_id> — unblock a user by their Telegram ID.
    Only works in the admin chat.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("Использование: /unban <user_id>")
        return

    user_id = int(args[1].strip())
    if await unblock_user(user_id):
        await message.answer(f"✅ Пользователь <code>{user_id}</code> разблокирован.", parse_mode="HTML")
    else:
        await message.answer(f"Пользователь <code>{user_id}</code> не был в бан-листе.", parse_mode="HTML")


@router.message(Command("banlist"))
async def cmd_banlist(message: types.Message):
    """
    /banlist — show all blocked users.
    Only works in the admin chat.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return

    blocked = await get_blocked_list()
    if not blocked:
        await message.answer("Бан-лист пуст.")
        return

    lines = ["🚫 <b>Бан-лист:</b>\n"]
    for uid, reason, blocked_at in blocked:
        lines.append(f"• <code>{uid}</code> — {reason or '—'}")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("stats"))
async def cmd_stats(message: types.Message):
    """
    /stats — show bot statistics (suggestions, approvals, rejections, blocks, per-moderator).
    Only works in the admin chat.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return

    stats = await get_stats()
    total = stats.get("total_suggestions", 0)
    approved = stats.get("approved", 0)
    rejected = stats.get("rejected", 0)
    blocked = stats.get("blocked", 0)
    pending = total - approved - rejected - blocked

    lines = [
        "📊 <b>Статистика бота</b>\n",
        f"📩 Всего предложений: <b>{total}</b>",
        f"✅ Одобрено: <b>{approved}</b>",
        f"❌ Отклонено: <b>{rejected}</b>",
        f"🚫 Заблокировано: <b>{blocked}</b>",
        f"⏳ В ожидании: <b>{max(0, pending)}</b>",
    ]

    # Per-moderator breakdown
    mod_stats = await get_moderator_stats()
    if mod_stats:
        lines.append("\n👥 <b>По модераторам:</b>\n")
        for admin_id, admin_name, a, r, b in mod_stats:
            total_mod = a + r + b
            lines.append(
                f"• <b>{admin_name}</b>: "
                f"✅{a} ❌{r} 🚫{b} (всего: {total_mod})"
            )

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(F.chat.id == ADMIN_CHAT_ID, F.reply_to_message)
async def admin_reply_to_user(message: types.Message, bot: Bot):
    """
    When an admin replies to a moderation message in the admin chat,
    send their reply directly to the user in PM.
    """
    # Ignore commands
    if message.text and message.text.startswith("/"):
        return

    reply = message.reply_to_message
    if not reply:
        return

    user_id = extract_user_id(reply)
    if not user_id:
        logger.warning(f"Could not extract user_id from reply message: {reply.text or reply.caption}")
        return

    logger.info(f"Admin {message.from_user.id} is replying to user {user_id}")

    try:
        if message.text:
            await bot.send_message(
                chat_id=user_id,
                text=f"💬 <b>Ответ от администрации:</b>\n\n{message.text}",
                parse_mode="HTML",
            )
        elif message.photo:
            caption = f"💬 <b>Ответ от администрации:</b>\n\n{message.caption}" if message.caption else "💬 <b>Ответ от администрации</b>"
            await bot.send_photo(
                chat_id=user_id,
                photo=message.photo[-1].file_id,
                caption=caption,
                parse_mode="HTML",
            )
        elif message.voice:
            caption = f"💬 <b>Ответ от администрации:</b>\n\n{message.caption}" if message.caption else "💬 <b>Ответ от администрации</b>"
            await bot.send_voice(
                chat_id=user_id,
                voice=message.voice.file_id,
                caption=caption,
                parse_mode="HTML",
            )
        elif message.video:
            caption = f"💬 <b>Ответ от администрации:</b>\n\n{message.caption}" if message.caption else "💬 <b>Ответ от администрации</b>"
            await bot.send_video(
                chat_id=user_id,
                video=message.video.file_id,
                caption=caption,
                parse_mode="HTML",
            )
        elif message.document:
            caption = f"💬 <b>Ответ от администрации:</b>\n\n{message.caption}" if message.caption else "💬 <b>Ответ от администрации</b>"
            await bot.send_document(
                chat_id=user_id,
                document=message.document.file_id,
                caption=caption,
                parse_mode="HTML",
            )
        else:
            await bot.send_message(
                chat_id=user_id,
                text="💬 <b>Ответ от администрации:</b>",
                parse_mode="HTML",
            )
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )

        await message.reply("✉️ Ответ отправлен автору предложения в ЛС.")
    except Exception as e:
        logger.error(f"Error sending reply to user {user_id}: {e}")
        await message.reply(f"❌ Не удалось отправить ответ: {e}")
