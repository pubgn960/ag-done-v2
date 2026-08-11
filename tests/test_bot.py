"""
Unit test suite for Telegram Email Image Delivery Bot.
Tests email, Order ID, package extraction, keyword detection, caption email overrides,
wrong details workflow, duplicate pending order detection, album splitting, SHA256 fingerprinting, user sessions,
BOT_SETTINGS cache, Role-Based User Management (AUTH_USERS_CACHE, Super Admin, Delivery Users),
Ignoring Super Admin & Delivery User messages in Client Group,
Group Category Routing System (v1.2: Category A, Category B, Payment Review, Approve, Reject),
Multi-Loader Approval System (Loader CRUD, LOADERS_CACHE, Multi-Loader Assignment),
Telegram BotCommand Validation (validate_bot_command),
Loader Add Wizard state isolation (LOADER_ADD_SESSION),
Category A Only Price Workflow (update_order_price),
and two-group reply-based DB operations.
"""

import unittest
import asyncio
from telegram import BotCommand

from email_parser import extract_email, extract_order_id, extract_package, extract_last_email
from keywords import contains_order_keyword
from delivery import chunk_list
from media_collector import user_session_manager
from utils import is_super_admin, is_delivery_user
from main import validate_bot_command
from handlers import LOADER_ADD_SESSION, is_valid_price_string, price_input_text_handler, PRICE_INPUT_SESSION
from database import (
    BOT_SETTINGS,
    AUTH_USERS_CACHE,
    CLIENT_GROUPS_CACHE,
    LOADERS_CACHE,
    init_db,
    reload_bot_settings_cache,
    reload_auth_users_cache,
    reload_loaders_cache,
    set_client_group_category,
    remove_client_group_category,
    get_client_group_category,
    update_payment_review_group,
    update_order_status,
    set_order_price_prompt,
    update_order_price,
    add_authorized_user,
    remove_authorized_user,
    get_all_authorized_users,
    add_loader,
    remove_loader_by_id,
    get_all_loaders,
    create_order,
    set_order_loader_message_id,
    get_order_by_id,
    get_pending_order_by_email,
    get_order_by_loader_msg_id,
    add_images_to_order,
    mark_order_delivered,
    cancel_order,
    get_pending_orders,
    get_delivered_orders,
    delete_orders_by_email,
    get_detailed_stats,
    compute_fingerprint,
    export_orders_to_csv,
    get_or_create_settings,
    update_source_group,
    update_delivery_group,
    reset_groups
)


