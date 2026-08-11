"""
Telegram Update Handlers for Telegram Email Image Delivery Bot.
Implements Two-Group Reply-Based Workflow, Privacy Protection (Exact Customer Message Copy without metadata),
Keyword-Based Order Detection (keywords.py), Caption Email Overrides, Wrong Details Workflow,
Duplicate Order Confirmation (Place Again / Cancel Inline Buttons), Edited Message Handling,
Ignore Super Admin & Delivery User Messages in Client Group, Silent Non-Reply/Unmatched Reply Handling in Loader Group,
Group Category Routing System (v1.2: Category A vs Category B with fixed Payment Review Group -1004441603990),
Multi-Loader Interactive Category B Approval System (/loaderadd, /loaderlist, /loaderremove, Accept/Reject buttons),
Category A Only Price Workflow in Client Group (Single Active Prompt, Strict Reply-Based Entry, Validation, '💰 Price: 15.5' / '💰 Price Updated: 30' new calculator messages),
Role-Based User Management (/user, /users), Telegram Reactions, and Admin Commands.
Utilizes global BOT_SETTINGS, AUTH_USERS_CACHE, CLIENT_GROUPS_CACHE, and LOADERS_CACHE for zero-database-query filtering.
Includes structured logging tags ([CLIENT], [LOADER], [DELIVERY], [REACTION], [DETECTOR], [SOURCE], [DELIVERY_GROUP], [AUTH], [CATEGORY], [PAYMENT], [LOADER_MGMT], [PRICE]).
"""

import io
import re
import os
import sys
import html
import shutil
import logging
from datetime import datetime, timezone
from typing import Dict, Any
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
from telegram.ext import ContextTypes
from telegram.error import TelegramError
from sqlalchemy import update as update_sql, select

from config import Config
from keywords import contains_order_keyword
from email_parser import extract_email, extract_order_id, extract_package, extract_last_email
from media_collector import media_collector, user_session_manager
from delivery import deliver_order_by_id, deliver_images_for_email
from database import (
    BOT_SETTINGS,
    AUTH_USERS_CACHE,
    CLIENT_GROUPS_CACHE,
    LOADERS_CACHE,
    AsyncSessionLocal,
    get_current_settings,
    update_source_group,
    update_delivery_group,
    update_payment_review_group,
    set_client_group_category,
    remove_client_group_category,
    get_client_group_category,
    update_order_status,
    set_order_price_prompt,
    update_order_price,
    remove_source_group,
    remove_delivery_group,
    reset_groups,
    create_order,
    set_order_loader_message_id,
    get_order_by_id,
    get_pending_order_by_email,
    get_exact_duplicate_pending_order,
    get_order_by_loader_msg_id,
    get_pending_orders,
    get_delivered_orders,
    get_all_orders_by_email,
    delete_orders_by_email,
    cancel_order,
    get_detailed_stats,
    export_orders_to_csv,
    get_db_file_path,
    dispose_engine,
    init_db,
    add_authorized_user,
    remove_authorized_user,
    get_all_authorized_users,
    add_loader,
    remove_loader_by_id,
    get_all_loaders,
    update_order_price,
    update_order_issue_state
)
from models import Order
from utils import (
    check_admin_permission,
    is_admin,
    is_super_admin,
    is_delivery_user,
    safe_set_message_reaction,
    get_uptime_str,
    get_memory_usage_mb,
    is_railway_environment,
    get_db_type_name,
    get_test_price,
    calculate_test_price,
    parse_test_order_packages,
    LoaderIssueType,
    LOADER_ISSUE_CONFIG
)
import json

logger = logging.getLogger(__name__)

# Temporary memory state for interactive /loaderadd step-by-step wizard (user_id -> session dict)
LOADER_ADD_SESSION: Dict[int, Dict[str, Any]] = {}

# Temporary memory state for interactive price input workflow (order_id -> session dict)
PRICE_INPUT_SESSION: Dict[int, Dict[str, Any]] = {}


def is_valid_price_string(text: str) -> bool:
    """Validates price string: exact numeric integers or decimals only (e.g. 15, 15.5, 2500, 2999.99)."""
    return bool(re.match(r'^\d+(\.\d+)?$', text.strip()))


# ==========================================
# Two-Group Workflow Message Handlers
# ==========================================

async def source_group_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Monitors messages in Group 1 (Client Group).
    Validates group ID strictly using in-memory BOT_SETTINGS and CLIENT_GROUPS_CACHE without querying database.
    Ignores messages sent by Super Admins and Delivery Users.
    Routes orders according to Group Category:
    - Category A (Trusted Groups): Directly forwards to Loader Group.
    - Category B (Payment Required Groups): Routes to Payment Review Group (-1004441603990) for Accept / Reject inline buttons.
    """
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat:
        return

    # Check against in-memory BOT_SETTINGS and CLIENT_GROUPS_CACHE (Zero DB SELECT query)
    is_client_group = (chat.id == BOT_SETTINGS["source_group_id"]) or (chat.id in CLIENT_GROUPS_CACHE)

    if not is_client_group:
        logger.warning(f"[CLIENT] Client Group is not configured yet. Ignored message in chat {chat.id} ({chat.title}).")
        return

    # Ignore Super Admin & Delivery User Messages in Client Group
    user_id = user.id if user else None
    if user_id:
        if is_super_admin(user_id):
            logger.info(f"[CLIENT] Ignored Super Admin message. User ID: {user_id}")
            return
        if is_delivery_user(user_id):
            logger.info(f"[CLIENT] Ignored Delivery User message. User ID: {user_id}")
            return

    text_content = message.text or message.caption or ""
    if not text_content:
        logger.debug(f"[CLIENT] Message {message.message_id} in Client Group has no text/caption content.")
        return

    # Keyword-Based Order Detection
    matched, keyword = contains_order_keyword(text_content)
    if not matched:
        logger.info("[DETECTOR] No keyword found. Message ignored.")
        return

    logger.info(f"[DETECTOR] Keyword matched: {keyword}")

    email = extract_email(text_content) or f"order_{message.message_id}@customer.com"
    package_desc = extract_package(text_content)

    # Determine Group Category ('A' or 'B')
    category = CLIENT_GROUPS_CACHE.get(chat.id, "A")

    # Check Duplicate Pending Order - Strict Content Deduplication (No False Positives)
    # Only triggers when all important fields (Package, UID, Email, Username, Password, etc.) are 100% identical
    existing_pending = await get_exact_duplicate_pending_order(email, text_content)
    if existing_pending:
        logger.info(f"[CLIENT] Exact duplicate pending order detected for email '{email}'. Prompting customer in Client Group.")
        dup_order = await create_order(
            email=email,
            client_chat_id=chat.id,
            original_message_id=message.message_id,
            package=package_desc,
            status="Duplicate_Pending",
            category=category,
            raw_text=text_content
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Place Again", callback_data=f"dup_confirm:{dup_order.id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"dup_cancel:{dup_order.id}")
            ]
        ])
        warning_msg = (
            "⚠️ <b>Duplicate Order Detected</b>\n\n"
            "Would you like to place this order again, or was it sent by mistake?"
        )
        try:
            await message.reply_text(warning_msg, reply_markup=keyboard, parse_mode="HTML")
        except Exception as e:
            logger.exception(f"[CLIENT] Failed to send duplicate order prompt: {e}")
        return

    # Add 👍 reaction to ORIGINAL customer order message in Client Group
    reacted = await safe_set_message_reaction(
        bot=context.bot,
        chat_id=chat.id,
        message_id=message.message_id,
        emoji="👍",
        fallback_emoji=None,
        log_tag="[REACTION]"
    )
    if reacted:
        logger.info("[REACTION] 👍 Order received")
    else:
        logger.warning("Reaction not supported.")

    if category == "B":
        # Category B Workflow: Forward to Payment Review Group (-1004441603990) with Accept / Reject buttons
        order = await create_order(
            email=email,
            client_chat_id=chat.id,
            original_message_id=message.message_id,
            package=package_desc,
            status="Pending Approval",
            category="B",
            raw_text=text_content
        )

        payment_group_id = BOT_SETTINGS["payment_review_group_id"] or Config.PAYMENT_REVIEW_GROUP_ID
        if payment_group_id:
            try:
                try:
                    await context.bot.copy_message(
                        chat_id=payment_group_id,
                        from_chat_id=chat.id,
                        message_id=message.message_id
                    )
                except Exception as e_copy:
                    logger.exception(f"[PAYMENT] copy_message failed: {e_copy}")

                group_title = chat.title or "Client Group"
                card_msg = (
                    f"🟨 <b>NEW ORDER</b>\n\n"
                    f"<b>Order ID:</b> #{order.id}\n\n"
                    f"<b>Email:</b>\n{html.escape(order.email)}\n\n"
                    f"<b>Group:</b>\n{html.escape(group_title)}\n\n"
                    f"Choose an action."
                )
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ Accept", callback_data=f"catb_accept:{order.id}"),
                        InlineKeyboardButton("❌ Reject", callback_data=f"catb_reject:{order.id}")
                    ]
                ])
                await context.bot.send_message(
                    chat_id=payment_group_id,
                    text=card_msg,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
                logger.info(f"[PAYMENT] Order #{order.id} routed to Payment Review Group (-1004441603990).")
            except Exception as e:
                logger.exception(f"[PAYMENT] Failed to route Order #{order.id} to Payment Review Group: {e}")
        else:
            logger.warning(f"[PAYMENT] Order #{order.id} registered as Category B, but Payment Review Group is not configured yet!")

    else:
        # Parse initial package progress
        parsed_init_pkg = parse_test_order_packages(text_content)
        init_progress_json = None
        if parsed_init_pkg:
            init_progress_items = [
                {"package": item["package"], "qty": item["qty"], "unit_price": item["unit_price"], "status": "Pending"}
                for item in parsed_init_pkg["packages"]
            ]
            init_progress_json = json.dumps(init_progress_items)

        # Category A Workflow: Forward directly to Loader Group, set status 'Pending'
        order = await create_order(
            email=email,
            client_chat_id=chat.id,
            original_message_id=message.message_id,
            package=package_desc,
            status="Pending",
            category="A",
            raw_text=text_content,
            package_progress=init_progress_json
        )

        # Auto-detect test price for supported packages and quantities
        if parsed_init_pkg:
            test_price_val = parsed_init_pkg["total_price"]
            init_price_str = f"{test_price_val:g}"
            await update_order_price(order.id, price_str=init_price_str)

        loader_group_id = BOT_SETTINGS["delivery_group_id"]
        if loader_group_id:
            try:
                try:
                    forwarded_msg = await context.bot.copy_message(
                        chat_id=loader_group_id,
                        from_chat_id=chat.id,
                        message_id=message.message_id
                    )
                except Exception as e_copy:
                    logger.exception(f"copy_message failed: {e_copy}. Fallback to raw text send_message.")
                    forwarded_msg = await context.bot.send_message(
                        chat_id=loader_group_id,
                        text=text_content
                    )

                await set_order_loader_message_id(order.id, forwarded_msg.message_id, loader_group_id=loader_group_id)
                logger.info(f"[CLIENT] Order copied to Loader Group {loader_group_id} (Order #{order.id}, Loader Msg ID: {forwarded_msg.message_id})")
                logger.info("[DETECTOR] Order forwarded.")
            except Exception as e:
                logger.exception(f"[CLIENT] Failed to post Order #{order.id} to Loader Group {loader_group_id}: {e}")
        else:
            logger.warning(f"[CLIENT] Order #{order.id} registered, but Loader Group is not configured yet!")


async def edited_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Monitors edited messages in Group 1 (Client Group).
    When a normal customer edits their order message, informs the customer that the order will be placed manually.
    Ignores edits from Super Admins and Delivery Users.
    """
    message = update.edited_message or update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat:
        return

    is_client_group = (chat.id == BOT_SETTINGS["source_group_id"]) or (chat.id in CLIENT_GROUPS_CACHE)
    if not is_client_group:
        return

    user_id = user.id if user else None
    if user_id and (is_super_admin(user_id) or is_delivery_user(user_id)):
        return

    logger.info(f"[CLIENT] Customer edited message {message.message_id} in Client Group {chat.id}.")

    reply_text = "This order will be placed again manually wait for team"
    try:
        await message.reply_text(reply_text, reply_to_message_id=message.message_id)
        logger.info(f"[CLIENT] Sent manual placement notice to customer for edited message {message.message_id}.")
    except Exception as e:
        logger.exception(f"[CLIENT] Failed to send manual placement notice for edited message: {e}")


