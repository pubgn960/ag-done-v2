"""
Delivery engine handling media group aggregation, auto-splitting (max 10 items),
dispatching image albums to the Client Group with Email as First Image Caption,
Telegram API retries, database status updates, Loader confirmations, Telegram reactions,
and Category A Only Price Workflow in Client Group (client_chat_id & original_message_id).
Includes structured logging tags ([DELIVERY], [REACTION], [PRICE]).
"""

import html
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from telegram import Bot, Message, InputMediaPhoto, InputMediaDocument, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import TelegramError, RetryAfter, TimedOut, NetworkError

from config import Config
from email_parser import extract_last_email
from database import (
    BOT_SETTINGS,
    CLIENT_GROUPS_CACHE,
    get_order_by_id,
    get_all_orders_by_email,
    get_current_settings,
    mark_order_delivered,
    update_order_status,
    delete_orders_by_email,
    update_order_price,
    update_order_package_progress,
    get_delivery_session_by_msg_id,
    close_delivery_session
)
from models import Order, Image
from utils import (
    safe_set_message_reaction,
    get_test_price,
    calculate_test_price,
    parse_test_order_packages,
    format_package_summary_and_price,
    format_package_progress_summary,
    format_loader_card_summary,
    format_full_loader_order_card,
    format_delivered_packages_caption,
    build_loader_package_keyboard,
    mark_selected_packages_delivered,
    advance_package_progress,
    get_unknown_package_keyboard
)

logger = logging.getLogger(__name__)

MAX_MEDIA_PER_ALBUM = 10
MediaUnion = Union[InputMediaPhoto, InputMediaDocument]


def chunk_list(lst: List[Any], chunk_size: int = MAX_MEDIA_PER_ALBUM) -> List[List[Any]]:
    """Splits a list into sublists of maximum length chunk_size."""
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


async def send_media_group_with_retry(
    bot: Bot,
    chat_id: int,
    media: List[MediaUnion],
    reply_to_message_id: Optional[int] = None,
    max_retries: int = Config.MAX_RETRY,
    delay: float = Config.RETRY_DELAY
) -> Optional[List[Message]]:
    """Sends a media group to a Telegram chat with retry handling for rate limits and network glitches."""
    if not media:
        return []

    for attempt in range(1, max_retries + 1):
        try:
            msgs = await bot.send_media_group(
                chat_id=chat_id,
                media=media,
                reply_to_message_id=reply_to_message_id if attempt == 1 else None
            )
            return list(msgs) if msgs else []
        except RetryAfter as e:
            wait_time = e.retry_after + 1
            logger.warning(f"Telegram Rate Limit (RetryAfter). Waiting {wait_time}s (Attempt {attempt}/{max_retries})...")
            await asyncio.sleep(wait_time)
        except (TimedOut, NetworkError) as e:
            logger.warning(f"Network error during send_media_group: {e}. Retrying in {delay}s...")
            await asyncio.sleep(delay)
        except TelegramError as e:
            logger.error(f"Telegram API Error delivering album: {e}")
            break
        except Exception as e:
            logger.error(f"Unexpected error delivering media group: {e}")
            break

    return None


