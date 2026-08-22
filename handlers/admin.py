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
import html
from datetime import datetime
from zoneinfo import ZoneInfo
from aiogram import Router, types, Bot, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo
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
    restore_db,
    import_archive_from_json,
    get_approved_archive_posts,
    get_archive_by_orig_id,
    get_archive_by_channel_msg_id,
    update_archive_status_by_id,
    get_daily_moderator_stats,
)
from config import ADMIN_CHAT_ID, CHANNEL_ID, BOT_VERSION
from daily_report import format_daily_report, send_daily_report
from permissions import check_owner, check_admin, check_callback_admin, is_chat_owner

CHANNEL_HEADER = "📩 <b>Новое анонимное сообщение:</b>"

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
async def cmd_help(message: types.Message, bot: Bot):
    """
    /help — list all available commands in the admin chat.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return
    if not await check_admin(message, bot):
        return

    text = (
        f"📖 <b>Команды бота (v{BOT_VERSION}):</b>\n\n"
        "⚡ <b>Управление и статус:</b>\n"
        "• /ping — проверка задержки и отклика бота\n"
        "• /stats — общая статистика и активность модераторов\n"
        "• /today — топ модераторов за сегодня (Кишинёв)\n"
        "• /daily_report — принудительно отправить ежедневный отчёт в чат\n"
        "• /archive — архив всех предложений (статистика, просмотр, экспорт)\n"
        "• /publish_approved — опубликовать ВСЕ одобренные посты из архива в канал\n"
        "• /delete <code>&lt;ID&gt;</code> — удалить пост из канала по ID (например /delete 123 или /delete #ID-123)\n"
        "• /backup — скачать резервную копию базы данных SQLite\n"
        "• /restore — инструкция по восстановлению из бэкапа\n"
        "• /broadcast <code>&lt;текст&gt;</code> — рассылка всем пользователям бота\n"
        "• /subcheck <code>[on/off]</code> — включить/выключить обязательную подписку на канал\n"
        "• /delay <code>[сек]</code> — задержка между автопубликациями в канал\n"
        "• /restart — мягкий перезапуск бота\n"
        "• /update — автообновление (git pull + pip + restart)\n"
        "• /help — список всех команд\n\n"
        "♻️ <b>Восстановление бэкапа:</b>\n"
        "• Отправьте файл <code>.db</code> в этот чат — полное восстановление БД\n"
        "• Отправьте файл <code>.json</code> (экспорт /archive export) — импорт архива\n\n"
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
async def cmd_ping(message: types.Message, bot: Bot):
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
async def cmd_backup(message: types.Message, bot: Bot):
    """
    /backup — send bot_data.db file directly to the admin chat.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return
    if not await check_owner(message, bot):
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


