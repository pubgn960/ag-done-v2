"""
Utility functions for security, role-based permission checking, reaction handling, system metrics, logging setup, and formatting.
"""

import os
import re
import sys
import time
import logging
from enum import Enum
from typing import Optional, Dict, Any
from telegram import Update, Bot, ReactionTypeEmoji
from config import Config
from database import AUTH_USERS_CACHE

logger = logging.getLogger(__name__)

# Start timestamp for calculating bot uptime
BOT_START_TIME = time.time()


class LoaderIssueType(str, Enum):
    """Extensible Enum representing loader-reported issue types."""
    WRONG_NAME = "wrong_name"
    WRONG_ACCOUNT = "wrong_account"
    LOGIN_FAILED = "login_failed"
    TWO_FACTOR = "two_factor"
    NEED_CONFIRMATION = "need_confirmation"


LOADER_ISSUE_CONFIG: Dict[str, Dict[str, str]] = {
    LoaderIssueType.WRONG_NAME: {
        "label": "⚠️ Wrong Name",
        "customer_text": (
            "⚠️ <b>Account Name Attention Required</b>\n\n"
            "The loader reported that the account name may be incorrect.\n"
            "Please check your account. Is this account correct?"
        ),
        "loader_yes_text": "✅ <b>Customer Confirmed</b>\nAccount name has been confirmed as CORRECT by customer.\nPlease continue delivery.",
        "loader_no_text": "❌ <b>Customer Response</b>\nCustomer stated account name is INCORRECT.\nPlease wait for updated account information.",
        "loader_timeout_text": "⏰ <b>Customer Timed Out</b>\nCustomer did not reply within time limit.\nPlease wait or verify with admin."
    },
    LoaderIssueType.WRONG_ACCOUNT: {
        "label": "❌ Wrong Account",
        "customer_text": (
            "❌ <b>Account Credentials Check</b>\n\n"
            "The loader reported that the account details/credentials appear incorrect.\n"
            "Please check your account. Is this account correct?"
        ),
        "loader_yes_text": "✅ <b>Customer Confirmed</b>\nAccount details have been confirmed as CORRECT by customer.\nPlease continue delivery.",
        "loader_no_text": "❌ <b>Customer Response</b>\nCustomer stated account details are INCORRECT.\nPlease wait for updated account info.",
        "loader_timeout_text": "⏰ <b>Customer Timed Out</b>\nCustomer did not reply to wrong account check."
    },
    LoaderIssueType.LOGIN_FAILED: {
        "label": "🔒 Login Failed",
        "customer_text": (
            "🔒 <b>Login Problem Reported</b>\n\n"
            "The loader was unable to log into your account.\n"
            "Please verify your login credentials. Are these details correct?"
        ),
        "loader_yes_text": "✅ <b>Customer Confirmed</b>\nCustomer confirmed login credentials are CORRECT.\nPlease attempt login again.",
        "loader_no_text": "❌ <b>Customer Response</b>\nCustomer stated login credentials need correction.\nPlease wait for updated login info.",
        "loader_timeout_text": "⏰ <b>Customer Timed Out</b>\nCustomer did not reply to login verification."
    },
    LoaderIssueType.TWO_FACTOR: {
        "label": "📵 2FA Problem",
        "customer_text": (
            "📵 <b>2FA / Verification Code Required</b>\n\n"
            "The loader encountered a 2FA prompt or security code issue.\n"
            "Are your account security settings ready for login?"
        ),
        "loader_yes_text": "✅ <b>Customer Confirmed</b>\nCustomer confirmed 2FA is ready.\nPlease try logging in again.",
        "loader_no_text": "❌ <b>Customer Response</b>\nCustomer stated 2FA is not ready yet.",
        "loader_timeout_text": "⏰ <b>Customer Timed Out</b>\nCustomer did not reply to 2FA prompt."
    },
    LoaderIssueType.NEED_CONFIRMATION: {
        "label": "📝 Need Confirmation",
        "customer_text": (
            "📝 <b>Order Confirmation Required</b>\n\n"
            "The loader requested confirmation for your order details.\n"
            "Is this order ready for processing?"
        ),
        "loader_yes_text": "✅ <b>Customer Confirmed</b>\nOrder details confirmed as CORRECT by customer.\nPlease proceed with delivery.",
        "loader_no_text": "❌ <b>Customer Response</b>\nCustomer indicated order details are INCORRECT.",
        "loader_timeout_text": "⏰ <b>Customer Timed Out</b>\nCustomer did not reply to order confirmation."
    }
}


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


