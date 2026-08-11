"""
Database manager providing asynchronous SQLAlchemy 2.0 session management, CRUD operations,
Order tracking for two-group reply-based workflow, SHA256 fingerprint deduplication, CSV export,
backup/restore, detailed statistics dashboard, Multi-Loader Category B management, and Category A Price Workflow.
Includes global in-memory BOT_SETTINGS, AUTH_USERS_CACHE, CLIENT_GROUPS_CACHE, and LOADERS_CACHE for high-performance zero-query filtering.
"""

import os
import io
import csv
import shutil
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple, Dict, Any
from sqlalchemy import select, func, delete, update
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from config import Config
from models import Base, Order, Image, Settings, AuthorizedUser, ClientGroup, Loader

logger = logging.getLogger(__name__)

# Extra connect_args for SQLite to prevent locking under concurrency
engine_args: Dict[str, Any] = {"echo": False, "future": True}
if Config.DATABASE_URL.startswith("sqlite"):
    engine_args["connect_args"] = {"timeout": 30}

# SQLAlchemy Async Engine initialization
engine = create_async_engine(Config.DATABASE_URL, **engine_args)

# Async Session Maker
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Global in-memory settings cache to avoid querying database on every message update
BOT_SETTINGS: Dict[str, Any] = {
    "source_group_id": None,
    "delivery_group_id": None,
    "payment_review_group_id": Config.PAYMENT_REVIEW_GROUP_ID,
    "source_group_title": None,
    "delivery_group_title": None,
    "payment_review_group_title": "Payment Review Group"
}

# Global in-memory user permission cache: telegram_user_id -> role ('admin' or 'delivery')
AUTH_USERS_CACHE: Dict[int, str] = {}

# Global in-memory client group category cache: chat_id -> category ('A' or 'B')
CLIENT_GROUPS_CACHE: Dict[int, str] = {}

# Global in-memory loaders cache: loader_id -> {"id": ..., "name": ..., "group_id": ...}
LOADERS_CACHE: Dict[int, Dict[str, Any]] = {}


async def dispose_engine() -> None:
    """Disposes active database engine connection pool."""
    logger.info("Disposing database connection engine...")
    await engine.dispose()


def compute_fingerprint(email: str, file_ids: List[str]) -> str:
    """
    Computes a unique SHA256 fingerprint for an upload.
    Formula: SHA256(email + image_count + sorted_file_ids)
    """
    email_clean = email.lower().strip()
    count_str = str(len(file_ids))
    sorted_ids = "".join(sorted(file_ids))
    raw_payload = f"{email_clean}:{count_str}:{sorted_ids}"
    return hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()


async def init_db() -> None:
    """Initializes database schema and default Settings, AuthorizedUsers, ClientGroups, and Loaders records."""
    logger.info("Initializing database tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialized successfully.")

    await get_or_create_settings()
    await reload_auth_users_cache()
    await reload_bot_settings_cache()
    await reload_loaders_cache()


# ==========================================
# Dynamic Settings Operations & Cache
# ==========================================

async def get_or_create_settings() -> Settings:
    """Retrieves or initializes the single Settings record (id=1)."""
    async with AsyncSessionLocal() as session:
        stmt = select(Settings).where(Settings.id == 1)
        res = await session.execute(stmt)
        settings = res.scalar_one_or_none()

        if not settings:
            settings = Settings(
                id=1,
                source_group_id=None,
                source_group_title=None,
                delivery_group_id=None,
                delivery_group_title=None,
                payment_review_group_id=Config.PAYMENT_REVIEW_GROUP_ID,
                payment_review_group_title="Payment Review Group",
                updated_at=datetime.now(timezone.utc)
            )
            session.add(settings)
            await session.commit()
            await session.refresh(settings)
            logger.info("Initialized default Settings record in database.")

        return settings


async def get_current_settings() -> Settings:
    """Retrieves current Settings record directly from database."""
    return await get_or_create_settings()


