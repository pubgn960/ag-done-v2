"""
Utility functions for security, role-based permission checking, reaction handling, system metrics, logging setup, and formatting.
"""

import os
import re
import sys
import time
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from telegram import Update, Bot, ReactionTypeEmoji, InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database import AUTH_USERS_CACHE
from email_parser import extract_order_section

logger = logging.getLogger(__name__)

# Start timestamp for calculating bot uptime
BOT_START_TIME = time.time()


class LoaderIssueType(str, Enum):
    """Extensible Enum representing loader-reported issue types."""
    WRONG_NAME = "wrong_name"
    WRONG_PASSWORD = "wrong_password"
    GOOGLE_LINKED = "google_linked"
    TWO_FACTOR = "two_factor"
    LOGIN_FAILED = "login_failed"
    WRONG_ACCOUNT = "wrong_account"
    NEED_CONFIRMATION = "need_confirmation"


ISSUE_WORKFLOW_CONFIG: Dict[str, Dict[str, Any]] = {
    LoaderIssueType.WRONG_NAME: {
        "issue_id": LoaderIssueType.WRONG_NAME,
        "label": "⚠️ Wrong Name",
        "keywords": ["wrong name", "wrongname", "name wrong"],
        "requires_screenshot": True,
        "missing_screenshot_msg": "⚠️ Please attach a screenshot as proof for Wrong Name verification.",
        "customer_title": "⚠️ Name Verification Required",
        "customer_message": "The loader reported that the account name may be incorrect.",
        "customer_text": "⚠️ <b>Name Verification Required</b>\n\nThe loader reported that the account name may be incorrect.",
        "customer_update_prompt": "❌ <b>Order Paused</b>\n\nPlease send the correct account name.\n\nExamples\n\nAccount Name:\nPlayer123\n\nor simply\n\nPlayer123",
        "approve_label": "✅ Approve",
        "reject_label": "❌ Update Account",
        "loader_success_msg": "✅ Customer confirmed that the account name is correct.\n\nYou may continue the delivery now.\n\nReply with delivery screenshots when finished.",
        "loader_failure_msg": "❌ Order Cancelled\n\nCustomer confirmed the account name is incorrect.\n\nPlease stop this delivery and wait for updated account details.",
        "loader_yes_text": "✅ Customer confirmed that the account name is correct.\n\nYou may continue the delivery now.\n\nReply with delivery screenshots when finished.",
        "loader_no_text": "❌ Order Cancelled\n\nCustomer confirmed the account name is incorrect.\n\nPlease stop this delivery and wait for updated account details.",
        "log_tag": "[WRONG_NAME]"
    },
    LoaderIssueType.WRONG_PASSWORD: {
        "issue_id": LoaderIssueType.WRONG_PASSWORD,
        "label": "⚠️ Wrong Password",
        "keywords": ["wrong password", "wrongpassword", "password wrong", "incorrect password"],
        "requires_screenshot": False,
        "missing_screenshot_msg": "⚠️ Please attach a screenshot if available for Wrong Password verification.",
        "customer_title": "⚠️ Password Verification Required",
        "customer_message": "The loader reported that the password appears to be incorrect.",
        "customer_text": "⚠️ <b>Password Verification Required</b>\n\nThe loader reported that the password appears to be incorrect.",
        "customer_update_prompt": "🔄 Please send new password.",
        "approve_label": "✅ Password is Correct",
        "reject_label": "🔄 Updating Password",
        "loader_success_msg": "✅ Customer confirmed the password is correct.\n\nYou may continue delivery.",
        "loader_failure_msg": "❌ Order Cancelled\n\nOrder has been cancelled by the customer.\n\nPlease stop this delivery.",
        "loader_updating_msg": "🔄 Customer is updating the password.\n\nPlease wait for the new password.",
        "loader_yes_text": "✅ Customer confirmed the password is correct.\n\nYou may continue delivery.",
        "loader_no_text": "❌ Order Cancelled\n\nOrder has been cancelled by the customer.\n\nPlease stop this delivery.",
        "log_tag": "[WRONG_PASSWORD]"
    },
    LoaderIssueType.GOOGLE_LINKED: {
        "issue_id": LoaderIssueType.GOOGLE_LINKED,
        "label": "⚠️ Google Linked",
        "keywords": ["google linked", "linked google", "google account linked", "already linked", "google bind"],
        "requires_screenshot": False,
        "missing_screenshot_msg": "⚠️ Please attach a screenshot if available for Google Linked verification.",
        "customer_title": "⚠️ Google Account Verification",
        "customer_message": "The loader reported that this account is already linked with Google.",
        "customer_text": "⚠️ <b>Google Account Verification</b>\n\nThe loader reported that this account is already linked with Google.",
        "customer_update_prompt": "❌ <b>Order Paused</b>\n\nPlease send updated login details or tell us which login method should be used.\n\nExamples\n\nFacebook\n\nActivision\n\nEmail:\nabc@gmail.com\n\nPassword:\n123456",
        "approve_label": "✅ Continue",
        "reject_label": "❌ Update Account",
        "loader_success_msg": "✅ Customer confirmed to continue with Google linked account.\n\nYou may continue the delivery now.\n\nReply with delivery screenshots when finished.",
        "loader_failure_msg": "❌ Order Cancelled\n\nCustomer rejected Google linked account.\n\nPlease stop this delivery and wait for updated account details.",
        "loader_yes_text": "✅ Customer confirmed to continue with Google linked account.\n\nYou may continue the delivery now.\n\nReply with delivery screenshots when finished.",
        "loader_no_text": "❌ Order Cancelled\n\nCustomer rejected Google linked account.\n\nPlease stop this delivery and wait for updated account details.",
        "log_tag": "[GOOGLE_LINKED]"
    },
    LoaderIssueType.TWO_FACTOR: {
        "issue_id": LoaderIssueType.TWO_FACTOR,
        "label": "📵 2FA Problem",
        "keywords": ["2fa", "2fa issue", "two factor", "two-factor", "verification code", "authenticator", "backup code"],
        "requires_screenshot": False,
        "missing_screenshot_msg": "⚠️ Please attach a screenshot if available for 2FA verification.",
        "customer_title": "⚠️ Two-Factor Authentication Required",
        "customer_message": "The loader requires additional verification.",
        "customer_text": "⚠️ <b>Two-Factor Authentication Required</b>\n\nThe loader requires additional verification.",
        "customer_update_prompt": "❌ <b>Order Paused</b>\n\nPlease send one of the following\n\n• Verification Code\n\n• Authenticator Code\n\n• Backup Code\n\nExamples\n\n123456\n\nAuthenticator Code:\n123456",
        "approve_label": "✅ Send Information",
        "reject_label": "❌ Cancel",
        "loader_success_msg": "✅ Customer provided verification information.\n\nYou may continue the delivery now.\n\nReply with delivery screenshots when finished.",
        "loader_failure_msg": "❌ Order Cancelled\n\nCustomer cancelled two-factor verification.\n\nPlease stop this delivery and wait for updated account details.",
        "loader_yes_text": "✅ Customer provided verification information.\n\nYou may continue the delivery now.\n\nReply with delivery screenshots when finished.",
        "loader_no_text": "❌ Order Cancelled\n\nCustomer cancelled two-factor verification.\n\nPlease stop this delivery and wait for updated account details.",
        "log_tag": "[TWO_FACTOR]"
    },
    LoaderIssueType.LOGIN_FAILED: {
        "issue_id": LoaderIssueType.LOGIN_FAILED,
        "label": "🔒 Login Failed",
        "keywords": ["login failed", "cannot login", "login error", "invalid credentials", "unable to login", "login unsuccessful"],
        "requires_screenshot": False,
        "missing_screenshot_msg": "⚠️ Please attach a screenshot if available for Login Failed verification.",
        "customer_title": "⚠️ Login Verification Required",
        "customer_message": "The loader was unable to log in using the provided account details.\n\nPlease verify the account information.",
        "customer_text": "⚠️ <b>Login Verification Required</b>\n\nThe loader was unable to log in using the provided account details.",
        "customer_update_prompt": "❌ <b>Order Paused</b>\n\nPlease send the updated login details.\n\nExamples\n\nEmail:\nabc@gmail.com\n\nPassword:\nPakistan123\n\nor simply send the corrected information.",
        "approve_label": "✅ Retry Login",
        "reject_label": "❌ Update Account",
        "loader_success_msg": "✅ Customer requested retry login with existing details.\n\nYou may continue the delivery now.\n\nReply with delivery screenshots when finished.",
        "loader_failure_msg": "❌ Order Cancelled\n\nCustomer stated account details are incorrect.\n\nPlease stop this delivery and wait for updated account details.",
        "loader_yes_text": "✅ Customer requested retry login with existing details.\n\nYou may continue the delivery now.\n\nReply with delivery screenshots when finished.",
        "loader_no_text": "❌ Order Cancelled\n\nCustomer stated account details are incorrect.\n\nPlease stop this delivery and wait for updated account details.",
        "log_tag": "[LOGIN_FAILED]"
    },
    LoaderIssueType.WRONG_ACCOUNT: {
        "issue_id": LoaderIssueType.WRONG_ACCOUNT,
        "label": "❌ Wrong Account",
        "keywords": ["wrong account", "wrongaccount"],
        "requires_screenshot": False,
        "customer_title": "⚠️ Account Credentials Verification",
        "customer_message": "The loader reported that the account credentials appear incorrect.",
        "customer_text": "⚠️ <b>Account Credentials Verification</b>\n\nThe loader reported that the account credentials appear incorrect.",
        "customer_update_prompt": "❌ <b>Order Paused</b>\n\nPlease send your updated account details below.",
        "approve_label": "✅ Confirm Details",
        "reject_label": "❌ Update Account",
        "loader_success_msg": "✅ Customer confirmed account details. Please continue delivery.",
        "loader_failure_msg": "❌ Customer reported account details are incorrect. Please wait for updated info.",
        "loader_yes_text": "✅ Customer confirmed account details. Please continue delivery.",
        "loader_no_text": "❌ Customer reported account details are incorrect. Please wait for updated info.",
        "log_tag": "[WRONG_ACCOUNT]"
    },
    LoaderIssueType.NEED_CONFIRMATION: {
        "issue_id": LoaderIssueType.NEED_CONFIRMATION,
        "label": "📝 Need Confirmation",
        "keywords": ["need confirmation"],
        "requires_screenshot": False,
        "customer_title": "⚠️ Order Confirmation Required",
        "customer_message": "The loader requested confirmation for your order details.",
        "customer_text": "⚠️ <b>Order Confirmation Required</b>\n\nThe loader requested confirmation for your order details.",
        "customer_update_prompt": "❌ <b>Order Paused</b>\n\nPlease send your updated order details below.",
        "approve_label": "✅ Confirm Order",
        "reject_label": "❌ Update Order",
        "loader_success_msg": "✅ Customer confirmed order. Please proceed with delivery.",
        "loader_failure_msg": "❌ Customer indicated order details are incorrect.",
        "loader_yes_text": "✅ Customer confirmed order. Please proceed with delivery.",
        "loader_no_text": "❌ Customer indicated order details are incorrect.",
        "log_tag": "[NEED_CONFIRMATION]"
    }
}

