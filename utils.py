"""
Utility functions for security, role-based permission checking, reaction handling, system metrics, logging setup, and formatting.
"""

import os
import sys
import time
import logging
from typing import Optional
from telegram import Update, Bot, ReactionTypeEmoji
from config import Config
from database import AUTH_USERS_CACHE

logger = logging.getLogger(__name__)

# Start timestamp for calculating bot uptime
BOT_START_TIME = time.time()


def setup_logging(level: int = logging.INFO) -> None:
    """Configures structured application logging without exposing secrets."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    # Silence verbose 3rd party logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)


def is_super_admin(user_id: Optional[int]) -> bool:
    """
    Verifies if a user has Super Admin role ('admin').
    User ID 1573531032 is always Super Admin.

    Args:
        user_id (Optional[int]): Telegram User ID.

    Returns:
        bool: True if Super Admin, False otherwise.
    """
    if not user_id:
        return False

    if user_id == 1573531032 or (Config.ADMIN_IDS and user_id in Config.ADMIN_IDS):
        return True

    return AUTH_USERS_CACHE.get(user_id) == "admin"


def is_delivery_user(user_id: Optional[int]) -> bool:
    """
    Verifies if a user is authorized for delivery ('delivery' or 'admin').
    Default seeds: 1573531032 (Admin), 1078400998 (Delivery), 1858358195 (Delivery).

    Args:
        user_id (Optional[int]): Telegram User ID.

    Returns:
        bool: True if authorized for delivery, False otherwise.
    """
    if not user_id:
        return False

    if user_id in (1573531032, 1078400998, 1858358195):
        return True

    return AUTH_USERS_CACHE.get(user_id) in ("admin", "delivery")


def is_admin(user_id: Optional[int]) -> bool:
    """Backward-compatible alias for is_super_admin."""
    return is_super_admin(user_id)


async def check_admin_permission(update: Update) -> bool:
    """
    Verifies Super Admin access for command updates.
    Sends ⛔ You are not authorized to use this command. if unauthorized.

    Args:
        update (Update): Telegram Update object.

    Returns:
        bool: True if user is authorized Super Admin, False otherwise.
    """
    user = update.effective_user
    user_id = user.id if user else None

    if is_super_admin(user_id):
        return True

    logger.warning(f"Unauthorized command access attempt by user_id: {user_id}")
    if update.effective_message:
        await update.effective_message.reply_text(
            "⛔ You are not authorized to use this command."
        )
    return False


async def safe_set_message_reaction(
    bot: Bot,
    chat_id: Optional[int],
    message_id: Optional[int],
    emoji: str = "👍",
    fallback_emoji: Optional[str] = None,
    log_tag: str = "[REACTION]"
) -> bool:
    """
    Safely sets a Telegram reaction emoji on a message.
    Gracefully handles cases where reactions are disabled in chat or unsupported by API.
    Never stops the workflow. Logs 'Reaction not supported' on failure.
    """
    if not chat_id or not message_id:
        return False

    # Primary emoji attempt
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)]
        )
        return True
    except Exception as e:
        logger.debug(f"Reaction '{emoji}' via ReactionTypeEmoji failed: {e}. Trying raw string list...")

    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[emoji]
        )
        return True
    except Exception as e:
        logger.warning(f"Reaction not supported: {e}")

    # Fallback emoji attempt if specified
    if fallback_emoji:
        try:
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=fallback_emoji)]
            )
            return True
        except Exception:
            pass

        try:
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[fallback_emoji]
            )
            return True
        except Exception as e2:
            logger.warning(f"Reaction not supported: {e2}")

    return False


def get_uptime_str() -> str:
    """Calculates and formats bot uptime into readable string (e.g. 2d 5h 12m 30s)."""
    elapsed = int(time.time() - BOT_START_TIME)
    days, remainder = divmod(elapsed, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0 or days > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0 or days > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")

    return " ".join(parts)


def get_memory_usage_mb() -> str:
    """Returns process RAM memory usage in MB."""
    try:
        import psutil
        process = psutil.Process(os.getpid())
        mem_bytes = process.memory_info().rss
        return f"{mem_bytes / (1024 * 1024):.2f} MB"
    except ImportError:
        pass

    try:
        import resource
        mem_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

        if sys.platform == "darwin":
            return f"{mem_kb / (1024 * 1024):.2f} MB"
        return f"{mem_kb / 1024:.2f} MB"
    except Exception:
        return "N/A"


def is_railway_environment() -> bool:
    """Checks if running inside Railway cloud hosting environment."""
    railway_vars = ["RAILWAY_STATIC_URL", "RAILWAY_SERVICE_NAME", "RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID"]
    return any(os.getenv(var) for var in railway_vars)


def get_db_type_name() -> str:
    """Returns human-readable name of the database engine in use."""
    if "postgres" in Config.DATABASE_URL.lower():
        return "PostgreSQL"
    elif "sqlite" in Config.DATABASE_URL.lower():
        return "SQLite"
    return "Unknown DB"