async def reload_bot_settings_cache() -> Dict[str, Any]:
    """
    Loads Settings and ClientGroups records from database once and populates global in-memory caches.
    Outputs structured [CACHE] logs on startup.
    """
    settings = await get_or_create_settings()
    BOT_SETTINGS["source_group_id"] = settings.source_group_id
    BOT_SETTINGS["delivery_group_id"] = settings.delivery_group_id
    BOT_SETTINGS["payment_review_group_id"] = getattr(settings, "payment_review_group_id", None) or Config.PAYMENT_REVIEW_GROUP_ID
    BOT_SETTINGS["source_group_title"] = settings.source_group_title
    BOT_SETTINGS["delivery_group_title"] = settings.delivery_group_title
    BOT_SETTINGS["payment_review_group_title"] = getattr(settings, "payment_review_group_title", None) or "Payment Review Group"

    # Pre-load Client Groups into CLIENT_GROUPS_CACHE in RAM
    async with AsyncSessionLocal() as session:
        stmt = select(ClientGroup)
        res = await session.execute(stmt)
        groups = list(res.scalars().all())

        CLIENT_GROUPS_CACHE.clear()
        for g in groups:
            CLIENT_GROUPS_CACHE[g.chat_id] = g.category

    if settings.source_group_id and settings.source_group_id not in CLIENT_GROUPS_CACHE:
        CLIENT_GROUPS_CACHE[settings.source_group_id] = "A"

    src_id = BOT_SETTINGS["source_group_id"]
    del_id = BOT_SETTINGS["delivery_group_id"]
    pay_id = BOT_SETTINGS["payment_review_group_id"]

    logger.info("[CACHE]")
    if src_id:
        logger.info(f"[CACHE] Source Group Loaded: {src_id}")
    if del_id:
        logger.info(f"[CACHE] Delivery Group Loaded: {del_id}")
    if pay_id:
        logger.info(f"[CACHE] Payment Review Group Loaded: {pay_id}")
    logger.info(f"[CACHE] Loaded {len(CLIENT_GROUPS_CACHE)} Client Group Category mapping(s) into memory.")

    return BOT_SETTINGS


async def update_source_group(chat_id: int, title: str) -> Settings:
    """
    Updates the Source Group (Client Group) configuration in database,
    commits transaction, and immediately updates the global BOT_SETTINGS cache.
    """
    logger.info(f"[SOURCE] Saving Source Group: {chat_id}")
    async with AsyncSessionLocal() as session:
        stmt = select(Settings).where(Settings.id == 1)
        res = await session.execute(stmt)
        settings = res.scalar_one_or_none()

        if not settings:
            settings = Settings(
                id=1,
                source_group_id=chat_id,
                source_group_title=title,
                delivery_group_id=None,
                delivery_group_title=None,
                payment_review_group_id=Config.PAYMENT_REVIEW_GROUP_ID,
                payment_review_group_title="Payment Review Group",
                updated_at=datetime.now(timezone.utc)
            )
            session.add(settings)
        else:
            settings.source_group_id = chat_id
            settings.source_group_title = title
            settings.updated_at = datetime.now(timezone.utc)

        await session.commit()
        logger.info("[SOURCE] Database commit successful.")

    # Immediately refresh in-memory cache
    await reload_bot_settings_cache()
    logger.info(f"[SOURCE] Source Group saved: {chat_id}")
    return settings


async def update_delivery_group(chat_id: int, title: str) -> Settings:
    """
    Updates the Delivery Group (Loader Group) configuration in database,
    commits transaction, and immediately updates the global BOT_SETTINGS cache.
    """
    logger.info(f"[DELIVERY_GROUP] Saving Delivery Group: {chat_id}")
    async with AsyncSessionLocal() as session:
        stmt = select(Settings).where(Settings.id == 1)
        res = await session.execute(stmt)
        settings = res.scalar_one_or_none()

        if not settings:
            settings = Settings(
                id=1,
                source_group_id=None,
                source_group_title=None,
                delivery_group_id=chat_id,
                delivery_group_title=title,
                payment_review_group_id=Config.PAYMENT_REVIEW_GROUP_ID,
                payment_review_group_title="Payment Review Group",
                updated_at=datetime.now(timezone.utc)
            )
            session.add(settings)
        else:
            settings.delivery_group_id = chat_id
            settings.delivery_group_title = title
            settings.updated_at = datetime.now(timezone.utc)

        await session.commit()
        logger.info("[DELIVERY_GROUP] Database commit successful.")

    # Immediately refresh in-memory cache
    await reload_bot_settings_cache()
    logger.info(f"[DELIVERY_GROUP] Delivery Group saved: {chat_id}")
    return settings