# Alias for backward compatibility
LOADER_ISSUE_CONFIG = ISSUE_WORKFLOW_CONFIG


def detect_loader_issue(caption_text: str) -> Optional[Tuple[Dict[str, Any], str]]:
    """
    Scans loader caption text for issue keywords defined in ISSUE_WORKFLOW_CONFIG.
    Returns (issue_config_dict, issue_id) if matched, else None.
    Matching is case-insensitive.
    """
    if not caption_text:
        return None

    caption_lower = caption_text.lower().strip()
    for issue_id, cfg in ISSUE_WORKFLOW_CONFIG.items():
        keywords = cfg.get("keywords", [])
        for kw in keywords:
            pattern = r'\b' + re.escape(kw) + r'\b'
            if re.search(pattern, caption_lower):
                issue_id_str = issue_id.value if hasattr(issue_id, 'value') else str(issue_id)
                return cfg, issue_id_str

    return None


def build_customer_issue_keyboard(order_id: int, issue_type: Union[str, LoaderIssueType]) -> InlineKeyboardMarkup:
    """
    Builds customer issue response inline keyboard.
    For WRONG_PASSWORD, returns 3 buttons:
      [✅ Password is Correct]
      [🔄 Updating Password]
      [❌ Cancel Order]
    For other issue types, returns 2 buttons (Approve / Reject).
    """
    issue_type_str = issue_type.value if hasattr(issue_type, 'value') else str(issue_type or "")
    if issue_type_str in (LoaderIssueType.WRONG_PASSWORD.value, "wrong_password", "wrongpassword"):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Password is Correct", callback_data=f"cust_confirm:pw_correct:{order_id}:wrong_password")],
            [InlineKeyboardButton("🔄 Updating Password", callback_data=f"cust_confirm:pw_updating:{order_id}:wrong_password")],
            [InlineKeyboardButton("❌ Cancel Order", callback_data=f"cust_confirm:pw_cancel:{order_id}:wrong_password")]
        ])

    issue_cfg = ISSUE_WORKFLOW_CONFIG.get(issue_type_str, ISSUE_WORKFLOW_CONFIG.get(LoaderIssueType.WRONG_NAME))
    approve_lbl = issue_cfg.get("approve_label", "✅ Approve") if issue_cfg else "✅ Approve"
    reject_lbl = issue_cfg.get("reject_label", "❌ Update Account") if issue_cfg else "❌ Update Account"

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(approve_lbl, callback_data=f"cust_confirm:yes:{order_id}:{issue_type_str}"),
            InlineKeyboardButton(reject_lbl, callback_data=f"cust_confirm:no:{order_id}:{issue_type_str}")
        ]
    ])


