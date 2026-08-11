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


PACKAGE_PRICES: Dict[str, float] = {
    "108000": 565.0,
    "96000": 505.0,
    "72000": 377.0,
    "48000": 255.0,
    "43200": 230.0,
    "38400": 212.0,
    "24000": 133.0,
    "21600": 120.0,
    "19200": 110.0,
    "16800": 96.0,
    "14400": 83.0,
    "12000": 70.0,
    "10800": 64.5,
    "9600": 56.0,
    "7200": 43.0,
    "5040": 33.0,
    "2400": 17.0,
    "880": 8.0,
    "420": 4.5,
    "80": 1.0,
}

# Backward compatibility alias
TEST_PACKAGE_PRICES = PACKAGE_PRICES


def parse_test_order_packages(order_text: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Parses complete order text to detect BOTH known and unknown packages and quantities.
    Normalizes all supported separators (+, comma, &, /, newline, spaces) into '+' before matching.
    Preserves exact order of appearance in original text.

    Returns:
        Optional[Dict[str, Any]]: Structured dictionary with 'packages' list, 'known_total' float,
        'has_unknown' bool, and 'total_price' float (if all priced), or None if no supported package is found.
    """
    if not order_text:
        return None

    # Isolate Order section first to prevent scanning entire message body (Recovery Codes, Passwords, etc.)
    target_section = extract_order_section(order_text)
    if target_section:
        raw_text = target_section.lower().strip()
    else:
        if not any(k in order_text.lower() for k in ("email:", "password:", "recovery codes:", "recovery:", "uid:", "phone:")):
            raw_text = order_text.lower().strip()
        else:
            return None

    # 1. Normalize shorthand notation and thousands formatting (by descending package length/value)
    for pkg_val in sorted(PACKAGE_PRICES.keys(), key=lambda x: (-len(x), -int(x))):
        val_int = int(pkg_val)
        fmt_comma = f"{val_int:,}"
        raw_text = re.sub(r'\b' + re.escape(fmt_comma) + r'\b', pkg_val, raw_text)

        if val_int >= 1000:
            k_val = val_int / 1000.0
            k_str = f"{k_val:g}k"
            raw_text = re.sub(r'\b' + re.escape(k_str) + r'\b', pkg_val, raw_text)

    # 2. Normalize spaces around quantity multipliers (e.g. '2400 x 2' -> '2400x2', '2 x 2400' -> '2x2400')
    raw_text = re.sub(r'\s*([*xX×])\s*', r'\1', raw_text)

    # 3. Normalize ALL supported package separators (+, ,, &, /, newline, remaining whitespace) into '+'
    raw_text = re.sub(r'[,&/\n\r+|]+', '+', raw_text)
    raw_text = re.sub(r'\s+', '+', raw_text)

    segments = [s.strip() for s in raw_text.split('+') if s.strip()]

    detected = []
    known_total = 0.0
    has_unknown = False

    segment_regex = re.compile(
        r'^(?:'
        r'(?P<num1>\d+)(?:cp)?[*xX×](?P<num2>\d+)(?:cp)?'
        r'|'
        r'(?P<pkg_standalone>\d+)(?:cp)?'
        r')$',
        re.IGNORECASE
    )

    for seg in segments:
        match = segment_regex.match(seg)
        if not match:
            continue

        gd = match.groupdict()
        qty = 1
        pkg = None

        if gd.get("pkg_standalone"):
            pkg = gd["pkg_standalone"]
            qty = 1
        elif gd.get("num1") and gd.get("num2"):
            n1 = int(gd["num1"])
            n2 = int(gd["num2"])

            if str(n2) in TEST_PACKAGE_PRICES:
                pkg = str(n2)
                qty = n1
            elif str(n1) in TEST_PACKAGE_PRICES:
                pkg = str(n1)
                qty = n2
            else:
                if n1 >= n2:
                    pkg = str(n1)
                    qty = n2
                else:
                    pkg = str(n2)
                    qty = n1

        if not pkg:
            continue

        unit_price = TEST_PACKAGE_PRICES.get(pkg)
        is_known = (unit_price is not None)

        if is_known:
            item_total = unit_price * qty
            known_total += item_total
            detected.append({
                "package": pkg,
                "qty": qty,
                "known": True,
                "unit_price": unit_price,
                "total": item_total,
                "status": "Pending"
            })
        else:
            has_cp = bool(re.search(r'cp', seg, re.IGNORECASE))
            pkg_int = int(pkg)
            if not has_cp and pkg_int < 400:
                continue

            has_unknown = True
            detected.append({
                "package": pkg,
                "qty": qty,
                "known": False,
                "unit_price": None,
                "total": None,
                "status": "Unpriced"
            })

    if detected:
        return {
            "packages": detected,
            "known_total": round(known_total, 2),
            "has_unknown": has_unknown,
            "total_price": round(known_total, 2) if not has_unknown else None
        }

    return None


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
    Returns an inline keyboard with '📝 Add Unknown Package Price' button if any unpriced package exists.
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

    for item in items:
        if item.get("status") == "Unpriced" or item.get("unit_price") is None:
            pkg_name = item.get("package", "")
            return InlineKeyboardMarkup([
                [InlineKeyboardButton(f"📝 Add Price for {pkg_name}", callback_data=f"add_unk_price:{order_id}:{pkg_name}")]
            ])
    return None


def update_unknown_package_price(progress_data: Any, target_pkg: str, price_val: float) -> Tuple[List[Dict[str, Any]], float, bool]:
    """
    Updates the price for a specific unknown package item in progress_data.
    Returns (updated_items, new_total_price, has_remaining_unpriced).
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

    for item in items:
        if str(item.get("package")) == str(target_pkg) and (item.get("status") == "Unpriced" or item.get("unit_price") is None):
            item["unit_price"] = price_val
            item["total"] = price_val * item.get("qty", 1)
            item["status"] = "Pending"
            break

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


def format_loader_card_summary(progress_data: Any, total_price: Optional[float] = None) -> str:
    """
    Formats the Loader Group order card text showing:
    - Delivered packages with ✅
    - Selected packages with ☑
    - Pending packages with ⬜
    - Remaining packages list if partial delivery
    - '🎉 Order Completed' when all packages are delivered
    - Total price
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

    title = "📦 Package" if len(items) == 1 else "📦 Package(s)"
    lines = [title, ""]

    all_delivered = True
    calc_total = 0.0
    has_unpriced = False
    remaining_packages = []

    for item in items:
        pkg_name = item.get("package", "")
        qty = item.get("qty", 1)
        status = item.get("status", "Pending")
        unit_price = item.get("unit_price")

        qty_str = f" ×{qty}" if qty > 1 else ""
        pkg_display = f"{pkg_name} CP{qty_str}"

        if status == "Delivered":
            checkbox = "✅"
        elif status == "Selected":
            checkbox = "☑"
            all_delivered = False
            remaining_packages.append(pkg_display)
        else: # Pending or Unpriced
            checkbox = "⬜"
            all_delivered = False
            remaining_packages.append(pkg_display)

        if status == "Unpriced" or unit_price is None:
            has_unpriced = True
            lines.append(f"❓ {pkg_display}")
        else:
            item_total = unit_price * qty
            calc_total += item_total
            lines.append(f"{checkbox} {pkg_display}")

    if all_delivered and not has_unpriced:
        lines.append("")
        lines.append("🎉 Order Completed")
    elif remaining_packages and len(remaining_packages) < len(items):
        lines.append("")
        lines.append(f"Remaining Packages: {', '.join(remaining_packages)}")

    final_total = total_price if (total_price is not None and not has_unpriced) else calc_total
    total_str = f"{final_total:g}$" if isinstance(final_total, float) else f"{final_total}$"

    lines.append("")
    if has_unpriced:
        lines.append(f"💰 Known Total: {total_str}")
    else:
        lines.append(f"💰 Total Price: {total_str}")

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
    else:
        items = []

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


def mark_selected_packages_delivered(progress_data: Any, loader_id: int) -> Tuple[List[Dict[str, Any]], bool, int]:
    """
    Marks all packages currently selected by loader_id as 'Delivered'.
    If no packages were explicitly selected by loader_id, advances the next pending package.
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

    for item in items:
        if item.get("status") == "Selected" and item.get("selected_by_loader") == loader_id:
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