async def update_payment_review_group(chat_id: int, title: str) -> Settings:
    """
    Updates the Payment Review Group configuration in database and refreshes BOT_SETTINGS cache.
    """
    logger.info(f"[PAYMENT] Saving Payment Review Group: {chat_id}")
    async with AsyncSessionLocal() as session:
        stmt = select(Settings).where(Settings.id == 1)
        res = await session.execute(stmt)
        settings = res.scalar_one_or_none()

        if not settings:
            settings = Settings(
                id=1,
                source_group_id=None,
                source_group_title=None,
                delivery_group_id=None,
                delivery_group_title=None,
                payment_review_group_id=chat_id,
                payment_review_group_title=title,
                updated_at=datetime.now(timezone.utc)
            )
            session.add(settings)
        else:
            settings.payment_review_group_id = chat_id
            settings.payment_review_group_title = title
            settings.updated_at = datetime.now(timezone.utc)

        await session.commit()
        logger.info("[PAYMENT] Database commit successful.")

    await reload_bot_settings_cache()
    logger.info(f"[PAYMENT] Payment Review Group saved: {chat_id}")
    return settings


async def remove_source_group() -> Settings:
    """Removes Client Group configuration from database and refreshes in-memory cache."""
    async with AsyncSessionLocal() as session:
        stmt = select(Settings).where(Settings.id == 1)
        res = await session.execute(stmt)
        settings = res.scalar_one_or_none()

        if settings:
            settings.source_group_id = None
            settings.source_group_title = None
            settings.updated_at = datetime.now(timezone.utc)
            await session.commit()

    await reload_bot_settings_cache()
    return await get_current_settings()


async def remove_delivery_group() -> Settings:
    """Removes Loader Group configuration from database and refreshes in-memory cache."""
    async with AsyncSessionLocal() as session:
        stmt = select(Settings).where(Settings.id == 1)
        res = await session.execute(stmt)
        settings = res.scalar_one_or_none()

        if settings:
            settings.delivery_group_id = None
            settings.delivery_group_title = None
            settings.updated_at = datetime.now(timezone.utc)
            await session.commit()

    await reload_bot_settings_cache()
    return await get_current_settings()


async def reset_groups() -> Settings:
    """Resets both Client and Loader Group configurations in database and refreshes in-memory cache."""
    async with AsyncSessionLocal() as session:
        stmt = select(Settings).where(Settings.id == 1)
        res = await session.execute(stmt)
        settings = res.scalar_one_or_none()

        if settings:
            settings.source_group_id = None
            settings.source_group_title = None
            settings.delivery_group_id = None
            settings.delivery_group_title = None
            settings.payment_review_group_id = Config.PAYMENT_REVIEW_GROUP_ID
            settings.payment_review_group_title = "Payment Review Group"
            settings.updated_at = datetime.now(timezone.utc)
            await session.commit()

    await reload_bot_settings_cache()
    return await get_current_settings()


# ==========================================
# Multi-Loader Operations & Cache
# ==========================================

async def reload_loaders_cache() -> Dict[int, Dict[str, Any]]:
    """Loads all registered loaders from DB into LOADERS_CACHE in RAM."""
    async with AsyncSessionLocal() as session:
        stmt = select(Loader).order_by(Loader.id)
        res = await session.execute(stmt)
        loaders = list(res.scalars().all())

        LOADERS_CACHE.clear()
        for l in loaders:
            LOADERS_CACHE[l.id] = {
                "id": l.id,
                "name": l.loader_name,
                "group_id": l.group_id
            }

    logger.info(f"[CACHE] Loaded {len(LOADERS_CACHE)} Loader(s) into memory.")
    return LOADERS_CACHE