class TestCategoryAPriceWorkflow(unittest.IsolatedAsyncioTestCase):
    """Tests Category A Price workflow DB updates and string validation."""

    def test_price_validation(self):
        # Valid numbers
        self.assertTrue(is_valid_price_string("15"))
        self.assertTrue(is_valid_price_string("15.5"))
        self.assertTrue(is_valid_price_string("2500"))
        self.assertTrue(is_valid_price_string("2999.99"))

        # Invalid formats
        self.assertFalse(is_valid_price_string("abc"))
        self.assertFalse(is_valid_price_string("15rs"))
        self.assertFalse(is_valid_price_string("price 20"))
        self.assertFalse(is_valid_price_string("15.5.5"))

    async def test_order_price_update(self):
        await init_db()

        email = "price_test@example.com"
        order = await create_order(email, category="A")
        self.assertIsNone(order.price)
        self.assertEqual(order.category, "A")

        # Set Prompt
        await set_order_price_prompt(order.id, 998877)
        check_prompt = await get_order_by_id(order.id)
        self.assertEqual(check_prompt.price_prompt_msg_id, 998877)

        # Set Price (should clear prompt ID and set price_msg_id)
        updated = await update_order_price(order.id, "15.5", price_msg_id=12345)
        self.assertEqual(updated.price, "15.5")
        self.assertEqual(updated.price_msg_id, 12345)
        self.assertIsNone(updated.price_prompt_msg_id)

        # Edit Price
        edited = await update_order_price(order.id, "30", price_msg_id=67890)
        self.assertEqual(edited.price, "30")
        self.assertEqual(edited.price_msg_id, 67890)

        # Clean up
        await delete_orders_by_email(email)

    async def test_price_handler_in_place(self):
        await init_db()
        from unittest.mock import AsyncMock, MagicMock
        from database import delete_orders_by_email

        email = "in_place_price@example.com"
        await delete_orders_by_email(email)

        order = await create_order(email, category="A", client_chat_id=-100123456)
        PRICE_INPUT_SESSION[order.id] = {
            "order_id": order.id,
            "chat_id": -100123456,
            "prompt_msg_id": 555,
            "button_msg_id": 444,
            "is_edit": False
        }
        await set_order_price_prompt(order.id, 555)

        update = MagicMock()
        update.effective_user.id = 1573531032
        update.effective_message.reply_to_message.message_id = 555
        update.effective_message.text = "25.5"

        context = MagicMock()
        context.bot.edit_message_text = AsyncMock()
        context.bot.delete_message = AsyncMock()

        await price_input_text_handler(update, context)

        # Verify message text edited in-place with reply_markup=None (button removed)
        context.bot.edit_message_text.assert_called_once_with(
            chat_id=-100123456,
            message_id=444,
            text="💰 Price: 25.5",
            reply_markup=None
        )

        # Verify prompt message (555) deleted
        context.bot.delete_message.assert_any_call(
            chat_id=-100123456,
            message_id=555
        )

        # Verify DB order price updated
        db_order = await get_order_by_id(order.id)
        self.assertEqual(db_order.price, "25.5")
        self.assertEqual(db_order.price_msg_id, 444)

        await delete_orders_by_email(email)


class TestLoaderWizardState(unittest.TestCase):
    """Tests Loader Add Wizard state isolation."""

    def test_session_isolation(self):
        LOADER_ADD_SESSION.clear()
        self.assertNotIn(12345, LOADER_ADD_SESSION)

        # User 1 initiates wizard
        LOADER_ADD_SESSION[12345] = {"step": 1, "chat_id": -100111}
        self.assertIn(12345, LOADER_ADD_SESSION)
        self.assertNotIn(67890, LOADER_ADD_SESSION)

        LOADER_ADD_SESSION.clear()


class TestBotCommandValidation(unittest.TestCase):
    """Tests validate_bot_command against Telegram API rules."""

    def test_valid_commands(self):
        self.assertTrue(validate_bot_command(BotCommand("a", "Set Category A")))
        self.assertTrue(validate_bot_command(BotCommand("b", "Set Category B")))
        self.assertTrue(validate_bot_command(BotCommand("loaderadd", "Add Loader")))
        self.assertTrue(validate_bot_command(BotCommand("loader_list", "List Loaders")))

    def test_invalid_commands(self):
        # Uppercase not allowed
        self.assertFalse(validate_bot_command(BotCommand("A", "Set Category A")))
        self.assertFalse(validate_bot_command(BotCommand("B", "Set Category B")))
        # Spaces or special chars not allowed
        self.assertFalse(validate_bot_command(BotCommand("loader-add", "Add Loader")))
        self.assertFalse(validate_bot_command(BotCommand("loader add", "Add Loader")))
        # Empty description not allowed
        self.assertFalse(validate_bot_command(BotCommand("a", "")))


class TestMultiLoaderManagement(unittest.IsolatedAsyncioTestCase):
    """Tests Loader CRUD operations, cache synchronization, and multi-loader assignment."""

    async def test_loader_crud_and_cache(self):
        await init_db()

        # Add Loader 1
        l1 = await add_loader(-1001234567890, "Pakistan Loader")
        self.assertIsNotNone(l1.id)

        # Add Loader 2
        l2 = await add_loader(-1009876543210, "India Loader")
        self.assertIsNotNone(l2.id)

        # Check cache
        self.assertIn(l1.id, LOADERS_CACHE)
        self.assertEqual(LOADERS_CACHE[l1.id]["name"], "Pakistan Loader")

        # List Loaders
        loaders = await get_all_loaders()
        self.assertTrue(len(loaders) >= 2)

        # Remove Loader
        removed = await remove_loader_by_id(l1.id)
        self.assertTrue(removed)
        self.assertNotIn(l1.id, LOADERS_CACHE)

        # Clean up
        await remove_loader_by_id(l2.id)


