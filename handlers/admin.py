# --------------------------------------------------------
# Telegram Anonymous Suggestion Bot
# Author: @NAWMBAD (2026)
# Licensed under the MIT License
# --------------------------------------------------------

import re
import os
import sys
import time
import asyncio
import subprocess
import logging
from aiogram import Router, types, Bot, F
from aiogram.filters import Command
from aiogram.types import FSInputFile
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

from database import (
    block_user,
    unblock_user,
    get_blocked_list,
    get_stats,
    get_moderator_stats,
    get_all_active_users,
    get_users_count,
    set_user_inactive,
    get_setting,
    set_setting,
    get_archive_stats,
    get_recent_archive,
    get_archive_by_id,
    export_full_archive_json,
)
from config import ADMIN_CHAT_ID, BOT_VERSION

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


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    """
    /help — list all available commands in the admin chat.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return

    text = (
        f"📖 <b>Команды бота (v{BOT_VERSION}):</b>\n\n"
        "⚡ <b>Управление и статус:</b>\n"
        "• /ping — проверка задержки и отклика бота\n"
        "• /stats — общая статистика и активность модераторов\n"
        "• /archive — архив всех предложений (статистика, просмотр, экспорт)\n"
        "• /backup — скачать резервную копию базы данных SQLite\n"
        "• /broadcast <code>&lt;текст&gt;</code> — рассылка всем пользователям бота\n"
        "• /delay <code>[сек]</code> — задержка между автопубликациями в канал\n"
        "• /restart — мягкий перезапуск бота\n"
        "• /update — автообновление (git pull + pip + restart)\n"
        "• /help — список всех команд\n\n"
        "✏️ <b>Редактирование предложки:</b>\n"
        "• Ответьте на предложку командой <code>/edit Новый текст</code> чтобы изменить текст перед публикацией\n\n"
        "🚫 <b>Модерация пользователей:</b>\n"
        "• /ban <code>&lt;id&gt;</code> — заблокировать по ID\n"
        "• /unban <code>&lt;id&gt;</code> — разблокировать по ID\n"
        "• /banlist — список заблокированных\n\n"
        "💬 <b>Ответ автору предложения:</b>\n"
        "• Ответьте (Reply) на сообщение с предложкой в этом чате, и ваш текст/медиа отправится автору в ЛС."
    )
    await message.answer(text, parse_mode="HTML")


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
    await msg.edit_text(f"🏓 <b>Понг!</b>\n⏱ Задержка: <code>{latency} ms</code>\n🏷 Версия: <code>v{BOT_VERSION}</code>", parse_mode="HTML")


@router.message(Command("backup"))
async def cmd_backup(message: types.Message):
    """
    /backup — send bot_data.db file directly to the admin chat.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return

    db_path = "bot_data.db"
    if not os.path.exists(db_path):
        await message.answer("⚠️ Файл базы данных <code>bot_data.db</code> пока не создан.", parse_mode="HTML")
        return

    size_kb = round(os.path.getsize(db_path) / 1024, 2)
    users_count = await get_users_count()

    doc = FSInputFile(db_path, filename="bot_data.db")
    await message.answer_document(
        document=doc,
        caption=(
            f"💾 <b>Резервная копия базы данных</b>\n\n"
            f"📦 Размер: <code>{size_kb} KB</code>\n"
            f"👥 Пользователей в базе: <code>{users_count}</code>\n"
            f"🏷 Версия бота: <code>v{BOT_VERSION}</code>"
        ),
        parse_mode="HTML",
    )