async def add_loader(group_id: int, loader_name: str) -> Loader:
    """Adds or updates a Loader in DB and refreshes LOADERS_CACHE."""
    async with AsyncSessionLocal() as session:
        stmt = select(Loader).where(Loader.group_id == group_id)
        res = await session.execute(stmt)
        loader = res.scalar_one_or_none()

        if not loader:
            loader = Loader(
                loader_name=loader_name,
                group_id=group_id,
                created_at=datetime.now(timezone.utc)
            )
            session.add(loader)
        else:
            loader.loader_name = loader_name

        await session.commit()

    await reload_loaders_cache()
    logger.info(f"[LOADER_MGMT] Added loader '{loader_name}' with Group ID {group_id}.")
    return loader


async def remove_loader_by_id(loader_id: int) -> bool:
    """Removes a Loader by ID from DB and refreshes LOADERS_CACHE."""
    async with AsyncSessionLocal() as session:
        stmt = delete(Loader).where(Loader.id == loader_id)
        res = await session.execute(stmt)
        await session.commit()
        count = res.rowcount

    await reload_loaders_cache()
    logger.info(f"[LOADER_MGMT] Removed loader ID {loader_id}.")
    return count > 0


async def get_all_loaders() -> List[Loader]:
    """Retrieves all registered loaders from database."""
    async with AsyncSessionLocal() as session:
        stmt = select(Loader).order_by(Loader.id)
        res = await session.execute(stmt)
        return list(res.scalars().all())


# ==========================================
# Client Group Category Routing Operations
# ==========================================

async def set_client_group_category(chat_id: int, title: str, category: str) -> ClientGroup:
    """
    Sets or updates Client Group category ('A' or 'B') in DB and refreshes CLIENT_GROUPS_CACHE.
    Category A: Trusted Groups (Direct to Loader Group)
    Category B: Payment Required Groups (Forward to Payment Review Group -1004441603990)
    """
    cat_clean = category.upper().strip()
    if cat_clean not in ("A", "B"):
        cat_clean = "A"

    async with AsyncSessionLocal() as session:
        stmt = select(ClientGroup).where(ClientGroup.chat_id == chat_id)
        res = await session.execute(stmt)
        group = res.scalar_one_or_none()

        now = datetime.now(timezone.utc)
        if not group:
            group = ClientGroup(
                chat_id=chat_id,
                group_name=title,
                category=cat_clean,
                created_at=now,
                updated_at=now
            )
            session.add(group)
        else:
            group.group_name = title
            group.category = cat_clean
            group.updated_at = now

        await session.commit()

    # Ensure source_group_id is set if not already set
    await update_source_group(chat_id, title)
    await reload_bot_settings_cache()

    logger.info(f"[CATEGORY] Group assigned to Category {cat_clean}. Chat ID: {chat_id}")
    return group


async def remove_client_group_category(chat_id: int) -> bool:
    """Removes Client Group category assignment from DB and refreshes cache."""
    async with AsyncSessionLocal() as session:
        stmt = delete(ClientGroup).where(ClientGroup.chat_id == chat_id)
        res = await session.execute(stmt)
        await session.commit()
        count = res.rowcount

    await reload_bot_settings_cache()
    logger.info(f"[CATEGORY] Group category removed for Chat ID: {chat_id}")
    return count > 0


async def get_client_group_category(chat_id: int) -> str:
    """Gets category ('A' or 'B') for a chat ID from cache or DB."""
    if chat_id in CLIENT_GROUPS_CACHE:
        return CLIENT_GROUPS_CACHE[chat_id]

    async with AsyncSessionLocal() as session:
        stmt = select(ClientGroup.category).where(ClientGroup.chat_id == chat_id)
        res = await session.execute(stmt)
        cat = res.scalar_one_or_none()
        if cat:
            CLIENT_GROUPS_CACHE[chat_id] = cat
            return cat

    return "A"


async def update_order_status(order_id: int, status: str) -> Optional[Order]:
    """Updates status for an Order by ID."""
    async with AsyncSessionLocal() as session:
        stmt = (
            update(Order)
            .where(Order.id == order_id)
            .values(status=status)
        )
        await session.execute(stmt)
        await session.commit()

        res = await session.execute(
            select(Order).options(joinedload(Order.images)).where(Order.id == order_id)
        )
        return res.unique().scalar_one_or_none()


async def set_order_price_prompt(order_id: int, prompt_msg_id: int) -> None:
    """Stores the active price prompt message ID for an Order."""
    async with AsyncSessionLocal() as session:
        stmt = (
            update(Order)
            .where(Order.id == order_id)
            .values(price_prompt_msg_id=prompt_msg_id)
        )
        await session.execute(stmt)
        await session.commit()