async def duplicate_order_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles inline keyboard button callbacks for duplicate order confirmation (Place Again / Cancel).
    Executing 'Place Again' creates a brand new Order in database, generates a new Order ID,
    detects Group Category (A vs B), forwards order, saves loader_message_id, and edits warning message.
    """
    query = update.callback_query
    if not query or not query.data:
        return

    try:
        await query.answer()
    except Exception as e:
        logger.exception(f"[CLIENT] Failed to answer callback query: {e}")

    data = query.data
    if not data.startswith(("dup_confirm:", "dup_cancel:")):
        return

    action, order_id_str = data.split(":", 1)
    if not order_id_str.isdigit():
        return

    order_id = int(order_id_str)
    dup_order_record = await get_order_by_id(order_id)

    if not dup_order_record:
        try:
            await query.edit_message_text("❌ Order record not found.")
        except Exception as e:
            logger.exception(f"[CLIENT] Failed to edit message: {e}")
        return

    client_chat_id = dup_order_record.client_chat_id
    original_msg_id = dup_order_record.original_message_id
    email = dup_order_record.email
    package_desc = dup_order_record.package or ""

    if action == "dup_confirm":
        # Customer pressed ✅ Place Again - Execute exact workflow of a brand new order
        logger.info(f"[CLIENT] Customer pressed 'Place Again' for email '{email}'. Creating new Order...")

        # Determine Group Category ('A' or 'B')
        category = CLIENT_GROUPS_CACHE.get(client_chat_id, "A") if client_chat_id else "A"

        if category == "B":
            # Create brand new Order with status 'Pending Approval'
            new_order = await create_order(
                email=email,
                client_chat_id=client_chat_id,
                original_message_id=original_msg_id,
                package=package_desc,
                status="Pending Approval",
                category="B"
            )
            payment_group_id = BOT_SETTINGS["payment_review_group_id"] or Config.PAYMENT_REVIEW_GROUP_ID
            if payment_group_id and client_chat_id and original_msg_id:
                try:
                    try:
                        await context.bot.copy_message(
                            chat_id=payment_group_id,
                            from_chat_id=client_chat_id,
                            message_id=original_msg_id
                        )
                    except Exception as e_copy:
                        logger.exception(f"[PAYMENT] copy_message failed for Place Again order: {e_copy}")

                    card_msg = (
                        f"🟨 <b>NEW ORDER</b>\n\n"
                        f"<b>Order ID:</b> #{new_order.id}\n\n"
                        f"<b>Email:</b>\n{html.escape(new_order.email)}\n\n"
                        f"<b>Group:</b>\nClient Group\n\n"
                        f"Choose an action."
                    )
                    keyboard = InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("✅ Accept", callback_data=f"catb_accept:{new_order.id}"),
                            InlineKeyboardButton("❌ Reject", callback_data=f"catb_reject:{new_order.id}")
                        ]
                    ])
                    await context.bot.send_message(
                        chat_id=payment_group_id,
                        text=card_msg,
                        reply_markup=keyboard,
                        parse_mode="HTML"
                    )
                    logger.info(f"[PAYMENT] New Order #{new_order.id} (Place Again) routed to Payment Review Group.")
                except Exception as e:
                    logger.exception(f"[PAYMENT] Failed to route Place Again Order #{new_order.id} to Payment Review Group: {e}")

        else:
            # Category A: Create brand new Order with status 'Pending'
            new_order = await create_order(
                email=email,
                client_chat_id=client_chat_id,
                original_message_id=original_msg_id,
                package=package_desc,
                status="Pending",
                category="A"
            )
            loader_group_id = BOT_SETTINGS["delivery_group_id"]
            if loader_group_id and client_chat_id and original_msg_id:
                try:
                    forwarded_msg = await context.bot.copy_message(
                        chat_id=loader_group_id,
                        from_chat_id=client_chat_id,
                        message_id=original_msg_id
                    )
                    await set_order_loader_message_id(new_order.id, forwarded_msg.message_id, loader_group_id=loader_group_id)
                    logger.info(f"[CLIENT] New Order #{new_order.id} (Place Again) copied to Loader Group {loader_group_id} (Loader Msg ID: {forwarded_msg.message_id}).")
                except Exception as e:
                    logger.exception(f"[CLIENT] Failed to copy Place Again Order #{new_order.id} to Loader Group: {e}")

        # Add 👍 reaction to original customer order message
        if client_chat_id and original_msg_id:
            await safe_set_message_reaction(
                bot=context.bot,
                chat_id=client_chat_id,
                message_id=original_msg_id,
                emoji="👍",
                fallback_emoji=None,
                log_tag="[REACTION]"
            )

        # Edit duplicate message as required by spec:
        # ✅ New Order Created
        # Order #xxx
        edit_text = (
            f"✅ <b>New Order Created</b>\n"
            f"Order #{new_order.id}"
        )
        try:
            await query.edit_message_text(edit_text, parse_mode="HTML")
        except Exception as e:
            logger.exception(f"[CLIENT] Failed to edit duplicate message text: {e}")

    elif action == "dup_cancel":
        # Customer pressed ❌ Cancel
        await cancel_order(order_id)
        logger.info(f"[CLIENT] Duplicate Order #{order_id} cancelled by customer.")
        try:
            await query.edit_message_text("❌ Order cancelled.", parse_mode="HTML")
        except Exception as e:
            logger.exception(f"[CLIENT] Failed to edit cancelled message text: {e}")


# ==========================================
# Loader Review & Customer Confirmation Workflow Handlers
# ==========================================

async def loader_issue_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles Loader Issue reporting buttons (Wrong Name, Wrong Account, Login Failed, 2FA Problem, Need Confirmation).
    Callback data format: loader_issue:<issue_type>:<order_id>
    Sends prompt to customer in Client Group replying to order.original_message_id.
    """
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("loader_issue:"):
        return

    user = update.effective_user
    if not user or not (is_admin(user.id) or is_delivery_user(user.id)):
        try:
            await query.answer("⛔ Only authorized loaders can report issues.", show_alert=True)
        except Exception:
            pass
        return

    parts = query.data.split(":")
    if len(parts) < 3 or not parts[2].isdigit():
        return

    issue_type_str = parts[1]
    order_id = int(parts[2])

    order = await get_order_by_id(order_id)
    if not order or not order.client_chat_id or not order.original_message_id:
        try:
            await query.answer("❌ Order or client chat information not found.", show_alert=True)
        except Exception:
            pass
        return

    issue_cfg = LOADER_ISSUE_CONFIG.get(issue_type_str)
    if not issue_cfg:
        try:
            await query.answer("❌ Unknown issue type.", show_alert=True)
        except Exception:
            pass
        return

    # Update Order issue state in DB
    await update_order_issue_state(order.id, "Waiting_Customer_Confirmation", issue_type_str)

    # Prepare customer buttons in Client Group linked to unique Order ID
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, Correct", callback_data=f"cust_confirm:yes:{order.id}:{issue_type_str}"),
            InlineKeyboardButton("❌ No, Wrong", callback_data=f"cust_confirm:no:{order.id}:{issue_type_str}")
        ]
    ])

    try:
        await context.bot.send_message(
            chat_id=order.client_chat_id,
            text=issue_cfg["customer_text"],
            reply_to_message_id=order.original_message_id,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await query.answer("⚠️ Customer notified! Awaiting response.")
        logger.info(f"[LOADER_ISSUE] Order #{order.id} | Loader reported issue '{issue_type_str}'. Customer notified in Client Group {order.client_chat_id}.")
    except Exception as e:
        logger.exception(f"[LOADER_ISSUE] Failed to send customer issue notification for Order #{order.id}: {e}")
        try:
            await query.answer("❌ Failed to send notification to customer.", show_alert=True)
        except Exception:
            pass


async def customer_confirmation_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles Customer confirmation response (Yes / No) in Client Group.
    Callback data format: cust_confirm:<yes/no>:<order_id>:<issue_type>
    Updates Order issue_state and replies to original loader message in Loader Group.
    """
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("cust_confirm:"):
        return

    parts = query.data.split(":")
    if len(parts) < 4 or not parts[2].isdigit():
        return

    action = parts[1]
    order_id = int(parts[2])
    issue_type_str = parts[3]

    order = await get_order_by_id(order_id)
    if not order:
        try:
            await query.answer("❌ Order record not found.", show_alert=True)
        except Exception:
            pass
        return

    issue_cfg = LOADER_ISSUE_CONFIG.get(issue_type_str, LOADER_ISSUE_CONFIG[LoaderIssueType.WRONG_NAME])

    if action == "yes":
        new_state = "Confirmed"
        cust_ack = "✅ <b>Thank you!</b> Your confirmation has been recorded and sent to the loader."
        loader_notify_text = issue_cfg["loader_yes_text"]
        logger.info(f"[CUSTOMER_CONFIRM] Order #{order.id} | Customer confirmed account details as CORRECT for issue '{issue_type_str}'.")
    else:
        new_state = "Rejected"
        cust_ack = "❌ <b>Thank you!</b> Your response has been recorded. Please wait for updated account instructions."
        loader_notify_text = issue_cfg["loader_no_text"]
        logger.info(f"[CUSTOMER_CONFIRM] Order #{order.id} | Customer reported account details as INCORRECT for issue '{issue_type_str}'.")

    # Update DB state
    await update_order_issue_state(order.id, new_state, issue_type_str)

    # 1. Edit customer message in Client Group
    try:
        await query.edit_message_text(cust_ack, parse_mode="HTML")
        await query.answer("Response recorded!")
    except Exception as e:
        logger.warning(f"[CUSTOMER_CONFIRM] Could not edit customer reply text: {e}")

    # 2. Reply directly to ORIGINAL loader message in Loader Group
    loader_chat_id = order.loader_group_id or BOT_SETTINGS["delivery_group_id"]
    target_loader_msg_id = order.loader_message_id

    if loader_chat_id and target_loader_msg_id:
        try:
            await context.bot.send_message(
                chat_id=loader_chat_id,
                text=loader_notify_text,
                reply_to_message_id=target_loader_msg_id,
                parse_mode="HTML"
            )
            logger.info(f"[CUSTOMER_CONFIRM] Sent confirmation reply to Loader Group {loader_chat_id} (Loader Msg ID {target_loader_msg_id}) for Order #{order.id}.")
        except Exception as e:
            logger.exception(f"[CUSTOMER_CONFIRM] Failed to notify loader group for Order #{order.id}: {e}")


async def redeliver_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles loader redelivery confirmation buttons ('✅ Deliver Again' / '❌ Cancel').
    Callback data format: redeliver_yes:<order_id>:<reply_msg_id> or redeliver_cancel:<order_id>
    """
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("redeliver_"):
        return

    user = update.effective_user
    if not user or not (is_admin(user.id) or is_delivery_user(user.id)):
        try:
            await query.answer("⛔ Only authorized loaders can perform delivery.", show_alert=True)
        except Exception:
            pass
        return

    parts = query.data.split(":")
    action = parts[0]

    if len(parts) < 2 or not parts[1].isdigit():
        return

    order_id = int(parts[1])
    order = await get_order_by_id(order_id)
    if not order:
        try:
            await query.answer("❌ Order not found.", show_alert=True)
        except Exception:
            pass
        return

    if action == "redeliver_cancel":
        try:
            await query.edit_message_text(f"❌ Repeated delivery for Order #{order.id} was cancelled.")
            await query.answer("Cancelled")
        except Exception:
            pass
        return

    elif action == "redeliver_yes":
        if len(parts) < 3 or not parts[2].isdigit():
            return
        reply_msg_id = int(parts[2])

        logger.info(f"[DELIVERY] Repeated delivery initiated by loader {user.id} for Order #{order.id}.")
        try:
            await query.edit_message_text(f"⏳ Executing repeated delivery for Order #{order.id}...")
        except Exception:
            pass

        # Execute delivery via deliver_order_by_id
        from delivery import deliver_order_by_id
        client_chat_id = order.client_chat_id or BOT_SETTINGS["source_group_id"]
        loader_group_id = order.loader_group_id or BOT_SETTINGS["delivery_group_id"]

        success = await deliver_order_by_id(
            bot=context.bot,
            order_id=order.id,
            client_chat_id=client_chat_id,
            loader_group_id=loader_group_id,
            loader_reply_msg_id=reply_msg_id,
            caption_text=None
        )

        if success:
            try:
                await query.edit_message_text(f"✅ Repeated delivery for Order #{order.id} completed successfully!")
                await query.answer("Repeated delivery completed!")
            except Exception:
                pass
        else:
            try:
                await query.edit_message_text(f"❌ Failed to execute repeated delivery for Order #{order.id}.")
                await query.answer("Delivery failed", show_alert=True)
            except Exception:
                pass


# ==========================================
# Category A Only Price Workflow Handlers (CLIENT GROUP strictly)
# ==========================================

async def price_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles '💰 Price', quick price buttons, custom price ForceReply, and '✏️ Edit Price' callbacks in Client Group.
    """
    query = update.callback_query
    if not query or not query.data or not query.data.startswith(("price_set:", "price_edit:", "price_val:", "price_custom:")):
        return

    user = update.effective_user
    if not user or not (is_admin(user.id) or is_delivery_user(user.id)):
        try:
            await query.answer("⛔ Only authorized admins can set price.", show_alert=True)
        except Exception:
            pass
        return

    data = query.data
    parts = data.split(":")
    action = parts[0]

    if len(parts) < 2 or not parts[1].isdigit():
        return

    order_id = int(parts[1])
    order = await get_order_by_id(order_id)
    if not order or not order.client_chat_id:
        try:
            await query.answer("❌ Order not found.", show_alert=True)
        except Exception:
            pass
        return

    category = order.category or CLIENT_GROUPS_CACHE.get(order.client_chat_id, "A")
    if category != "A":
        try:
            await query.answer("⚠️ Price workflow is available only for Category A orders.", show_alert=True)
        except Exception:
            pass
        return

    if action in ("price_set", "price_edit"):
        # Show quick numeric price buttons + Custom button
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("10", callback_data=f"price_val:{order.id}:10"),
                InlineKeyboardButton("15", callback_data=f"price_val:{order.id}:15"),
                InlineKeyboardButton("20", callback_data=f"price_val:{order.id}:20"),
                InlineKeyboardButton("25", callback_data=f"price_val:{order.id}:25"),
            ],
            [
                InlineKeyboardButton("30", callback_data=f"price_val:{order.id}:30"),
                InlineKeyboardButton("50", callback_data=f"price_val:{order.id}:50"),
                InlineKeyboardButton("100", callback_data=f"price_val:{order.id}:100"),
                InlineKeyboardButton("⌨️ Custom", callback_data=f"price_custom:{order.id}"),
            ]
        ])
        try:
            await query.message.edit_reply_markup(reply_markup=keyboard)
            await query.answer("Select price or choose Custom")
        except Exception as e:
            logger.warning(f"[PRICE] Could not edit reply markup for price selection: {e}")
        return

    elif action == "price_val":
        if len(parts) < 3:
            return
        price_value = parts[2]
        new_price_text = f"💰 Price: {price_value}"
        try:
            await query.message.edit_text(text=new_price_text, reply_markup=None)
            await update_order_price(order.id, price_value, price_msg_id=query.message.message_id)
            await query.answer(f"✅ Price set to {price_value}")
            logger.info(f"[PRICE]\nOrder #{order.id}\nPrice set to {price_value} via quick button.")
        except Exception as e:
            logger.exception(f"[PRICE] Failed to update price via quick button: {e}")
        return

    elif action == "price_custom":
        active_session = PRICE_INPUT_SESSION.get(order.id)
        is_waiting = bool(active_session or order.price_prompt_msg_id)
        if is_waiting and active_session:
            try:
                await query.answer("⚠️ Please enter the price in the opened reply box.", show_alert=True)
            except Exception:
                pass
            return

        try:
            prompt_msg = await context.bot.send_message(
                chat_id=order.client_chat_id,
                text="Enter order price (e.g. 20):",
                reply_to_message_id=query.message.message_id,
                reply_markup=ForceReply(selective=True)
            )
            await set_order_price_prompt(order.id, prompt_msg.message_id)
            PRICE_INPUT_SESSION[order.id] = {
                "order_id": order.id,
                "chat_id": order.client_chat_id,
                "prompt_msg_id": prompt_msg.message_id,
                "button_msg_id": query.message.message_id,
                "is_edit": bool(order.price),
                "created_at": datetime.now(timezone.utc)
            }
            await query.answer("Reply input opened!")
            logger.info(f"[PRICE] Prompted admin for Order #{order.id} custom price with ForceReply (Prompt Msg ID: {prompt_msg.message_id}).")
        except Exception as e:
            logger.exception(f"[PRICE] Failed to send price custom prompt: {e}")


async def price_input_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles admin text input for Category A Price setting and editing in Client Group.
    Enforces Bug 2 Fix: Entry MUST be a reply to the bot's prompt message ('Enter order price:' or 'Enter new price:').
    Ignores unreplied text or non-matching replies completely.
    """
    user = update.effective_user
    message = update.effective_message
    chat = update.effective_chat

    reply_to = message.reply_to_message
    reply_msg_id = reply_to.message_id
    rep_text = reply_to.text or reply_to.caption or ""

    # 1. Match reply_to.message_id against active price prompt message ID
    target_order_id = None
    session = None

    for oid, sess in list(PRICE_INPUT_SESSION.items()):
        if sess.get("prompt_msg_id") == reply_msg_id or sess.get("button_msg_id") == reply_msg_id:
            target_order_id = oid
            session = sess
            break

    # DB fallback check if session lost after bot restart
    if not target_order_id:
        async with AsyncSessionLocal() as db_sess:
            stmt = select(Order).where(
                (Order.price_prompt_msg_id == reply_msg_id) | (Order.price_msg_id == reply_msg_id)
            )
            res = await db_sess.execute(stmt)
            matched_order = res.scalar_one_or_none()
            if matched_order:
                target_order_id = matched_order.id
                session = {
                    "order_id": matched_order.id,
                    "chat_id": matched_order.client_chat_id,
                    "prompt_msg_id": reply_msg_id,
                    "button_msg_id": matched_order.price_msg_id,
                    "is_edit": bool(matched_order.price)
                }

    # Extra fallback: If reply_to text contains prompt phrases ("Enter order price" / "Click below to set price")
    if not target_order_id and ("Enter order price" in rep_text or "Enter new price" in rep_text or "Click below to set price" in rep_text or "💰 Price" in rep_text):
        async with AsyncSessionLocal() as db_sess:
            stmt = select(Order).where(Order.client_chat_id == chat.id).order_by(Order.created_at.desc())
            res = await db_sess.execute(stmt)
            latest_order = res.scalars().first()
            if latest_order:
                target_order_id = latest_order.id
                session = {
                    "order_id": latest_order.id,
                    "chat_id": chat.id,
                    "prompt_msg_id": reply_msg_id,
                    "button_msg_id": latest_order.price_msg_id,
                    "is_edit": bool(latest_order.price)
                }

    # Rule: If reply_to is NOT a price prompt message, ignore completely
    if not target_order_id or not session:
        return

    text = (message.text or "").strip()

    # Handle cancel command
    if text.startswith("/") or text.lower() in ("cancel", "exit"):
        PRICE_INPUT_SESSION.pop(target_order_id, None)
        if text.lower() in ("cancel", "exit"):
            await message.reply_text("❌ Price input cancelled.")
        return

    # Validate price format (e.g. 15, 15.5, 2500, 2999.99; reject abc, 15rs, price 20)
    if not is_valid_price_string(text):
        await message.reply_text("❌ Invalid price.\nPlease enter numbers only.")
        return

    order = await get_order_by_id(target_order_id)
    if not order:
        PRICE_INPUT_SESSION.pop(target_order_id, None)
        await message.reply_text(f"❌ Order #{target_order_id} not found.")
        return

    is_edit = session.get("is_edit", False) or bool(order.price)

    # 2. Save order.price = text
    # 3. Update the price message in-place (remove button, show added price)
    new_price_text = f"💰 Price: {text}"
    action_str = "updated to" if is_edit else "set to"

    target_msg_id = session.get("button_msg_id") or order.price_msg_id
    price_edited = False

    if target_msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=session["chat_id"],
                message_id=target_msg_id,
                text=new_price_text,
                reply_markup=None
            )
            logger.info(f"[PRICE] Updated price message #{target_msg_id} in Client Group: '{new_price_text}' and removed button.")
            await update_order_price(order.id, text, price_msg_id=target_msg_id)
            price_edited = True
        except Exception as e:
            logger.warning(f"[PRICE] Could not edit original price message #{target_msg_id}: {e}")

    if not price_edited:
        try:
            reply_target = order.original_message_id or reply_msg_id
            sent_price_msg = await context.bot.send_message(
                chat_id=session["chat_id"],
                text=new_price_text,
                reply_to_message_id=reply_target
            )
            await update_order_price(order.id, text, price_msg_id=sent_price_msg.message_id)
            logger.info(f"[PRICE] Sent new custom price message #{sent_price_msg.message_id} in Client Group: '{new_price_text}'.")
        except Exception as ex:
            logger.exception(f"[PRICE] Failed to post price message fallback: {ex}")
            return

    # Delete temporary prompt message ("Enter order price:")
    prompt_msg_id = session.get("prompt_msg_id") or order.price_prompt_msg_id
    if prompt_msg_id:
        try:
            await context.bot.delete_message(
                chat_id=session["chat_id"],
                message_id=prompt_msg_id
            )
        except Exception:
            pass

    # Delete admin's typed text message ("15.5") so no extra text remains at the bottom
    try:
        await context.bot.delete_message(
            chat_id=session["chat_id"],
            message_id=message.message_id
        )
    except Exception:
        pass

    # Log required format:
    # [PRICE]
    # Order #25
    # Price set to 15.5 (or Price updated to 30)
    logger.info(f"[PRICE]\nOrder #{order.id}\nPrice {action_str} {text}")

    # Remove active price session
    PRICE_INPUT_SESSION.pop(target_order_id, None)