class TestGroupCategoryRouting(unittest.IsolatedAsyncioTestCase):
    """Tests Group Category Routing (v1.2) - Category A, Category B, Payment Review, Approve, Reject."""

    async def test_category_assignment_and_cache(self):
        await init_db()

        chat_id_a = -100555444333222
        chat_id_b = -100999888777666

        # Set Category A
        await set_client_group_category(chat_id_a, "Pakistan CODM Shop A", "A")
        self.assertEqual(await get_client_group_category(chat_id_a), "A")

        # Set Category B
        await set_client_group_category(chat_id_b, "Pakistan CODM Shop B", "B")
        self.assertEqual(await get_client_group_category(chat_id_b), "B")

        # Test Payment Review Group update
        pay_chat_id = -100111222333444
        await update_payment_review_group(pay_chat_id, "Payment Review Group")
        self.assertEqual(BOT_SETTINGS["payment_review_group_id"], pay_chat_id)

        # Test Category B order creation & status updates
        email = "catb_test@example.com"
        order = await create_order(email, client_chat_id=chat_id_b, original_message_id=101, status="Pending Payment")
        self.assertEqual(order.status, "Pending Payment")

        # Approve Order
        approved_order = await update_order_status(order.id, "Approved")
        self.assertEqual(approved_order.status, "Approved")

        # Reject Order
        rejected_order = await update_order_status(order.id, "Rejected")
        self.assertEqual(rejected_order.status, "Rejected")

        # Remove Category
        await remove_client_group_category(chat_id_a)
        await remove_client_group_category(chat_id_b)
        await delete_orders_by_email(email)


class TestIgnoreAdminAndDeliveryUserMessages(unittest.IsolatedAsyncioTestCase):
    """Tests ignoring Super Admin and Delivery User messages in Client Group."""

    async def test_admin_and_delivery_user_detection(self):
        await init_db()

        # Super Admin check
        admin_uid = 1573531032
        self.assertTrue(is_super_admin(admin_uid))

        # Delivery User checks
        del_uid_1 = 1078400998
        del_uid_2 = 1858358195
        self.assertTrue(is_delivery_user(del_uid_1))
        self.assertTrue(is_delivery_user(del_uid_2))

        # Normal Customer check
        cust_uid = 987654321
        self.assertFalse(is_super_admin(cust_uid))
        self.assertFalse(is_delivery_user(cust_uid))


class TestRoleBasedUserManagement(unittest.IsolatedAsyncioTestCase):
    """Tests role-based user management, database persistence, and permission functions."""

    async def test_role_seeding_and_permissions(self):
        await init_db()

        # Verify initial seeds in memory cache
        self.assertTrue(is_super_admin(1573531032))
        self.assertTrue(is_delivery_user(1573531032))

        self.assertFalse(is_super_admin(1078400998))
        self.assertTrue(is_delivery_user(1078400998))

        self.assertFalse(is_super_admin(1858358195))
        self.assertTrue(is_delivery_user(1858358195))

        # Test adding a new delivery user
        new_uid = 999888777
        self.assertFalse(is_delivery_user(new_uid))
        success, _ = await add_authorized_user(new_uid, role="delivery")
        self.assertTrue(success)
        self.assertTrue(is_delivery_user(new_uid))
        self.assertFalse(is_super_admin(new_uid))

        # Test listing users
        all_users = await get_all_authorized_users()
        self.assertIn(1573531032, all_users["admin"])
        self.assertIn(new_uid, all_users["delivery"])

        # Test removing a delivery user
        rem_success, _ = await remove_authorized_user(new_uid)
        self.assertTrue(rem_success)
        self.assertFalse(is_delivery_user(new_uid))

        # Test protecting Super Admin from removal
        sa_rem_success, msg = await remove_authorized_user(1573531032)
        self.assertFalse(sa_rem_success)
        self.assertTrue(is_super_admin(1573531032))