async def update_order_price(order_id: int, price_str: Optional[str] = None, price_msg_id: Optional[int] = None) -> Optional[Order]:
    """Updates order price and clears active price prompt message ID."""
    async with AsyncSessionLocal() as session:
        values: Dict[str, Any] = {
            "price_prompt_msg_id": None
        }
        if price_str is not None:
            values["price"] = price_str
        if price_msg_id is not None:
            values["price_msg_id"] = price_msg_id

        stmt = (
            update(Order)
            .where(Order.id == order_id)
            .values(**values)
        )
        await session.execute(stmt)
        await session.commit()

        res = await session.execute(
            select(Order).options(joinedload(Order.images)).where(Order.id == order_id)
        )
        return res.unique().scalar_one_or_none()


# ==========================================
# Role-Based User Management Operations
# ==========================================

async def reload_auth_users_cache() -> Dict[int, str]:
    """
    Loads AuthorizedUser records from database into global AUTH_USERS_CACHE in RAM.
    Seeds default Super Admin (1573531032) and default Delivery Users (1078400998, 1858358195) if database table is empty.
    """
    async with AsyncSessionLocal() as session:
        stmt = select(AuthorizedUser)
        res = await session.execute(stmt)
        users = list(res.scalars().all())

        if not users:
            logger.info("[AUTH] Table authorized_users is empty. Seeding default Super Admin and Delivery Users...")
            default_seeds = [
                (1573531032, "admin"),
                (1078400998, "delivery"),
                (1858358195, "delivery")
            ]
            for uid, role in default_seeds:
                u = AuthorizedUser(
                    telegram_user_id=uid,
                    role=role,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(u)
            await session.commit()

            res = await session.execute(select(AuthorizedUser))
            users = list(res.scalars().all())

        AUTH_USERS_CACHE.clear()
        for u in users:
            AUTH_USERS_CACHE[u.telegram_user_id] = u.role

    logger.info(f"[AUTH] Loaded {len(AUTH_USERS_CACHE)} authorized user(s) into memory.")
    return AUTH_USERS_CACHE


async def add_authorized_user(telegram_user_id: int, role: str = "delivery") -> Tuple[bool, str]:
    """
    Adds or updates an authorized user in the database and refreshes AUTH_USERS_CACHE.
    """
    async with AsyncSessionLocal() as session:
        stmt = select(AuthorizedUser).where(AuthorizedUser.telegram_user_id == telegram_user_id)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

        if not user:
            user = AuthorizedUser(
                telegram_user_id=telegram_user_id,
                role=role,
                created_at=datetime.now(timezone.utc)
            )
            session.add(user)
        else:
            user.role = role

        await session.commit()

    await reload_auth_users_cache()
    logger.info(f"[AUTH] Added/Updated user {telegram_user_id} with role '{role}'.")
    return True, f"User {telegram_user_id} added with role '{role}'"


async def remove_authorized_user(telegram_user_id: int) -> Tuple[bool, str]:
    """
    Removes an authorized user from the database and refreshes AUTH_USERS_CACHE.
    Super Admin (1573531032) cannot be removed.
    """
    if telegram_user_id == 1573531032:
        return False, "Super Admin (1573531032) cannot be removed."

    async with AsyncSessionLocal() as session:
        stmt = delete(AuthorizedUser).where(AuthorizedUser.telegram_user_id == telegram_user_id)
        res = await session.execute(stmt)
        await session.commit()
        count = res.rowcount

    await reload_auth_users_cache()
    if count > 0:
        logger.info(f"[AUTH] Removed user {telegram_user_id}.")
        return True, f"User {telegram_user_id} removed."
    return False, f"User {telegram_user_id} not found."


async def get_all_authorized_users() -> Dict[str, List[int]]:
    """Returns lists of user IDs grouped by role ('admin', 'delivery')."""
    admins: List[int] = []
    delivery_users: List[int] = []

    for uid, role in AUTH_USERS_CACHE.items():
        if role == "admin":
            admins.append(uid)
        elif role == "delivery":
            delivery_users.append(uid)

    return {"admin": admins, "delivery": delivery_users}


# ==========================================
# Two-Group Order CRUD & Operations
# ==========================================

async def create_order(
    email: str,
    client_chat_id: Optional[int] = None,
    original_message_id: Optional[int] = None,
    package: str = "",
    status: str = "Pending",
    category: str = "A"
) -> Order:
    """
    Creates a new Order record and returns generated Order object.
    """
    email_clean = email.lower().strip()
    async with AsyncSessionLocal() as session:
        new_order = Order(
            email=email_clean,
            package=package,
            client_chat_id=client_chat_id,
            original_message_id=original_message_id,
            loader_group_id=None,
            loader_message_id=None,
            status=status,
            category=category,
            price=None,
            image_count=0,
            media_group_id=None,
            fingerprint=None,
            created_at=datetime.now(timezone.utc)
        )
        session.add(new_order)
        await session.commit()
        await session.refresh(new_order)

        logger.info(f"New Order | Order ID: #{new_order.id} | Email: {email_clean} | Status: {status} | Category: {category}")
        return new_order


async def set_order_loader_message_id(order_id: int, loader_message_id: int, loader_group_id: Optional[int] = None) -> None:
    """Updates the forwarded loader message ID and optional loader group ID for an order."""
    async with AsyncSessionLocal() as session:
        values: Dict[str, Any] = {"loader_message_id": loader_message_id}
        if loader_group_id is not None:
            values["loader_group_id"] = loader_group_id

        stmt = (
            update(Order)
            .where(Order.id == order_id)
            .values(**values)
        )
        await session.execute(stmt)
        await session.commit()
        logger.info(f"Forwarded Order | Order ID: #{order_id} -> Loader Msg ID: {loader_message_id} (Loader Group: {loader_group_id})")


async def get_order_by_id(order_id: int) -> Optional[Order]:
    """Retrieves an Order by Order ID with images eagerly loaded."""
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Order)
            .options(joinedload(Order.images))
            .where(Order.id == order_id)
        )
        res = await session.execute(stmt)
        return res.unique().scalar_one_or_none()


