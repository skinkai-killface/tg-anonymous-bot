# --------------------------------------------------------
# Telegram Anonymous Suggestion Bot
# Author: @NAWMBAD (2026)
# Licensed under the MIT License
# --------------------------------------------------------

import os
from dotenv import load_dotenv

load_dotenv()

BOT_VERSION = "1.3.1"

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN or BOT_TOKEN == "your_bot_token_here":
    raise ValueError("No BOT_TOKEN provided in .env file. Please add your token.")

# ID чата/группы администраторов, куда бот отправляет предложку на модерацию
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
if ADMIN_CHAT_ID == 0:
    raise ValueError("No ADMIN_CHAT_ID provided in .env file.")

# ID канала, куда бот публикует одобренные сообщения (например -1001234567890)
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
if CHANNEL_ID == 0:
    raise ValueError("No CHANNEL_ID provided in .env file.")