# ==========================================
# Multi-Loader Category B Callback Handler
# ==========================================

async def category_b_approval_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles Multi-Loader Category B Inline Button Callbacks:
    - catb_reject:<order_id>
    - catb_accept:<order_id>
    - catb_select_loader:<order_id>:<loader_id>
    - catb_cancel:<order_id>
    """
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("catb_"):
        return

    try:
        await query.answer()
    except Exception as e:
        logger.exception(f"[PAYMENT] Failed to answer query: {e}")

    data = query.data
    parts = data.split(":")
    action = parts[0]

    if action == "catb_reject":
        if len(parts) < 2 or not parts[1].isdigit():
            return
        order_id = int(parts[1])

        await update_order_status(order_id, "Rejected")
        logger.info(f"[PAYMENT] Order #{order_id} rejected via button.")

        card_text = (
            f"❌ <b>Order Rejected</b>\n\n"
            f"<b>Order ID:</b> #{order_id}"
        )
        try:
            await query.edit_message_text(card_text, parse_mode="HTML")
        except Exception as e:
            logger.exception(f"[PAYMENT] Failed to edit rejected review card: {e}")

    elif action == "catb_accept":
        if len(parts) < 2 or not parts[1].isdigit():
            return
        order_id = int(parts[1])

        # Load loader information from DB if cache is empty
        if not LOADERS_CACHE:
            await reload_loaders_cache()

        loaders = list(LOADERS_CACHE.values())
        if not loaders:
            loaders = await get_all_loaders()

        buttons = []
        if not loaders and BOT_SETTINGS["delivery_group_id"]:
            buttons.append([InlineKeyboardButton("📦 Primary Loader", callback_data=f"catb_select_loader:{order_id}:primary")])
        else:
            for l in loaders:
                l_id = l["id"] if isinstance(l, dict) else l.id
                l_name = l["name"] if isinstance(l, dict) else l.loader_name
                buttons.append([InlineKeyboardButton(f"📦 {l_name}", callback_data=f"catb_select_loader:{order_id}:{l_id}")])

        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=f"catb_cancel:{order_id}")])
        keyboard = InlineKeyboardMarkup(buttons)

        select_text = (
            f"Select Loader\n\n"
            f"<b>Order ID:</b> #{order_id}"
        )
        try:
            await query.edit_message_text(select_text, reply_markup=keyboard, parse_mode="HTML")
        except Exception as e:
            logger.exception(f"[PAYMENT] Failed to edit Select Loader card: {e}")

    elif action == "catb_select_loader":
        if len(parts) < 3 or not parts[1].isdigit():
            return
        order_id = int(parts[1])
        loader_key = parts[2]

        order = await get_order_by_id(order_id)
        if not order:
            try:
                await query.edit_message_text(f"❌ Order #{order_id} not found.")
            except Exception as e:
                logger.exception(f"[LOADER] Failed to edit message: {e}")
            return

        # 1. Load loader information from DB if cache is empty
        if not LOADERS_CACHE:
            await reload_loaders_cache()

        target_group_id = None
        loader_name = "Loader Group"

        if loader_key == "primary":
            target_group_id = BOT_SETTINGS["delivery_group_id"]
            loader_name = BOT_SETTINGS["delivery_group_title"] or "Primary Loader"
        elif loader_key.isdigit():
            lid = int(loader_key)
            if lid in LOADERS_CACHE:
                target_group_id = LOADERS_CACHE[lid]["group_id"]
                loader_name = LOADERS_CACHE[lid]["name"]
            else:
                # Direct DB lookup fallback
                loaders = await get_all_loaders()
                for l in loaders:
                    if l.id == lid:
                        target_group_id = l.group_id
                        loader_name = l.loader_name
                        break

        logger.info(f"[LOADER]\nSelected Loader:\n{loader_name} (Group ID: {target_group_id})")

        if not target_group_id:
            logger.error(f"[LOADER]\nCopy Failed\nLoader Group for ID '{loader_key}' not found.")
            try:
                await query.edit_message_text(f"❌ Loader Group for ID '{loader_key}' not found.")
            except Exception as e:
                logger.exception(f"[LOADER] Failed to edit message: {e}")
            return

        # 2. Copy ORIGINAL customer message to selected loader group
        if order.client_chat_id and order.original_message_id:
            try:
                forwarded_msg = await context.bot.copy_message(
                    chat_id=target_group_id,
                    from_chat_id=order.client_chat_id,
                    message_id=order.original_message_id
                )
                logger.info(f"[LOADER]\nCopy Success\nOrder #{order.id} copied to Loader Group '{loader_name}' ({target_group_id}) with Loader Msg ID {forwarded_msg.message_id}.")

                # 3. Save loader_group_id, loader_message_id, and 4. status = Pending
                await set_order_loader_message_id(order.id, forwarded_msg.message_id, loader_group_id=target_group_id)
                await update_order_status(order.id, "Pending")
            except Exception as e:
                logger.exception(f"[LOADER]\nCopy Failed\nFailed to copy Order #{order.id} to Loader Group {target_group_id}: {e}")
                try:
                    await query.edit_message_text(f"❌ Failed to forward order to loader group: {e}")
                except Exception as e_edit:
                    logger.exception(f"[LOADER] Failed to edit error message: {e_edit}")
                return

        # 5. Edit review card:
        # ✅ Order Approved
        # Loader:
        # Pakistan Loader
        # Order #xxx
        success_text = (
            f"✅ <b>Order Approved</b>\n\n"
            f"<b>Loader:</b>\n{html.escape(loader_name)}\n\n"
            f"<b>Order #{order.id}</b>"
        )
        try:
            await query.edit_message_text(success_text, parse_mode="HTML")
        except Exception as e:
            logger.exception(f"[LOADER] Failed to edit review card: {e}")

    elif action == "catb_cancel":
        if len(parts) < 2 or not parts[1].isdigit():
            return
        order_id = int(parts[1])
        order = await get_order_by_id(order_id)

        if not order:
            return

        # Revert message back to initial Accept / Reject buttons
        card_msg = (
            f"🟨 <b>NEW ORDER</b>\n\n"
            f"<b>Order ID:</b> #{order.id}\n\n"
            f"<b>Email:</b>\n{html.escape(order.email)}\n\n"
            f"<b>Group:</b>\nClient Group\n\n"
            f"Choose an action."
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Accept", callback_data=f"catb_accept:{order.id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"catb_reject:{order.id}")
            ]
        ])
        try:
            await query.edit_message_text(card_msg, reply_markup=keyboard, parse_mode="HTML")
        except Exception as e:
            logger.exception(f"[PAYMENT] Failed to edit cancelled review card: {e}")


async def delivery_group_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Monitors messages in Loader Groups.
    Validates group ID strictly using in-memory BOT_SETTINGS and LOADERS_CACHE without querying database.
    Enforces Role-Based Permission Check (User must have 'delivery' or 'admin' role).
    Validates that incoming text or media is sent strictly as a reply to a valid bot Order Message.
    Ignores non-reply messages and unmatched replies silently without sending error cards in chat.
    Supports Wrong Details Workflow ('wrong' text reply) and Caption Email Overrides.
    """
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat:
        return

    # Check against in-memory BOT_SETTINGS and LOADERS_CACHE (Zero DB SELECT query)
    is_known_loader = (chat.id == BOT_SETTINGS["delivery_group_id"]) or any(
        l["group_id"] == chat.id for l in LOADERS_CACHE.values()
    )

    if not is_known_loader:
        logger.debug(f"[LOADER] Ignored message in unconfigured chat {chat.id} ({chat.title}).")
        return

    # Role-Based Permission Check for Delivery Users
    user_id = user.id if user else None
    if not is_delivery_user(user_id):
        logger.warning(f"[LOADER] Unauthorized user {user_id} attempted to deliver order in Loader Group {chat.id}.")
        try:
            await message.reply_text("⛔ You are not authorized to deliver orders.")
        except Exception as e:
            logger.exception(f"[LOADER] Failed to send unauthorized delivery error notice: {e}")
        return

    reply_to = message.reply_to_message

    # Rule 1: Message MUST be a reply - Silent Ignore without error messages in chat
    if not reply_to:
        logger.info("[LOADER] Ignored non-reply message.")
        return

    text_content = message.text or message.caption or ""

    # Rule 2: Identify order from database using replied message ID or text Order ID
    order = await get_order_by_loader_msg_id(reply_to.message_id)
    if not order:
        reply_text = reply_to.text or reply_to.caption or ""
        order_id_from_text = extract_order_id(reply_text)
        if order_id_from_text:
            order = await get_order_by_id(order_id_from_text)

    # Silent Ignore if reply does not match any valid order in DB
    if not order:
        logger.info("[LOADER] Ignored reply that does not match any active order.")
        return

    # Wrong Details Workflow: Check if loader reply contains the word 'wrong' (case-insensitive)
    if "wrong" in text_content.lower():
        logger.info(f"[LOADER] Wrong details reported by loader for Order #{order.id}.")
        client_chat_id = order.client_chat_id or BOT_SETTINGS["source_group_id"]
        if client_chat_id and order.original_message_id:
            try:
                await context.bot.send_message(
                    chat_id=client_chat_id,
                    text="❌ Please check and correct your details, then send them again.",
                    reply_to_message_id=order.original_message_id
                )
                logger.info(f"[CLIENT] Wrong details notice sent to Client Group for Order #{order.id}.")
            except Exception as e:
                logger.exception(f"[CLIENT] Failed to send wrong details notice for Order #{order.id}: {e}")

        # React to loader message with ❌ (fallback ⚠️)
        await safe_set_message_reaction(
            bot=context.bot,
            chat_id=chat.id,
            message_id=message.message_id,
            emoji="❌",
            fallback_emoji="⚠️",
            log_tag="[REACTION]"
        )

        # Do NOT deliver images, keep order Pending, do NOT delete anything
        return

    is_media = bool(message.photo or (message.document and (message.document.mime_type or "").startswith("image/")))
    if not is_media:
        return

    # Rule 3: Check Order Status (Repeated Delivery Workflow - Part 1 Requirement)
    if order.status == "Delivered":
        logger.info(f"[LOADER] Repeated delivery attempt for Order #{order.id} (already Delivered). Presenting Deliver Again prompt.")
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Deliver Again", callback_data=f"redeliver_yes:{order.id}:{message.message_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"redeliver_cancel:{order.id}")
            ]
        ])
        try:
            await message.reply_text(
                f"⚠️ <b>This order was already delivered.</b>\n\nOrder ID: #{order.id}\nDo you want to deliver it again?",
                reply_to_message_id=message.message_id,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.exception(f"[LOADER] Failed to send redelivery prompt: {e}")
        return

    if order.status == "Cancelled":
        logger.info(f"[LOADER] Reply failed: Order #{order.id} is Cancelled.")
        try:
            await message.reply_text(
                f"❌ Order #{order.id} has been cancelled.",
                reply_to_message_id=message.message_id
            )
        except Exception as e:
            logger.exception(f"[LOADER] Failed to send order cancelled notice: {e}")
        return

    if order.status == "Expired":
        logger.info(f"[LOADER] Reply failed: Order #{order.id} is Expired.")
        try:
            await message.reply_text(
                f"⏰ Order #{order.id} has expired (Pending Too Long).",
                reply_to_message_id=message.message_id
            )
        except Exception as e:
            logger.exception(f"[LOADER] Failed to send order expired notice: {e}")
        return

    logger.info(f"[LOADER] Processing media reply for Order #{order.id} (Email: '{order.email}')...")

    # Pass media to collector with caption text for email override processing
    await media_collector.add_reply_media_message(
        message=message,
        order_id=order.id,
        email=order.email,
        bot=context.bot,
        caption_text=text_content
    )


# ==========================================
# Multi-Loader Management Commands
# ==========================================

async def loaderadd_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles /loaderadd command. Supports direct arguments (/loaderadd <group_id> <name>)
    or step-by-step interactive wizard (/loaderadd -> Ask Group ID -> Ask Loader Name).
    """
    if not await check_admin_permission(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    uid = user.id if user else None
    args = context.args or []

    if len(args) >= 2 and args[0].lstrip("-").isdigit():
        group_id = int(args[0])
        loader_name = " ".join(args[1:])
        await add_loader(group_id, loader_name)
        LOADER_ADD_SESSION.pop(uid, None)
        await update.effective_message.reply_text("✅ Loader Added Successfully")
        return

    # Interactive Step-by-Step wizard reserved strictly for this admin user
    if uid:
        LOADER_ADD_SESSION[uid] = {
            "step": 1,
            "chat_id": chat.id if chat else None,
            "created_at": datetime.now(timezone.utc)
        }
        await update.effective_message.reply_text("Send Loader Group ID")


async def loader_text_wizard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles text input during interactive /loaderadd step-by-step wizard.
    Strictly restricted to the specific admin user who initiated /loaderadd.
    Silently bypasses all messages if user has no active wizard session.
    """
    user = update.effective_user
    message = update.effective_message
    chat = update.effective_chat

    # Rule 1 & 2: Immediately return without replying or consuming if no active session exists for user
    if not user or not message or user.id not in LOADER_ADD_SESSION:
        return

    session = LOADER_ADD_SESSION[user.id]

    # Rule 5: Match initiating chat context
    if chat and session.get("chat_id") and chat.id != session.get("chat_id"):
        return

    text = (message.text or "").strip()

    # Rule 4: Cancel wizard if admin issues a command or cancels
    if text.startswith("/") or text.lower() in ("cancel", "exit"):
        LOADER_ADD_SESSION.pop(user.id, None)
        logger.info(f"[LOADER_MGMT] Cancelled /loaderadd wizard for user {user.id}.")
        if text.lower() in ("cancel", "exit"):
            await message.reply_text("❌ Loader add wizard cancelled.")
        return

    # Rule 4: Timeout session after 5 minutes (300 seconds)
    created_at = session.get("created_at")
    if created_at and (datetime.now(timezone.utc) - created_at).total_seconds() > 300:
        LOADER_ADD_SESSION.pop(user.id, None)
        logger.info(f"[LOADER_MGMT] Timed out /loaderadd wizard for user {user.id}.")
        return

    step = session.get("step", 1)

    if step == 1:
        if not text.lstrip("-").isdigit():
            await message.reply_text("❌ Invalid Loader Group ID. Must be numeric (e.g. -1001234567890).")
            return

        session["group_id"] = int(text)
        session["step"] = 2
        await message.reply_text("Send Loader Name")
        return

    elif step == 2:
        group_id = session.get("group_id")
        loader_name = text

        if not group_id or not loader_name:
            await message.reply_text("❌ Error adding loader. Please try again with /loaderadd.")
            LOADER_ADD_SESSION.pop(user.id, None)
            return

        try:
            await add_loader(group_id, loader_name)
            await message.reply_text("✅ Loader Added Successfully")
        except Exception as e:
            logger.exception(f"[LOADER_MGMT] Failed to add loader: {e}")
            await message.reply_text(f"❌ Failed to add loader: {e}")
        finally:
            # Rule 4: Completely remove wizard state after completion
            LOADER_ADD_SESSION.pop(user.id, None)


async def loaderlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /loaderlist command showing all registered loaders."""
    if not await check_admin_permission(update):
        return

    loaders = await get_all_loaders()
    if not loaders:
        await update.effective_message.reply_text("Registered Loaders\n\nNone")
        return

    lines = ["Registered Loaders\n"]
    for idx, l in enumerate(loaders, 1):
        lines.append(f"{idx}.\n{l.loader_name}\n{l.group_id}\n")

    await update.effective_message.reply_text("\n".join(lines))


async def loaderremove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /loaderremove <id> command."""
    if not await check_admin_permission(update):
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("⚠️ Usage: <code>/loaderremove 2</code>", parse_mode="HTML")
        return

    loader_id = int(context.args[0])
    removed = await remove_loader_by_id(loader_id)

    if removed:
        await update.effective_message.reply_text("✅ Loader Removed")
    else:
        await update.effective_message.reply_text(f"❌ Loader ID #{loader_id} not found.")


# ==========================================
# Group Category Routing System Commands (v1.2)
# ==========================================

async def category_a_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /A command executed inside any Client Group by Super Admin.
    Assigns the group to Category A (Trusted Groups).
    """
    if not await verify_admin_and_group(update, context):
        return

    chat = update.effective_chat
    group_name = chat.title or "Client Group"

    await set_client_group_category(chat.id, group_name, "A")
    logger.info(f"[CATEGORY] Group assigned to Category A. Chat ID: {chat.id}")

    await update.effective_message.reply_text("✅ This group has been assigned to Category A.")


async def category_b_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /B command executed inside any Client Group by Super Admin.
    Assigns the group to Category B (Payment Required Groups).
    """
    if not await verify_admin_and_group(update, context):
        return

    chat = update.effective_chat
    group_name = chat.title or "Client Group"

    await set_client_group_category(chat.id, group_name, "B")
    logger.info(f"[CATEGORY] Group assigned to Category B. Chat ID: {chat.id}")

    await update.effective_message.reply_text("✅ This group has been assigned to Category B.")


async def category_check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /category command executed inside a Client Group to check category.
    """
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        if update.effective_message:
            await update.effective_message.reply_text("⚠️ This command must be executed inside a Telegram Group.")
        return

    group_name = chat.title or "Client Group"
    category = await get_client_group_category(chat.id)

    reply_msg = (
        f"Current Category\n\n"
        f"Group:\n{group_name}\n\n"
        f"Category:\n{category}"
    )
    await update.effective_message.reply_text(reply_msg)


async def remove_category_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /removecategory command executed inside a Client Group by Super Admin.
    """
    if not await verify_admin_and_group(update, context):
        return

    chat = update.effective_chat
    await remove_client_group_category(chat.id)

    await update.effective_message.reply_text("✅ Group category removed successfully.")


async def paymentgroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /paymentgroup command executed inside the private Payment Review Group by Super Admin.
    """
    if not await verify_admin_and_group(update, context):
        return

    chat = update.effective_chat
    group_name = chat.title or "Payment Review Group"

    await update_payment_review_group(chat.id, group_name)
    await update.effective_message.reply_text("✅ Payment Review Group configured successfully.")


async def approve_order_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /approve <order_id> command executed inside Payment Review Group (-1004441603990) or by Super Admin.
    Updates order status to Approved, forwards original order to Loader Group.
    """
    chat = update.effective_chat
    user = update.effective_user

    payment_review_id = BOT_SETTINGS["payment_review_group_id"] or Config.PAYMENT_REVIEW_GROUP_ID
    is_in_payment_group = bool(chat and chat.id == payment_review_id)
    is_admin_user = is_super_admin(user.id if user else None)

    if not is_in_payment_group and not is_admin_user:
        if update.effective_message:
            await update.effective_message.reply_text("⛔ This command can only be used inside the Payment Review Group.")
        return

    if not context.args or not context.args[0].lstrip("#").isdigit():
        await update.effective_message.reply_text("⚠️ Usage: <code>/approve 1025</code>", parse_mode="HTML")
        return

    order_id = int(context.args[0].lstrip("#"))
    order = await get_order_by_id(order_id)

    if not order:
        await update.effective_message.reply_text(f"❌ Order #{order_id} not found.")
        return

    # Update order status to Approved
    await update_order_status(order.id, "Approved")

    loader_group_id = BOT_SETTINGS["delivery_group_id"]
    if loader_group_id and order.client_chat_id and order.original_message_id:
        try:
            try:
                forwarded_msg = await context.bot.copy_message(
                    chat_id=loader_group_id,
                    from_chat_id=order.client_chat_id,
                    message_id=order.original_message_id
                )
            except Exception as e_copy:
                logger.exception(f"copy_message failed: {e_copy}")
                forwarded_msg = await context.bot.send_message(
                    chat_id=loader_group_id,
                    text=f"Order #{order.id} | Email: {order.email}"
                )

            await set_order_loader_message_id(order.id, forwarded_msg.message_id, loader_group_id=loader_group_id)
            logger.info(f"[PAYMENT] Order #{order.id} approved. Forwarded to Loader Group.")
        except Exception as e:
            logger.exception(f"[PAYMENT] Failed to forward approved Order #{order.id} to Loader Group: {e}")
    else:
        logger.warning(f"[PAYMENT] Order #{order.id} approved, but Loader Group is not configured yet!")

    await update.effective_message.reply_text(f"✅ Order #{order.id} approved and forwarded to Loader Group.")


async def reject_order_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /reject <order_id> command executed inside Payment Review Group (-1004441603990) or by Super Admin.
    Updates order status to Rejected. Does NOT forward to Loader Group.
    """
    chat = update.effective_chat
    user = update.effective_user

    payment_review_id = BOT_SETTINGS["payment_review_group_id"] or Config.PAYMENT_REVIEW_GROUP_ID
    is_in_payment_group = bool(chat and chat.id == payment_review_id)
    is_admin_user = is_super_admin(user.id if user else None)

    if not is_in_payment_group and not is_admin_user:
        if update.effective_message:
            await update.effective_message.reply_text("⛔ This command can only be used inside the Payment Review Group.")
        return

    if not context.args or not context.args[0].lstrip("#").isdigit():
        await update.effective_message.reply_text("⚠️ Usage: <code>/reject 1025</code>", parse_mode="HTML")
        return

    order_id = int(context.args[0].lstrip("#"))
    order = await get_order_by_id(order_id)

    if not order:
        await update.effective_message.reply_text(f"❌ Order #{order_id} not found.")
        return

    # Update order status to Rejected
    await update_order_status(order.id, "Rejected")
    logger.info(f"[PAYMENT] Order #{order.id} rejected.")

    await update.effective_message.reply_text(f"❌ Order #{order.id} rejected.")


# ==========================================
# Role-Based User Management Commands
# ==========================================

async def user_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles /user delivery add <user_id> and /user delivery remove <user_id> commands (Super Admin only).
    """
    if not await check_admin_permission(update):
        return

    args = context.args or []

    if len(args) == 3 and args[0].lower() == "delivery":
        sub_action = args[1].lower()
        target_uid_str = args[2].strip()

        if not target_uid_str.isdigit():
            await update.effective_message.reply_text("❌ Invalid Telegram User ID. Must be numeric.", parse_mode="HTML")
            return

        target_uid = int(target_uid_str)

        if sub_action == "add":
            success, msg = await add_authorized_user(target_uid, role="delivery")
            reply = (
                f"✅ Delivery user added successfully.\n\n"
                f"User ID:\n{target_uid}"
            )
            await update.effective_message.reply_text(reply)
            return

        elif sub_action == "remove":
            success, msg = await remove_authorized_user(target_uid)
            if success:
                reply = (
                    f"✅ Delivery user removed successfully.\n\n"
                    f"User ID:\n{target_uid}"
                )
            else:
                reply = f"❌ {msg}"
            await update.effective_message.reply_text(reply)
            return

    usage_msg = (
        "🛠 <b>User Management Usage</b>\n\n"
        "• <code>/user delivery add 123456789</code>\n"
        "• <code>/user delivery remove 123456789</code>\n"
        "• <code>/users</code> - List all authorized users"
    )
    await update.effective_message.reply_text(usage_msg, parse_mode="HTML")


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles /users command listing Super Admin and Delivery Users (Super Admin only).
    """
    if not await check_admin_permission(update):
        return

    user_groups = await get_all_authorized_users()
    admins = user_groups.get("admin", [1573531032])
    delivery_users = user_groups.get("delivery", [])

    lines = ["👑 Super Admin\n"]
    for a in admins:
        lines.append(f"{a}")

    lines.append("\n📦 Delivery Users\n")
    if delivery_users:
        for d in delivery_users:
            lines.append(f"{d}")
    else:
        lines.append("None")

    await update.effective_message.reply_text("\n".join(lines))


# ==========================================
# Self-Configuring Commands & Validation
# ==========================================

async def verify_admin_and_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Validates Admin user permission, group chat type, and Bot Admin status."""
    chat = update.effective_chat
    user = update.effective_user

    if not is_super_admin(user.id if user else None):
        if update.effective_message:
            await update.effective_message.reply_text("⛔ You are not authorized to use this command.")
        return False

    if not chat or chat.type not in ("group", "supergroup"):
        if update.effective_message:
            await update.effective_message.reply_text("⚠️ This command must be executed inside a Telegram Group or Supergroup.")
        return False

    try:
        bot_member = await chat.get_member(context.bot.id)
        if bot_member.status not in ("administrator", "creator"):
            if update.effective_message:
                await update.effective_message.reply_text("❌ Please promote the bot to an Administrator in this group first.")
            return False
    except Exception as e:
        logger.warning(f"Error checking bot admin permissions: {e}")
        if update.effective_message:
            await update.effective_message.reply_text("❌ Failed to verify bot admin permissions in this chat.")
        return False

    return True


async def source_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /source command run inside Group 1 (Client Group).
    Saves Chat ID and Name to DB as Client Group and immediately updates BOT_SETTINGS cache.
    """
    if not await verify_admin_and_group(update, context):
        return

    chat = update.effective_chat
    group_name = chat.title or "Client Group"

    logger.info(f"[SOURCE] Command received in chat: {chat.id}")
    logger.info("[SOURCE] Saving Source Group...")
    settings = await update_source_group(chat.id, group_name)

    title_escaped = html.escape(settings.source_group_title or group_name)

    reply_msg = (
        f"✅ <b>Client Group Saved</b>\n\n"
        f"<b>Group:</b>\n{title_escaped}\n\n"
        f"<b>ID:</b>\n<code>{settings.source_group_id}</code>"
    )
    await update.effective_message.reply_text(reply_msg, parse_mode="HTML")


async def delivery_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /delivery command run inside Group 2 (Loader Group).
    Saves Chat ID and Name to DB as Loader Group and immediately updates BOT_SETTINGS cache.
    """
    if not await verify_admin_and_group(update, context):
        return

    chat = update.effective_chat
    group_name = chat.title or "Loader Group"

    logger.info(f"[DELIVERY_GROUP] Command received in chat: {chat.id}")
    logger.info("[DELIVERY_GROUP] Saving Delivery Group...")
    settings = await update_delivery_group(chat.id, group_name)

    title_escaped = html.escape(settings.delivery_group_title or group_name)

    reply_msg = (
        f"✅ <b>Loader Group Saved</b>\n\n"
        f"<b>Group:</b>\n{title_escaped}\n\n"
        f"<b>ID:</b>\n<code>{settings.delivery_group_id}</code>"
    )
    await update.effective_message.reply_text(reply_msg, parse_mode="HTML")


async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /groups command displaying active group settings from database."""
    if not await check_admin_permission(update):
        return

    settings = await get_current_settings()

    src_title = html.escape(settings.source_group_title or "Unconfigured")
    del_title = html.escape(settings.delivery_group_title or "Unconfigured")

    msg = (
        f"📥 <b>Client Group</b>\n\n{src_title}\n\n"
        f"📤 <b>Loader Group</b>\n\n{del_title}\n\n"
        f"<b>Status</b>\n\nReady"
    )
    await update.effective_message.reply_text(msg, parse_mode="HTML")


async def resetgroups_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /resetgroups command."""
    if not await check_admin_permission(update):
        return

    await reset_groups()
    await update.effective_message.reply_text("✅ All group settings have been reset.", parse_mode="HTML")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /status command displaying system diagnostics."""
    if not await check_admin_permission(update):
        return

    settings = await get_current_settings()
    stats = await get_detailed_stats()

    src_str = html.escape(settings.source_group_title or "Unconfigured")
    if settings.source_group_id:
        src_str += f" ({settings.source_group_id})"

    del_str = html.escape(settings.delivery_group_title or "Unconfigured")
    if settings.delivery_group_id:
        del_str += f" ({settings.delivery_group_id})"

    msg = (
        "🤖 <b>Bot Status</b>\n\n"
        f"<b>Status:</b> Online\n"
        f"<b>Database:</b> Connected ({get_db_type_name()})\n"
        f"<b>Client Group:</b> {src_str}\n"
        f"<b>Loader Group:</b> {del_str}\n"
        f"<b>Total Orders:</b> {stats['total_orders']}\n"
        f"<b>Pending Orders:</b> {stats['pending_orders']}\n"
        f"<b>Delivered Orders:</b> {stats['delivered_orders']}\n"
        f"<b>Version:</b> v1.0.0\n"
        f"<b>Uptime:</b> {get_uptime_str()}"
    )
    await update.effective_message.reply_text(msg, parse_mode="HTML")


async def setup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /setup command showing step-by-step guidance."""
    if not await check_admin_permission(update):
        return

    settings = await get_current_settings()
    src_ok = bool(settings.source_group_id)
    del_ok = bool(settings.delivery_group_id)

    lines = [
        "🤖 <b>Two-Group Reply-Based Setup Wizard</b>\n",
        f"{'✅' if src_ok else '❌'} <b>Client Group:</b> {html.escape(settings.source_group_title or 'Unconfigured')}",
        f"{'✅' if del_ok else '❌'} <b>Loader Group:</b> {html.escape(settings.delivery_group_title or 'Unconfigured')}\n",
        "<b>Setup Instructions:</b>",
        "1. Add bot to Client Group → Promote to Admin → Send <code>/source</code>",
        "2. Add bot to Loader Group → Promote to Admin → Send <code>/delivery</code>"
    ]
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


# ==========================================
# Order Management Commands
# ==========================================

async def pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /pending command listing all pending orders."""
    if not await check_admin_permission(update):
        return

    pending = await get_pending_orders()
    if not pending:
        await update.effective_message.reply_text("✅ No pending orders! All orders delivered.")
        return

    details = [f"⏳ <b>Pending Orders ({len(pending)})</b>:\n"]
    for idx, order in enumerate(pending[:15], 1):
        created_str = order.created_at.strftime("%Y-%m-%d %H:%M UTC")
        email_escaped = html.escape(order.email)
        pkg_escaped = html.escape(order.package or "Standard Package")
        details.append(f"{idx}. Order <code>#{order.id}</code> | Email: <code>{email_escaped}</code>\n    Package: <i>{pkg_escaped}</i> | Created: <code>{created_str}</code>")

    if len(pending) > 15:
        details.append(f"\n... and {len(pending) - 15} more pending order(s).")

    await update.effective_message.reply_text("\n".join(details), parse_mode="HTML")


async def delivered_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /delivered command showing latest delivered orders."""
    if not await check_admin_permission(update):
        return

    delivered = await get_delivered_orders(limit=15)
    if not delivered:
        await update.effective_message.reply_text("ℹ️ No delivered orders found.")
        return

    details = [f"✅ <b>Latest Delivered Orders ({len(delivered)})</b>:\n"]
    for idx, order in enumerate(delivered, 1):
        delivered_str = order.delivered_at.strftime("%Y-%m-%d %H:%M UTC") if order.delivered_at else "N/A"
        email_escaped = html.escape(order.email)
        price_str = f" | Price: Rs.{order.price}" if order.price else ""
        details.append(f"{idx}. Order <code>#{order.id}</code> | Email: <code>{email_escaped}</code>{price_str} | Images: <code>{len(order.images)}</code> | Delivered: <code>{delivered_str}</code>")

    await update.effective_message.reply_text("\n".join(details), parse_mode="HTML")


async def find_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /find <order_id_or_email> command."""
    if not await check_admin_permission(update):
        return

    if not context.args:
        await update.effective_message.reply_text("⚠️ Usage: <code>/find 10025</code> or <code>/find email@example.com</code>", parse_mode="HTML")
        return

    raw_arg = context.args[0].strip()

    if raw_arg.lstrip("#").isdigit():
        order_id = int(raw_arg.lstrip("#"))
        order = await get_order_by_id(order_id)
        if not order:
            await update.effective_message.reply_text(f"❌ Order <code>#{order_id}</code> not found.", parse_mode="HTML")
            return
        orders = [order]
    else:
        email = extract_email(raw_arg)
        if not email:
            await update.effective_message.reply_text("❌ Invalid Order ID or email format.")
            return
        orders = await get_all_orders_by_email(email)

    if not orders:
        await update.effective_message.reply_text("❌ No matching orders found.")
        return

    details = [f"🔍 <b>Found {len(orders)} matching order(s)</b>:\n"]
    for idx, order in enumerate(orders, 1):
        created_str = order.created_at.strftime("%Y-%m-%d %H:%M UTC")
        delivered_str = order.delivered_at.strftime("%Y-%m-%d %H:%M UTC") if order.delivered_at else "N/A"
        email_escaped = html.escape(order.email)
        price_str = f" | Price: Rs.{order.price}" if order.price else ""
        status_icon = "✅" if order.status == "Delivered" else ("⏳" if order.status in ("Pending", "Pending Approval", "Pending Payment") else "❌")
        details.append(
            f"{idx}. {status_icon} Order <code>#{order.id}</code> | Status: <b>{order.status}</b>\n"
            f"    Email: <code>{email_escaped}</code>{price_str} | Images: <b>{len(order.images)}</b>\n"
            f"    Created: <code>{created_str}</code> | Delivered: <code>{delivered_str}</code>"
        )

    await update.effective_message.reply_text("\n".join(details), parse_mode="HTML")


async def order_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /order <order_id> command displaying complete order information."""
    if not await check_admin_permission(update):
        return

    if not context.args or not context.args[0].lstrip("#").isdigit():
        await update.effective_message.reply_text("⚠️ Usage: <code>/order 10025</code>", parse_mode="HTML")
        return

    order_id = int(context.args[0].lstrip("#"))
    order = await get_order_by_id(order_id)

    if not order:
        await update.effective_message.reply_text(f"❌ Order <code>#{order_id}</code> not found.", parse_mode="HTML")
        return

    created_str = order.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    delivered_str = order.delivered_at.strftime("%Y-%m-%d %H:%M:%S UTC") if order.delivered_at else "Pending"
    email_escaped = html.escape(order.email)
    pkg_escaped = html.escape(order.package or "Standard Package")

    status_icon = "✅" if order.status == "Delivered" else ("⏳" if order.status in ("Pending", "Pending Approval", "Pending Payment") else "❌")

    msg = (
        f"📦 <b>Order Detailed Information</b>\n\n"
        f"<b>Order ID:</b> #{order.id}\n"
        f"<b>Status:</b> {status_icon} {order.status}\n"
        f"<b>Category:</b> {order.category or 'A'}\n"
        f"<b>Price:</b> {order.price or 'Unset'}\n"
        f"<b>Email:</b> <code>{email_escaped}</code>\n"
        f"<b>Package:</b> <i>{pkg_escaped}</i>\n"
        f"<b>Stored Images:</b> {len(order.images)}\n"
        f"<b>Client Chat ID:</b> <code>{order.client_chat_id or 'N/A'}</code>\n"
        f"<b>Loader Group ID:</b> <code>{order.loader_group_id or 'N/A'}</code>\n"
        f"<b>Loader Msg ID:</b> <code>{order.loader_message_id or 'N/A'}</code>\n"
        f"<b>Created Time:</b> <code>{created_str}</code>\n"
        f"<b>Delivered Time:</b> <code>{delivered_str}</code>"
    )
    await update.effective_message.reply_text(msg, parse_mode="HTML")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /cancel <order_id> command."""
    if not await check_admin_permission(update):
        return

    if not context.args or not context.args[0].lstrip("#").isdigit():
        await update.effective_message.reply_text("⚠️ Usage: <code>/cancel 10025</code>", parse_mode="HTML")
        return

    order_id = int(context.args[0].lstrip("#"))
    order, success = await cancel_order(order_id)

    if not success or not order:
        await update.effective_message.reply_text(f"❌ Order <code>#{order_id}</code> not found.", parse_mode="HTML")
        return

    await update.effective_message.reply_text(f"✅ Order <code>#{order_id}</code> has been cancelled successfully.", parse_mode="HTML")


async def resend_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /resend <order_id_or_email> command."""
    if not await check_admin_permission(update):
        return

    if not context.args:
        await update.effective_message.reply_text("⚠️ Usage: <code>/resend 10025</code> or <code>/resend email@example.com</code>", parse_mode="HTML")
        return

    raw_arg = context.args[0].strip()

    if raw_arg.lstrip("#").isdigit():
        order_id = int(raw_arg.lstrip("#"))
        await update.effective_message.reply_text(f"⏳ Processing re-delivery for Order <code>#{order_id}</code>...", parse_mode="HTML")
        res = await deliver_order_by_id(
            bot=context.bot,
            order_id=order_id,
            target_delivery_chat_id=update.effective_chat.id
        )
        if res:
            await update.effective_message.reply_text(f"✅ Re-delivery of Order <code>#{order_id}</code> completed.", parse_mode="HTML")
        else:
            await update.effective_message.reply_text(f"❌ Re-delivery of Order <code>#{order_id}</code> failed.", parse_mode="HTML")
    else:
        email = extract_email(raw_arg)
        if not email:
            await update.effective_message.reply_text("❌ Invalid Order ID or email format.")
            return

        await update.effective_message.reply_text(f"⏳ Processing re-delivery for <code>{html.escape(email)}</code>...", parse_mode="HTML")
        await deliver_images_for_email(
            bot=context.bot,
            chat_id=update.effective_chat.id,
            email=email,
            reply_to_message_id=update.effective_message.message_id
        )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /stats command displaying detailed dashboard statistics."""
    if not await check_admin_permission(update):
        return

    stats = await get_detailed_stats()

    msg = (
        "📊 <b>Bot Statistics Dashboard</b>\n\n"
        f"📦 <b>Total Orders:</b> <code>{stats['total_orders']}</code>\n"
        f"⏳ <b>Pending Orders:</b> <code>{stats['pending_orders']}</code>\n"
        f"✅ <b>Delivered Orders:</b> <code>{stats['delivered_orders']}</code>\n"
        f"❌ <b>Cancelled Orders:</b> <code>{stats['cancelled_orders']}</code>\n\n"
        f"📅 <b>Today's Orders:</b> <code>{stats['today_orders']}</code>\n"
        f"🚀 <b>Today's Deliveries:</b> <code>{stats['today_deliveries']}</code>\n"
        f"⚡ <b>Avg Delivery Time:</b> <code>{stats['avg_delivery_time']}</code>\n\n"
        f"⚙️ <b>Retention Limit:</b> <code>{Config.CLEANUP_DAYS} Days</code>"
    )
    await update.effective_message.reply_text(msg, parse_mode="HTML")


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /delete command to purge records for an email."""
    if not await check_admin_permission(update):
        return

    if not context.args:
        await update.effective_message.reply_text("⚠️ Usage: <code>/delete email@example.com</code>", parse_mode="HTML")
        return

    raw_input = " ".join(context.args)
    email = extract_email(raw_input)

    if not email:
        await update.effective_message.reply_text("❌ Invalid email format provided.")
        return

    deleted_count = await delete_orders_by_email(email)
    email_escaped = html.escape(email)
    if deleted_count > 0:
        await update.effective_message.reply_text(f"✅ Successfully deleted <b>{deleted_count}</b> record(s) for <code>{email_escaped}</code>.", parse_mode="HTML")
    else:
        await update.effective_message.reply_text(f"❌ No records found for <code>{email_escaped}</code>.", parse_mode="HTML")


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /export command generating and sending a CSV export."""
    if not await check_admin_permission(update):
        return

    await update.effective_message.reply_text("⏳ Generating database CSV export...")

    csv_data = await export_orders_to_csv()
    csv_bytes = csv_data.encode("utf-8")
    document_file = io.BytesIO(csv_bytes)
    document_file.name = "orders_export.csv"

    await update.effective_message.reply_document(
        document=document_file,
        caption="📄 <b>Orders Database Export (CSV)</b>",
        parse_mode="HTML"
    )


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /backup command creating and sending a SQLite database file backup."""
    if not await check_admin_permission(update):
        return

    db_path = await get_db_file_path()
    if not db_path:
        await update.effective_message.reply_text("⚠️ Backup available only for local SQLite database installations.")
        return

    await update.effective_message.reply_text("⏳ Creating SQLite database backup...")
    try:
        with open(db_path, "rb") as f:
            db_bytes = f.read()

        db_file = io.BytesIO(db_bytes)
        db_file.name = "bot_database_backup.db"

        await update.effective_message.reply_document(
            document=db_file,
            caption="💾 <b>SQLite Database Backup</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.exception(f"Error creating database backup: {e}")
        await update.effective_message.reply_text(f"❌ Failed to create database backup: {e}")


async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /restore command to restore SQLite database from an attached .db file."""
    if not await check_admin_permission(update):
        return

    message = update.effective_message
    if not message.reply_to_message or not message.reply_to_message.document:
        await update.effective_message.reply_text(
            "⚠️ Usage: Reply to a message containing an attached <code>.db</code> backup file with <code>/restore</code>.",
            parse_mode="HTML"
        )
        return

    doc = message.reply_to_message.document
    if not doc.file_name.endswith(".db"):
        await update.effective_message.reply_text("❌ Attached file must be a <code>.db</code> database backup file.", parse_mode="HTML")
        return

    db_path = await get_db_file_path()
    if not db_path:
        await update.effective_message.reply_text("⚠️ Restore is supported only for SQLite database installations.")
        return

    await update.effective_message.reply_text("⏳ Restoring database from backup file...")
    try:
        telegram_file = await context.bot.get_file(doc.file_id)
        backup_dest = f"{db_path}.bak"

        await dispose_engine()

        if os.path.exists(db_path):
            shutil.copyfile(db_path, backup_dest)

        await telegram_file.download_to_drive(custom_path=db_path)
        await init_db()

        await update.effective_message.reply_text("✅ Database successfully restored from backup!")
    except Exception as e:
        logger.exception(f"Error restoring database backup: {e}")
        await update.effective_message.reply_text(f"❌ Database restore failed: {e}")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
    if not await check_admin_permission(update):
        return

    user = update.effective_user
    name = html.escape(user.first_name if user else "Admin")
    settings = await get_current_settings()

    welcome_msg = (
        f"🤖 <b>Telegram Email Image Delivery Bot v1.0.0</b>\n\n"
        f"Welcome {name}! You are authenticated as a bot administrator.\n\n"
        f"📥 <b>Client Group</b>: {html.escape(settings.source_group_title or 'Unconfigured')}\n"
        f"📤 <b>Loader Group</b>: {html.escape(settings.delivery_group_title or 'Unconfigured')}\n\n"
        f"Type <code>/help</code> or <code>/setup</code> for guidance."
    )
    await update.effective_message.reply_text(welcome_msg, parse_mode="HTML")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /help command listing all commands."""
    if not await check_admin_permission(update):
        return

    help_msg = (
        "🛠 <b>Admin Commands & Management Suite</b>\n\n"
        "<b>Group Configuration:</b>\n"
        "• <code>/source</code> - Mark current group as Client Group\n"
        "• <code>/delivery</code> - Mark current group as Loader Group\n"
        "• <code>/paymentgroup</code> - Mark current group as Payment Review Group\n"
        "• <code>/A</code> - Set Client Group to Category A (Trusted)\n"
        "• <code>/B</code> - Set Client Group to Category B (Payment Review)\n"
        "• <code>/category</code> - View current group category\n"
        "• <code>/removecategory</code> - Remove group category\n"
        "• <code>/groups</code> - Show group status\n"
        "• <code>/resetgroups</code> - Reset all group settings\n"
        "• <code>/status</code> - Display bot status\n"
        "• <code>/setup</code> - View setup guide\n\n"
        "<b>Multi Loader Management:</b>\n"
        "• <code>/loaderadd</code> - Add a new Loader Group\n"
        "• <code>/loaderlist</code> - List all registered Loaders\n"
        "• <code>/loaderremove &lt;id&gt;</code> - Delete a Loader\n\n"
        "<b>Payment Review Workflow:</b>\n"
        "• <code>/approve &lt;id&gt;</code> - Approve Category B order & forward to Loader\n"
        "• <code>/reject &lt;id&gt;</code> - Reject Category B order\n\n"
        "<b>User Management:</b>\n"
        "• <code>/user delivery add &lt;id&gt;</code> - Add Delivery User\n"
        "• <code>/user delivery remove &lt;id&gt;</code> - Remove Delivery User\n"
        "• <code>/users</code> - List all authorized users\n\n"
        "<b>Order & Data Management:</b>\n"
        "• <code>/pending</code> - List pending orders\n"
        "• <code>/delivered</code> - List latest delivered orders\n"
        "• <code>/find &lt;id_or_email&gt;</code> - Search orders\n"
        "• <code>/order &lt;id&gt;</code> - Complete order information\n"
        "• <code>/cancel &lt;id&gt;</code> - Cancel a pending order\n"
        "• <code>/resend &lt;id&gt;</code> - Re-deliver an order\n"
        "• <code>/delete &lt;email&gt;</code> - Delete records for email\n"
        "• <code>/stats</code> - Rich statistics dashboard\n"
        "• <code>/export</code> - Export CSV report\n"
        "• <code>/backup</code> - Backup SQLite database\n"
        "• <code>/restore</code> - Restore SQLite database\n"
    )
    await update.effective_message.reply_text(help_msg, parse_mode="HTML")


async def removesource_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /removesource command."""
    if not await check_admin_permission(update):
        return

    await remove_source_group()
    await update.effective_message.reply_text("✅ Client Group Removed Successfully.", parse_mode="HTML")


async def removedelivery_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /removedelivery command."""
    if not await check_admin_permission(update):
        return

    await remove_delivery_group()
    await update.effective_message.reply_text("✅ Loader Group Removed Successfully.", parse_mode="HTML")