def validate_customer_update_for_issue(text: str, issue_type: Optional[str] = None) -> bool:
    """
    Validates whether customer response text is a valid account update depending on active issue type.
    Rejects general chatter (ok, done, thanks, hello, ❤️, 🔥, etc.).
    Supports both labeled fields (e.g. Password: 123) and plain text inputs (e.g. Pakistan123 for wrong_password).
    """
    if not text or not text.strip():
        return False

    t_trimmed = text.strip()
    t_lower = t_trimmed.lower()

    # 1. Ignore generic chatter
    chatter_words = {
        "ok", "done", "thanks", "hello", "fast", "completed", "ty", "sure", "please",
        "thank you", "k", "kk", "thx", "❤️", "🔥", "👍", "😊"
    }
    if t_lower in chatter_words or any(emoji in t_lower for emoji in ["❤️", "🔥", "👍"]):
        return False

    # 2. Check universal email match or field indicators
    if re.search(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', text):
        return True

    valid_field_indicators = [
        "email", "mail", "gmail", "yahoo", "hotmail", "outlook", "icloud", "proton",
        "password", "pass", "pwd",
        "recovery", "backup", "2fa", "authenticator", "code", "codes",
        "uid", "nickname", "nick", "username", "account", "name", "platform", "login"
    ]
    for indicator in valid_field_indicators:
        if re.search(r'\b' + re.escape(indicator) + r'\b', t_lower):
            return True

    # 3. Issue-Aware Smart Validation for Plain Text
    clean_issue = (issue_type.value if hasattr(issue_type, 'value') else str(issue_type or "")).lower()

    if clean_issue == "wrong_password":
        return len(t_trimmed) >= 3
    elif clean_issue == "wrong_name":
        return len(t_trimmed) >= 2
    elif clean_issue == "two_factor":
        if re.search(r'\b\d{4,8}\b', text) or re.search(r'^[A-Za-z0-9\-\s]{4,16}$', t_trimmed):
            return True
        return len(t_trimmed) >= 4
    elif clean_issue == "google_linked":
        return len(t_trimmed) >= 2
    elif clean_issue == "login_failed":
        return len(t_trimmed) >= 3

    # Generic Fallback: If 4+ digit codes or non-chatter text >= 3 chars
    if re.search(r'\b\d{4,8}\b', text):
        return True

    return len(t_trimmed) >= 3


def has_valid_account_update_fields(text: str, issue_type: Optional[str] = None) -> bool:
    """Alias for validate_customer_update_for_issue for backward compatibility."""
    return validate_customer_update_for_issue(text, issue_type)


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


def is_ignored_user(user_id: Optional[int]) -> bool:
    """
    Checks if a user ID belongs to trusted internal users who must be completely ignored
    by the order detection engine and group workflow handlers.
    Writes single required log entry:
    [IGNORE_USER]
    Ignored trusted user.
    User ID: <id>
    Reason: Internal User
    """
    if not user_id:
        return False

    if user_id in Config.IGNORED_USER_IDS:
        logger.info(f"[IGNORE_USER]\nIgnored trusted user.\nUser ID: {user_id}\nReason: Internal User")
        return True

    return False


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


ALLOWED_REACTION_EMOJIS: Set[str] = {"👍", "❤️", "✅", "❌", "⏳"}


async def safe_set_message_reaction(
    bot: Bot,
    chat_id: Optional[int],
    message_id: Optional[int],
    emoji: str = "👍",
    fallback_emoji: Optional[str] = None,
    log_tag: str = "[REACTION]"
) -> bool:
    """
    Safely sets a Telegram reaction emoji on a message using standard Unicode reactions:
    👍, ❤️, ✅, ❌, ⏳.
    Never uses custom_emoji_id.
    Gracefully handles cases where reactions are disabled in chat or unsupported by API.
    """
    if not chat_id or not message_id:
        return False

    emoji_map = {
        "⚠️": "⏳",
        "📥": "👍"
    }
    target_emoji = emoji_map.get(emoji, emoji)
    if target_emoji not in ALLOWED_REACTION_EMOJIS:
        target_emoji = "👍"

    logger.info(f"{log_tag} Attempting reaction '{target_emoji}' on Message ID #{message_id} in Chat #{chat_id}...")

    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=target_emoji)]
        )
        logger.info(f"{log_tag} Success: Reaction '{target_emoji}' set on Message ID #{message_id} in Chat #{chat_id}.")
        return True
    except Exception as e:
        logger.warning(f"{log_tag} Telegram rejected reaction '{target_emoji}' on Message ID #{message_id} in Chat #{chat_id}: {e}")
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


SUPPORTED_PACKAGES: Set[str] = {
    "80", "420", "880", "2400", "4800", "5040", "7200", "9600", "10800",
    "12000", "14400", "16800", "19200", "21600", "24000", "38400", "43200",
    "48000", "55200", "72000", "96000", "108000"
}

PACKAGE_PRICES: Dict[str, float] = {
    "108000": 563.0,
    "96000": 503.0,
    "72000": 375.0,
    "55200": 291.0,
    "48000": 254.0,
    "43200": 229.0,
    "38400": 211.0,
    "24000": 132.0,
    "21600": 119.0,
    "19200": 109.0,
    "16800": 95.0,
    "14400": 82.0,
    "12000": 69.0,
    "10800": 64.0,
    "9600": 55.0,
    "7200": 42.0,
    "5040": 33.0,
    "4800": 29.0,
    "2400": 16.5,
    "880": 8.0,
    "420": 4.5,
    "80": 1.0,
}

TEST_PACKAGE_PRICES = PACKAGE_PRICES


def reload_package_prices_cache(new_prices: Dict[str, float]) -> None:
    """Reloads in-memory package price cache immediately without restarting."""
    global PACKAGE_PRICES, SUPPORTED_PACKAGES
    PACKAGE_PRICES.clear()
    if new_prices:
        PACKAGE_PRICES.update(new_prices)
    else:
        PACKAGE_PRICES.update(TEST_PACKAGE_PRICES)
    SUPPORTED_PACKAGES.clear()
    SUPPORTED_PACKAGES.update(PACKAGE_PRICES.keys())