async def get_pending_order_by_email(email: str) -> Optional[Order]:
    """Retrieves an active Pending Order matching email if one exists."""
    email_clean = email.lower().strip()
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Order)
            .options(joinedload(Order.images))
            .where(Order.email == email_clean, Order.status.in_(["Pending", "Pending Approval", "Pending Payment"]))
            .order_by(Order.created_at.desc())
        )
        res = await session.execute(stmt)
        return res.unique().scalars().first()


async def get_order_by_loader_msg_id(loader_msg_id: int) -> Optional[Order]:
    """Retrieves an Order matching loader_message_id with images eagerly loaded."""
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Order)
            .options(joinedload(Order.images))
            .where(Order.loader_message_id == loader_msg_id)
        )
        res = await session.execute(stmt)
        return res.unique().scalar_one_or_none()


async def add_images_to_order(
    order_id: int,
    file_items: List[Tuple[str, str]],
    media_group_id: Optional[str] = None
) -> Tuple[Optional[Order], bool]:
    """
    Adds images to an Order by Order ID using SHA256 fingerprint duplicate protection.

    Returns:
        Tuple[Optional[Order], bool]: (Order object, is_duplicate)
    """
    if not file_items:
        return None, False

    async with AsyncSessionLocal() as session:
        stmt = select(Order).options(joinedload(Order.images)).where(Order.id == order_id)
        res = await session.execute(stmt)
        order = res.unique().scalar_one_or_none()

        if not order:
            logger.warning(f"Attempted to add images to non-existent Order ID: #{order_id}")
            return None, False

        file_ids = [item[0] for item in file_items]
        fingerprint = compute_fingerprint(order.email, file_ids)

        # Check duplicate fingerprint
        fp_stmt = select(Order).where(Order.fingerprint == fingerprint, Order.id != order_id)
        fp_res = await session.execute(fp_stmt)
        if fp_res.scalar_one_or_none():
            logger.info(f"Duplicate Delivery | Fingerprint duplicate for Order ID: #{order_id}")
            return order, True

        # Check duplicate media group ID if present
        if media_group_id and order.media_group_id == media_group_id:
            logger.info(f"Duplicate Delivery | Media group duplicate for Order ID: #{order_id}")
            return order, True

        order.fingerprint = fingerprint
        if media_group_id:
            order.media_group_id = media_group_id

        start_pos = len(order.images)
        for idx, (file_id, file_type) in enumerate(file_items):
            img = Image(
                order_id=order.id,
                telegram_file_id=file_id,
                file_type=file_type,
                position=start_pos + idx
            )
            session.add(img)

        order.image_count = start_pos + len(file_items)
        await session.commit()
        session.expire_all()

        res = await session.execute(
            select(Order).options(joinedload(Order.images)).where(Order.id == order_id)
        )
        updated_order = res.unique().scalar_one()

        logger.info(f"Album Completed | Order ID: #{updated_order.id} | Added: {len(file_items)} | Total: {updated_order.image_count}")
        return updated_order, False