@router.message(Command("archive"))
async def cmd_archive(message: types.Message, bot: Bot):
    """
    /archive — show archive statistics and last 5 suggestions.
    /archive <id> — inspect full details of an archived post (and resend media).
    /archive export — export the entire archive as JSON document.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return

    args = message.text.split(maxsplit=1)
    subcmd = args[1].strip() if len(args) > 1 else ""

    if subcmd == "export":
        json_data = await export_full_archive_json()
        export_file = "archive_export.json"
        with open(export_file, "w", encoding="utf-8") as f:
            f.write(json_data)

        doc = FSInputFile(export_file, filename=f"archive_export_{int(time.time())}.json")
        stats = await get_archive_stats()
        await message.answer_document(
            document=doc,
            caption=(
                f"🗄 <b>Экспорт архива постов</b>\n\n"
                f"📊 Всего постов: <code>{stats['total']}</code>\n"
                f"✅ Одобрено: <code>{stats['approved']}</code>\n"
                f"❌ Отклонено: <code>{stats['rejected']}</code>\n"
                f"🚫 Заблокировано: <code>{stats['blocked']}</code>"
            ),
            parse_mode="HTML",
        )
        try:
            os.remove(export_file)
        except Exception:
            pass
        return

    if subcmd.isdigit():
        archive_id = int(subcmd)
        item = await get_archive_by_id(archive_id)
        if not item:
            await message.reply(f"⚠️ Архивный пост #{archive_id} не найден.", parse_mode="HTML")
            return

        status_emoji = {
            "approved": "✅ Одобрено",
            "rejected": "❌ Отклонено",
            "blocked": "🚫 Заблокирован",
            "pending": "⏳ На модерации",
        }.get(item["status"], item["status"])

        author_tag = f"@{item['user_handle']}" if item['user_handle'] else "нет юзернейма"
        mod_info = f"{item['moderator_name']} (ID: {item['moderator_id']})" if item['moderator_id'] else "—"

        text_block = f"📝 <b>Текст:</b>\n{item['text_content']}" if item['text_content'] else "<i>(без текста)</i>"
        if item['edited_text']:
            text_block += f"\n\n✏️ <b>Отредактированный текст:</b>\n{item['edited_text']}"

        card = (
            f"🗄 <b>Архивный пост #{item['id']}</b>\n\n"
            f"👤 <b>Автор:</b> <a href=\"tg://user?id={item['user_id']}\">{item['user_name']}</a> [ID: <code>{item['user_id']}</code>, {author_tag}]\n"
            f"📂 <b>Тип контента:</b> <code>{item['content_type']}</code>\n"
            f"⚖️ <b>Статус:</b> {status_emoji}\n"
            f"👮 <b>Модератор:</b> {mod_info}\n"
            f"📅 <b>Создан:</b> <code>{item['created_at']}</code>\n"
            f"⏱ <b>Модерирован:</b> <code>{item['moderated_at'] or '—'}</code>\n\n"
            f"{text_block}"
        )

        await message.answer(card, parse_mode="HTML")

        # Also resend media attachments if available
        media_list = item.get("media_list", [])
        if media_list:
            try:
                if item["content_type"] == "photo":
                    await bot.send_photo(chat_id=message.chat.id, photo=media_list[0]["file_id"], caption=f"📸 Медиа из архива #{item['id']}")
                elif item["content_type"] == "video":
                    await bot.send_video(chat_id=message.chat.id, video=media_list[0]["file_id"], caption=f"🎬 Видео из архива #{item['id']}")
                elif item["content_type"] == "voice":
                    await bot.send_voice(chat_id=message.chat.id, voice=media_list[0]["file_id"], caption=f"🎙 Голосовое из архива #{item['id']}")
                elif item["content_type"] == "document":
                    await bot.send_document(chat_id=message.chat.id, document=media_list[0]["file_id"], caption=f"📄 Документ из архива #{item['id']}")
            except Exception as e:
                logger.warning(f"Could not resend archive media #{item['id']}: {e}")
        return

    # Default /archive summary
    stats = await get_archive_stats()
    recent = await get_recent_archive(5)

    recent_lines = []
    for r in recent:
        st_icon = {"approved": "✅", "rejected": "❌", "blocked": "🚫", "pending": "⏳"}.get(r["status"], "▫️")
        snippet = (r["text_content"][:30] + "...") if r["text_content"] else f"[{r['content_type']}]"
        recent_lines.append(f"#{r['id']} {st_icon} <b>{r['user_name']}</b>: <i>{snippet}</i> (<code>{r['created_at']}</code>)")

    recent_text = "\n".join(recent_lines) if recent_lines else "<i>Архив пока пуст</i>"

    await message.answer(
        f"🗄 <b>Архив предложки</b>\n\n"
        f"📊 <b>Всего постов:</b> <code>{stats['total']}</code>\n"
        f"✅ Одобрено: <code>{stats['approved']}</code> | ❌ Отклонено: <code>{stats['rejected']}</code>\n"
        f"🚫 Заблокировано: <code>{stats['blocked']}</code> | ⏳ На модерации: <code>{stats['pending']}</code>\n\n"
        f"📋 <b>Последние записи:</b>\n"
        f"{recent_text}\n\n"
        f"💡 <b>Команды архива:</b>\n"
        f"• <code>/archive &lt;ID&gt;</code> — открыть полный пост с медиа\n"
        f"• <code>/archive export</code> — скачать весь архив в JSON",
        parse_mode="HTML",
    )


@router.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message, bot: Bot):
    """
    /broadcast <text> (or reply to a media/text) — send a broadcast to all registered users.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return

    # Check if replying to a message or has text argument
    reply = message.reply_to_message
    broadcast_text = ""
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        broadcast_text = args[1].strip()

    if not broadcast_text and not reply:
        await message.answer(
            "ℹ️ <b>Использование рассылки:</b>\n\n"
            "1. <code>/broadcast Ваш текст</code>\n"
            "2. Или ответьте (Reply) командой <code>/broadcast</code> на фото/видео/пост, который хотите разослать.",
            parse_mode="HTML",
        )
        return

    users = await get_all_active_users()
    if not users:
        await message.answer("⚠️ В базе пока нет зарегистрированных пользователей для рассылки.")
        return

    status_msg = await message.answer(f"📢 <b>Начинаю рассылку...</b>\n👥 Всего получателей: <b>{len(users)}</b>", parse_mode="HTML")

    sent = 0
    blocked = 0
    errors = 0

    for idx, user_id in enumerate(users):
        try:
            if reply:
                # Copy original replied message (supports photos, videos, formatting, buttons)
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=reply.chat.id,
                    message_id=reply.message_id,
                )
            else:
                await bot.send_message(
                    chat_id=user_id,
                    text=broadcast_text,
                    parse_mode="HTML",
                )
            sent += 1
        except TelegramForbiddenError:
            # User blocked the bot
            blocked += 1
            await set_user_inactive(user_id)
        except TelegramBadRequest as e:
            if "chat not found" in str(e).lower() or "user is deactivated" in str(e).lower():
                blocked += 1
                await set_user_inactive(user_id)
            else:
                errors += 1
        except Exception:
            errors += 1

        # Periodic status update every 25 users
        if idx > 0 and idx % 25 == 0:
            try:
                await status_msg.edit_text(
                    f"📢 <b>Рассылка в процессе...</b>\n"
                    f"Прогресс: <b>{idx}/{len(users)}</b>\n"
                    f"✅ Доставлено: {sent} | 🚫 Заблокировали: {blocked}",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        # Telegram rate-limit delay (~25 msgs/sec)
        await asyncio.sleep(0.04)

    await status_msg.edit_text(
        f"📢 <b>Рассылка завершена!</b>\n\n"
        f"👥 Всего адресатов: <b>{len(users)}</b>\n"
        f"✅ Успешно доставлено: <b>{sent}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked}</b>\n"
        f"⚠️ Ошибок: <b>{errors}</b>",
        parse_mode="HTML",
    )


@router.message(Command("delay"))
async def cmd_delay(message: types.Message):
    """
    /delay [seconds] — configure publishing delay between approved posts.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        current = await get_setting("publish_delay_seconds", "0")
        await message.answer(
            f"⏱ <b>Текущая задержка публикации:</b> <code>{current} сек.</code>\n\n"
            f"Чтобы изменить: <code>/delay 300</code> (например, 300 сек = 5 мин).\n"
            f"Чтобы отключить (мгновенно): <code>/delay 0</code>",
            parse_mode="HTML",
        )
        return

    new_val = args[1].strip()
    if not new_val.isdigit():
        await message.answer("⚠️ Значение задержки должно быть числом секунд (например: <code>/delay 60</code>).", parse_mode="HTML")
        return

    sec = int(new_val)
    await set_setting("publish_delay_seconds", str(sec))
    if sec == 0:
        await message.answer("✅ Задержка отключена: посты публикуются <b>мгновенно</b> при одобрении.", parse_mode="HTML")
    else:
        await message.answer(f"✅ Установлена задержка публикации: <b>{sec} сек.</b> ({round(sec/60, 1)} мин.)", parse_mode="HTML")


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

    os.execv(sys.executable, [sys.executable] + sys.argv)


from auto_updater import run_update_process


@router.callback_query(F.data == "apply_update")
async def on_apply_update_callback(callback: types.CallbackQuery, bot: Bot):
    """Admin clicked the 'Update Bot Now' inline button."""
    await callback.answer()
    status_msg = await callback.message.reply("🔄 <b>Запуск обновления бота...</b>\n\n⏳ <code>git pull origin main</code>", parse_mode="HTML")
    await run_update_process(status_msg, bot)


@router.message(Command("update"))
async def cmd_update(message: types.Message, bot: Bot):
    """
    /update — pull latest code from GitHub, install deps if needed, and restart.
    Full auto-deploy from admin chat.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return

    admin_name = message.from_user.full_name
    logger.info(f"Update initiated by admin {message.from_user.id} ({admin_name})")

    status_msg = await message.answer("🔄 <b>Обновление бота...</b>\n\n⏳ <code>git pull origin main</code>", parse_mode="HTML")
    await run_update_process(status_msg, bot)


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
    /stats — show bot statistics.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return

    stats = await get_stats()
    total = stats.get("total_suggestions", 0)
    approved = stats.get("approved", 0)
    rejected = stats.get("rejected", 0)
    blocked = stats.get("blocked", 0)
    pending = total - approved - rejected - blocked
    users_count = await get_users_count()

    lines = [
        "📊 <b>Статистика бота</b>\n",
        f"👥 Пользователей в базе: <b>{users_count}</b>",
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

    # Ignore replies to bot's edit prompt or update messages
    if reply.from_user and reply.from_user.is_bot:
        if reply.text and ("Отправьте новый текст" in reply.text or "Как изменить текст" in reply.text or "Обновление" in reply.text):
            return

    user_id = extract_user_id(reply)
    if not user_id:
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
