"""
Main entry point for Telegram Email Image Delivery Bot.
Initializes database, configures handlers, sets Telegram '/' UI command menu with command validation,
populates global in-memory BOT_SETTINGS, AUTH_USERS_CACHE, CLIENT_GROUPS_CACHE, and LOADERS_CACHE on startup,
starts background tasks, and runs bot polling.
"""

import re
import sys
import asyncio
import logging
from telegram import BotCommand
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

from config import Config
from database import init_db, cleanup_old_records, check_order_timeouts, reload_bot_settings_cache, reload_auth_users_cache, reload_loaders_cache
from utils import setup_logging
from handlers import (
    source_group_handler,
    edited_message_handler,
    delivery_group_handler,
    duplicate_order_callback_handler,
    category_b_approval_callback_handler,
    price_callback_handler,
    price_input_text_handler,
    loader_issue_callback_handler,
    customer_confirmation_callback_handler,
    unknown_package_price_callback_handler,
    loader_pkg_toggle_callback_handler,
    loader_pkg_confirm_callback_handler,
    loader_pkg_cancel_callback_handler,
    category_a_command,
    category_b_command,
    category_check_command,
    remove_category_command,
    paymentgroup_command,
    approve_order_command,
    reject_order_command,
    loaderadd_command,
    loaderlist_command,
    loaderremove_command,
    loader_text_wizard_handler,
    user_command,
    users_command,
    start_command,
    help_command,
    find_command,
    order_info_command,
    cancel_command,
    resend_command,
    delete_command,
    stats_command,
    pending_command,
    delivered_command,
    export_command,
    backup_command,
    restore_command,
    setup_command,
    source_command,
    delivery_command,
    groups_command,
    status_command,
    removesource_command,
    removedelivery_command,
    resetgroups_command
)

# Initialize application logging
setup_logging()
logger = logging.getLogger("main")


def validate_bot_command(cmd: BotCommand) -> bool:
    """
    Validates a Telegram BotCommand against Telegram API rules:
    - Name: lowercase letters (a-z), digits (0-9), underscore (_), length 1-32.
    - Description: length 1-256.
    """
    name_pattern = r'^[a-z0-9_]{1,32}$'
    if not re.match(name_pattern, cmd.command):
        return False
    if not (1 <= len(cmd.description) <= 256):
        return False
    return True


async def periodic_maintenance_task() -> None:
    """Background task running every hour for order timeouts and 24h database retention cleanup."""
    while True:
        try:
            await asyncio.sleep(3600)  # Check every hour
            # Check order timeouts (pending longer than 24 hours)
            expired = await check_order_timeouts(timeout_hours=24)
            if expired > 0:
                logger.info(f"Periodic check marked {expired} pending order(s) as Expired (⏰ Pending Too Long).")

            # Retention cleanup if configured
            if Config.CLEANUP_DAYS > 0:
                await cleanup_old_records(Config.CLEANUP_DAYS)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic maintenance task: {e}")


async def post_init(application: Application) -> None:
    """Post-initialization callback run inside the active application event loop."""
    logger.info("Initializing database schema...")
    await init_db()

    # Load Settings, Authorized Users, Client Groups, and Loaders from DB once on startup into RAM
    await reload_bot_settings_cache()
    await reload_auth_users_cache()
    await reload_loaders_cache()

    # Register Clean & Frequently Used Bot Commands for Telegram '/' menu UI
    raw_commands = [
        BotCommand("start", "Start Bot"),
        BotCommand("help", "Help"),
        BotCommand("setup", "Setup Guide"),
        BotCommand("source", "Set Client Group"),
        BotCommand("delivery", "Set Loader Group"),
        BotCommand("paymentgroup", "Set Payment Review Group"),
        BotCommand("a", "Set Category A"),
        BotCommand("b", "Set Category B"),
        BotCommand("category", "View Group Category"),
        BotCommand("loaderadd", "Add Loader"),
        BotCommand("loaderlist", "List Loaders"),
        BotCommand("loaderremove", "Remove Loader"),
        BotCommand("user", "Manage Delivery Users"),
        BotCommand("users", "List Authorized Users"),
        BotCommand("groups", "Group Configuration"),
        BotCommand("status", "Bot Status"),
        BotCommand("pending", "Pending Orders"),
        BotCommand("find", "Find Order"),
        BotCommand("stats", "Statistics")
    ]

    valid_commands = []
    for cmd in raw_commands:
        if validate_bot_command(cmd):
            valid_commands.append(cmd)
        else:
            logger.warning(f"[COMMANDS] Skipping invalid BotCommand name='{cmd.command}' desc='{cmd.description}'")

    try:
        await application.bot.set_my_commands(valid_commands)
        logger.info(f"[COMMANDS] Registered {len(valid_commands)} bot commands successfully.")
    except Exception:
        logger.exception("[COMMANDS] Failed to register bot commands.")

    # Initial order timeout check on startup
    expired = await check_order_timeouts(timeout_hours=24)
    if expired > 0:
        logger.info(f"Startup check marked {expired} pending order(s) as Expired.")

    if Config.CLEANUP_DAYS > 0:
        cleaned = await cleanup_old_records(Config.CLEANUP_DAYS)
        if cleaned > 0:
            logger.info(f"Startup retention check purged {cleaned} expired records.")

    # Schedule background maintenance task in active event loop
    asyncio.create_task(periodic_maintenance_task())

    logger.info("Bot initialization complete. Active and listening for updates...")