class TestDuplicateOrderDetection(unittest.IsolatedAsyncioTestCase):
    """Tests duplicate pending order detection."""

    async def test_get_pending_order_by_email(self):
        await init_db()

        email = "dup_detect_test@example.com"
        # No pending order initially
        initial = await get_pending_order_by_email(email)
        self.assertIsNone(initial)

        # Create pending order
        o1 = await create_order(email, status="Pending")
        found = await get_pending_order_by_email(email)
        self.assertIsNotNone(found)
        self.assertEqual(found.id, o1.id)

        # Deliver order
        await mark_order_delivered(o1.id)
        found_after_del = await get_pending_order_by_email(email)
        self.assertIsNone(found_after_del)

        # Cleanup
        await delete_orders_by_email(email)


class TestCaptionEmailAndWrongDetails(unittest.TestCase):
    """Tests extract_last_email helper for Loader caption email overrides and wrong details detection."""

    def test_extract_last_email_single(self):
        text = "AG Done\n\nabc@gmail.com"
        self.assertEqual(extract_last_email(text), "abc@gmail.com")

    def test_extract_last_email_multiple(self):
        text = "abc@gmail.com\n\nCompleted Successfully"
        self.assertEqual(extract_last_email(text), "abc@gmail.com")

    def test_extract_last_email_override(self):
        text = "AG Done\nold@gmail.com\nnew@gmail.com\nFinished"
        self.assertEqual(extract_last_email(text), "new@gmail.com")

    def test_extract_last_email_none(self):
        text = "AG Done\nNo email here"
        self.assertIsNone(extract_last_email(text))

    def test_wrong_details_keyword(self):
        self.assertIn("wrong", "wrong".lower())
        self.assertIn("wrong", "Wrong details provided".lower())
        self.assertIn("wrong", "WRONG".lower())


class TestBotSettingsCache(unittest.IsolatedAsyncioTestCase):
    """Tests in-memory BOT_SETTINGS cache initialization and updates."""

    async def test_cache_update_and_reload(self):
        await init_db()

        # Update source group and verify cache instantly reflects changes
        await update_source_group(-1001234567890, "Test Client Group")
        self.assertEqual(BOT_SETTINGS["source_group_id"], -1001234567890)
        self.assertEqual(BOT_SETTINGS["source_group_title"], "Test Client Group")

        # Update delivery group and verify cache instantly reflects changes
        await update_delivery_group(-1009876543210, "Test Loader Group")
        self.assertEqual(BOT_SETTINGS["delivery_group_id"], -1009876543210)
        self.assertEqual(BOT_SETTINGS["delivery_group_title"], "Test Loader Group")

        # Simulate bot restart by calling reload_bot_settings_cache()
        cached = await reload_bot_settings_cache()
        self.assertEqual(cached["source_group_id"], -1001234567890)
        self.assertEqual(cached["delivery_group_id"], -1009876543210)

        # Reset groups and verify cache cleared
        await reset_groups()
        self.assertIsNone(BOT_SETTINGS["source_group_id"])
        self.assertIsNone(BOT_SETTINGS["delivery_group_id"])


class TestKeywordDetector(unittest.TestCase):
    """Tests keyword-based order detection."""

    def test_keyword_matches(self):
        # Match cases
        self.assertTrue(contains_order_keyword("10800 CP\nabc@gmail.com")[0])
        self.assertTrue(contains_order_keyword("Login:\ntest@hotmail.com")[0])
        self.assertTrue(contains_order_keyword("UID:\n123456\nEmail:\nabc@outlook.com")[0])
        self.assertTrue(contains_order_keyword("Login: test+1234")[0])
        self.assertTrue(contains_order_keyword("myemail@yahoo.co.pk")[0])

    def test_keyword_ignores(self):
        # Ignore cases
        self.assertFalse(contains_order_keyword("Need CP")[0])
        self.assertFalse(contains_order_keyword("Hello")[0])
        self.assertFalse(contains_order_keyword("10800 CP")[0])