@router.message(Command("restore"))
async def cmd_restore(message: types.Message, bot: Bot):
    """
    /restore — show instructions on how to restore from backup.
    Only works in the admin chat.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return
    if not await check_owner(message, bot):
        return

    await message.answer(
        "♻️ <b>Восстановление из бэкапа</b>\n\n"
        "Просто отправьте файл в этот чат — бот определит тип автоматически:\n\n"
        "📦 <b>Файл <code>.db</code></b> — полное восстановление базы данных\n"
        "<i>Восстановит: пользователей, блок-лист, статистику, архив, настройки</i>\n"
        "⚠️ Текущая БД сохранится как <code>bot_data.db.bak</code> перед заменой.\n\n"
        "📋 <b>Файл <code>.json</code></b> — импорт архива предложений\n"
        "<i>Восстановит только таблицу архива (из /archive export)</i>\n"
        "Пользователи, блок-лист и настройки не затрагиваются.\n\n"
        "💡 <b>Как получить бэкап:</b>\n"
        "• БД: /backup\n"
        "• Архив: /archive export",
        parse_mode="HTML",
    )


@router.message(F.chat.id == ADMIN_CHAT_ID, F.document)
async def on_document_restore(message: types.Message, bot: Bot):
    """
    Intercepts documents sent to admin chat.
    - .db files  → full database restore
    - .json files → archive import
    Other files are ignored.
    """
    if not await check_owner(message, bot):
        return

    doc = message.document
    if not doc or not doc.file_name:
        return

    fname = doc.file_name.lower()

    if fname.endswith(".db"):
        await _handle_db_restore(message, bot, doc)
    elif fname.endswith(".json"):
        await _handle_json_import(message, bot, doc)
    # Other file types — fall through to admin_reply_to_user handler


async def _handle_db_restore(message: types.Message, bot: Bot, doc) -> None:
    """Download and restore a .db backup file."""
    import os
    import tempfile

    status = await message.reply(
        "⏳ <b>Получен .db файл — начинаю восстановление базы данных...</b>",
        parse_mode="HTML",
    )

    # Download to a temp file
    try:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name
        file_info = await bot.get_file(doc.file_id)
        await bot.download_file(file_info.file_path, tmp_path)
    except Exception as e:
        await status.edit_text(
            f"❌ <b>Ошибка загрузки файла:</b> <code>{e}</code>",
            parse_mode="HTML",
        )
        return

    # Restore
    result = await restore_db(tmp_path)

    # Clean up temp file
    try:
        os.remove(tmp_path)
    except Exception:
        pass

    if result["ok"]:
        await status.edit_text(
            f"✅ <b>База данных успешно восстановлена!</b>\n\n"
            f"👥 Пользователей: <code>{result['users']}</code>\n"
            f"🚫 Заблокированных: <code>{result['blocked']}</code>\n\n"
            f"<i>Старая БД сохранена как <code>bot_data.db.bak</code></i>",
            parse_mode="HTML",
        )
    else:
        await status.edit_text(
            f"❌ <b>Ошибка восстановления:</b>\n<code>{result['error']}</code>",
            parse_mode="HTML",
        )


async def _handle_json_import(message: types.Message, bot: Bot, doc) -> None:
    """Download and import an archive JSON export."""
    import os
    import json as _json
    import tempfile

    status = await message.reply(
        "⏳ <b>Получен .json файл — начинаю импорт архива...</b>",
        parse_mode="HTML",
    )

    # Download to a temp file
    try:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        file_info = await bot.get_file(doc.file_id)
        await bot.download_file(file_info.file_path, tmp_path)
    except Exception as e:
        await status.edit_text(
            f"❌ <b>Ошибка загрузки файла:</b> <code>{e}</code>",
            parse_mode="HTML",
        )
        return

    # Parse JSON
    try:
        with open(tmp_path, "r", encoding="utf-8") as f:
            records = _json.load(f)
        if not isinstance(records, list):
            raise ValueError("Ожидается JSON-массив (список постов)")
    except Exception as e:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        await status.edit_text(
            f"❌ <b>Ошибка парсинга JSON:</b> <code>{e}</code>\n\n"
            f"<i>Убедитесь что файл создан через /archive export</i>",
            parse_mode="HTML",
        )
        return
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    # Import
    result = await import_archive_from_json(records)

    if result["ok"]:
        await status.edit_text(
            f"✅ <b>Архив успешно импортирован!</b>\n\n"
            f"📥 Импортировано записей: <code>{result['imported']}</code>\n"
            f"⚠️ Пропущено (невалидные): <code>{result['skipped']}</code>",
            parse_mode="HTML",
        )
    else:
        await status.edit_text(
            f"❌ <b>Ошибка импорта:</b>\n<code>{result['error']}</code>\n"
            f"Импортировано до ошибки: <code>{result['imported']}</code>",
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
    if not await check_admin(message, bot):
        return

    args = message.text.split(maxsplit=1)
    subcmd = args[1].strip() if len(args) > 1 else ""

    if subcmd in ("publish", "publish_approved", "send"):
        await cmd_publish_approved(message, bot)
        return

    if subcmd == "export":
        if not await check_owner(message, bot):
            return
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
        f"• <code>/archive export</code> — скачать весь архив в JSON\n"
        f"• <code>/publish_approved</code> — опубликовать все одобренные посты в канал",
        parse_mode="HTML",
    )


@router.message(Command("publish_approved"))
@router.message(Command("publish"))
async def cmd_publish_approved(message: types.Message, bot: Bot):
    """
    /publish_approved — send all approved suggestions from archive to CHANNEL_ID.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return
    if not await check_owner(message, bot):
        return

    posts = await get_approved_archive_posts()
    if not posts:
        await message.answer("⚠️ В архиве нет одобренных предложений (status='approved').", parse_mode="HTML")
        return

    status_msg = await message.answer(
        f"🚀 <b>Публикация одобренных постов из архива...</b>\n\n"
        f"📊 Найдено одобренных записей: <code>{len(posts)}</code>",
        parse_mode="HTML",
    )

    published = 0
    errors = 0

    for idx, item in enumerate(posts):
        try:
            archive_id = item.get("id")
            id_footer = f"\n\n🆔 <b>#ID-{archive_id}</b>" if archive_id else ""

            # Determine channel header
            if item.get("is_anonymous", True):
                pub_header = CHANNEL_HEADER
            else:
                name = html.escape(item.get("user_name") or "Пользователь")
                tag = f" (@{item['user_handle']})" if item.get("user_handle") else ""
                pub_header = f'📩 <b>Новое сообщение от <a href="tg://user?id={item["user_id"]}">{name}</a>{tag}:</b>'

            content = item.get("edited_text") or item.get("text_content") or ""
            ctype = item.get("content_type", "text")
            media_list = item.get("media_list", [])

            sent_msg_id = None

            if ctype == "text":
                post_text = f"{pub_header}\n\n{content}{id_footer}" if content else f"{pub_header}{id_footer}"
                sent = await bot.send_message(chat_id=CHANNEL_ID, text=post_text, parse_mode="HTML")
                sent_msg_id = sent.message_id

            elif ctype == "photo" and media_list:
                post_caption = f"{pub_header}\n\n{content}{id_footer}" if content else f"{pub_header}{id_footer}"
                sent = await bot.send_photo(chat_id=CHANNEL_ID, photo=media_list[0]["file_id"], caption=post_caption, parse_mode="HTML")
                sent_msg_id = sent.message_id

            elif ctype == "video" and media_list:
                post_caption = f"{pub_header}\n\n{content}{id_footer}" if content else f"{pub_header}{id_footer}"
                sent = await bot.send_video(chat_id=CHANNEL_ID, video=media_list[0]["file_id"], caption=post_caption, parse_mode="HTML")
                sent_msg_id = sent.message_id

            elif ctype == "animation" and media_list:
                post_caption = f"{pub_header}\n\n{content}{id_footer}" if content else f"{pub_header}{id_footer}"
                sent = await bot.send_animation(chat_id=CHANNEL_ID, animation=media_list[0]["file_id"], caption=post_caption, parse_mode="HTML")
                sent_msg_id = sent.message_id

            elif ctype == "voice" and media_list:
                post_caption = f"{pub_header}\n\n{content}{id_footer}" if content else f"{pub_header}{id_footer}"
                sent = await bot.send_voice(chat_id=CHANNEL_ID, voice=media_list[0]["file_id"], caption=post_caption, parse_mode="HTML")
                sent_msg_id = sent.message_id

            elif ctype == "video_note" and media_list:
                await bot.send_message(chat_id=CHANNEL_ID, text=f"{pub_header}{id_footer}", parse_mode="HTML")
                sent = await bot.send_video_note(chat_id=CHANNEL_ID, video_note=media_list[0]["file_id"])
                sent_msg_id = sent.message_id

            elif ctype == "audio" and media_list:
                post_caption = f"{pub_header}\n\n{content}{id_footer}" if content else f"{pub_header}{id_footer}"
                sent = await bot.send_audio(chat_id=CHANNEL_ID, audio=media_list[0]["file_id"], caption=post_caption, parse_mode="HTML")
                sent_msg_id = sent.message_id

            elif ctype == "document" and media_list:
                post_caption = f"{pub_header}\n\n{content}{id_footer}" if content else f"{pub_header}{id_footer}"
                sent = await bot.send_document(chat_id=CHANNEL_ID, document=media_list[0]["file_id"], caption=post_caption, parse_mode="HTML")
                sent_msg_id = sent.message_id

            elif ctype == "sticker" and media_list:
                await bot.send_message(chat_id=CHANNEL_ID, text=f"{pub_header}{id_footer}", parse_mode="HTML")
                sent = await bot.send_sticker(chat_id=CHANNEL_ID, sticker=media_list[0]["file_id"])
                sent_msg_id = sent.message_id

            elif ctype == "album" and media_list:
                post_caption = f"{pub_header}\n\n{content}{id_footer}" if content else f"{pub_header}{id_footer}"
                media_input = []
                for m_idx, m in enumerate(media_list):
                    cap = post_caption if m_idx == 0 else None
                    if m.get("type") == "photo":
                        media_input.append(InputMediaPhoto(media=m["file_id"], caption=cap, parse_mode="HTML"))
                    elif m.get("type") == "video":
                        media_input.append(InputMediaVideo(media=m["file_id"], caption=cap, parse_mode="HTML"))
                if media_input:
                    sent_msgs = await bot.send_media_group(chat_id=CHANNEL_ID, media=media_input)
                    if sent_msgs:
                        sent_msg_id = sent_msgs[0].message_id

            if archive_id and sent_msg_id:
                await update_archive_status_by_id(archive_id, "approved", channel_msg_id=sent_msg_id)

            published += 1
        except Exception as e:
            logger.error(f"Failed to republish archive post #{item.get('id')}: {e}")
            errors += 1

        # Periodic status report
        if (idx + 1) % 5 == 0 or (idx + 1) == len(posts):
            try:
                await status_msg.edit_text(
                    f"🚀 <b>Публикация постов из архива...</b>\n\n"
                    f"Прогресс: <b>{idx + 1}/{len(posts)}</b>\n"
                    f"✅ Опубликовано: <b>{published}</b> | ⚠️ Ошибок: <b>{errors}</b>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        await asyncio.sleep(1.0)

    await status_msg.edit_text(
        f"✅ <b>Публикация постов завершена!</b>\n\n"
        f"📊 Всего постов в архиве: <code>{len(posts)}</code>\n"
        f"🎉 Опубликовано в канал: <code>{published}</code>\n"
        f"⚠️ Ошибок: <code>{errors}</code>",
        parse_mode="HTML",
    )


@router.message(Command("delete"))
@router.message(Command("del"))
async def cmd_delete_post(message: types.Message, bot: Bot):
    """
    /delete <ID> (or reply to a message) — delete a published post from the channel by ID.
    Usage:
      /delete 123
      /delete #ID-123
      /delete (replying to a forwarded channel message)
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return
    if not await check_owner(message, bot):
        return

    args = message.text.split(maxsplit=1)
    target_id_str = args[1].strip() if len(args) > 1 else ""

    item = None

    # 1. Check if replying to a message
    if not target_id_str and message.reply_to_message:
        reply = message.reply_to_message
        # Check if forwarded from channel
        if reply.forward_from_chat and reply.forward_from_chat.id == CHANNEL_ID and reply.forward_from_message_id:
            item = await get_archive_by_channel_msg_id(reply.forward_from_message_id)
            if not item:
                # Try directly deleting the channel message ID
                try:
                    await bot.delete_message(chat_id=CHANNEL_ID, message_id=reply.forward_from_message_id)
                    await message.reply(f"✅ Пост (сообщение #{reply.forward_from_message_id}) успешно удалён из канала!", parse_mode="HTML")
                    return
                except Exception as e:
                    await message.reply(f"❌ Ошибка при удалении сообщения {reply.forward_from_message_id} из канала: {e}")
                    return
        else:
            if reply.message_id:
                item = await get_archive_by_orig_id(reply.message_id)

    if not item and target_id_str:
        # Clean string: remove '#', 'ID-', 'id-', etc.
        clean_id = re.sub(r"[^\d]", "", target_id_str)
        if clean_id.isdigit():
            target_id = int(clean_id)
            # First search by archive ID
            item = await get_archive_by_id(target_id)
            # If not found by archive ID, search by channel_msg_id
            if not item:
                item = await get_archive_by_channel_msg_id(target_id)

    if not item:
        await message.reply(
            "⚠️ <b>Пост не найден.</b>\n\n"
            "<b>Использование:</b>\n"
            "• <code>/delete 123</code> (где 123 — ID поста, например #ID-123)\n"
            "• Или ответьте (Reply) командой <code>/delete</code> на пересланный из канала пост.",
            parse_mode="HTML",
        )
        return

    archive_id = item["id"]
    channel_msg_id = item.get("channel_msg_id")

    if not channel_msg_id:
        await message.reply(
            f"⚠️ Пост <b>#ID-{archive_id}</b> найден в архиве, но у него не сохранён ID сообщения в канале (возможно, он был опубликован до этого обновления).",
            parse_mode="HTML",
        )
        return

    # Delete message(s) from channel
    media_list = item.get("media_list", [])
    content_type = item.get("content_type")

    deleted_count = 0
    err_msg = ""

    try:
        if content_type == "album" and media_list and len(media_list) > 1:
            for offset in range(len(media_list)):
                try:
                    await bot.delete_message(chat_id=CHANNEL_ID, message_id=channel_msg_id + offset)
                    deleted_count += 1
                except Exception:
                    pass
        else:
            await bot.delete_message(chat_id=CHANNEL_ID, message_id=channel_msg_id)
            deleted_count = 1
    except Exception as e:
        err_msg = str(e)

    if deleted_count > 0:
        await update_archive_status_by_id(archive_id, "deleted")
        await message.reply(
            f"🗑 <b>Пост #ID-{archive_id} успешно удалён из канала!</b>",
            parse_mode="HTML",
        )
    else:
        await message.reply(
            f"❌ <b>Не удалось удалить пост #ID-{archive_id} из канала.</b>\n\n"
            f"Причина: <code>{err_msg or 'Сообщение не найдено или уже удалено'}</code>",
            parse_mode="HTML",
        )


@router.message(Command("today"))
@router.message(Command("daily"))
async def cmd_today_report(message: types.Message, bot: Bot):
    """
    /today — show live moderation leaderboard for today (Europe/Chisinau).
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return
    if not await check_admin(message, bot):
        return

    tz = ZoneInfo("Europe/Chisinau")
    now = datetime.now(tz)
    today_str = now.strftime("%Y-%m-%d")
    display_date = now.strftime("%d.%m.%Y")

    stats = await get_daily_moderator_stats(today_str)
    report_text = format_daily_report(stats, f"{display_date} (сегодня)")
    await message.answer(report_text, parse_mode="HTML")


@router.message(Command("daily_report"))
async def cmd_trigger_daily_report(message: types.Message, bot: Bot):
    """
    /daily_report [YYYY-MM-DD] — manually trigger daily report in admin chat.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return
    if not await check_admin(message, bot):
        return

    args = message.text.split(maxsplit=1)
    target_date = args[1].strip() if len(args) > 1 else None

    await send_daily_report(bot, date_str=target_date)


@router.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message, bot: Bot):
    """
    /broadcast <text> (or reply to a media/text) — send a broadcast to all registered users.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return
    if not await check_owner(message, bot):
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
async def cmd_delay(message: types.Message, bot: Bot):
    """
    /delay [seconds] — configure publishing delay between approved posts.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return
    if not await check_owner(message, bot):
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


@router.message(Command("subcheck"))
async def cmd_subcheck(message: types.Message, bot: Bot):
    """
    /subcheck [on/off] — toggle mandatory channel subscription check.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return
    if not await check_owner(message, bot):
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        current = await get_setting("subcheck_enabled", "1")
        status_str = "ВКЛЮЧЕНА ✅" if current == "1" else "ВЫКЛЮЧЕНА ❌"
        await message.answer(
            f"🔒 <b>Обязательная подписка на канал:</b> {status_str}\n\n"
            f"Чтобы включить: <code>/subcheck on</code>\n"
            f"Чтобы выключить: <code>/subcheck off</code>",
            parse_mode="HTML",
        )
        return

    val = args[1].strip().lower()
    if val in ("on", "1", "true", "вкл"):
        await set_setting("subcheck_enabled", "1")
        await message.answer("✅ Обязательная подписка на канал <b>ВКЛЮЧЕНА</b>.", parse_mode="HTML")
    elif val in ("off", "0", "false", "выкл"):
        await set_setting("subcheck_enabled", "0")
        await message.answer("❌ Обязательная подписка на канал <b>ВЫКЛЮЧЕНА</b>.", parse_mode="HTML")
    else:
        await message.answer("⚠️ Использование: <code>/subcheck on</code> или <code>/subcheck off</code>", parse_mode="HTML")


@router.message(Command("restart"))
async def cmd_restart(message: types.Message, bot: Bot):
    """
    /restart — gracefully restart the bot process.
    Only works in the admin chat.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return
    if not await check_owner(message, bot):
        return

    await message.answer("🔄 <b>Перезапуск бота...</b>", parse_mode="HTML")
    logger.info(f"Restart initiated by admin {message.from_user.id} ({message.from_user.full_name})")
    await asyncio.sleep(1)

    os.execv(sys.executable, [sys.executable] + sys.argv)


from auto_updater import run_update_process


@router.callback_query(F.data == "apply_update")
async def on_apply_update_callback(callback: types.CallbackQuery, bot: Bot):
    """Admin clicked the 'Update Bot Now' inline button."""
    if not await check_callback_admin(callback, bot):
        return
    # Additional owner check for destructive update action
    if not await is_chat_owner(bot, callback.from_user.id, callback.message.chat.id):
        await callback.answer("🔒 Обновление доступно только владельцу.", show_alert=True)
        return
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
    if not await check_owner(message, bot):
        return

    admin_name = message.from_user.full_name
    logger.info(f"Update initiated by admin {message.from_user.id} ({admin_name})")

    status_msg = await message.answer("🔄 <b>Обновление бота...</b>\n\n⏳ <code>git pull origin main</code>", parse_mode="HTML")
    await run_update_process(status_msg, bot)


@router.message(Command("ban"))
async def cmd_ban(message: types.Message, bot: Bot):
    """
    /ban <user_id> — block a user by their Telegram ID.
    Only works in the admin chat.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return
    if not await check_owner(message, bot):
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("Использование: /ban <user_id>")
        return

    user_id = int(args[1].strip())
    await block_user(user_id, reason=f"Banned by {message.from_user.full_name}")
    await message.answer(f"🚫 Пользователь <code>{user_id}</code> заблокирован.", parse_mode="HTML")


@router.message(Command("unban"))
async def cmd_unban(message: types.Message, bot: Bot):
    """
    /unban <user_id> — unblock a user by their Telegram ID.
    Only works in the admin chat.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return
    if not await check_owner(message, bot):
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
async def cmd_banlist(message: types.Message, bot: Bot):
    """
    /banlist — show all blocked users.
    Only works in the admin chat.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return
    if not await check_admin(message, bot):
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
async def cmd_stats(message: types.Message, bot: Bot):
    """
    /stats — show bot statistics.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return
    if not await check_admin(message, bot):
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
    if not await check_admin(message, bot):
        return

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