async def mark_order_delivered(order_id: int) -> Optional[Order]:
    """Updates order status to 'Delivered' and sets delivered_at timestamp."""
    async with AsyncSessionLocal() as session:
        now_utc = datetime.now(timezone.utc)
        stmt = (
            update(Order)
            .where(Order.id == order_id)
            .values(
                status="Delivered",
                delivered_at=now_utc
            )
        )
        await session.execute(stmt)
        await session.commit()

        res = await session.execute(
            select(Order).options(joinedload(Order.images)).where(Order.id == order_id)
        )
        order = res.unique().scalar_one_or_none()
        logger.info(f"Delivery Completed | Order ID: #{order_id} marked as Delivered.")
        return order


async def cancel_order(order_id: int) -> Tuple[Optional[Order], bool]:
    """Cancels a pending order."""
    async with AsyncSessionLocal() as session:
        stmt = select(Order).options(joinedload(Order.images)).where(Order.id == order_id)
        res = await session.execute(stmt)
        order = res.unique().scalar_one_or_none()

        if not order:
            return None, False

        if order.status == "Cancelled":
            return order, True

        upd_stmt = (
            update(Order)
            .where(Order.id == order_id)
            .values(status="Cancelled")
        )
        await session.execute(upd_stmt)
        await session.commit()

        order.status = "Cancelled"
        logger.info(f"Cancelled Order | Order ID: #{order_id} marked as Cancelled.")
        return order, True


async def get_pending_orders() -> List[Order]:
    """Retrieves all orders in Pending, Pending Approval, or Pending Payment status."""
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Order)
            .options(joinedload(Order.images))
            .where(Order.status.in_(["Pending", "Pending Approval", "Pending Payment"]))
            .order_by(Order.created_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.unique().scalars().all())


async def get_delivered_orders(limit: int = 15) -> List[Order]:
    """Retrieves latest delivered orders."""
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Order)
            .options(joinedload(Order.images))
            .where(Order.status == "Delivered")
            .order_by(Order.delivered_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.unique().scalars().all())


async def get_all_orders_by_email(email: str) -> List[Order]:
    """Retrieves all Orders matching an email."""
    email_clean = email.lower().strip()
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Order)
            .options(joinedload(Order.images))
            .where(Order.email == email_clean)
            .order_by(Order.created_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.unique().scalars().all())


async def delete_orders_by_email(email: str) -> int:
    """Deletes all orders matching email."""
    email_clean = email.lower().strip()
    async with AsyncSessionLocal() as session:
        stmt = select(Order.id).where(Order.email == email_clean)
        res = await session.execute(stmt)
        ids = list(res.scalars().all())

        if not ids:
            return 0

        await session.execute(delete(Image).where(Image.order_id.in_(ids)))
        del_stmt = delete(Order).where(Order.id.in_(ids))
        result = await session.execute(del_stmt)
        await session.commit()
        count = result.rowcount
        logger.info(f"Deleted {count} order(s) for email: {email_clean}")
        return count


