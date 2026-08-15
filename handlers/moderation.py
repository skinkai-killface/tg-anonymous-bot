from aiogram import Router, types, Bot, F

from config import CHANNEL_ID
from blocked import block_user

router = Router()

CHANNEL_HEADER = "📩 <b>Новое анонимное сообщение:</b>"


def strip_header(text: str) -> str:
    """
    Strips the moderation header (author info, ID, 'Новое предложение', etc.)
    and returns ONLY the user's actual text/caption.
    If the user sent media without text, returns empty string "".
    """
    if not text:
        return ""

    # Split by double newline first
    if "\n\n" in text:
        parts = text.split("\n\n", 1)
        user_content = parts[1].strip()
        # Clean up any trailing admin status if present
        if user_content.startswith("✅ Одобрено") or user_content.startswith("❌ Отклонено"):
            return ""
        return user_content

    # If no \n\n, check if the whole text is only the moderation header
    lines = text.splitlines()
    filtered_lines = []
    for line in lines:
        stripped = line.strip()
        if (
            "Новое предложение" in stripped
            or "Автор:" in stripped
            or "ID:" in stripped
            or "Одобрено" in stripped
            or "Отклонено" in stripped
            or "Заблокирован" in stripped
            or not stripped
        ):
            continue
        filtered_lines.append(line)

    return "\n".join(filtered_lines).strip()


@router.callback_query(F.data.startswith("approve:"))
async def on_approve(callback: types.CallbackQuery, bot: Bot):
    """
    Admin pressed "Approve". Publish the content to the channel with the anonymous header.
    """
    _, user_id_str, _ = callback.data.split(":")
    user_id = int(user_id_str)

    # The original suggestion content is in the message that has the buttons.
    source_msg = callback.message

    if source_msg.text:
        content = strip_header(source_msg.text)
        post_text = f"{CHANNEL_HEADER}\n\n{content}" if content else CHANNEL_HEADER
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=post_text,
            parse_mode="HTML",
        )
    elif source_msg.photo:
        caption = strip_header(source_msg.caption or "")
        post_caption = f"{CHANNEL_HEADER}\n\n{caption}" if caption else CHANNEL_HEADER
        await bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=source_msg.photo[-1].file_id,
            caption=post_caption,
            parse_mode="HTML",
        )
    elif source_msg.video:
        caption = strip_header(source_msg.caption or "")
        post_caption = f"{CHANNEL_HEADER}\n\n{caption}" if caption else CHANNEL_HEADER
        await bot.send_video(
            chat_id=CHANNEL_ID,
            video=source_msg.video.file_id,
            caption=post_caption,
            parse_mode="HTML",
        )
    elif source_msg.document:
        caption = strip_header(source_msg.caption or "")
        post_caption = f"{CHANNEL_HEADER}\n\n{caption}" if caption else CHANNEL_HEADER
        await bot.send_document(
            chat_id=CHANNEL_ID,
            document=source_msg.document.file_id,
            caption=post_caption,
            parse_mode="HTML",
        )

    # Update the admin message to show it was approved
    admin_name = callback.from_user.full_name
    try:
        if source_msg.text:
            await source_msg.edit_text(
                source_msg.text + f"\n\n✅ <b>Одобрено</b> — {admin_name}",
                parse_mode="HTML",
            )
        else:
            await source_msg.edit_caption(
                caption=(source_msg.caption or "")
                + f"\n\n✅ <b>Одобрено</b> — {admin_name}",
                parse_mode="HTML",
            )
    except Exception:
        pass  # message may already be edited

    # Notify the user that their suggestion was approved
    try:
        await bot.send_message(
            chat_id=user_id,
            text="🎉 Твоё сообщение было одобрено и опубликовано в канале!",
        )
    except Exception:
        pass  # user may have blocked the bot

    await callback.answer("Опубликовано ✅")


@router.callback_query(F.data.startswith("reject:"))
async def on_reject(callback: types.CallbackQuery, bot: Bot):
    """
    Admin pressed "Reject". Mark as rejected and notify the user.
    """
    _, user_id_str, _ = callback.data.split(":")
    user_id = int(user_id_str)

    source_msg = callback.message
    admin_name = callback.from_user.full_name

    # Update the admin message to show it was rejected
    try:
        if source_msg.text:
            await source_msg.edit_text(
                source_msg.text + f"\n\n❌ <b>Отклонено</b> — {admin_name}",
                parse_mode="HTML",
            )
        else:
            await source_msg.edit_caption(
                caption=(source_msg.caption or "")
                + f"\n\n❌ <b>Отклонено</b> — {admin_name}",
                parse_mode="HTML",
            )
    except Exception:
        pass

    # Notify the user
    try:
        await bot.send_message(
            chat_id=user_id,
            text="😔 К сожалению, твоё сообщение было отклонено.",
        )
    except Exception:
        pass

    await callback.answer("Отклонено ❌")


@router.callback_query(F.data.startswith("block:"))
async def on_block(callback: types.CallbackQuery, bot: Bot):
    """
    Admin pressed "Block". Block the user and reject the message.
    """
    _, user_id_str, _ = callback.data.split(":")
    user_id = int(user_id_str)

    # Block the user
    block_user(user_id, reason=f"Blocked by {callback.from_user.full_name}")

    source_msg = callback.message
    admin_name = callback.from_user.full_name

    # Update the admin message
    try:
        if source_msg.text:
            await source_msg.edit_text(
                source_msg.text
                + f"\n\n🚫 <b>Заблокирован и отклонён</b> — {admin_name}",
                parse_mode="HTML",
            )
        else:
            await source_msg.edit_caption(
                caption=(source_msg.caption or "")
                + f"\n\n🚫 <b>Заблокирован и отклонён</b> — {admin_name}",
                parse_mode="HTML",
            )
    except Exception:
        pass

    # Notify the user
    try:
        await bot.send_message(
            chat_id=user_id,
            text="⛔ Вы были заблокированы. Ваши предложения больше не принимаются.",
        )
    except Exception:
        pass

    await callback.answer("Пользователь заблокирован 🚫")
