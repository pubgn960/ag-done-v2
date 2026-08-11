"""
Media Group Collector module supporting debounced album buffering for Reply-Based Delivery Workflow.
Buffers incoming images/documents replied to an order message, links them via Order ID,
and triggers automated Delivery Group dispatching upon completion.
Includes caption text capture for Loader caption email overrides.
Also maintains short-lived user email session tracking.
"""

import time
import asyncio
import logging
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple, Any
from telegram import Message, Bot

from config import Config
from database import add_images_to_order

logger = logging.getLogger(__name__)


class UserSessionManager:
    """
    Manages short-lived (5-minute) user email session tracking.
    Stores email addresses sent by users to correlate subsequent messages within timeout.
    """

    def __init__(self, timeout: float = Config.USER_SESSION_TIMEOUT):
        self.timeout = timeout
        self.sessions: Dict[int, Tuple[str, float]] = {}

    def update_session(self, user_id: int, email: str) -> None:
        """Stores user email with current timestamp."""
        self.sessions[user_id] = (email.lower().strip(), time.time())

    def get_session_email(self, user_id: int) -> Optional[str]:
        """Retrieves user email if within session timeout window."""
        if user_id not in self.sessions:
            return None
        email, timestamp = self.sessions[user_id]
        if time.time() - timestamp > self.timeout:
            del self.sessions[user_id]
            return None
        return email


class MediaGroupCollector:
    """
    In-memory debouncer and collector for Telegram photo albums and single photo/document messages
    replied to an Order Notification in the Loader/Source Group.
    """

    def __init__(self, timeout: float = Config.MEDIA_GROUP_TIMEOUT):
        self.timeout = timeout
        # Structure: buffer_key -> { "order_id": int, "email": str, "loader_chat_id": int, "loader_msg_id": int, "items": [(msg_id, file_id, file_type)], "caption_texts": [str], "task": Task }
        self._buffers: Dict[str, Dict[str, Any]] = {}
        # LRU cache for processed album keys
        self._processed_cache: OrderedDict = OrderedDict()
        self._lock: asyncio.Lock = asyncio.Lock()

    async def add_reply_media_message(
        self,
        message: Message,
        order_id: int,
        email: str,
        bot: Bot,
        caption_text: Optional[str] = None
    ) -> None:
        """
        Buffers an incoming photo or photo-document message that is a reply to an Order Notification.

        Args:
            message (Message): Telegram message object containing photo or document.
            order_id (int): Extracted target Order ID.
            email (str): Extracted target customer email.
            bot (Bot): Telegram bot instance for triggering delivery.
            caption_text (Optional[str]): Caption or text content from Loader reply message.
        """
        file_id: Optional[str] = None
        file_type: str = "photo"

        # 1. Extract Photo or Photo-Document file_id
        if message.photo:
            file_id = message.photo[-1].file_id
            file_type = "photo"
        elif message.document:
            mime = message.document.mime_type or ""
            if mime.startswith("image/"):
                file_id = message.document.file_id
                file_type = "document"

        if not file_id:
            logger.warning(f"[LOADER] Received non-image document or unsupported media in message {message.message_id}")
            return

        msg_id = message.message_id
        media_group_id = message.media_group_id
        loader_chat_id = message.chat.id
        txt = caption_text or message.caption or message.text or ""

        # Unique buffer key for media group or single message
        buffer_key = f"{order_id}_{media_group_id}" if media_group_id else f"{order_id}_single_{msg_id}"

        # Single Photo / Document (No media group ID)
        if not media_group_id:
            logger.info(f"[LOADER] Album collected (1 {file_type}) for Order #{order_id} (Loader Msg ID: {msg_id})")
            updated_order, is_dup = await add_images_to_order(
                order_id=order_id,
                file_items=[(file_id, file_type)],
                media_group_id=None
            )

            if is_dup:
                logger.info(f"[LOADER] Duplicate Ignored | Single photo upload for Order #{order_id}")
                return

            if updated_order:
                # Trigger instant delivery
                from delivery import deliver_order_by_id
                await deliver_order_by_id(
                    bot=bot,
                    order_id=order_id,
                    loader_chat_id=loader_chat_id,
                    loader_reply_msg_id=msg_id,
                    caption_text=txt
                )
            return

        # Media Group (Album) - Thread safe buffering
        async with self._lock:
            if buffer_key in self._processed_cache:
                logger.info(f"[LOADER] Duplicate Ignored | Media Group '{media_group_id}' for Order #{order_id}")
                return

            if buffer_key in self._buffers:
                buf = self._buffers[buffer_key]
                if buf.get("task") and not buf["task"].done():
                    buf["task"].cancel()
                buf["items"].append((msg_id, file_id, file_type))
                if txt:
                    buf["caption_texts"].append(txt)
            else:
                self._buffers[buffer_key] = {
                    "order_id": order_id,
                    "email": email,
                    "media_group_id": media_group_id,
                    "loader_chat_id": loader_chat_id,
                    "loader_msg_id": msg_id,
                    "bot": bot,
                    "items": [(msg_id, file_id, file_type)],
                    "caption_texts": [txt] if txt else [],
                    "task": None
                }

            # Schedule debounced flush task
            task = asyncio.create_task(self._schedule_flush(buffer_key))
            self._buffers[buffer_key]["task"] = task

    async def _schedule_flush(self, buffer_key: str) -> None:
        """Waits for debounce timeout before flushing media group to database."""
        try:
            await asyncio.sleep(self.timeout)
            await self._flush_media_group(buffer_key)
        except asyncio.CancelledError:
            pass

    async def _flush_media_group(self, buffer_key: str) -> None:
        """Flushes buffered album, saves images to Order ID, and triggers automatic delivery."""
        async with self._lock:
            buf = self._buffers.pop(buffer_key, None)

        if not buf or not buf.get("items"):
            return

        items: List[Tuple[int, str, str]] = buf["items"]
        items.sort(key=lambda x: x[0])  # Sort by message_id to preserve original sequence

        order_id: int = buf["order_id"]
        media_group_id: str = buf["media_group_id"]
        loader_chat_id: int = buf["loader_chat_id"]
        loader_msg_id: int = buf["loader_msg_id"]
        bot: Bot = buf["bot"]
        caption_texts: List[str] = buf.get("caption_texts", [])
        combined_caption = "\n".join(caption_texts) if caption_texts else None

        file_items = [(it[1], it[2]) for it in items]
        logger.info(f"[LOADER] Album collected ({len(file_items)} images) for Order #{order_id} (Media Group: '{media_group_id}')")

        updated_order, is_dup = await add_images_to_order(
            order_id=order_id,
            file_items=file_items,
            media_group_id=media_group_id
        )

        async with self._lock:
            self._processed_cache[buffer_key] = True
            if len(self._processed_cache) > 1000:
                self._processed_cache.popitem(last=False)

        if is_dup:
            logger.info(f"[LOADER] Duplicate Ignored | Media Group '{media_group_id}' for Order #{order_id}")
            return

        if updated_order:
            # Trigger automatic delivery to Client Group
            from delivery import deliver_order_by_id
            await deliver_order_by_id(
                bot=bot,
                order_id=order_id,
                loader_chat_id=loader_chat_id,
                loader_reply_msg_id=loader_msg_id,
                caption_text=combined_caption
            )


# Singletons
user_session_manager = UserSessionManager()
media_collector = MediaGroupCollector()
