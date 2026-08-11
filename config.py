"""
Configuration management for Telegram Email Image Delivery Bot.
Core environment variables: BOT_TOKEN, ADMIN_IDS, DATABASE_URL, and PAYMENT_REVIEW_GROUP_ID.
Group configurations are managed dynamically via Telegram commands (/source and /delivery) and stored in DB.
Fixed Payment Review Group default ID: -1004441603990.
"""

import os
import logging
from typing import Set
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def safe_int(env_name: str, default: int) -> int:
    """Safely converts an environment variable to int with fallback default."""
    val = os.getenv(env_name, "").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        logger.warning(f"Invalid integer value for {env_name}: '{val}'. Falling back to default {default}.")
        return default


def safe_float(env_name: str, default: float) -> float:
    """Safely converts an environment variable to float with fallback default."""
    val = os.getenv(env_name, "").strip()
    if not val:
        return default
    try:
        return float(val)
    except ValueError:
        logger.warning(f"Invalid float value for {env_name}: '{val}'. Falling back to default {default}.")
        return default


class Config:
    """Validated Application Configuration."""

    # Core Required Environment Variables
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
    RAW_ADMIN_IDS: str = os.getenv("ADMIN_IDS", "")
    ADMIN_IDS: Set[int] = set()
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///bot_database.db")

    # Fixed Payment Review Group Chat ID (-1004441603990)
    PAYMENT_REVIEW_GROUP_ID: int = safe_int("PAYMENT_REVIEW_GROUP_ID", -1004441603990)

    # Media Group Debounce Window (seconds)
    MEDIA_GROUP_TIMEOUT: float = safe_float("MEDIA_GROUP_TIMEOUT", 2.0)

    # User Email Session Expiry (seconds - default 5 minutes = 300s)
    USER_SESSION_TIMEOUT: float = safe_float("USER_SESSION_TIMEOUT", 300.0)

    # Retry configuration for Telegram API calls
    MAX_RETRY: int = safe_int("MAX_RETRY", 3)
    RETRY_DELAY: float = safe_float("RETRY_DELAY", 2.0)

    # Storage Cleanup Retention (days)
    CLEANUP_DAYS: int = safe_int("CLEANUP_DAYS", 30)

    # Post-delivery option: delete order from DB after successful delivery
    DELETE_AFTER_DELIVERY: bool = os.getenv("DELETE_AFTER_DELIVERY", "false").lower() in ("true", "1", "yes")

    @classmethod
    def load_and_validate(cls) -> None:
        """Parses and validates environment settings."""
        cls.BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
        cls.RAW_ADMIN_IDS = os.getenv("ADMIN_IDS", "")
        cls.DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///bot_database.db")
        cls.PAYMENT_REVIEW_GROUP_ID = safe_int("PAYMENT_REVIEW_GROUP_ID", -1004441603990)
        cls.MEDIA_GROUP_TIMEOUT = safe_float("MEDIA_GROUP_TIMEOUT", 2.0)
        cls.USER_SESSION_TIMEOUT = safe_float("USER_SESSION_TIMEOUT", 300.0)
        cls.MAX_RETRY = safe_int("MAX_RETRY", 3)
        cls.RETRY_DELAY = safe_float("RETRY_DELAY", 2.0)
        cls.CLEANUP_DAYS = safe_int("CLEANUP_DAYS", 30)
        cls.DELETE_AFTER_DELIVERY = os.getenv("DELETE_AFTER_DELIVERY", "false").lower() in ("true", "1", "yes")

        # Parse Admin IDs
        if cls.RAW_ADMIN_IDS:
            try:
                cleaned_str = cls.RAW_ADMIN_IDS.replace(",", " ").replace(";", " ")
                cls.ADMIN_IDS = {int(x.strip()) for x in cleaned_str.split() if x.strip().lstrip("-").isdigit()}
            except Exception as e:
                logger.error(f"Error parsing ADMIN_IDS: {e}")

        # Adapt DATABASE_URL for SQLAlchemy 2 Async drivers
        if cls.DATABASE_URL.startswith("postgres://"):
            cls.DATABASE_URL = cls.DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
        elif cls.DATABASE_URL.startswith("postgresql://") and not cls.DATABASE_URL.startswith("postgresql+asyncpg://"):
            cls.DATABASE_URL = cls.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif cls.DATABASE_URL.startswith("sqlite://") and not cls.DATABASE_URL.startswith("sqlite+aiosqlite://"):
            cls.DATABASE_URL = cls.DATABASE_URL.replace("sqlite://", "sqlite+aiosqlite://", 1)

        if not cls.BOT_TOKEN:
            logger.warning("BOT_TOKEN is not defined in environment variables!")


Config.load_and_validate()