async def check_order_timeouts(timeout_hours: int = 24) -> int:
    """
    Checks for pending orders created longer than timeout_hours ago and marks them Expired.
    """
    if timeout_hours <= 0:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=timeout_hours)
    async with AsyncSessionLocal() as session:
        stmt = select(Order).where(Order.status.in_(["Pending", "Pending Approval", "Pending Payment"]), Order.created_at < cutoff)
        res = await session.execute(stmt)
        expired_orders = list(res.scalars().all())

        if not expired_orders:
            return 0

        expired_ids = [o.id for o in expired_orders]
        upd_stmt = (
            update(Order)
            .where(Order.id.in_(expired_ids))
            .values(status="Expired")
        )
        await session.execute(upd_stmt)
        await session.commit()

        for o in expired_orders:
            logger.warning(f"Timeout | Order ID: #{o.id} created at {o.created_at} marked as Expired (⏰ Pending Too Long).")

        return len(expired_ids)


async def get_detailed_stats() -> Dict[str, Any]:
    """Computes comprehensive statistics for the bot dashboard."""
    async with AsyncSessionLocal() as session:
        total_orders = (await session.execute(select(func.count(Order.id)))).scalar() or 0
        pending_orders = (await session.execute(select(func.count(Order.id)).where(Order.status.in_(["Pending", "Pending Approval", "Pending Payment"])))).scalar() or 0
        delivered_orders = (await session.execute(select(func.count(Order.id)).where(Order.status == "Delivered"))).scalar() or 0
        cancelled_orders = (await session.execute(select(func.count(Order.id)).where(Order.status == "Cancelled"))).scalar() or 0

        # Today's metrics (UTC)
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_orders = (await session.execute(select(func.count(Order.id)).where(Order.created_at >= today_start))).scalar() or 0
        today_deliveries = (await session.execute(select(func.count(Order.id)).where(Order.delivered_at >= today_start))).scalar() or 0

        # Calculate Average Delivery Time
        stmt = select(Order.created_at, Order.delivered_at).where(Order.status == "Delivered", Order.delivered_at.isnot(None))
        res = await session.execute(stmt)
        del_times = list(res.all())

        avg_delivery_str = "N/A"
        if del_times:
            durations = [(d_at - c_at).total_seconds() for c_at, d_at in del_times if d_at and c_at]
            if durations:
                avg_seconds = sum(durations) / len(durations)
                if avg_seconds < 60:
                    avg_delivery_str = f"{int(avg_seconds)}s"
                elif avg_seconds < 3600:
                    avg_delivery_str = f"{int(avg_seconds // 60)}m {int(avg_seconds % 60)}s"
                else:
                    avg_delivery_str = f"{round(avg_seconds / 3600, 1)}h"

        return {
            "total_orders": total_orders,
            "pending_orders": pending_orders,
            "delivered_orders": delivered_orders,
            "cancelled_orders": cancelled_orders,
            "today_orders": today_orders,
            "today_deliveries": today_deliveries,
            "avg_delivery_time": avg_delivery_str
        }


async def cleanup_old_records(days: int) -> int:
    """Deletes orders older than days threshold."""
    if days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with AsyncSessionLocal() as session:
        stmt = delete(Order).where(Order.created_at < cutoff)
        res = await session.execute(stmt)
        await session.commit()
        count = res.rowcount
        if count > 0:
            logger.info(f"Storage Retention: Purged {count} order(s) older than {days} days.")
        return count


async def export_orders_to_csv() -> str:
    """Generates CSV formatted string containing all orders export data using standard csv module."""
    async with AsyncSessionLocal() as session:
        stmt = select(Order).options(joinedload(Order.images)).order_by(Order.created_at.desc())
        res = await session.execute(stmt)
        orders = res.unique().scalars().all()

        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(["Order ID", "Email", "Package", "Status", "Price", "Images", "Created", "Delivered"])

        for o in orders:
            created_str = o.created_at.strftime("%Y-%m-%d %H:%M:%S")
            delivered_str = o.delivered_at.strftime("%Y-%m-%d %H:%M:%S") if o.delivered_at else "Pending"
            writer.writerow([o.id, o.email, o.package or "N/A", o.status, o.price or "N/A", len(o.images), created_str, delivered_str])

        return output.getvalue()


async def get_db_file_path() -> Optional[str]:
    """Helper to get SQLite database file path if using SQLite."""
    if Config.DATABASE_URL.startswith("sqlite"):
        path = Config.DATABASE_URL.split("///")[-1]
        if os.path.exists(path):
            return path
    return None
