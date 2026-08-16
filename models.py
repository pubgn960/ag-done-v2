"""
SQLAlchemy 2 Async declarative models for Telegram Email Image Delivery Bot.
Defines schemas and indexes for Orders, Images, Settings, AuthorizedUsers, ClientGroups, and Loaders tables
supporting two-group reply-based workflow, role-based user management, Group Category Routing (v1.2),
Multi Loader Approval System, and Category A Only Price Workflow with prompt & calculator tracking (v1.22.0).
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from sqlalchemy import String, Text, Integer, BigInteger, Float, Boolean, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base model class."""
    pass


class Settings(Base):
    """
    Stores dynamic application settings and group configurations.
    Maintains a single record (id=1).
    source_group_id: Client Group ID (where customers send orders)
    delivery_group_id: Loader Group ID (where bot forwards orders & loaders reply)
    payment_review_group_id: Payment Review Group ID (for Category B orders)
    """

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    source_group_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    source_group_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    delivery_group_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    delivery_group_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payment_review_group_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    payment_review_group_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    def __repr__(self) -> str:
        return f"<Settings(id={self.id}, client_group={self.source_group_id}, loader_group={self.delivery_group_id}, payment_group={self.payment_review_group_id})>"


class ClientGroup(Base):
    """
    Stores Client Group category assignments ('A' or 'B').
    Category A: Trusted Groups (Direct to Loader Group)
    Category B: Payment Required Groups (Forward to Payment Review Group)
    """

    __tablename__ = "client_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    group_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    category: Mapped[str] = mapped_column(String(10), nullable=False, default="A")  # 'A' or 'B'
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    def __repr__(self) -> str:
        return f"<ClientGroup(chat_id={self.chat_id}, category='{self.category}')>"


class Loader(Base):
    """
    Stores Loader Groups for Multi-Loader Category B approval system.
    """

    __tablename__ = "loaders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    loader_name: Mapped[str] = mapped_column(String(255), nullable=False)
    group_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    def __repr__(self) -> str:
        return f"<Loader(id={self.id}, name='{self.loader_name}', group_id={self.group_id})>"


class AuthorizedUser(Base):
    """
    Stores authorized users and their roles for permission enforcement.
    roles: 'admin' (Super Admin), 'delivery' (Delivery User)
    """

    __tablename__ = "authorized_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="delivery")  # 'admin' or 'delivery'
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    def __repr__(self) -> str:
        return f"<AuthorizedUser(id={self.id}, user_id={self.telegram_user_id}, role='{self.role}')>"


class Order(Base):
    """
    Represents an Order record in the two-group reply-based workflow.
    Tracks client message, forwarded loader message, loader group ID, status, package details, category ('A'/'B'), price, price prompt msg ID, price msg ID, and stored image file_ids.
    """

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    package: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    client_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    original_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    loader_group_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    loader_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="Pending", index=True)  # Pending, Pending Approval, Pending Payment, Approved, Rejected, Delivered, Cancelled, Expired
    category: Mapped[Optional[str]] = mapped_column(String(10), nullable=True, default="A")  # 'A' or 'B'
    price: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    price_prompt_msg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    price_msg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    image_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    media_group_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    issue_state: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    issue_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    last_issue_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    package_progress: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cancellation_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_requested_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    cancellation_decision: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    cancellation_decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True
    )

    # Relationship to images ordered by position
    images: Mapped[List["Image"]] = relationship(
        "Image",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="Image.position"
    )

    def __repr__(self) -> str:
        img_count = len(self.__dict__['images']) if 'images' in self.__dict__ else self.image_count
        return f"<Order(id={self.id}, email='{self.email}', category='{self.category}', price='{self.price}', status='{self.status}', images={img_count})>"


class Image(Base):
    """
    Represents an individual image stored within an order.
    Supports photos and photo documents. Stores telegram file_id only.
    """

    __tablename__ = "images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    telegram_file_id: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(String(50), nullable=False, default="photo")  # 'photo' or 'document'
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Many-to-one relationship to Order
    order: Mapped["Image"] = relationship("Order", back_populates="images")

    def __repr__(self) -> str:
        return f"<Image(id={self.id}, order_id={self.order_id}, file_type='{self.file_type}', position={self.position})>"


class DeliverySession(Base):
    """
    Stores active Delivery Sessions when a loader confirms delivery.
    Links the Delivery Session prompt message (delivery_session_message_id) directly to the Order (order_id).
    Persists across Railway restarts and bot reboots.
    """

    __tablename__ = "delivery_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    loader_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    delivery_session_message_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    selected_packages: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON string of selected items
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="waiting_images")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    def __repr__(self) -> str:
        return f"<DeliverySession(id={self.id}, order_id={self.order_id}, loader_id={self.loader_id}, session_msg_id={self.delivery_session_message_id}, status='{self.status}')>"


# Compound index for email + creation timestamp queries
Index("idx_orders_email_created_desc", Order.email, Order.created_at.desc())


