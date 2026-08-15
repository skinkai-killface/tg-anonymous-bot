# --------------------------------------------------------
# Telegram Anonymous Suggestion Bot
# Author: @NAWMBAD (2026)
# Licensed under the MIT License
# --------------------------------------------------------

from handlers.start import router as start_router
from handlers.suggest import router as suggest_router
from handlers.moderation import router as moderation_router
from handlers.admin import router as admin_router

__all__ = ["start_router", "suggest_router", "moderation_router", "admin_router"]
