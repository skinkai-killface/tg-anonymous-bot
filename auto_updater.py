# --------------------------------------------------------
# Telegram Anonymous Suggestion Bot
# Author: @NAWMBAD (2026)
# Licensed under the MIT License
# --------------------------------------------------------

"""
Background worker that periodically checks GitHub for new commits on origin/main.
When a new update is detected, notifies the admin chat with an 'Update Now' button.
"""

import os
import sys
import asyncio
import logging
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMIN_CHAT_ID

logger = logging.getLogger(__name__)

# Check interval in seconds (every 5 minutes)
CHECK_INTERVAL_SECONDS = 300

# In-memory tracking of the last commit hash we notified admins about
_last_notified_hash: str | None = None


def get_update_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Обновить бота сейчас", callback_data="apply_update"),
        ]
    ])


async def get_git_output(args: list[str], bot_dir: str) -> str:
    """Helper to run git commands asynchronously."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=bot_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (stdout.decode(errors="replace") + stderr.decode(errors="replace")).strip()


async def check_for_updates() -> tuple[bool, str, str]:
    """
    Checks if GitHub origin/main has a newer commit than local HEAD.
    Uses 'git ls-remote' for fast, non-blocking check, and fetches if newer commit is found.
    Returns (has_update, commit_msg, remote_hash).
    """
    bot_dir = os.path.dirname(os.path.abspath(__file__))

    try:
        local_hash = await get_git_output(["git", "rev-parse", "HEAD"], bot_dir)
        ls_output = await get_git_output(["git", "ls-remote", "origin", "refs/heads/main"], bot_dir)

        if not ls_output or not local_hash:
            return False, "", ""

        parts = ls_output.split()
        if not parts:
            return False, "", ""

        remote_hash = parts[0].strip()

        if remote_hash and local_hash and remote_hash != local_hash:
            # Fetch latest commits from origin/main
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "fetch", "origin", "main",
                    cwd=bot_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=20.0)

                # Check if origin/main has commits that local HEAD does NOT have
                behind_count = await get_git_output(
                    ["git", "rev-list", "HEAD..FETCH_HEAD", "--count"], bot_dir
                )
                if not behind_count.isdigit() or int(behind_count) == 0:
                    return False, "", ""

                commit_msg = await get_git_output(
                    ["git", "log", "-1", "--pretty=format:%s", "FETCH_HEAD"], bot_dir
                )
            except Exception:
                return False, "", ""

            if not commit_msg:
                commit_msg = f"Новый коммит {remote_hash[:7]}"

            return True, commit_msg, remote_hash

    except Exception as e:
        logger.warning(f"Error checking for updates: {e}")

    return False, "", ""


async def run_update_process(status_msg, bot: Bot) -> None:
    """
    Performs the full git pull + pip install + restart routine.
    Can be invoked from /update command or the inline update button.
    """
    bot_dir = os.path.dirname(os.path.abspath(__file__))

    # Step 1: git pull
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "pull", "origin", "main",
            cwd=bot_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        git_output = (stdout.decode(errors="replace") + stderr.decode(errors="replace")).strip()

        if proc.returncode != 0:
            await status_msg.edit_text(
                f"❌ <b>Ошибка git pull:</b>\n<pre>{git_output}</pre>",
                parse_mode="HTML",
            )
            return

        if "Already up to date" in git_output or "Already up-to-date" in git_output:
            await status_msg.edit_text(
                "✅ <b>Уже установлена последняя версия.</b>",
                parse_mode="HTML",
            )
            return

    except asyncio.TimeoutError:
        await status_msg.edit_text("❌ <b>Таймаут git pull</b> (30 сек.)", parse_mode="HTML")
        return
    except Exception as e:
        await status_msg.edit_text(f"❌ <b>Ошибка:</b> <code>{e}</code>", parse_mode="HTML")
        return

    # Step 2: pip install if requirements changed
    req_changed = "requirements.txt" in git_output
    pip_info = "не менялись (пропущено)"

    py_exec = sys.executable
    for venv_path in [
        os.path.join(bot_dir, "venv", "bin", "python"),
        os.path.join(bot_dir, ".venv", "bin", "python"),
        os.path.join(bot_dir, "venv", "Scripts", "python.exe"),
    ]:
        if os.path.isfile(venv_path):
            py_exec = venv_path
            break

    if req_changed:
        await status_msg.edit_text(
            f"🔄 <b>Обновление бота...</b>\n\n"
            f"✅ <code>git pull</code>:\n<pre>{git_output[:400]}</pre>\n\n"
            f"⏳ Обновление зависимостей <code>requirements.txt</code>...",
            parse_mode="HTML",
        )
        try:
            pip_cmd = [
                py_exec, "-m", "pip", "install",
                "--no-cache-dir",
                "--disable-pip-version-check",
                "--break-system-packages",
                "-r", "requirements.txt",
            ]
            pip_proc = await asyncio.create_subprocess_exec(
                *pip_cmd,
                cwd=bot_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            p_stdout, p_stderr = await asyncio.wait_for(pip_proc.communicate(), timeout=60.0)
            pip_out = (p_stdout.decode(errors="replace") + p_stderr.decode(errors="replace")).strip()
            pip_info = pip_out.split("\n")[-1] if pip_out else "установлено"
        except asyncio.TimeoutError:
            pip_info = "⚠️ таймаут pip (продолжаем запуск)"
        except Exception as e:
            pip_info = f"⚠️ ошибка pip ({e})"

    # Step 3: Success & Restart
    await status_msg.edit_text(
        f"🚀 <b>Обновление успешно завершено!</b>\n\n"
        f"📦 <code>git pull</code>:\n<pre>{git_output[:400]}</pre>\n"
        f"📦 <code>pip</code>: {pip_info}\n\n"
        f"🔄 Перезапуск через 2 сек...",
        parse_mode="HTML",
    )

    await asyncio.sleep(2)
    os.execv(py_exec, [py_exec] + sys.argv)


async def auto_update_checker_loop(bot: Bot) -> None:
    """Background loop that periodically checks for new commits on GitHub."""
    global _last_notified_hash
    logger.info("Auto-update background checker started.")

    bot_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        _last_notified_hash = await get_git_output(["git", "rev-parse", "HEAD"], bot_dir)
    except Exception:
        pass

    # Quick initial delay after startup
    await asyncio.sleep(5)

    while True:
        try:
            has_update, commit_msg, remote_hash = await check_for_updates()
            if has_update and remote_hash != _last_notified_hash:
                _last_notified_hash = remote_hash
                logger.info(f"New GitHub update detected: {commit_msg} ({remote_hash[:7]})")

                await bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=(
                        f"🆕 <b>Доступно новое обновление на GitHub!</b>\n\n"
                        f"📝 <b>Что нового:</b> <code>{commit_msg}</code>\n"
                        f"🔖 <b>Коммит:</b> <code>{remote_hash[:7]}</code>\n\n"
                        f"Нажмите кнопку ниже, чтобы применить обновление:"
                    ),
                    parse_mode="HTML",
                    reply_markup=get_update_keyboard(),
                )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Error in auto_update_checker_loop: {e}")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