class PackagePrice(Base):
    """
    Stores official production package prices in the database per category ('A' or 'B').
    Allows Super Admins to dynamically update prices via /updateprices A or /updateprices B command.
    """

    __tablename__ = "package_prices"

    category: Mapped[str] = mapped_column(String(10), primary_key=True, default="A")
    package: Mapped[str] = mapped_column(String(50), primary_key=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    updated_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    def __repr__(self) -> str:
        return f"<PackagePrice(category='{self.category}', package='{self.package}', price={self.price}, updated_by={self.updated_by})>"


class Wallet(Base):
    """
    Stores Category B Client Wallets.
    Unique identity: (client_group_id, telegram_user_id).
    Wallet system exists ONLY for Category B. Category A does NOT use wallets.
    """
    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_group_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    balance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    __table_args__ = (
        UniqueConstraint("client_group_id", "telegram_user_id", name="uq_wallet_group_user"),
        Index("idx_wallet_group_user", "client_group_id", "telegram_user_id"),
    )

    def __repr__(self) -> str:
        return f"<Wallet(id={self.id}, group_id={self.client_group_id}, user_id={self.telegram_user_id}, balance={self.balance})>"


class BinanceClientIdentity(Base):
    """
    Stores registered Binance UIDs per Category B Client Group & Telegram User.
    Identity mapping: (client_group_id, telegram_user_id) -> binance_uid.
    """
    __tablename__ = "binance_client_identities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_group_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    binance_uid: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="LINKED")  # "LINKED", "UNLINKED"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    __table_args__ = (
        UniqueConstraint("client_group_id", "telegram_user_id", name="uq_binance_group_user"),
        Index("idx_binance_uid_lookup", "binance_uid"),
    )

    def __repr__(self) -> str:
        return f"<BinanceClientIdentity(group_id={self.client_group_id}, user_id={self.telegram_user_id}, binance_uid='{self.binance_uid}')>"


class WalletTransaction(Base):
    """
    Ledger recording all wallet operations (+ topups, - order deductions, refunds).
    """
    __tablename__ = "wallet_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # "TOPUP", "ORDER_DEDUCTION", "REFUND", "ADJUSTMENT"
    amount: Mapped[float] = mapped_column(Float, nullable=False)  # Positive for topup/refund, negative for deduction
    before_balance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    after_balance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)
    provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # "Binance", "Bybit", "Admin"
    transaction_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="COMPLETED")  # "COMPLETED", "PENDING", "FAILED"
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True
    )

    def __repr__(self) -> str:
        return f"<WalletTransaction(id={self.id}, wallet_id={self.wallet_id}, type='{self.type}', amount={self.amount})>"


class PaymentTransaction(Base):
    """
    Stores top-up payment records from providers (Binance / Bybit / Admin).
    Enforces unique constraint on (provider, transaction_id) to prevent duplicate payment crediting.
    """
    __tablename__ = "payment_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # "Binance", "Bybit", "Admin"
    transaction_id: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USDT")  # "USDT", "USDC"
    destination: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    wallet_id: Mapped[Optional[int]] = mapped_column(ForeignKey("wallets.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="VERIFIED")  # "VERIFIED", "UNVERIFIED", "REJECTED"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("provider", "transaction_id", name="uq_payment_provider_tx"),
        Index("idx_payment_provider_tx", "provider", "transaction_id"),
    )

    def __repr__(self) -> str:
        return f"<PaymentTransaction(id={self.id}, provider='{self.provider}', tx_id='{self.transaction_id}', amount={self.amount})>"


class DeliveryLedger(Base):
    """
    Stores individual delivery ledger entries and running totals for accounting.
    Includes deduplication protection via dedup_hash, audit reasons, and manual flags.
    """

    __tablename__ = "delivery_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)
    package: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    before_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    now_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    running_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loader: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    dedup_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True, index=True)
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True
    )

    def __repr__(self) -> str:
        return f"<DeliveryLedger(id={self.id}, chat_id={self.chat_id}, order_id={self.order_id}, package='{self.package}', running_total={self.running_total})>"


class CalculatorLedger(Base):
    """
    Stores individual calculation entries for the Simple Running Total Calculator system.
    """

    __tablename__ = "calculator_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    before_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    after_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    admin_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True
    )

    def __repr__(self) -> str:
        return f"<CalculatorLedger(id={self.id}, amount={self.amount}, before={self.before_total}, after={self.after_total})>"


class RunningTotalLedger(Base):
    """
    Stores individual entries and running totals for the Production Simple Running Total System.
    Scoped per Client Group via chat_id.
    Action types: AUTO_DELIVERY, MANUAL_PLUS, MANUAL_MINUS, PAY, UNDO.
    """

    __tablename__ = "running_total_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    before_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    after_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)
    admin_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True
    )

    def __repr__(self) -> str:
        return f"<RunningTotalLedger(id={self.id}, chat_id={self.chat_id}, action='{self.action_type}', amount={self.amount}, before={self.before_total}, after={self.after_total})>"