async def deliver_order_by_id(
    bot: Bot,
    order_id: int,
    loader_chat_id: Optional[int] = None,
    loader_reply_msg_id: Optional[int] = None,
    target_delivery_chat_id: Optional[int] = None,
    caption_text: Optional[str] = None,
    session_images: Optional[List[Image]] = None
) -> bool:
    """
    Delivers stored image albums for an order to the Client Group, placing Email as the caption
    of the FIRST image in the album (no separate text message or summary card sent to customer),
    adds ❤️ reactions to original customer message and loader delivery message, and notifies Loader Group.
    Supports Category A Only Price Workflow in Client Group:
    - Category A: Attaches '💰 Price' button in Client Group (client_chat_id & original_message_id).
    - Category B: Attaches NO Price buttons, keeping existing Category B workflow unchanged.
    """
    order = await get_order_by_id(order_id)
    auto_price_added = False

    if not order:
        logger.warning(f"[DELIVERY] Delivery attempted for Order #{order_id} but order was not found.")
        return False

    if session_images is not None and len(session_images) > 0:
        all_images: List[Image] = list(session_images)
    else:
        all_images = list(order.images) if order.images else []

    if not all_images:
        logger.warning(f"[DELIVERY] Delivery attempted for Order #{order_id} but no stored images were found.")
        if loader_chat_id and loader_reply_msg_id:
            try:
                await bot.send_message(
                    chat_id=loader_chat_id,
                    text=f"❌ Unable to deliver order #{order_id}: No stored images found.",
                    reply_to_message_id=loader_reply_msg_id
                )
            except Exception:
                pass
        return False

    # Duplicate delivery prevention
    if order.status == "Delivered":
        logger.info(f"[DELIVERY] Duplicate Delivery | Order #{order_id} is already delivered. Ignored.")
        if loader_chat_id and loader_reply_msg_id:
            try:
                await bot.send_message(
                    chat_id=loader_chat_id,
                    text=f"⚠️ This order (#{order_id}) has already been delivered.",
                    reply_to_message_id=loader_reply_msg_id
                )
            except Exception:
                pass
        return False

    if order.status == "Cancelled":
        logger.info(f"[DELIVERY] Cancelled Order | Delivery attempted for cancelled Order #{order_id}. Ignored.")
        if loader_chat_id and loader_reply_msg_id:
            try:
                await bot.send_message(
                    chat_id=loader_chat_id,
                    text=f"❌ Order #{order_id} has been cancelled.",
                    reply_to_message_id=loader_reply_msg_id
                )
            except Exception:
                pass
        return False

    # Determine Client Group Chat ID (from order, target parameter, or in-memory cache)
    client_chat_id = target_delivery_chat_id or order.client_chat_id or BOT_SETTINGS["source_group_id"]
    loader_group_id = loader_chat_id or order.loader_group_id or BOT_SETTINGS["delivery_group_id"]

    if not client_chat_id:
        logger.error(f"[DELIVERY] Delivery failed: Client Group ID not found for Order #{order_id}.")
        if loader_group_id and loader_reply_msg_id:
            try:
                await bot.send_message(
                    chat_id=loader_group_id,
                    text="❌ Client Group is not configured yet. Send <code>/source</code> in your Client Group.",
                    reply_to_message_id=loader_reply_msg_id,
                    parse_mode="HTML"
                )
            except Exception:
                pass
        return False

    total_images = len(all_images)

    # Determine Email for First Image Caption: extract last email from loader caption or fallback to DB order.email
    caption_email = extract_last_email(caption_text)
    email_for_caption = caption_email if caption_email else order.email

    # Fetch active delivery session to determine packages selected for THIS session
    active_ds = None
    selected_delivery_items: List[Dict[str, Any]] = []
    if loader_reply_msg_id:
        active_ds = await get_delivery_session_by_msg_id(loader_reply_msg_id)
        if active_ds and active_ds.selected_packages:
            try:
                selected_delivery_items = json.loads(active_ds.selected_packages)
            except Exception:
                selected_delivery_items = []

    # Get current package progress items from DB or initialize from raw_text
    if order.package_progress:
        try:
            progress_items = json.loads(order.package_progress)
        except Exception:
            progress_items = []
    else:
        full_content = order.raw_text or order.package or caption_text or ""
        parsed_pkg = parse_test_order_packages(full_content)
        if parsed_pkg:
            progress_items = [
                {"package": item["package"], "qty": item["qty"], "unit_price": item["unit_price"], "status": "Pending"}
                for item in parsed_pkg["packages"]
            ]
        else:
            progress_items = []

    # If no selected items from active session, fallback to currently 'Selected' items or next pending
    if not selected_delivery_items:
        selected_delivery_items = [it for it in progress_items if it.get("status") == "Selected"]
        if not selected_delivery_items:
            for it in progress_items:
                if it.get("status") != "Delivered":
                    selected_delivery_items = [it]
                    break

    # Build screenshot caption containing ONLY packages delivered in this session
    delivered_caption_block = format_delivered_packages_caption(selected_delivery_items)
    if delivered_caption_block and "📦 Delivered Package" not in email_for_caption:
        email_for_caption = f"{email_for_caption}\n\n{delivered_caption_block}"

    # Auto-save price if missing
    if not order.price:
        full_content = order.raw_text or order.package or caption_text or ""
        parsed_pkg = parse_test_order_packages(full_content)
        if parsed_pkg and parsed_pkg.get("total_price"):
            await update_order_price(order.id, price_str=f"{parsed_pkg['total_price']:g}")
            auto_price_added = True
    else:
        auto_price_added = True

    logger.info(f"[DELIVERY] Delivering Order #{order_id} ({total_images} images, caption email: '{email_for_caption}') to Client Group {client_chat_id}")

    # 1. Dispatch image albums to Client Group in batches of max 10 items (grouped by file_type)
    grouped_batches: List[List[Image]] = []
    current_batch: List[Image] = []

    for img in all_images:
        if not current_batch:
            current_batch.append(img)
        elif len(current_batch) >= MAX_MEDIA_PER_ALBUM or current_batch[0].file_type != img.file_type:
            grouped_batches.append(current_batch)
            current_batch = [img]
        else:
            current_batch.append(img)

    if current_batch:
        grouped_batches.append(current_batch)

    last_sent_customer_msg_id = None
    delivered_count = 0
    for idx, batch in enumerate(grouped_batches):
        media_group: List[MediaUnion] = []
        for img in batch:
            item_caption = email_for_caption if idx == 0 and len(media_group) == 0 else None
            if img.file_type == "document":
                media_group.append(InputMediaDocument(media=img.telegram_file_id, caption=item_caption))
            else:
                media_group.append(InputMediaPhoto(media=img.telegram_file_id, caption=item_caption))

        if idx > 0:
            await asyncio.sleep(1.0)

        sent = await send_media_group_with_retry(
            bot=bot,
            chat_id=client_chat_id,
            media=media_group,
            reply_to_message_id=order.original_message_id if idx == 0 else None
        )
        if sent:
            delivered_count += len(batch)
            if len(sent) > 0:
                last_sent_customer_msg_id = sent[0].message_id

    # 2. Mark package progress as Delivered and update Order cards and status
    loader_user_id = active_ds.loader_id if active_ds else 0
    updated_items, is_all_completed, delivered_cnt = mark_selected_packages_delivered(progress_items, loader_id=loader_user_id)
    updated_progress_json = json.dumps(updated_items)
    await update_order_package_progress(order.id, updated_progress_json)
    order.package_progress = updated_progress_json

    if is_all_completed:
        await mark_order_delivered(order.id)
        logger.info(f"[DELIVERY] Order Completed | Order #{order.id} ALL packages delivered!")
    else:
        await update_order_status(order.id, "Partially Delivered")
        logger.info(f"[DELIVERY] Partial Delivery Completed | Order #{order.id} ({delivered_cnt} packages delivered, remaining packages pending).")

    # Trigger Delivery Ledger Accounting in Customer Group (Client Group)
    newly_delivered_names = []
    if active_ds and active_ds.selected_packages:
        try:
            sel_list = json.loads(active_ds.selected_packages)
            newly_delivered_names = [it["package"] for it in sel_list if isinstance(it, dict) and "package" in it]
        except Exception:
            pass

    if not newly_delivered_names and progress_items:
        newly_delivered_names = [it["package"] for it in progress_items if isinstance(it, dict) and "package" in it]

    newly_delivered_str = "+".join(newly_delivered_names) if newly_delivered_names else (order.package or "")
    session_key = active_ds.delivery_session_message_id if active_ds else loader_reply_msg_id
    dedup_hash = f"{order.id}:{newly_delivered_str}:{session_key}"
    loader_name_str = f"Loader #{active_ds.loader_id}" if active_ds and active_ds.loader_id else "Loader"
    customer_target_msg_id = last_sent_customer_msg_id or order.original_message_id

    try:
        from handlers import process_delivery_ledger_event
        await process_delivery_ledger_event(
            order_id=order.id,
            package_str=newly_delivered_str,
            loader_name=loader_name_str,
            bot=bot,
            chat_id=client_chat_id,  # ALWAYS Customer Group (Client Group)
            dedup_hash=dedup_hash,
            reply_to_message_id=customer_target_msg_id  # Reply to Customer delivery message
        )
    except Exception as e_led:
        logger.exception(f"[LEDGER] Failed to process delivery ledger event for Order #{order.id}: {e_led}")

    try:
        from database import execute_auto_delivery_total
        from utils import calculate_delivered_packages_value
        val, ok_p = calculate_delivered_packages_value(newly_delivered_str)
        if ok_p and val is not None and val > 0:
            await execute_auto_delivery_total(order.id, val)
    except Exception as e_rt:
        logger.exception(f"[RUNNING_TOTAL] Failed to auto record running total for Order #{order.id}: {e_rt}")

    # Update Client Group Summary card if price_msg_id exists
    if order.price_msg_id and client_chat_id:
        try:
            client_summary = format_package_progress_summary(updated_items, order.price)
            await bot.edit_message_text(chat_id=client_chat_id, message_id=order.price_msg_id, text=client_summary)
        except Exception as e_c:
            logger.warning(f"Failed to edit Client Group progress card: {e_c}")

    # Update Loader Group Order Card in-place
    if order.loader_message_id and loader_group_id:
        try:
            loader_summary = format_full_loader_order_card(order)
            loader_kb = build_loader_package_keyboard(order.id, updated_items, None)
            try:
                await bot.edit_message_caption(chat_id=loader_group_id, message_id=order.loader_message_id, caption=loader_summary, reply_markup=loader_kb)
            except Exception:
                await bot.edit_message_text(chat_id=loader_group_id, message_id=order.loader_message_id, text=loader_summary, reply_markup=loader_kb)
        except Exception as e_l:
            logger.warning(f"Failed to edit Loader Group progress card: {e_l}")

    # Close active Delivery Session in DB if present
    if loader_reply_msg_id:
        try:
            ds = await get_delivery_session_by_msg_id(loader_reply_msg_id)
            if ds:
                await close_delivery_session(ds.id)
        except Exception:
            pass

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    logger.info(f"[DELIVERY] Images sent | Order #{order.id} ({delivered_count}/{total_images} images) delivered to Client Group {client_chat_id}.")

    # 3. Reaction On Customer Order (Add ❤️ reaction to original customer order message in Client Group)
    if order.original_message_id and client_chat_id:
        cust_reacted = await safe_set_message_reaction(
            bot=bot,
            chat_id=client_chat_id,
            message_id=order.original_message_id,
            emoji="❤️",
            fallback_emoji=None,
            log_tag="[REACTION]"
        )
        if cust_reacted:
            logger.info("[REACTION] ❤️ Customer delivery completed")
        else:
            logger.warning("Reaction not supported.")

    # 4. Reaction On Loader Messages & Plain Notice to Loader Group
    if loader_group_id:
        target_reply_target = loader_reply_msg_id or order.loader_message_id

        # A. React ❤️ to the bot-generated order card message in Loader Group (order.loader_message_id)
        if order.loader_message_id:
            bot_msg_reacted = await safe_set_message_reaction(
                bot=bot,
                chat_id=loader_group_id,
                message_id=order.loader_message_id,
                emoji="❤️",
                fallback_emoji=None,
                log_tag="[REACTION]"
            )
            if bot_msg_reacted:
                logger.info("[REACTION] ❤️ Added to loader order message.")
            else:
                logger.warning("Reaction to loader order message failed or not supported.")

        # B. React ❤️ to the loader's uploaded screenshot/reply message (loader_reply_msg_id)
        if loader_reply_msg_id and loader_reply_msg_id != order.loader_message_id:
            loader_screenshot_reacted = await safe_set_message_reaction(
                bot=bot,
                chat_id=loader_group_id,
                message_id=loader_reply_msg_id,
                emoji="❤️",
                fallback_emoji=None,
                log_tag="[REACTION]"
            )
            if loader_screenshot_reacted:
                logger.info("[REACTION] ❤️ Added to loader screenshot.")
            else:
                logger.warning("Reaction to loader screenshot failed or not supported.")

        loader_notice = (
            f"✅ <b>DELIVERED</b>\n\n"
            f"<b>Order ID:</b>\n#{order.id}\n\n"
            f"<b>Images:</b>\n{total_images}\n\n"
            f"<b>Delivered:</b>\n{now_str}"
        )
        try:
            await bot.send_message(
                chat_id=loader_group_id,
                text=loader_notice,
                reply_to_message_id=target_reply_target,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"[DELIVERY] Failed to send loader delivery confirmation: {e}")

    # 5. Category A Only Price Workflow in CLIENT GROUP (client_chat_id & original_message_id)
    # TEST IMPLEMENTATION: If automatic price was added (e.g. 2400 CP), skip sending Price Button UI completely
    if auto_price_added:
        logger.info(f"[PRICE] Automatic price was added for Order #{order.id}. Skipping Price Button UI.")
        if Config.DELETE_AFTER_DELIVERY:
            logger.info(f"DELETE_AFTER_DELIVERY enabled. Purging order #{order.id} for email: '{order.email}'")
            await delete_orders_by_email(order.email)
        return True

    order_category = order.category or (CLIENT_GROUPS_CACHE.get(client_chat_id, "A") if client_chat_id else "A")
    if order_category == "A" and client_chat_id:
        if order.price:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✏️ Edit Price", callback_data=f"price_edit:{order.id}")]])
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("💰 Price", callback_data=f"price_set:{order.id}")]])
        try:
            sent_btn_msg = await bot.send_message(
                chat_id=client_chat_id,
                text="💰 Click below to set price:",
                reply_to_message_id=order.original_message_id,
                reply_markup=keyboard
            )
            await update_order_price(order.id, price_str=order.price, price_msg_id=sent_btn_msg.message_id)
            logger.info(f"[PRICE] Category A Price button sent to Client Group {client_chat_id} for Order #{order.id}.")
        except Exception as e:
            logger.exception(f"[PRICE] Failed to send Price button to Client Group: {e}")

    # 6. Optional post-delivery cleanup
    if Config.DELETE_AFTER_DELIVERY:
        logger.info(f"DELETE_AFTER_DELIVERY enabled. Purging order #{order.id} for email: '{order.email}'")
        await delete_orders_by_email(order.email)

    return True


async def deliver_images_for_email(
    bot: Bot,
    chat_id: int,
    email: str,
    reply_to_message_id: Optional[int] = None
) -> bool:
    """Finds pending orders for email and delivers them to target chat (used by /resend)."""
    email_clean = email.lower().strip()
    orders = await get_all_orders_by_email(email_clean)

    if not orders:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text="❌ No orders found for this email.",
                reply_to_message_id=reply_to_message_id
            )
        except Exception:
            pass
        return False

    success = False
    for order in orders:
        res = await deliver_order_by_id(
            bot=bot,
            order_id=order.id,
            target_delivery_chat_id=chat_id
        )
        if res:
            success = True

    return success