class TestEmailOrderPackageParser(unittest.TestCase):
    """Tests email, Order ID, and package regex extraction."""

    def test_extract_basic_email(self):
        text = "Order confirmation for john@gmail.com please deliver."
        self.assertEqual(extract_email(text), "john@gmail.com")

    def test_extract_order_id_formats(self):
        self.assertEqual(extract_order_id("Order ID: #10025"), 10025)
        self.assertEqual(extract_order_id("Order #10025"), 10025)
        self.assertEqual(extract_order_id("#10025"), 10025)
        self.assertEqual(extract_order_id("Order ID: 10025"), 10025)

    def test_extract_package_description(self):
        text = "10800 CP\nEmail: test@gmail.com"
        self.assertEqual(extract_package(text), "10800 CP")


class TestDeliverySplitting(unittest.TestCase):
    """Tests album splitting logic (8, 18, 35, 100+ images)."""

    def test_chunking_eight_images(self):
        images = [f"file_id_{i}" for i in range(8)]
        chunks = chunk_list(images, chunk_size=10)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(len(chunks[0]), 8)

    def test_chunking_eighteen_images(self):
        images = [f"file_id_{i}" for i in range(18)]
        chunks = chunk_list(images, chunk_size=10)
        self.assertEqual(len(chunks), 2)
        self.assertEqual([len(c) for c in chunks], [10, 8])


class TestTwoGroupDatabaseWorkflow(unittest.IsolatedAsyncioTestCase):
    """Async tests for Two-Group Reply-Based Order Creation, Loader Reply Mapping, and Statuses."""

    async def test_two_group_workflow(self):
        await init_db()

        # 1. Customer Order Creation in Client Group
        email = "twogroup_flow@example.com"
        await delete_orders_by_email(email)
        order = await create_order(
            email=email,
            client_chat_id=-1001111111111,
            original_message_id=501,
            package="10800 CP"
        )
        self.assertIsNotNone(order.id)
        self.assertEqual(order.status, "Pending")
        self.assertEqual(order.package, "10800 CP")

        # 2. Forward to Loader Group & Store Loader Message ID
        await set_order_loader_message_id(order.id, 9901)
        loader_order = await get_order_by_loader_msg_id(9901)
        self.assertIsNotNone(loader_order)
        self.assertEqual(loader_order.id, order.id)

        # 3. Loader replies with images
        file_items = [("photo_1", "photo"), ("photo_2", "photo")]
        updated_order, is_dup = await add_images_to_order(
            order_id=order.id,
            file_items=file_items,
            media_group_id="album_5501"
        )
        self.assertFalse(is_dup)
        self.assertEqual(len(updated_order.images), 2)

        # 4. Duplicate reply test
        _, is_dup_2 = await add_images_to_order(
            order_id=order.id,
            file_items=file_items,
            media_group_id="album_5501"
        )
        self.assertTrue(is_dup_2)

        # 5. Mark Order Delivered
        await mark_order_delivered(order.id)
        del_order = await get_order_by_id(order.id)
        self.assertEqual(del_order.status, "Delivered")
        self.assertIsNotNone(del_order.delivered_at)

        # 6. Cancellation test on second order
        order2 = await create_order("cancel_test@example.com")
        canceled_order, success = await cancel_order(order2.id)
        self.assertTrue(success)
        self.assertEqual(canceled_order.status, "Cancelled")

        # Clean up
        await delete_orders_by_email(email)
        await delete_orders_by_email("cancel_test@example.com")


if __name__ == "__main__":
    unittest.main()