def parse_bulk_prices_input(text: str) -> Tuple[Optional[Dict[str, float]], Optional[str]]:
    """
    Parses and validates a bulk price update text block.
    Performs UPSERT parsing (accepting existing and new packages).
    Supports formats:
      10800 64
      108.000 563
      96,000 503
      10800 = 64
      10800 : 64
      10800 -> 64
      10800 => 64
      5k -> 34
      5000 -> 34
    Strips noise tokens (CP, cp, $, ⚡, 🎉, etc.).
    Returns (price_map, None) on success, or (None, error_msg) on failure.
    """
    from order_parser import normalize_package_alias

    if not text or not text.strip():
        return None, "❌ Empty input text."

    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return None, "❌ Empty input text."

    parsed_map: Dict[str, float] = {}
    seen_packages: Set[str] = set()

    for line in lines:
        clean_line = re.sub(r'[$⚡🎉]|\bcp\b', '', line, flags=re.IGNORECASE).strip()
        if not clean_line:
            continue

        parts = re.split(r'\s*(?:->|=>|=|\:|\s)\s*', clean_line)
        parts = [p for p in parts if p]

        if len(parts) != 2:
            return None, f"❌ Invalid Price\n\n{line}"

        raw_pkg, raw_price = parts[0].strip(), parts[1].strip()

        # Remove thousands separators (. or ,) from package number e.g. 108.000 -> 108000, 96,000 -> 96000
        pkg_clean = raw_pkg.replace('.', '').replace(',', '')
        canonical_pkg = normalize_package_alias(pkg_clean)

        if not canonical_pkg.isdigit():
            return None, f"❌ Unknown Package\n\n{raw_pkg}"

        if canonical_pkg in seen_packages:
            return None, f"❌ Duplicate Package\n\n{canonical_pkg}"

        try:
            price_val = float(raw_price)
            if price_val <= 0:
                return None, f"❌ Invalid Price\n\n{line}"
        except ValueError:
            return None, f"❌ Invalid Price\n\n{line}"

        parsed_map[canonical_pkg] = price_val
        seen_packages.add(canonical_pkg)

    if not parsed_map:
        return None, "❌ No valid package prices found."

    return parsed_map, None


def format_export_prices(price_map: Dict[str, float]) -> str:
    """
    Formats prices for export in standard format matching requirements:
    Standard Packs first, blank line, Special Packs and any newly added packages second.
    """
    standard_order = ["10800", "5040", "2400", "880", "420", "80"]

    def _fmt_price(val: float) -> str:
        return f"{int(val)}" if val.is_integer() else f"{val:g}"

    std_lines = [f"{pkg} {_fmt_price(price_map[pkg])}" for pkg in standard_order if pkg in price_map]

    other_pkgs = [p for p in price_map.keys() if p not in standard_order]
    other_pkgs_sorted = sorted(other_pkgs, key=lambda x: int(x) if x.isdigit() else 0, reverse=True)

    spc_lines = [f"{pkg} {_fmt_price(price_map[pkg])}" for pkg in other_pkgs_sorted]

    if std_lines and spc_lines:
        return "\n".join(std_lines) + "\n\n" + "\n".join(spc_lines)
    return "\n".join(std_lines or spc_lines)


def _fmt_price_val(val: float) -> str:
    """Helper to format price cleanly (e.g. 16, 16.5)."""
    return f"{int(val)}" if val.is_integer() else f"{val:g}"


def format_ledger_entry_message(before: float, now: float, total: float) -> str:
    """
    Formats standard Delivery Ledger notice in exact 3-line format:
    Before 64$
    Now 15.5$
    Total 79.5$

    No heading, no emojis, no extra blank lines, no extra text.
    """
    return (
        f"Before {_fmt_price_val(before)}$\n"
        f"Now {_fmt_price_val(now)}$\n"
        f"Total {_fmt_price_val(total)}$"
    )


def format_calculator_result_message(before: float, now_val: float, total: float) -> str:
    """
    Formats standard Running Total Calculator result notice:
    ━━━━━━━━━━━━━━━━━━

    📊 Running Total

    Before
    97$

    Now
    +64$ (or -100$)

    Total
    161$ (or 61$)

    ━━━━━━━━━━━━━━━━━━
    """
    if now_val >= 0:
        now_str = f"+{_fmt_price_val(now_val)}"
    else:
        now_str = f"-{_fmt_price_val(abs(now_val))}"

    return (
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📊 <b>Running Total</b>\n\n"
        "Before\n"
        f"{_fmt_price_val(before)}$\n\n"
        "Now\n"
        f"{now_str}$\n\n"
        "Total\n"
        f"{_fmt_price_val(total)}$\n\n"
        "━━━━━━━━━━━━━━━━━━"
    )


def format_calculator_total_message(total: float) -> str:
    """
    Formats /total card:
    ━━━━━━━━━━━━━━━━━━

    📊 Current Total

    61$

    ━━━━━━━━━━━━━━━━━━
    """
    return (
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📊 <b>Current Total</b>\n\n"
        f"{_fmt_price_val(total)}$\n\n"
        "━━━━━━━━━━━━━━━━━━"
    )


def format_running_total_current_message(total: float) -> str:
    """
    Formats /total response:
    ━━━━━━━━━━━━━━━━━━

    📊 Current Delivery Total

    113.5$

    ━━━━━━━━━━━━━━━━━━
    """
    return (
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📊 <b>Current Delivery Total</b>\n\n"
        f"{_fmt_price_val(total)}$\n\n"
        "━━━━━━━━━━━━━━━━━━"
    )


def format_pay_record_message(before: float, paid: float, current: float) -> str:
    """
    Formats /pay response:
    ━━━━━━━━━━━━━━━━━━

    ✅ Payment Recorded

    Before
    113.5$

    Paid
    113.5$

    Current Total
    0$

    ━━━━━━━━━━━━━━━━━━
    """
    return (
        "━━━━━━━━━━━━━━━━━━\n\n"
        "✅ <b>Payment Recorded</b>\n\n"
        "Before\n"
        f"{_fmt_price_val(before)}$\n\n"
        "Paid\n"
        f"{_fmt_price_val(paid)}$\n\n"
        "Current Total\n"
        f"{_fmt_price_val(current)}$\n\n"
        "━━━━━━━━━━━━━━━━━━"
    )


def format_manual_adjustment_message(before: float, now_val: float, total: float) -> str:
    """
    Formats manual + / - adjustments:
    ━━━━━━━━━━━━━━━━━━

    Before
    64$

    Now
    +10$ (or -10$)

    Total
    74$ (or 64$)

    ━━━━━━━━━━━━━━━━━━
    """
    if now_val >= 0:
        now_str = f"+{_fmt_price_val(now_val)}"
    else:
        now_str = f"-{_fmt_price_val(abs(now_val))}"

    return (
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Before\n"
        f"{_fmt_price_val(before)}$\n\n"
        "Now\n"
        f"{now_str}$\n\n"
        "Total\n"
        f"{_fmt_price_val(total)}$\n\n"
        "━━━━━━━━━━━━━━━━━━"
    )


def calculate_delivered_packages_value(packages_str: str) -> Tuple[Optional[float], bool]:
    """
    Calculates sum of prices for delivered package(s) string (e.g., '10800', '10800+5040', '2x10800').
    Returns (total_value, all_known_bool).
    """
    if not packages_str or not packages_str.strip():
        return None, False

    parsed = parse_test_order_packages(packages_str)
    if not parsed or not parsed.get("packages"):
        return None, False

    total_val = 0.0
    all_known = True

    from order_parser import normalize_package_alias

    for p in parsed["packages"]:
        pkg_name = p.get("package")
        qty = p.get("qty", 1)
        canonical_pkg = normalize_package_alias(str(pkg_name))

        unit_price = PACKAGE_PRICES.get(canonical_pkg)
        if unit_price is None:
            unit_price = PACKAGE_PRICES.get(str(pkg_name))
        if unit_price is None:
            unit_price = p.get("unit_price")

        if unit_price is not None:
            total_val += unit_price * qty
        else:
            all_known = False

    if not all_known and parsed.get("known_total", 0) > 0:
        return parsed.get("known_total"), False

    if not all_known:
        return None, False

    return total_val, True


