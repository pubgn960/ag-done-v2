"""
Delivery engine handling media group aggregation, auto-splitting (max 10 items),
dispatching image albums to the Client Group with Email as First Image Caption,
Telegram API retries, database status updates, Loader confirmations, Telegram reactions,
and Category A Only Price Workflow in Client Group (client_chat_id & original_message_id).
Includes structured logging tags ([DELIVERY], [REACTION], [PRICE]).
"""

import html
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Union, Optional, Any
from telegram import Bot, InputMediaPhoto, InputMediaDocument, InlineKeyboardMarkup, InlineKeyboardButton
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
    delete_orders_by_email,
    update_order_price
)
from models import Order, Image
from utils import safe_set_message_reaction, get_test_price

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
) -> bool:
    """Sends a media group to a Telegram chat with retry handling for rate limits and network glitches."""
    if not media:
        return True

    for attempt in range(1, max_retries + 1):
        try:
            await bot.send_media_group(
                chat_id=chat_id,
                media=media,
                reply_to_message_id=reply_to_message_id if attempt == 1 else None
            )
            return True
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

    return False


async def deliver_order_by_id(
    bot: Bot,
    order_id: int,
    loader_chat_id: Optional[int] = None,
    loader_reply_msg_id: Optional[int] = None,
    target_delivery_chat_id: Optional[int] = None,
    caption_text: Optional[str] = None
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

    if not order or not order.images:
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

    all_images: List[Image] = list(order.images)
    total_images = len(all_images)

    # Determine Email for First Image Caption: extract last email from loader caption or fallback to DB order.email
    caption_email = extract_last_email(caption_text)
    email_for_caption = caption_email if caption_email else order.email

    # TEST IMPLEMENTATION (Proof of Concept):
    # Detect automatic price for 2400 CP package variations (returns numeric float 15.5 or None)
    # Uses complete order content (caption text, package description, email, or order.price)
    full_order_content = f"{caption_text or ''}\n{order.package or ''}\n{order.email or ''}"
    test_price_val = get_test_price(full_order_content)
    auto_price_added = False

    if test_price_val is not None:
        price_str = f"{test_price_val:g}" if isinstance(test_price_val, float) else str(test_price_val)
        if "💰 Price:" not in email_for_caption:
            email_for_caption = f"{email_for_caption}\n\n💰 Price: {price_str}"
        if not order.price:
            await update_order_price(order.id, price_str=price_str)
        auto_price_added = True
    elif order.price:
        if "💰 Price:" not in email_for_caption:
            email_for_caption = f"{email_for_caption}\n\n💰 Price: {order.price}"

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

    delivered_count = 0
    for idx, batch in enumerate(grouped_batches):
        media_group: List[MediaUnion] = []
        for b_idx, img in enumerate(batch):
            # Only the FIRST image of the FIRST batch gets the email as caption
            item_caption = email_for_caption if (idx == 0 and b_idx == 0) else None

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

    # 2. Mark order status as Delivered in DB
    updated_order = await mark_order_delivered(order.id)
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

    # 4. Reaction On Loader Delivery Message & Plain Notice to Loader Group (NO Price UI in Loader Group!)
    target_loader_msg_id = loader_reply_msg_id or order.loader_message_id
    if loader_group_id and target_loader_msg_id:
        loader_reacted = await safe_set_message_reaction(
            bot=bot,
            chat_id=loader_group_id,
            message_id=target_loader_msg_id,
            emoji="❤️",
            fallback_emoji=None,
            log_tag="[REACTION]"
        )
        if loader_reacted:
            logger.info("[REACTION] ❤️ Loader delivery")
        else:
            logger.warning("Reaction not supported.")

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
                reply_to_message_id=target_loader_msg_id,
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