def main() -> None:
    """Configures and launches the Telegram Bot Application."""
    if not Config.BOT_TOKEN:
        logger.critical("BOT_TOKEN is missing! Please configure it in .env file or environment variables.")
        sys.exit(1)

    logger.info("Starting Telegram Email Image Delivery Bot v1.2.0...")

    # Build python-telegram-bot application
    application = (
        ApplicationBuilder()
        .token(Config.BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Register Setup & Group Configuration Commands (supporting both lowercase and uppercase aliases)
    application.add_handler(CommandHandler("setup", setup_command))
    application.add_handler(CommandHandler("source", source_command))
    application.add_handler(CommandHandler("delivery", delivery_command))
    application.add_handler(CommandHandler("paymentgroup", paymentgroup_command))
    application.add_handler(CommandHandler(["a", "A"], category_a_command))
    application.add_handler(CommandHandler(["b", "B"], category_b_command))
    application.add_handler(CommandHandler("category", category_check_command))
    application.add_handler(CommandHandler("removecategory", remove_category_command))
    application.add_handler(CommandHandler("approve", approve_order_command))
    application.add_handler(CommandHandler("reject", reject_order_command))
    application.add_handler(CommandHandler("groups", groups_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("removesource", removesource_command))
    application.add_handler(CommandHandler("removedelivery", removedelivery_command))
    application.add_handler(CommandHandler("resetgroups", resetgroups_command))

    # Register Multi-Loader Commands
    application.add_handler(CommandHandler("loaderadd", loaderadd_command))
    application.add_handler(CommandHandler("loaderlist", loaderlist_command))
    application.add_handler(CommandHandler("loaderremove", loaderremove_command))

    # Register User Management Commands
    application.add_handler(CommandHandler("user", user_command))
    application.add_handler(CommandHandler("users", users_command))

    # Register Core & Admin Commands
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("pending", pending_command))
    application.add_handler(CommandHandler("delivered", delivered_command))
    application.add_handler(CommandHandler("find", find_command))
    application.add_handler(CommandHandler("order", order_info_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("resend", resend_command))
    application.add_handler(CommandHandler("delete", delete_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("export", export_command))
    application.add_handler(CommandHandler("backup", backup_command))
    application.add_handler(CommandHandler("restore", restore_command))

    # Register Interactive Callback Query Handlers
    application.add_handler(CallbackQueryHandler(duplicate_order_callback_handler, pattern="^dup_"))
    application.add_handler(CallbackQueryHandler(category_b_approval_callback_handler, pattern="^catb_"))
    application.add_handler(CallbackQueryHandler(price_callback_handler, pattern="^price_"))
    application.add_handler(CallbackQueryHandler(loader_issue_callback_handler, pattern="^loader_issue:"))
    application.add_handler(CallbackQueryHandler(customer_confirmation_callback_handler, pattern="^cust_confirm:"))
    application.add_handler(CallbackQueryHandler(redeliver_callback_handler, pattern="^redeliver_"))
    application.add_handler(CallbackQueryHandler(unknown_package_price_callback_handler, pattern="^add_unk_price:"))
    application.add_handler(CallbackQueryHandler(loader_pkg_toggle_callback_handler, pattern="^pkg_toggle:"))
    application.add_handler(CallbackQueryHandler(loader_pkg_confirm_callback_handler, pattern="^pkg_confirm:"))
    application.add_handler(CallbackQueryHandler(loader_pkg_cancel_callback_handler, pattern="^pkg_cancel:"))

    # Register price_input_text_handler first for reply messages
    application.add_handler(
        MessageHandler(
            filters.REPLY & filters.TEXT & (~filters.COMMAND),
            price_input_text_handler
        ),
        group=0
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & (~filters.COMMAND),
            loader_text_wizard_handler
        ),
        group=0
    )

    # Register Client Group Handler (Group 1 - Customer Orders)
    application.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION | filters.PHOTO) & (~filters.COMMAND) & (~filters.UpdateType.EDITED_MESSAGE),
            source_group_handler
        ),
        group=1
    )

    # Register Client Group Edited Message Handler (Group 1 - Customer Message Edits)
    application.add_handler(
        MessageHandler(
            filters.UpdateType.EDITED_MESSAGE & (~filters.COMMAND),
            edited_message_handler
        ),
        group=1
    )

    # Register Loader Group Handler (Group 2 - Loader Photos / Photo Documents / Text Replies like 'wrong')
    application.add_handler(
        MessageHandler(
            (filters.TEXT | filters.PHOTO | filters.Document.ALL) & (~filters.COMMAND),
            delivery_group_handler
        ),
        group=2
    )

    logger.info("Bot running in polling mode. Press Ctrl+C to stop.")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