TEST_PACKAGE_PRICES = {
    "10800": 63.5,
    "5040": 32.0,
    "2400": 15.5,
    "880": 8.0,
    "420": 4.5,
    "80": 1.0,
}

PACKAGE_REGEX = re.compile(
    r'(?:'
    r'\b(?P<qty_before>\d+)\s*[*xX×]\s*(?P<pkg_after>10800|5040|2400|880|420|80)\s*(?:cp)?\b'
    r'|'
    r'\b(?P<pkg_before>10800|5040|2400|880|420|80)\s*(?:cp)?\s*[*xX×]\s*(?P<qty_after>\d+)\b'
    r'|'
    r'\b(?P<pkg_standalone>10800|5040|2400|880|420|80)\s*(?:cp)?\b'
    r')',
    re.IGNORECASE
)


def parse_test_order_packages(order_text: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Parses complete order text to detect all supported packages, quantities, and prices.
    Preserves exact order of appearance in original text.

    Returns:
        Optional[Dict[str, Any]]: Structured dictionary with 'packages' list and 'total_price' float,
        or None if no supported package is found.
    """
    if not order_text:
        return None

    raw_text = order_text.lower().strip()
    raw_text = re.sub(r'\b10\.8k\b', '10800', raw_text)
    raw_text = re.sub(r'\b5\.04k\b', '5040', raw_text)
    raw_text = re.sub(r'\b2\.4k\b', '2400', raw_text)
    raw_text = re.sub(r'\b(\d+),(\d+)\b', r'\1\2', raw_text)

    detected = []
    total_price = 0.0

    for match in PACKAGE_REGEX.finditer(raw_text):
        gd = match.groupdict()

        if gd.get("pkg_after"):
            pkg = gd["pkg_after"]
            qty = int(gd["qty_before"])
        elif gd.get("pkg_before"):
            pkg = gd["pkg_before"]
            qty = int(gd["qty_after"])
        elif gd.get("pkg_standalone"):
            pkg = gd["pkg_standalone"]
            qty = 1
        else:
            continue

        unit_price = TEST_PACKAGE_PRICES.get(pkg)
        if unit_price is not None:
            item_total = unit_price * qty
            total_price += item_total
            detected.append({
                "package": pkg,
                "qty": qty,
                "unit_price": unit_price,
                "total": item_total
            })

    if detected:
        return {
            "packages": detected,
            "total_price": round(total_price, 2)
        }

    return None


def calculate_test_price(order_text: Optional[str]) -> Optional[float]:
    """Calculates total test price using single shared parser parse_test_order_packages."""
    parsed = parse_test_order_packages(order_text)
    return parsed["total_price"] if parsed else None


def format_package_summary_and_price(parsed_data: Dict[str, Any]) -> str:
    """
    Formats detected packages and total price into customer/delivery text block.

    Example Single Package:
    📦 Package:
    • 2400 CP

    💰 Price: 15.5$

    Example Multiple Packages:
    📦 Package(s):
    • 2400 CP ×2
    • 880 CP

    💰 Price: 23.5$
    """
    pkgs = parsed_data.get("packages", [])
    total = parsed_data.get("total_price", 0.0)

    total_str = f"{total:g}$" if isinstance(total, float) else f"{total}$"

    if not pkgs:
        return f"💰 Price: {total_str}"

    title = "📦 Package:" if len(pkgs) == 1 else "📦 Package(s):"
    lines = [title]

    for item in pkgs:
        pkg_name = item["package"]
        qty = item["qty"]
        if qty > 1:
            lines.append(f"• {pkg_name} CP ×{qty}")
        else:
            lines.append(f"• {pkg_name} CP")

    lines.append("")
    lines.append(f"💰 Price: {total_str}")

    return "\n".join(lines)


def get_test_price(order_text: Optional[str]) -> Optional[float]:
    """Backward-compatible alias for calculate_test_price."""
    return calculate_test_price(order_text)


def normalize_order_content_for_dedup(text_content: Optional[str]) -> str:
    """
    Normalizes order text content strictly for exact duplicate detection.
    Strips leading/trailing whitespace, converts to lowercase, and collapses extra internal spaces/newlines.
    Preserves all actual letters, numbers, symbols, packages, UIDs, usernames, passwords, and recovery codes intact.
    Any 1-character difference in content will produce a different normalized string.
    """
    if not text_content:
        return ""

    text = text_content.lower().strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    normalized_lines = [re.sub(r'[ \t]+', ' ', line) for line in lines]
    return "\n".join(normalized_lines)