def parse_test_order_packages(order_text: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Parses complete order text using Production Order Parser v2.
    """
    if not order_text:
        return None

    from order_parser import parse_order_v2
    parsed = parse_order_v2(order_text)

    pkgs = parsed.get("packages", [])
    if not pkgs:
        return None

    has_unknown = len(parsed.get("unknown_packages", [])) > 0
    total = parsed.get("total_price")
    known_total = total or sum((p.get("unit_price") or 0.0) for p in pkgs if p.get("known"))

    return {
        "packages": pkgs,
        "known_total": round(known_total, 2),
        "has_unknown": has_unknown,
        "total_price": round(total, 2) if (total is not None and not has_unknown) else None
    }


def calculate_test_price(order_text: Optional[str]) -> Optional[float]:
    """Calculates total test price using single shared parser parse_test_order_packages."""
    parsed = parse_test_order_packages(order_text)
    if parsed and not parsed.get("has_unknown"):
        return parsed["total_price"]
    elif parsed and parsed.get("known_total", 0.0) > 0:
        return parsed["known_total"]
    return None


def get_test_price(order_text: Optional[str]) -> Optional[float]:
    """Backward-compatible alias for calculate_test_price."""
    return calculate_test_price(order_text)


def format_package_summary_and_price(parsed_data: Dict[str, Any]) -> str:
    """
    Formats detected packages and total price into customer/delivery text block.
    """
    pkgs = parsed_data.get("packages", [])
    total = parsed_data.get("total_price") or parsed_data.get("known_total", 0.0)

    total_str = f"{total:g}$" if isinstance(total, float) else f"{total}$"

    if not pkgs:
        return f"💰 Price: {total_str}"

    title = "📦 Package:" if len(pkgs) == 1 else "📦 Package(s):"
    lines = [title]

    for item in pkgs:
        pkg_name = item["package"]
        qty = item["qty"]
        qty_str = f" ×{qty}" if qty > 1 else ""
        u_price = item.get("unit_price")
        if u_price is not None:
            lines.append(f"• {pkg_name} CP{qty_str}")
        else:
            lines.append(f"❓ {pkg_name} CP{qty_str}")

    lines.append("")
    lines.append(f"💰 Price: {total_str}")

    return "\n".join(lines)


def format_missing_packages_summary(progress_data: Any) -> str:
    """
    Formats missing package list display:
    ❌ Missing Packages

    108000
    96000
    """
    import json
    from order_parser import normalize_package_alias, get_dynamic_package_prices

    if isinstance(progress_data, str):
        try:
            items = json.loads(progress_data)
        except Exception:
            items = []
    elif isinstance(progress_data, list):
        items = progress_data
    else:
        items = []

    price_db = get_dynamic_package_prices()
    missing_pkgs = []

    for item in items:
        raw_pkg = str(item.get("package", "")).strip()
        canonical_pkg = normalize_package_alias(raw_pkg)
        u_p = item.get("unit_price")
        if u_p is None and canonical_pkg not in price_db and canonical_pkg not in missing_pkgs:
            missing_pkgs.append(canonical_pkg)
        elif (item.get("status") == "Unpriced" or u_p is None) and canonical_pkg not in missing_pkgs:
            missing_pkgs.append(canonical_pkg)

    if missing_pkgs:
        return "❌ Missing Packages\n\n" + "\n".join(missing_pkgs)
    return ""


def format_package_progress_summary(progress_data: Any, total_price: Optional[float] = None) -> str:
    """
    Formats package progress tracking checkboxes, unknown package statuses, and total price calculation.
    """
    import json
    if isinstance(progress_data, str):
        try:
            items = json.loads(progress_data)
        except Exception:
            items = []
    elif isinstance(progress_data, list):
        items = progress_data
    else:
        items = []

    if not items:
        if total_price is not None:
            t_str = f"{total_price:g}$" if isinstance(total_price, float) else f"{total_price}$"
            return f"💰 Total Price: {t_str}"
        return ""

    title = "📦 Package" if len(items) == 1 else "📦 Packages"
    lines = [title, ""]

    all_delivered = True
    calc_total = 0.0
    has_unpriced = False

    for item in items:
        pkg_name = item.get("package", "")
        qty = item.get("qty", 1)
        status = item.get("status", "Pending")
        unit_price = item.get("unit_price")

        is_done = (status == "Delivered")
        if not is_done:
            all_delivered = False

        qty_str = f" ×{qty}" if qty > 1 else ""

        if status == "Unpriced" or unit_price is None:
            has_unpriced = True
            lines.append(f"❓ {pkg_name} CP{qty_str}")
        else:
            item_total = unit_price * qty
            calc_total += item_total
            checkbox = "✅" if is_done else "☐"
            lines.append(f"{checkbox} {pkg_name} CP{qty_str}")

    if all_delivered and not has_unpriced:
        lines.append("")
        lines.append("🎉 All Packages Delivered")

    final_total = total_price if (total_price is not None and not has_unpriced) else calc_total
    total_str = f"{final_total:g}$" if isinstance(final_total, float) else f"{final_total}$"

    lines.append("")
    if has_unpriced:
        lines.append(f"💰 Known Total: {total_str}")
    else:
        lines.append(f"💰 Total Price: {total_str}")

    return "\n".join(lines)


def get_unknown_package_keyboard(order_id: int, progress_data: Any) -> Optional[InlineKeyboardMarkup]:
    """
    Returns an inline keyboard with '✏️ Add Price {pkg_name}' button for EACH unpriced package.
    Uses canonical package alias normalization.
    """
    import json
    from order_parser import normalize_package_alias, get_dynamic_package_prices

    if isinstance(progress_data, str):
        try:
            items = json.loads(progress_data)
        except Exception:
            items = []
    elif isinstance(progress_data, list):
        items = progress_data
    else:
        items = []

    price_db = get_dynamic_package_prices()
    buttons = []
    seen = set()

    for item in items:
        raw_pkg = str(item.get("package", "")).strip()
        canonical_pkg = normalize_package_alias(raw_pkg)

        unit_price = item.get("unit_price") or price_db.get(canonical_pkg)
        is_unpriced = (item.get("status") == "Unpriced" or unit_price is None)

        if is_unpriced and canonical_pkg and canonical_pkg not in seen:
            seen.add(canonical_pkg)
            buttons.append([
                InlineKeyboardButton(
                    f"✏️ Add Price {canonical_pkg}",
                    callback_data=f"add_unk_price:{order_id}:{canonical_pkg}"
                )
            ])

    if buttons:
        return InlineKeyboardMarkup(buttons)
    return None


def update_unknown_package_price(progress_data: Any, target_pkg: str, price_val: float) -> Tuple[List[Dict[str, Any]], float, bool]:
    """
    Updates the price for a specific unknown package item in progress_data.
    Applies canonical package alias normalization.
    Returns (updated_items, new_total_price, has_remaining_unpriced).
    """
    import json
    from order_parser import normalize_package_alias

    if isinstance(progress_data, str):
        try:
            items = json.loads(progress_data)
        except Exception:
            items = []
    elif isinstance(progress_data, list):
        items = progress_data
    else:
        items = []

    target_canonical = normalize_package_alias(target_pkg)

    for item in items:
        pkg_name = str(item.get("package", "")).strip()
        pkg_canonical = normalize_package_alias(pkg_name)
        if (pkg_canonical == target_canonical or pkg_name == target_pkg) and (item.get("status") == "Unpriced" or item.get("unit_price") is None):
            item["package"] = pkg_canonical
            item["unit_price"] = price_val
            item["total"] = price_val * item.get("qty", 1)
            item["status"] = "Pending"

    # Calculate new total
    new_total = 0.0
    has_remaining_unpriced = False
    for item in items:
        u_price = item.get("unit_price")
        if u_price is not None and item.get("status") != "Unpriced":
            new_total += u_price * item.get("qty", 1)
        else:
            has_remaining_unpriced = True

    return items, round(new_total, 2), has_remaining_unpriced


def format_order_details_block(raw_text: Optional[str], fallback_email: Optional[str] = None) -> str:
    """
    Extracts and formats the top ORDER DETAILS block from raw_text.
    Preserves Platform, Nick/Username, Email, Password, Recovery Codes, 2FA, etc.
    Strips out the Order/Package section so package status appears in PACKAGE STATUS.
    """
    if not raw_text:
        email_str = fallback_email or "No Email"
        return f"📧 Email:\n{email_str}"

    lines = raw_text.splitlines()
    details_lines = []
    in_order_sec = False

    OTHER_SECTION_HEADERS = (
        "email:", "mail:", "correo:", "correo electrónico:", "correo electronico:",
        "correo o número:", "correo o numero:", "correo o número fb:", "correo o numero fb:",
        "old password:", "new password:", "password:", "pass:", "pwd:", "contraseña:", "contrasena:", "clave:",
        "contraseña de fb:", "contrasena de fb:",
        "recovery:", "recovery code:", "recovery codes:", "backup codes:",
        "código:", "códigos:", "codigo:", "codigos:", "2fa:", "authenticator:",
        "phone:", "teléfono:", "telefono:", "celular:", "número:", "numero:",
        "uid:", "account id:", "nickname:", "username:", "nick:", "ign:", "usuario:", "nombre:",
        "platform:", "login:"
    )

    STANDALONE_HEADERS = (
        "order", "package", "packages", "cp", "order:", "package:", "packages:", "cp:",
        "códigos", "codigos", "código", "codigo", "contraseña", "contrasena", "clave",
        "correo", "email", "password", "pass", "pwd", "recovery", "nick", "ign", "usuario"
    )

    for line in lines:
        line_strip = line.strip()
        line_lower = line_strip.lower()

        if re.match(r'^(?:order|packages|package|cp)\s*[:=\-]', line_lower) or line_lower in ("order", "package", "packages", "cp", "order:", "package:", "packages:", "cp:"):
            in_order_sec = True
            continue
        elif not in_order_sec and not any(line_lower.startswith(h) for h in OTHER_SECTION_HEADERS) and line_lower not in STANDALONE_HEADERS and not re.search(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', line_strip):
            if re.search(r'\b(?:108000|96000|72000|48000|43200|38400|24000|21600|19200|16800|14400|12000|10800|9600|7200|5040|2400|880|420|80|5k|10k|2\.4k|2,4k)\b', line_lower) or re.search(r'\b\d+(?:cp)?[*xX×]\d+', line_lower):
                in_order_sec = True
                continue

        if in_order_sec:
            if any(line_lower.startswith(h) for h in OTHER_SECTION_HEADERS) or line_lower in STANDALONE_HEADERS:
                in_order_sec = False
            else:
                continue

        if line_lower in ("facebook", "fb", "meta", "activision", "activision id") or line_lower.startswith("platform:"):
            p_name = line_strip.split(":", 1)[-1].strip() if ":" in line_strip else line_strip
            details_lines.append(f"🎮 Platform:\n{p_name}" if p_name else "🎮 Platform:")
        elif any(line_lower.startswith(h) for h in ("nick:", "nickname:", "username:", "ign:", "usuario:", "nombre:")):
            n_val = line_strip.split(":", 1)[-1].strip()
            details_lines.append(f"👤 Nick:\n{n_val}" if n_val else "👤 Nick:")
        elif any(line_lower.startswith(h) for h in ("email:", "mail:", "correo:", "correo electrónico:", "correo electronico:", "correo o número:", "correo o numero:", "correo o número fb:", "correo o numero fb:")):
            e_val = line_strip.split(":", 1)[-1].strip()
            details_lines.append(f"📧 Email:\n{e_val}" if e_val else "📧 Email:")
        elif any(line_lower.startswith(h) for h in ("old password:", "new password:", "password:", "pass:", "pwd:", "contraseña:", "contrasena:", "clave:", "contraseña de fb:", "contrasena de fb:")):
            if line_lower.startswith("old password:"):
                p_val = line_strip.split(":", 1)[-1].strip()
                details_lines.append(f"🔑 Old Password:\n{p_val}" if p_val else "🔑 Old Password:")
            elif line_lower.startswith("new password:"):
                p_val = line_strip.split(":", 1)[-1].strip()
                details_lines.append(f"🔑 New Password:\n{p_val}" if p_val else "🔑 New Password:")
            else:
                p_val = line_strip.split(":", 1)[-1].strip()
                details_lines.append(f"🔑 Password:\n{p_val}" if p_val else "🔑 Password:")
        else:
            details_lines.append(line_strip)

    res_text = "\n".join([l for l in details_lines if l.strip()])
    if fallback_email and fallback_email.lower() not in res_text.lower():
        res_text = f"📧 Email:\n{fallback_email}\n\n" + res_text

    return res_text


def extract_password_from_text(text: str) -> Optional[str]:
    """Extracts password value from raw text or input message."""
    if not text:
        return None

    match_new = re.search(r'(?:new\s*password|nueva\s*contraseña|nueva\s*contrasena)\s*[:=\-]\s*([^\n]+)', text, re.IGNORECASE)
    if match_new:
        return match_new.group(1).strip()

    match_std = re.search(r'(?:password|pass|pwd|contraseña|contrasena|clave)\s*[:=\-]\s*([^\n]+)', text, re.IGNORECASE)
    if match_std:
        return match_std.group(1).strip()

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) == 1 and not re.search(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', lines[0]):
        if lines[0].lower() not in ("facebook", "fb", "activision", "order", "package"):
            return lines[0]

    return None


def build_updated_raw_text_with_passwords(old_raw_text: str, new_input_text: str) -> str:
    """
    Updates raw_text with Old Password and New Password formatting.
    If old_raw_text had 'Password: A' and new_input_text is 'B', becomes:
      Old Password: A
      New Password: B
    If old_raw_text had 'Old Password: A \n New Password: B' and new_input is 'C', becomes:
      Old Password: B
      New Password: C
    """
    old_pass = extract_password_from_text(old_raw_text) or "N/A"
    new_pass = extract_password_from_text(new_input_text) or new_input_text.strip()

    if not old_raw_text:
        return f"Old Password: {old_pass}\nNew Password: {new_pass}"

    lines = old_raw_text.splitlines()
    new_lines = []
    inserted = False

    pass_pattern = re.compile(
        r'^\s*(?:old\s*password|new\s*password|password|pass|pwd|contraseña|contrasena|clave)(?:\s*de\s*fb)?\s*[:=\-]',
        re.IGNORECASE
    )

    for line in lines:
        if pass_pattern.match(line):
            if not inserted:
                new_lines.append(f"Old Password: {old_pass}")
                new_lines.append(f"New Password: {new_pass}")
                inserted = True
            continue
        new_lines.append(line)

    if not inserted:
        new_lines.append(f"Old Password: {old_pass}")
        new_lines.append(f"New Password: {new_pass}")

    return "\n".join(new_lines)


def is_bot_user(user: Any, context: Any = None) -> bool:
    """
    Safely checks if a user is a bot or matches the current application bot ID.
    Compatible with unittest MagicMock instances and context=None.
    """
    if not user:
        return False

    is_bot_attr = getattr(user, "is_bot", False)
    if is_bot_attr is True:
        return True

    if context:
        bot = getattr(context, "bot", None)
        if bot:
            bot_id = getattr(bot, "id", None)
            user_id = getattr(user, "id", None)
            if isinstance(bot_id, int) and isinstance(user_id, int) and bot_id == user_id:
                return True

    return False


def is_bot_system_notification_text(text: Any) -> bool:
    """
    Checks if a message text is a bot-generated system/status notification.
    These notifications should never be processed as human loader inputs/replies.
    """
    if not isinstance(text, str):
        return False

    t_trimmed = text.strip()
    t_lower = t_trimmed.lower()

    notification_headers = [
        "❌ order cancelled",
        "❌ order record not found",
        "🔄 password updated",
        "🔄 customer is updating the password",
        "🔄 customer updated the account details",
        "⏳ waiting for customer",
        "✅ customer confirmed",
        "📦 delivered package",
        "📊 delivery ledger",
        "💰 price",
        "⚠️ no active delivery session",
        "⚠️ password verification required",
        "⚠️ this order was already delivered"
    ]

    for header in notification_headers:
        if header in t_lower:
            return True

    if t_trimmed.startswith(("❌ Order", "🔄 Customer", "🔄 Password", "⏳ Waiting", "✅ Customer", "📦 Delivered", "📊 Delivery")):
        return True

    return False


def format_package_status_block(progress_data: Any, total_price: Optional[float] = None) -> str:
    """
    Formats the PACKAGE STATUS section of the Loader Order Card showing:
    - Delivered packages with ✅
    - Selected packages with ☑
    - Pending packages with ⬜
    - NO price values displayed (Prices hidden from loaders)
    - '🎉 Order Completed' when all packages are delivered
    """
    import json
    if isinstance(progress_data, str):
        try:
            items = json.loads(progress_data)
        except Exception:
            items = []
    elif isinstance(progress_data, list):
        items = progress_data
    else:
        items = []

    if not items:
        return ""

    lines = []
    all_delivered = True

    for item in items:
        pkg_name = item.get("package", "")
        qty = item.get("qty", 1)
        status = item.get("status", "Pending")

        qty_str = f" ×{qty}" if qty > 1 else ""
        pkg_display = f"{pkg_name} CP{qty_str}"

        if status == "Delivered":
            checkbox = "✅"
        elif status == "Selected":
            checkbox = "☑"
            all_delivered = False
        else: # Pending or Unpriced
            checkbox = "⬜"
            all_delivered = False

        if status == "Unpriced":
            lines.append(f"❓ {pkg_display}")
        else:
            lines.append(f"{checkbox} {pkg_display}")

    if all_delivered:
        lines.append("")
        lines.append("🎉 Order Completed")

    return "\n".join(lines)


def format_full_loader_order_card(order: Any) -> str:
    """
    Renders the complete Loader Order Card containing:
    1. ORDER DETAILS block (Platform, Nick, Email, Password, Recovery Codes)
    2. PACKAGE STATUS block (⬜/☑/✅ checkboxes, Total Price, 🎉 Order Completed)
    """
    raw_text = getattr(order, "raw_text", "") or ""
    email = getattr(order, "email", "") or ""
    package_progress = getattr(order, "package_progress", None)
    price = getattr(order, "price", None)

    details_block = format_order_details_block(raw_text, email)
    status_block = format_package_status_block(package_progress, price)

    return (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📋 ORDER DETAILS\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"{details_block}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📦 PACKAGE STATUS\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"{status_block}"
    )


def format_loader_card_summary(progress_data: Any, total_price: Optional[float] = None, raw_text: Optional[str] = None, email: Optional[str] = None) -> str:
    """
    Formats the Loader Group order card text.
    If raw_text is provided, includes full ORDER DETAILS block at the top.
    """
    if raw_text:
        details_block = format_order_details_block(raw_text, email)
        status_block = format_package_status_block(progress_data, total_price)
        return (
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📋 ORDER DETAILS\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"{details_block}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📦 PACKAGE STATUS\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"{status_block}"
        )
    return format_package_status_block(progress_data, total_price)


def format_delivered_packages_caption(items: Any) -> str:
    """
    Formats the screenshot delivery caption for Client Group displaying ONLY the packages delivered in this session and calculating session total price.
    Example:
    📦 Delivered Package(s)

    ✅ 10800 CP
    ✅ 5040 CP

    💰 Price: 97.5$
    """
    import json
    if isinstance(items, str):
        try:
            item_list = json.loads(items)
        except Exception:
            item_list = []
    elif isinstance(items, list):
        item_list = items
    else:
        item_list = []

    if not item_list:
        return ""

    title = "📦 Delivered Package" if len(item_list) == 1 else "📦 Delivered Package(s)"
    lines = [title, ""]
    session_price = 0.0
    has_unpriced = False

    for item in item_list:
        pkg_name = str(item.get("package", "")).strip()
        qty = item.get("qty", 1)

        # Dynamic price resolution: Always look up the CURRENT price from PACKAGE_PRICES first!
        from order_parser import normalize_package_alias
        canonical_pkg = normalize_package_alias(pkg_name)

        unit_price = PACKAGE_PRICES.get(canonical_pkg)
        if unit_price is None:
            unit_price = PACKAGE_PRICES.get(pkg_name)
        if unit_price is None:
            unit_price = item.get("unit_price")
        if unit_price is None:
            unit_price = get_test_price(pkg_name)

        if unit_price is not None:
            session_price += unit_price * qty
        else:
            has_unpriced = True

        qty_str = f" ×{qty}" if qty > 1 else ""
        lines.append(f"✅ {pkg_name} CP{qty_str}")

    if not has_unpriced and session_price > 0:
        lines.append("")
        t_str = f"{session_price:g}$" if isinstance(session_price, float) else f"{session_price}$"
        lines.append(f"💰 Price: {t_str}")

    return "\n".join(lines)


def build_loader_package_keyboard(order_id: int, progress_data: Any, active_loader_id: Optional[int] = None) -> Optional[InlineKeyboardMarkup]:
    """
    Renders package selection toggle buttons and action buttons for the Loader Group.
    Delivered packages are removed and NEVER shown.
    Pending packages show: [ ⬜ 2400 ]
    Selected packages show: [ ☑ 2400 ] if selected by active_loader_id, or [ 🔒 2400 ] if selected by another loader.
    Bottom row: [ ✅ Confirm Delivery ] [ ❌ Cancel Selection ]
    """
    import json
    if isinstance(progress_data, str):
        try:
            items = json.loads(progress_data)
        except Exception:
            items = []
    elif isinstance(progress_data, list):
        items = progress_data
    if not items or len(items) <= 1:
        return None

    toggle_buttons = []
    for idx, item in enumerate(items):
        status = item.get("status", "Pending")
        if status == "Delivered":
            continue

        pkg_name = item.get("package", "")
        qty = item.get("qty", 1)
        qty_str = f" ×{qty}" if qty > 1 else ""

        selected_by = item.get("selected_by_loader")

        if status == "Selected":
            if active_loader_id and selected_by and selected_by != active_loader_id:
                label = f"🔒 {pkg_name}{qty_str}"
            else:
                label = f"☑ {pkg_name}{qty_str}"
        else:
            label = f"⬜ {pkg_name}{qty_str}"

        toggle_buttons.append(InlineKeyboardButton(label, callback_data=f"pkg_toggle:{order_id}:{idx}"))

    if not toggle_buttons:
        return None

    keyboard_rows = []
    row = []
    for btn in toggle_buttons:
        row.append(btn)
        if len(row) == 3:
            keyboard_rows.append(row)
            row = []
    if row:
        keyboard_rows.append(row)

    action_row = [
        InlineKeyboardButton("✅ Confirm Delivery", callback_data=f"pkg_confirm:{order_id}"),
        InlineKeyboardButton("❌ Cancel Selection", callback_data=f"pkg_cancel:{order_id}")
    ]
    keyboard_rows.append(action_row)

    return InlineKeyboardMarkup(keyboard_rows)


def toggle_package_selection(progress_data: Any, target_idx: int, loader_id: int) -> Tuple[List[Dict[str, Any]], str]:
    """
    Toggles selection state for item at target_idx.
    Returns (updated_items, status_code).
    """
    import json
    from datetime import datetime, timezone

    if isinstance(progress_data, str):
        try:
            items = json.loads(progress_data)
        except Exception:
            items = []
    elif isinstance(progress_data, list):
        items = progress_data
    else:
        items = []

    if target_idx < 0 or target_idx >= len(items):
        return items, "Invalid"

    item = items[target_idx]
    current_status = item.get("status", "Pending")
    selected_by = item.get("selected_by_loader")

    if current_status == "Delivered":
        return items, "Delivered"

    if current_status == "Selected":
        if selected_by and selected_by != loader_id:
            return items, "Locked"
        item["status"] = "Pending"
        item["selected_by_loader"] = None
        item["selected_time"] = None
        return items, "Deselected"
    else:
        item["status"] = "Selected"
        item["selected_by_loader"] = loader_id
        item["selected_time"] = datetime.now(timezone.utc).isoformat()
        return items, "Selected"


def cancel_loader_selections(progress_data: Any, loader_id: int) -> Tuple[List[Dict[str, Any]], int]:
    """
    Resets any package items selected by loader_id back to 'Pending'.
    Returns (updated_items, reset_count).
    """
    import json
    if isinstance(progress_data, str):
        try:
            items = json.loads(progress_data)
        except Exception:
            items = []
    elif isinstance(progress_data, list):
        items = progress_data
    else:
        items = []

    count = 0
    for item in items:
        if item.get("status") == "Selected" and item.get("selected_by_loader") == loader_id:
            item["status"] = "Pending"
            item["selected_by_loader"] = None
            item["selected_time"] = None
            count += 1

    return items, count


def get_loader_selected_packages(progress_data: Any, loader_id: int) -> List[Dict[str, Any]]:
    """
    Returns list of package items currently marked 'Selected' by loader_id.
    """
    import json
    if isinstance(progress_data, str):
        try:
            items = json.loads(progress_data)
        except Exception:
            items = []
    elif isinstance(progress_data, list):
        items = progress_data
    else:
        items = []

    return [it for it in items if it.get("status") == "Selected" and it.get("selected_by_loader") == loader_id]


def mark_selected_packages_delivered(progress_data: Any, loader_id: int = 0, selected_items: Optional[List[Dict[str, Any]]] = None) -> Tuple[List[Dict[str, Any]], bool, int]:
    """
    Marks selected packages as 'Delivered'.
    If selected_items is provided, matches items by package name/qty.
    Otherwise matches items with status == 'Selected'.
    If no items were selected, advances the next pending package item.
    Returns (updated_items, is_all_completed, delivered_count).
    """
    import json
    from datetime import datetime, timezone

    if isinstance(progress_data, str):
        try:
            items = json.loads(progress_data)
        except Exception:
            items = []
    elif isinstance(progress_data, list):
        items = progress_data
    else:
        items = []

    now_iso = datetime.now(timezone.utc).isoformat()
    delivered_count = 0

    target_pkg_names = []
    if selected_items:
        for s in selected_items:
            if s.get("package"):
                target_pkg_names.append(str(s.get("package")))

    if target_pkg_names:
        for item in items:
            pkg_name = str(item.get("package", ""))
            if pkg_name in target_pkg_names and item.get("status") != "Delivered":
                item["status"] = "Delivered"
                item["delivery_time"] = now_iso
                delivered_count += 1
                target_pkg_names.remove(pkg_name)
    else:
        for item in items:
            if item.get("status") == "Selected" and (loader_id == 0 or item.get("selected_by_loader") == loader_id or item.get("selected_by_loader") is None):
                item["status"] = "Delivered"
                item["delivery_time"] = now_iso
                delivered_count += 1

    # Fallback: if loader hadn't clicked toggle buttons before replying with screenshot, mark next pending package
    if delivered_count == 0:
        for item in items:
            if item.get("status") != "Delivered":
                item["status"] = "Delivered"
                item["selected_by_loader"] = loader_id
                item["delivery_time"] = now_iso
                delivered_count += 1
                break

    is_all_completed = all(it.get("status") == "Delivered" for it in items)
    return items, is_all_completed, delivered_count


def advance_package_progress(progress_data: Any) -> Tuple[Any, bool]:
    """
    Backward-compatible helper: Advances package delivery progress state by marking the next pending package item as 'Delivered'.
    Returns (updated_items, is_all_delivered).
    """
    items, all_done, _ = mark_selected_packages_delivered(progress_data, loader_id=0)
    return items, all_done


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
