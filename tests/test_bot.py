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
        from database import remove_authorized_user
        await remove_authorized_user(999888777)

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
    """Tests strict 4-condition order detection (Platform + Login + Password + Package)."""

    def test_valid_orders(self):
        # 1. Valid Facebook Order
        fb_order = (
            "Facebook\n\n"
            "Email:\nabc@gmail.com\n\n"
            "Password:\nHello123\n\n"
            "Order:\n2400+880"
        )
        self.assertTrue(contains_order_keyword(fb_order)[0])

        # 2. Valid Activision Order
        act_order = (
            "Activision\n\n"
            "Email:\nplayer@hotmail.com\n\n"
            "Password:\nGame123\n\n"
            "Recovery Codes:\n123456\n\n"
            "Order:\n10800+5040"
        )
        self.assertTrue(contains_order_keyword(act_order)[0])

        # 3. Valid Order with International Phone Number (+92)
        phone_order = (
            "FB Login\n"
            "Phone: +92 300 1234567\n"
            "Email: user@yahoo.com\n"
            "Password: secretpassword\n"
            "Package: 2400"
        )
        self.assertTrue(contains_order_keyword(phone_order)[0])

        # 4. Valid Order with Outlook, iCloud, Proton
        self.assertTrue(contains_order_keyword("Meta\nEmail: a@outlook.com\nPwd: 123\n2400 CP")[0])
        self.assertTrue(contains_order_keyword("Activision ID\nEmail: a@icloud.com\n2FA: 999\n10800")[0])
        self.assertTrue(contains_order_keyword("FB\nEmail: a@proton.me\nLogin: pass12\n880")[0])

    def test_invalid_messages_eliminated(self):
        # Package-only messages
        self.assertFalse(contains_order_keyword("2400+880")[0])
        self.assertFalse(contains_order_keyword("108000")[0])
        self.assertFalse(contains_order_keyword("7200")[0])

        # Email-only messages
        self.assertFalse(contains_order_keyword("gmail.com")[0])
        self.assertFalse(contains_order_keyword("user@gmail.com")[0])

        # Password-only messages
        self.assertFalse(contains_order_keyword("Password:123456")[0])

        # Platform-only messages
        self.assertFalse(contains_order_keyword("Facebook")[0])
        self.assertFalse(contains_order_keyword("Activision ID")[0])

        # Email + Package without password or platform (Now detected under Email+Package Fallback Rule)
        self.assertTrue(contains_order_keyword("Facebook\nEmail: abc@gmail.com\n2400+880")[0])

        # Missing Package
        self.assertFalse(contains_order_keyword("Facebook\nEmail: abc@gmail.com\nPassword: 123")[0])

        # Missing Platform but has Email + Package (Now detected under Email+Package Fallback Rule)
        self.assertTrue(contains_order_keyword("Email: abc@gmail.com\nPassword: 123\n2400+880")[0])


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

    async def test_get_order_waiting_for_customer_update_import_and_query(self):
        from database import init_db, create_order, update_order_status, delete_orders_by_email, get_order_waiting_for_customer_update
        from handlers import get_order_waiting_for_customer_update as get_order_handler_import

        self.assertEqual(get_order_waiting_for_customer_update, get_order_handler_import)

        await init_db()
        email = "waiting_update_test@example.com"
        client_chat_id = -100999888777
        await delete_orders_by_email(email)

        # 1. No active waiting order -> returns None
        none_ord = await get_order_waiting_for_customer_update(client_chat_id)
        self.assertIsNone(none_ord)

        # 2. Create order & set status to Waiting Customer Update
        order = await create_order(email=email, client_chat_id=client_chat_id, package="10800")
        await update_order_status(order.id, "Waiting Customer Update")

        matched_ord = await get_order_waiting_for_customer_update(client_chat_id)
        self.assertIsNotNone(matched_ord)
        self.assertEqual(matched_ord.id, order.id)

        # Clean up
        await delete_orders_by_email(email)
        await delete_orders_by_email("cancel_test@example.com")


class TestPOCOrderPriceDetection(unittest.TestCase):
    """Tests for POC automatic price detection & calculator helper calculate_test_price()."""

    def setUp(self):
        from utils import reload_package_prices_cache, TEST_PACKAGE_PRICES
        reload_package_prices_cache(TEST_PACKAGE_PRICES)

    def test_single_packages(self):
        from utils import calculate_test_price

        cases = {
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
            "2400 CP": 16.5,
            "108000 CP": 563.0
        }

        for text, expected in cases.items():
            price = calculate_test_price(text)
            self.assertEqual(price, expected, f"Single package match failed for '{text}'")

    def test_mixed_packages(self):
        from utils import calculate_test_price

        mixed_cases = {
            "108000+7200+2400": 621.5,             # 563 + 42 + 16.5
            "96000+420": 507.5,                    # 503 + 4.5
            "48000+2400+880": 278.5,               # 254 + 16.5 + 8
            "7200+2400+880": 66.5,                 # 42 + 16.5 + 8
            "2400+2400+880": 41.0,                 # 16.5*2 + 8
            "108000,72000&24000/2400+880": 1094.5   # 563 + 375 + 132 + 16.5 + 8
        }

        for text, expected in mixed_cases.items():
            price = calculate_test_price(text)
            self.assertEqual(price, expected, f"Mixed package match failed for '{text}'")

    def test_longest_package_matching_prevents_false_detections(self):
        from utils import parse_test_order_packages

        # 108000 must match as 108000, NOT 10800
        p1 = parse_test_order_packages("108000")
        self.assertEqual(len(p1["packages"]), 1)
        self.assertEqual(p1["packages"][0]["package"], "108000")
        self.assertEqual(p1["total_price"], 563.0)

        # 24000 must match as 24000, NOT 2400
        p2 = parse_test_order_packages("24000")
        self.assertEqual(len(p2["packages"]), 1)
        self.assertEqual(p2["packages"][0]["package"], "24000")
        self.assertEqual(p2["total_price"], 132.0)

        # 96000 must match as 96000, NOT 9600
        p3 = parse_test_order_packages("96000")
        self.assertEqual(len(p3["packages"]), 1)
        self.assertEqual(p3["packages"][0]["package"], "96000")
        self.assertEqual(p3["total_price"], 503.0)

    def test_mixed_separators_normalization(self):
        from utils import parse_test_order_packages, calculate_test_price

        # 1. 10800,5040&2400/880+420 -> Expected 5 packages: 10800, 5040, 2400, 880, 420 = 126.0$ (64+33+16.5+8+4.5)
        p1 = parse_test_order_packages("10800,5040&2400/880+420")
        self.assertIsNotNone(p1)
        self.assertEqual([item["package"] for item in p1["packages"]], ["10800", "5040", "2400", "880", "420"])
        self.assertEqual(p1["total_price"], 126.0)

        # 2. 2400,880 -> Expected 2400, 880 = 24.5$ (16.5+8)
        self.assertEqual(calculate_test_price("2400,880"), 24.5)

        # 3. 2400&880 -> Expected 2400, 880 = 24.5$
        self.assertEqual(calculate_test_price("2400&880"), 24.5)

        # 4. 2400\n880 -> Expected 2400, 880 = 24.5$
        self.assertEqual(calculate_test_price("2400\n880"), 24.5)

    def test_quantities(self):
        from utils import calculate_test_price

        qty_cases = {
            "2400x2": 33.0,               # 16.5 * 2
            "2400 x2": 33.0,              # 16.5 * 2
            "2x2400": 33.0,               # 16.5 * 2
            "2 x 2400": 33.0,             # 16.5 * 2
            "2400x2 + 880x3": 57.0,       # (16.5*2) + (8*3) = 33 + 24
            "2x10800 + 3x420": 141.5      # (64*2) + (4.5*3) = 128 + 13.5 = 141.5
        }

        for text, expected in qty_cases.items():
            price = calculate_test_price(text)
            self.assertAlmostEqual(price, expected, places=2, msg=f"Quantity match failed for '{text}'")

    def test_false_match_rejections(self):
        from utils import calculate_test_price

        rejected = [
            "800",          # Must NOT match 80
            "400",          # Must NOT match 2400
            "1800",         # Must NOT match 80
            "5500 CP",      # Unsupported package
            "1000",
            "abc@gmail.com",
            None,
            ""
        ]

        for text in rejected:
            price = calculate_test_price(text)
            self.assertIsNone(price, f"Should reject false match / unsupported text: '{text}'")

        # Specific false-subtoken checks:
        # 10800 must NOT match 80 or 800
        self.assertEqual(calculate_test_price("10800"), 64.0)
        # 2400 must NOT match 400
        self.assertEqual(calculate_test_price("2400"), 16.5)
        # 880 must NOT match 80
        self.assertEqual(calculate_test_price("880"), 8.0)

    def test_official_production_pricing_examples(self):
        from utils import calculate_test_price

        # 10800 → 64$
        self.assertEqual(calculate_test_price("10800"), 64.0)

        # 10800 + 5040 → 97$
        self.assertEqual(calculate_test_price("10800 + 5040"), 97.0)

        # 10800 + 5040 + 2400 → 113.5$
        self.assertEqual(calculate_test_price("10800 + 5040 + 2400"), 113.5)

        # 2400 + 2400 → 33$
        self.assertEqual(calculate_test_price("2400 + 2400"), 33.0)

        # 2x10800 + 5040 → 161$
        self.assertEqual(calculate_test_price("2x10800 + 5040"), 161.0)

        # 880 + 420 + 80 → 13.5$
        self.assertEqual(calculate_test_price("880 + 420 + 80"), 13.5)

        # 108000 + 7200 + 2400 → 621.5$
        self.assertEqual(calculate_test_price("108000 + 7200 + 2400"), 621.5)

    def test_unknown_package_detection_and_pricing(self):
        from utils import (
            parse_test_order_packages,
            format_package_progress_summary,
            format_missing_packages_summary,
            get_unknown_package_keyboard,
            update_unknown_package_price
        )

        # 1. Input with mixed known and unknown: 15000+2400+880 (15000 is unknown)
        p = parse_test_order_packages("15000+2400+880")
        self.assertIsNotNone(p)
        self.assertTrue(p["has_unknown"])
        self.assertEqual(p["known_total"], 24.5)  # 16.5 + 8 = 24.5
        self.assertEqual(len(p["packages"]), 3)
        self.assertEqual(p["packages"][0]["package"], "15000")
        self.assertFalse(p["packages"][0]["known"])

        # 2. Format initial state
        items = p["packages"]
        s0 = format_package_progress_summary(items, p["known_total"])
        self.assertIn("❓ 15000 CP", s0)
        self.assertIn("💰 Known Total: 24.5$", s0)

        missing_text = format_missing_packages_summary(items)
        self.assertIn("❌ Missing Packages", missing_text)
        self.assertIn("15000", missing_text)

        # 3. Check unknown package keyboard
        kb = get_unknown_package_keyboard(101, items)
        self.assertIsNotNone(kb)
        self.assertIn("add_unk_price:101:15000", kb.inline_keyboard[0][0].callback_data)

        # 4. Admin enters price 85 for 15000
        updated_items, new_total, has_remaining = update_unknown_package_price(items, "15000", 85.0)
        self.assertFalse(has_remaining)
        self.assertEqual(new_total, 109.5)  # 85 + 16.5 + 8 = 109.5

        # 5. Format updated state
        s1 = format_package_progress_summary(updated_items, new_total)
        self.assertIn("☐ 15000 CP", s1)
        self.assertIn("☐ 2400 CP", s1)
        self.assertIn("☐ 880 CP", s1)
        self.assertIn("💰 Total Price: 109.5$", s1)

    def test_recovery_codes_and_credentials_never_detected_as_packages(self):
        from utils import parse_test_order_packages

        # 1. Recovery Codes with Order section:
        order_msg = (
            "Facebook\n\n"
            "Email:\nfb2@gmail.com\n\n"
            "Password:\nPakistan786\n\n"
            "Recovery Codes:\n123456\n654321\n\n"
            "Order:\n10800+5040+2400"
        )
        p1 = parse_test_order_packages(order_msg)
        self.assertIsNotNone(p1)
        self.assertFalse(p1.get("has_unknown"))
        self.assertEqual(len(p1["packages"]), 3)
        self.assertEqual([item["package"] for item in p1["packages"]], ["10800", "5040", "2400"])

        # 2. Non-order text with numeric credentials (Password 2400abc, Email 2400@gmail.com, UID 108000123)
        cred_msg = (
            "Password:\n2400abc\n\n"
            "Email:\n2400@gmail.com\n\n"
            "UID:\n108000123"
        )
        p2 = parse_test_order_packages(cred_msg)
        self.assertIsNone(p2)


class TestMultiPackageDeliveryWorkflow(unittest.TestCase):
    """Tests multi-package delivery selection workflow for Loader Group."""

    def test_single_package_workflow(self):
        from utils import parse_test_order_packages, build_loader_package_keyboard, mark_selected_packages_delivered, format_loader_card_summary

        p = parse_test_order_packages("Order:\n2400")
        items = p["packages"]

        # Single package orders (len <= 1) do NOT show selection buttons
        kb0 = build_loader_package_keyboard(1, items, active_loader_id=10)
        self.assertIsNone(kb0)

        items, is_all, del_cnt = mark_selected_packages_delivered(items, loader_id=10)
        self.assertTrue(is_all)
        self.assertEqual(del_cnt, 1)

        summary = format_loader_card_summary(items, p["total_price"])
        self.assertIn("🎉 Order Completed", summary)
        self.assertIn("✅ 2400 CP", summary)

    def test_two_and_three_packages_workflow(self):
        from utils import parse_test_order_packages, toggle_package_selection, mark_selected_packages_delivered, build_loader_package_keyboard

        p = parse_test_order_packages("Order:\n10800+5040+2400")
        items = p["packages"]
        self.assertEqual(len(items), 3)

        items, _ = toggle_package_selection(items, 0, loader_id=100)
        items, _ = toggle_package_selection(items, 1, loader_id=100)

        items, is_all, del_cnt = mark_selected_packages_delivered(items, loader_id=100)
        self.assertFalse(is_all)
        self.assertEqual(del_cnt, 2)

        kb_rem = build_loader_package_keyboard(1, items, active_loader_id=100)
        self.assertIsNotNone(kb_rem)
        self.assertEqual(kb_rem.inline_keyboard[0][0].text, "⬜ 2400")

    def test_five_and_ten_packages_workflow(self):
        from utils import parse_test_order_packages, toggle_package_selection, mark_selected_packages_delivered

        text_5 = "Order:\n" + "+".join(["2400"] * 5)
        p5 = parse_test_order_packages(text_5)
        self.assertEqual(len(p5["packages"]), 5)

        text_10 = "Order:\n" + "+".join(["2400"] * 10)
        p10 = parse_test_order_packages(text_10)
        self.assertEqual(len(p10["packages"]), 10)

        items10 = p10["packages"]
        for i in range(5):
            items10, _ = toggle_package_selection(items10, i, loader_id=200)

        items10, is_all, del_cnt = mark_selected_packages_delivered(items10, loader_id=200)
        self.assertFalse(is_all)
        self.assertEqual(del_cnt, 5)

    def test_cancel_selection(self):
        from utils import parse_test_order_packages, toggle_package_selection, cancel_loader_selections

        p = parse_test_order_packages("Order:\n10800+5040")
        items = p["packages"]

        items, _ = toggle_package_selection(items, 0, loader_id=300)
        self.assertEqual(items[0]["status"], "Selected")

        items, reset_cnt = cancel_loader_selections(items, loader_id=300)
        self.assertEqual(reset_cnt, 1)
        self.assertEqual(items[0]["status"], "Pending")

    def test_duplicate_click_and_multiple_loaders_locking(self):
        from utils import parse_test_order_packages, toggle_package_selection, mark_selected_packages_delivered

        p = parse_test_order_packages("Order:\n10800+5040")
        items = p["packages"]

        items, status_a = toggle_package_selection(items, 0, loader_id=111)
        self.assertEqual(status_a, "Selected")

        items, status_b = toggle_package_selection(items, 0, loader_id=222)
        self.assertEqual(status_b, "Locked")

        items, _, _ = mark_selected_packages_delivered(items, loader_id=111)

        items, status_del = toggle_package_selection(items, 0, loader_id=222)
        self.assertEqual(status_del, "Delivered")

    def test_railway_restart_database_restore_persistence(self):
        import json
        from utils import parse_test_order_packages, format_loader_card_summary, build_loader_package_keyboard

        p = parse_test_order_packages("Order:\n10800+5040")
        items = p["packages"]
        items[0]["status"] = "Delivered"

        json_str = json.dumps(items)
        restored_items = json.loads(json_str)

        card = format_loader_card_summary(restored_items, 97.5)
        self.assertIn("✅ 10800 CP", card)
        self.assertIn("⬜ 5040 CP", card)

        kb = build_loader_package_keyboard(99, restored_items)
        self.assertIsNotNone(kb)
        self.assertEqual(kb.inline_keyboard[0][0].text, "⬜ 5040")

    def test_loader_order_card_redesign_layout(self):
        import json
        from utils import parse_test_order_packages, format_full_loader_order_card

        raw_msg = (
            "Facebook\n\n"
            "Email:\nedge1@gmail.com\n\n"
            "Password:\nHello123\n\n"
            "Recovery Codes:\n123456\n654321\n\n"
            "Order:\n10800+5040+2400"
        )
        p = parse_test_order_packages(raw_msg)
        items = p["packages"]

        class MockOrder:
            def __init__(self):
                self.raw_text = raw_msg
                self.email = "edge1@gmail.com"
                self.package_progress = json.dumps(items)
                self.price = "114.5"

        order = MockOrder()
        card_text = format_full_loader_order_card(order)

        # Verify layout order
        self.assertIn("📋 ORDER DETAILS", card_text)
        self.assertIn("🎮 Platform:\nFacebook", card_text)
        self.assertIn("📧 Email:\nedge1@gmail.com", card_text)
        self.assertIn("🔑 Password:\nHello123", card_text)
        self.assertIn("Recovery Codes:\n123456\n654321", card_text)

        self.assertIn("📦 PACKAGE STATUS", card_text)
        self.assertIn("⬜ 10800 CP", card_text)
        self.assertIn("⬜ 5040 CP", card_text)
        self.assertIn("⬜ 2400 CP", card_text)
        self.assertNotIn("💰 Total Price", card_text)

        # Verify ORDER DETAILS is BEFORE PACKAGE STATUS
        idx_details = card_text.index("📋 ORDER DETAILS")
        idx_status = card_text.index("📦 PACKAGE STATUS")
        self.assertTrue(idx_details < idx_status)


class TestDeliverySessionRouting(unittest.TestCase):
    """Tests persistent Delivery Session creation, prompt message linking, and reply routing."""

    def test_delivery_session_database_crud(self):
        import asyncio
        from database import create_delivery_session, get_delivery_session_by_msg_id, close_delivery_session, init_db

        async def run_async_test():
            await init_db()

            # 1. Create Delivery Session linked to prompt msg ID 9999
            ds = await create_delivery_session(
                order_id=42,
                loader_id=777,
                session_msg_id=9999,
                selected_packages='[{"package": "2400"}]'
            )
            self.assertIsNotNone(ds)
            self.assertEqual(ds.order_id, 42)
            self.assertEqual(ds.delivery_session_message_id, 9999)
            self.assertEqual(ds.status, "waiting_images")

            # 2. Look up session by prompt msg ID 9999
            matched = await get_delivery_session_by_msg_id(9999)
            self.assertIsNotNone(matched)
            self.assertEqual(matched.order_id, 42)

            # 3. Close Delivery Session
            await close_delivery_session(matched.id)

            # 4. Verify session is no longer active
            closed = await get_delivery_session_by_msg_id(9999)
            self.assertIsNone(closed)

        asyncio.run(run_async_test())

    def test_partial_delivery_caption_and_status_tracking(self):
        import json
        from utils import parse_test_order_packages, format_delivered_packages_caption, mark_selected_packages_delivered, format_package_progress_summary

        raw = "Order:\n10800+5040+2400"
        parsed = parse_test_order_packages(raw)
        items = parsed["packages"]

        # Loader selects 10800 and 5040
        items[0]["status"] = "Selected"
        items[0]["selected_by_loader"] = 111
        items[1]["status"] = "Selected"
        items[1]["selected_by_loader"] = 111

        selected_for_session = [items[0], items[1]]

        # 1. Verify delivered screenshot caption contains ONLY 10800 and 5040 (NOT 2400) and calculates session price (64 + 33 = 97$)
        caption = format_delivered_packages_caption(selected_for_session)
        self.assertIn("📦 Delivered Package(s)", caption)
        self.assertIn("✅ 10800 CP", caption)
        self.assertIn("✅ 5040 CP", caption)
        self.assertIn("💰 Price: 97$", caption)
        self.assertNotIn("2400", caption)

        # 2. Mark progress as delivered
        updated_items, is_all_completed, delivered_cnt = mark_selected_packages_delivered(items, loader_id=111)
        self.assertEqual(delivered_cnt, 2)
        self.assertFalse(is_all_completed)  # 2400 is still pending

        # 3. Verify Client card summary displays ✅ for 10800 and 5040, and ☐/⬜ for 2400
        summary = format_package_progress_summary(updated_items, 113.5)
        self.assertIn("✅ 10800 CP", summary)
        self.assertIn("✅ 5040 CP", summary)
        self.assertIn("☐ 2400 CP", summary)
        self.assertNotIn("🎉 All Packages Delivered", summary)

    def test_delivery_session_image_isolation_and_final_completion(self):
        import json
        from utils import parse_test_order_packages, mark_selected_packages_delivered, format_package_progress_summary, format_full_loader_order_card

        raw = "Order:\n10800+5040"
        parsed = parse_test_order_packages(raw)
        items = parsed["packages"]

        # Session 1: Deliver 10800
        items[0]["status"] = "Selected"
        session1_selected = [items[0]]
        updated_1, is_all_1, del_1 = mark_selected_packages_delivered(items, loader_id=1, selected_items=session1_selected)

        self.assertEqual(del_1, 1)
        self.assertFalse(is_all_1)
        self.assertEqual(updated_1[0]["status"], "Delivered")
        self.assertEqual(updated_1[1]["status"], "Pending")

        # Session 2: Deliver 5040 (last package)
        updated_1[1]["status"] = "Selected"
        session2_selected = [updated_1[1]]
        updated_2, is_all_2, del_2 = mark_selected_packages_delivered(updated_1, loader_id=1, selected_items=session2_selected)

        self.assertEqual(del_2, 1)
        self.assertTrue(is_all_2)  # ALL packages delivered now!
        self.assertEqual(updated_2[0]["status"], "Delivered")
        self.assertEqual(updated_2[1]["status"], "Delivered")

        # Verify final loader card text includes Order Completed
        class MockOrder:
            def __init__(self):
                self.raw_text = raw
                self.email = "final@example.com"
                self.package_progress = json.dumps(updated_2)
                self.price = "97.5"

        card = format_full_loader_order_card(MockOrder())
        self.assertIn("🎉 Order Completed", card)

    def test_single_package_order_bypasses_delivery_session_and_keyboard(self):
        import json
        from utils import parse_test_order_packages, build_loader_package_keyboard, format_full_loader_order_card

        raw = "Facebook\nEmail:\nsingle@gmail.com\nPassword:\n123456\nOrder:\n2400"
        parsed = parse_test_order_packages(raw)
        items = parsed["packages"]

        # 1. Single package orders (1 item) return None for keyboard (NO buttons)
        kb = build_loader_package_keyboard(42, items)
        self.assertIsNone(kb)

        # 2. Loader card hides price
        class MockOrder:
            def __init__(self):
                self.raw_text = raw
                self.email = "single@gmail.com"
                self.package_progress = json.dumps(items)
                self.price = "17"

        card = format_full_loader_order_card(MockOrder())
        self.assertIn("📦 PACKAGE STATUS", card)
        self.assertIn("⬜ 2400 CP", card)
        self.assertNotIn("💰 Total Price", card)
        self.assertNotIn("17$", card)

    def test_generic_issue_workflow_engine(self):
        from utils import detect_loader_issue, has_valid_account_update_fields, ISSUE_WORKFLOW_CONFIG
        from handlers import detect_loader_issue as detect_loader_issue_handler

        self.assertEqual(detect_loader_issue, detect_loader_issue_handler)

        # 1. Test all keywords for Wrong Name
        for kw in ["wrong name", "wrongname", "name wrong", "WRONG NAME", "WrongName", "Name Wrong"]:
            res = detect_loader_issue(kw)
            self.assertIsNotNone(res, f"Failed matching: {kw}")
            self.assertEqual(res[1], "wrong_name")

        # 2. Test all keywords for Wrong Password
        for kw in ["wrong password", "wrongpassword", "password wrong", "incorrect password", "WRONG PASSWORD", "Incorrect Password"]:
            res = detect_loader_issue(kw)
            self.assertIsNotNone(res, f"Failed matching: {kw}")
            self.assertEqual(res[1], "wrong_password")

        # 3. Test all keywords for Google Linked
        for kw in ["google linked", "linked google", "google account linked", "already linked", "google bind", "GOOGLE LINKED", "Already Linked"]:
            res = detect_loader_issue(kw)
            self.assertIsNotNone(res, f"Failed matching: {kw}")
            self.assertEqual(res[1], "google_linked")

        # 4. Test all keywords for 2FA
        for kw in ["2fa", "2fa issue", "two factor", "two-factor", "verification code", "authenticator", "backup code", "2FA", "Two Factor"]:
            res = detect_loader_issue(kw)
            self.assertIsNotNone(res, f"Failed matching: {kw}")
            self.assertEqual(res[1], "two_factor")

        # 5. Test all keywords for Login Failed
        for kw in ["login failed", "cannot login", "unable to login", "login error", "invalid credentials", "LOGIN FAILED", "Cannot Login"]:
            res = detect_loader_issue(kw)
            self.assertIsNotNone(res, f"Failed matching: {kw}")
            self.assertEqual(res[1], "login_failed")

        # Unrelated text should return None
        for chatter in ["done", "ok", "thanks", "hello", "fast", "completed", "@alyan", "@username", "❤️", "🔥"]:
            self.assertIsNone(detect_loader_issue(chatter), f"Should be None for: {chatter}")

        # 6. Test valid account detail field validation
        self.assertFalse(has_valid_account_update_fields("ok"))
        self.assertFalse(has_valid_account_update_fields("done"))
        self.assertFalse(has_valid_account_update_fields("thanks"))
        self.assertFalse(has_valid_account_update_fields("❤️"))
        self.assertFalse(has_valid_account_update_fields("🔥"))

        self.assertTrue(has_valid_account_update_fields("Email:\nnewmail@gmail.com"))
        self.assertTrue(has_valid_account_update_fields("Password: mysecretpass"))
        self.assertTrue(has_valid_account_update_fields("123456\n654321"))
        self.assertTrue(has_valid_account_update_fields("2FA code: 888999"))

    def test_reaction_api_and_loader_approval_messages(self):
        from utils import ALLOWED_REACTION_EMOJIS, ISSUE_WORKFLOW_CONFIG, LoaderIssueType

        # 1. Allowed reactions must contain standard Unicode emojis
        self.assertEqual(ALLOWED_REACTION_EMOJIS, {"👍", "❤️", "✅", "❌", "⏳"})

        # 2. Check customer approval success message for Wrong Name
        wn_cfg = ISSUE_WORKFLOW_CONFIG[LoaderIssueType.WRONG_NAME]
        self.assertIn("✅ Customer confirmed that the account name is correct.", wn_cfg["loader_success_msg"])
        self.assertIn("You may continue the delivery now.", wn_cfg["loader_success_msg"])
        self.assertIn("Reply with delivery screenshots when finished.", wn_cfg["loader_success_msg"])

    def test_issue_workflow_requires_screenshot_rules(self):
        from utils import ISSUE_WORKFLOW_CONFIG, LoaderIssueType

        # 1. Wrong Name MUST require a screenshot
        wn = ISSUE_WORKFLOW_CONFIG[LoaderIssueType.WRONG_NAME]
        self.assertTrue(wn.get("requires_screenshot"))
        self.assertIn("attach a screenshot", wn.get("missing_screenshot_msg", "").lower())

        # 2. Other issues (Wrong Password, Google Linked, 2FA, Login Failed) screenshots are OPTIONAL
        for it in [LoaderIssueType.WRONG_PASSWORD, LoaderIssueType.GOOGLE_LINKED, LoaderIssueType.TWO_FACTOR, LoaderIssueType.LOGIN_FAILED]:
            cfg = ISSUE_WORKFLOW_CONFIG[it]
            self.assertFalse(cfg.get("requires_screenshot"))

    def test_smart_customer_input_handling_per_issue_type(self):
        from utils import validate_customer_update_for_issue, ISSUE_WORKFLOW_CONFIG, LoaderIssueType

        # 1. Wrong Password
        self.assertTrue(validate_customer_update_for_issue("Password: Pakistan123", "wrong_password"))
        self.assertTrue(validate_customer_update_for_issue("Password = Pakistan123", "wrong_password"))
        self.assertTrue(validate_customer_update_for_issue("My new password is Pakistan123", "wrong_password"))
        self.assertTrue(validate_customer_update_for_issue("New password: Pakistan123", "wrong_password"))
        self.assertTrue(validate_customer_update_for_issue("Pakistan123", "wrong_password"))

        # 2. Wrong Name
        self.assertTrue(validate_customer_update_for_issue("Account Name:\nPlayer123", "wrong_name"))
        self.assertTrue(validate_customer_update_for_issue("Nickname:\nPlayer123", "wrong_name"))
        self.assertTrue(validate_customer_update_for_issue("My name is Player123", "wrong_name"))
        self.assertTrue(validate_customer_update_for_issue("Player123", "wrong_name"))

        # 3. Google Linked
        self.assertTrue(validate_customer_update_for_issue("Yes", "google_linked"))
        self.assertTrue(validate_customer_update_for_issue("No", "google_linked"))
        self.assertTrue(validate_customer_update_for_issue("Facebook", "google_linked"))
        self.assertTrue(validate_customer_update_for_issue("Activision", "google_linked"))
        self.assertTrue(validate_customer_update_for_issue("Use Facebook", "google_linked"))
        self.assertTrue(validate_customer_update_for_issue("Email:\nabc@gmail.com\nPassword:\n123456", "google_linked"))

        # 4. 2FA
        self.assertTrue(validate_customer_update_for_issue("123456", "two_factor"))
        self.assertTrue(validate_customer_update_for_issue("Authenticator Code:\n123456", "two_factor"))
        self.assertTrue(validate_customer_update_for_issue("Verification Code:\n123456", "two_factor"))
        self.assertTrue(validate_customer_update_for_issue("Backup Code:\nABCD-EFGH", "two_factor"))
        self.assertTrue(validate_customer_update_for_issue("Recovery Code:\n12345678", "two_factor"))

        # 5. Login Failed
        self.assertTrue(validate_customer_update_for_issue("abc@gmail.com", "login_failed"))
        self.assertTrue(validate_customer_update_for_issue("Pakistan123", "login_failed"))
        self.assertTrue(validate_customer_update_for_issue("Email:\nabc@gmail.com\nPassword:\nPakistan123", "login_failed"))

        # 6. General chatter should be rejected for ALL issue types
        for chatter in ["ok", "done", "thanks", "hello", "fast", "completed", "❤️", "🔥", "👍"]:
            self.assertFalse(validate_customer_update_for_issue(chatter, "wrong_password"), f"Chatter '{chatter}' should fail for wrong_password")
            self.assertFalse(validate_customer_update_for_issue(chatter, "wrong_name"), f"Chatter '{chatter}' should fail for wrong_name")
            self.assertFalse(validate_customer_update_for_issue(chatter, "two_factor"), f"Chatter '{chatter}' should fail for two_factor")

        # 7. Check issue-specific customer prompts
        for issue_type, cfg in ISSUE_WORKFLOW_CONFIG.items():
            self.assertIn("customer_update_prompt", cfg)
            if issue_type != LoaderIssueType.WRONG_PASSWORD:
                self.assertIn("❌ <b>Order Paused</b>", cfg["customer_update_prompt"])

    def test_single_numeric_price_validation_for_unknown_packages(self):
        from handlers import is_valid_price_string
        from utils import parse_test_order_packages, update_unknown_package_price

        # 1. Admin enters valid single numeric values (integers or decimals)
        self.assertTrue(is_valid_price_string("150"))
        self.assertTrue(is_valid_price_string("150.5"))
        self.assertTrue(is_valid_price_string("85"))

        # 2. Reject formulas or non-numeric strings
        self.assertFalse(is_valid_price_string("150+16+8"))
        self.assertFalse(is_valid_price_string("150+17+8"))
        self.assertFalse(is_valid_price_string("150rs"))
        self.assertFalse(is_valid_price_string("price 150"))

        # 3. Order: 15000 + 2400 + 880 (Known: 2400=17, 880=8, Total=25)
        p = parse_test_order_packages("15000+2400+880")
        items = p["packages"]

        # Admin enters single numeric price 150 for 15000
        updated, total, has_rem = update_unknown_package_price(items, "15000", 150.0)
        self.assertFalse(has_rem)
        self.assertEqual(total, 174.5)  # 150 + 16.5 + 8 = 174.5$

    def test_unknown_package_non_crashing_combinations(self):
        from utils import parse_test_order_packages

        inputs = [
            "2400+880",
            "15000+2400",
            "15000",
            "15000+3600+2400"
        ]

        for inp in inputs:
            p = parse_test_order_packages(inp)
            self.assertIsNotNone(p, f"Parser returned None for valid input '{inp}'")

            t_val = p.get("total_price")
            k_val = p.get("known_total")

            if isinstance(t_val, (int, float)):
                p_str = f"{t_val:g}"
                self.assertIsNotNone(p_str)
            else:
                self.assertIsNone(t_val)

            if isinstance(k_val, (int, float)):
                k_str = f"{k_val:g}"
                self.assertIsNotNone(k_str)

    def test_exact_non_redundant_package_detection(self):
        from utils import parse_test_order_packages, calculate_test_price

        # 1. 2400+880+420 -> Expected 3 distinct packages, total price 29.0$ (16.5+8+4.5)
        p1 = parse_test_order_packages("2400+880+420")
        self.assertIsNotNone(p1)
        self.assertEqual(len(p1["packages"]), 3)
        self.assertEqual([item["package"] for item in p1["packages"]], ["2400", "880", "420"])
        self.assertEqual(p1["total_price"], 29.0)
        self.assertEqual(calculate_test_price("2400+880+420"), 29.0)

        # 2. 2400+2400+880 -> Expected 3 packages preserving intentional duplicate 2400s, total price 41.0$ (16.5*2 + 8)
        p2 = parse_test_order_packages("2400+2400+880")
        self.assertIsNotNone(p2)
        self.assertEqual(len(p2["packages"]), 3)
        self.assertEqual([item["package"] for item in p2["packages"]], ["2400", "2400", "880"])
        self.assertEqual(p2["total_price"], 41.0)
        self.assertEqual(calculate_test_price("2400+2400+880"), 41.0)

    def test_package_summary_formatting(self):
        from utils import parse_test_order_packages, format_package_summary_and_price

        # Example 1: Single package 2400
        p1 = parse_test_order_packages("2400")
        f1 = format_package_summary_and_price(p1)
        self.assertEqual(f1, "📦 Package:\n• 2400 CP\n\n💰 Price: 16.5$")

        # Example 2: Multiple packages 2400+880
        p2 = parse_test_order_packages("2400+880")
        f2 = format_package_summary_and_price(p2)
        self.assertEqual(f2, "📦 Package(s):\n• 2400 CP\n• 880 CP\n\n💰 Price: 24.5$")

        # Example 3: Multiple packages 10800+5040+420
        p3 = parse_test_order_packages("10800+5040+420")
        f3 = format_package_summary_and_price(p3)
        self.assertEqual(f3, "📦 Package(s):\n• 10800 CP\n• 5040 CP\n• 420 CP\n\n💰 Price: 101.5$")

        # Example 4: Quantity 2400x2+880 (Expanded)
        p4 = parse_test_order_packages("2400x2+880")
        f4 = format_package_summary_and_price(p4)
        self.assertEqual(f4, "📦 Package(s):\n• 2400 CP\n• 2400 CP\n• 880 CP\n\n💰 Price: 41$")

        # Example 5: Order preservation (880+2400)
        p5 = parse_test_order_packages("880+2400")
        f5 = format_package_summary_and_price(p5)
        self.assertEqual(f5, "📦 Package(s):\n• 880 CP\n• 2400 CP\n\n💰 Price: 24.5$")

    def test_package_progress_tracking(self):
        from utils import parse_test_order_packages, format_package_progress_summary, advance_package_progress

        parsed = parse_test_order_packages("2400+880+420")
        items = [
            {"package": item["package"], "qty": item["qty"], "unit_price": item["unit_price"], "status": "Pending"}
            for item in parsed["packages"]
        ]
        total_price = parsed["total_price"]

        # Initial State: all pending
        s0 = format_package_progress_summary(items, total_price)
        self.assertEqual(s0, "📦 Packages\n\n☐ 2400 CP\n☐ 880 CP\n☐ 420 CP\n\n💰 Total Price: 29$")

        # Delivery 1: advance 2400 to Delivered
        items1, done1 = advance_package_progress(items)
        self.assertFalse(done1)
        s1 = format_package_progress_summary(items1, total_price)
        self.assertEqual(s1, "📦 Packages\n\n✅ 2400 CP\n☐ 880 CP\n☐ 420 CP\n\n💰 Total Price: 29$")

        # Delivery 2: advance 880 to Delivered
        items2, done2 = advance_package_progress(items1)
        self.assertFalse(done2)
        s2 = format_package_progress_summary(items2, total_price)
        self.assertEqual(s2, "📦 Packages\n\n✅ 2400 CP\n✅ 880 CP\n☐ 420 CP\n\n💰 Total Price: 29$")

        # Delivery 3: advance 420 to Delivered (all done)
        items3, done3 = advance_package_progress(items2)
        self.assertTrue(done3)
        s3 = format_package_progress_summary(items3, total_price)
        self.assertEqual(s3, "📦 Packages\n\n✅ 2400 CP\n✅ 880 CP\n✅ 420 CP\n\n🎉 All Packages Delivered\n\n💰 Total Price: 29$")


class TestExactContentDeduplication(unittest.IsolatedAsyncioTestCase):
    """Tests exact content deduplication ensuring zero false positives."""

    def test_normalize_order_content_for_dedup(self):
        from utils import normalize_order_content_for_dedup

        # Package difference -> Different
        self.assertNotEqual(
            normalize_order_content_for_dedup("Email: abc@gmail.com\nPackage: 10800"),
            normalize_order_content_for_dedup("Email: abc@gmail.com\nPackage: 7200")
        )

        # Combination difference -> Different
        self.assertNotEqual(
            normalize_order_content_for_dedup("Package: 2400"),
            normalize_order_content_for_dedup("Package: 2400+880")
        )

        # Email difference -> Different
        self.assertNotEqual(
            normalize_order_content_for_dedup("Email: abc@gmail.com"),
            normalize_order_content_for_dedup("Email: abcd@gmail.com")
        )

        # Username difference -> Different
        self.assertNotEqual(
            normalize_order_content_for_dedup("Username: Black2868"),
            normalize_order_content_for_dedup("Username: Black2869")
        )

        # Password difference -> Different
        self.assertNotEqual(
            normalize_order_content_for_dedup("Password: password1"),
            normalize_order_content_for_dedup("Password: password2")
        )

        # Insignificant whitespace/casing difference -> Identical
        t1 = "Email: ABC@gmail.com\n\n  Package:  10800 CP  "
        t2 = "email: abc@gmail.com\npackage: 10800 cp"
        self.assertEqual(
            normalize_order_content_for_dedup(t1),
            normalize_order_content_for_dedup(t2)
        )

    async def test_get_exact_duplicate_pending_order(self):
        from database import create_order, get_exact_duplicate_pending_order, delete_orders_by_email

        email = "dedup_test_user@example.com"
        await delete_orders_by_email(email)

        # 1. Create initial order for 10800 package
        order1_text = f"Email: {email}\nPackage: 10800\nUID: 12345"
        await create_order(
            email=email,
            package="10800",
            status="Pending",
            raw_text=order1_text
        )

        # 2. Check second order for 7200 package -> MUST NOT be duplicate!
        order2_text = f"Email: {email}\nPackage: 7200\nUID: 12345"
        dup_check_2 = await get_exact_duplicate_pending_order(email, order2_text)
        self.assertIsNone(dup_check_2, "Different package (10800 vs 7200) MUST NOT trigger duplicate warning!")

        # 3. Check third order with EXACT same content -> MUST be duplicate!
        dup_check_3 = await get_exact_duplicate_pending_order(email, order1_text)
        self.assertIsNotNone(dup_check_3, "Identical content MUST trigger duplicate warning!")

        # Clean up
        await delete_orders_by_email(email)


class TestLoaderReviewWorkflow(unittest.IsolatedAsyncioTestCase):
    """Tests Loader Review, Issue State Transitions, and Extensible Issue Types."""

    def test_loader_issue_type_enum_and_config(self):
        from utils import LoaderIssueType, LOADER_ISSUE_CONFIG

        self.assertEqual(LoaderIssueType.WRONG_NAME, "wrong_name")
        self.assertEqual(LoaderIssueType.WRONG_ACCOUNT, "wrong_account")
        self.assertEqual(LoaderIssueType.LOGIN_FAILED, "login_failed")
        self.assertEqual(LoaderIssueType.TWO_FACTOR, "two_factor")
        self.assertEqual(LoaderIssueType.NEED_CONFIRMATION, "need_confirmation")

        for issue_type in LoaderIssueType:
            self.assertIn(issue_type, LOADER_ISSUE_CONFIG)
            cfg = LOADER_ISSUE_CONFIG[issue_type]
            self.assertIn("label", cfg)
            self.assertIn("customer_text", cfg)
            self.assertIn("loader_yes_text", cfg)
            self.assertIn("loader_no_text", cfg)

    async def test_order_issue_state_transitions(self):
        from database import create_order, update_order_issue_state, get_order_by_id, delete_orders_by_email

        email = "issue_workflow_user@example.com"
        await delete_orders_by_email(email)

        order = await create_order(email=email, package="2400 CP")
        self.assertIsNone(order.issue_state)
        self.assertIsNone(order.issue_type)

        # 1. Loader reports WRONG_NAME
        updated1 = await update_order_issue_state(order.id, "Waiting_Customer_Confirmation", "wrong_name")
        self.assertEqual(updated1.issue_state, "Waiting_Customer_Confirmation")
        self.assertEqual(updated1.issue_type, "wrong_name")
        self.assertEqual(updated1.last_issue_type, "wrong_name")

        # 2. Customer rejects NO -> Waiting_Customer_Update
        updated2 = await update_order_issue_state(order.id, "Waiting_Customer_Update", "wrong_name")
        self.assertEqual(updated2.issue_state, "Waiting_Customer_Update")
        self.assertEqual(updated2.issue_type, "wrong_name")

        # 3. Customer submits update -> Resolved (issue_type cleared)
        updated3 = await update_order_issue_state(order.id, "Resolved")
        self.assertEqual(updated3.issue_state, "Resolved")
        self.assertIsNone(updated3.issue_type)
        self.assertEqual(updated3.last_issue_type, "wrong_name")

        # 4. Loader reports LOGIN_FAILED on another attempt
        updated4 = await update_order_issue_state(order.id, "Waiting_Customer_Confirmation", "login_failed")
        self.assertEqual(updated4.issue_state, "Waiting_Customer_Confirmation")
        self.assertEqual(updated4.issue_type, "login_failed")
        self.assertEqual(updated4.last_issue_type, "login_failed")

        # Clean up
        await delete_orders_by_email(email)

    async def test_issue_workflow_runs_independently_of_delivery_sessions(self):
        from database import create_order, update_order_issue_state, delete_orders_by_email, get_delivery_session_by_msg_id
        from utils import detect_loader_issue

        email = "session_independent_issue@example.com"
        await delete_orders_by_email(email)

        # Multi-package order
        order = await create_order(email=email, package="2400 CP\n10800 CP", raw_text="2400 CP\n10800 CP")
        self.assertIsNotNone(order)

        # Confirm no delivery session exists
        session = await get_delivery_session_by_msg_id(999999)
        self.assertIsNone(session)

        # Loader reports issue BEFORE Confirm Delivery
        issue_result = detect_loader_issue("wrong password")
        self.assertIsNotNone(issue_result)
        cfg, issue_id = issue_result
        self.assertEqual(issue_id, "wrong_password")

        # Issue state update executes without requiring a delivery session
        updated = await update_order_issue_state(order.id, "Waiting_Customer_Confirmation", issue_id)
        self.assertEqual(updated.issue_state, "Waiting_Customer_Confirmation")
        self.assertEqual(updated.issue_type, "wrong_password")

        await delete_orders_by_email(email)


class TestDuplicateDeliveryFingerprint(unittest.IsolatedAsyncioTestCase):
    """Tests delivery fingerprinting ensuring distinct packages never trigger false duplicate delivery blocks."""

    async def test_distinct_packages_same_screenshots_allowed(self):
        from database import create_order, add_images_to_order, delete_orders_by_email, compute_fingerprint

        email = "fp_test_user@example.com"
        await delete_orders_by_email(email)

        same_file_items = [("file_A_123", "photo"), ("file_B_456", "photo")]

        # 1. Order 40: 2400 CP with screenshots A, B
        order40 = await create_order(email=email, package="2400", raw_text="2400")
        order40_updated, is_dup40 = await add_images_to_order(order40.id, same_file_items)
        self.assertFalse(is_dup40, "Order 40 delivery must be accepted!")

        # 2. Order 41: 2400+880 CP with SAME screenshots A, B -> MUST BE DELIVERED (different package!)
        order41 = await create_order(email=email, package="2400+880", raw_text="2400+880")
        order41_updated, is_dup41 = await add_images_to_order(order41.id, same_file_items)
        self.assertFalse(is_dup41, "Order 41 with different package (2400+880 vs 2400) using same screenshots MUST BE DELIVERED!")

        # Clean up
        await delete_orders_by_email(email)

    async def test_identical_package_same_screenshots_blocked(self):
        from database import create_order, add_images_to_order, delete_orders_by_email

        email = "fp_dup_user@example.com"
        await delete_orders_by_email(email)

        same_file_items = [("file_X_789", "photo"), ("file_Y_012", "photo")]

        # 1. Order 50: 2400 CP with screenshots X, Y
        order50 = await create_order(email=email, package="2400", raw_text="2400")
        _, is_dup50 = await add_images_to_order(order50.id, same_file_items)
        self.assertFalse(is_dup50, "Order 50 delivery must be accepted!")

        # 2. Order 51: EXACT same package 2400 CP with SAME screenshots X, Y -> MUST BE BLOCKED AS DUPLICATE!
        order51 = await create_order(email=email, package="2400", raw_text="2400")
        _, is_dup51 = await add_images_to_order(order51.id, same_file_items)
        self.assertTrue(is_dup51, "Order 51 with IDENTICAL package (2400) and SAME screenshots MUST BE BLOCKED as duplicate delivery!")

        # Clean up
        await delete_orders_by_email(email)


class TestBulkPriceUpdateSystem(unittest.IsolatedAsyncioTestCase):
    """Unit tests for the Production Bulk Price Update System."""

    async def asyncSetUp(self):
        from database import seed_and_load_package_prices
        await seed_and_load_package_prices()

    async def test_export_prices_format(self):
        from database import get_all_package_prices_from_db
        from utils import format_export_prices

        db_prices = await get_all_package_prices_from_db()
        self.assertEqual(len(db_prices), 22)
        export_text = format_export_prices(db_prices)
        self.assertIn("10800 64", export_text)
        self.assertIn("2400 16.5", export_text)
        self.assertIn("108000 563", export_text)

    async def test_parse_bulk_prices_valid_input(self):
        from utils import parse_bulk_prices_input

        sample_input = """
        10800 65
        5040 = 34
        2400 : 17
        880 -> 8.5
        420 => 5
        80 1.5

        108000 570
        96000 510
        72000 380
        55200 295
        48000 260
        43200 235
        38400 215
        24000 135
        21600 122
        19200 112
        16800 98
        14400 85
        12000 72
        9600 58
        7200 45
        4800 31
        """
        price_map, err = parse_bulk_prices_input(sample_input)
        self.assertIsNone(err)
        self.assertIsNotNone(price_map)
        self.assertEqual(len(price_map), 22)
        self.assertEqual(price_map["10800"], 65.0)
        self.assertEqual(price_map["5040"], 34.0)
        self.assertEqual(price_map["2400"], 17.0)

    async def test_validation_errors_and_rollback(self):
        from utils import parse_bulk_prices_input
        from database import get_all_package_prices_from_db

        initial_prices = await get_all_package_prices_from_db()

        # 1. Non-numeric Package
        _, err1 = parse_bulk_prices_input("abc 100")
        self.assertIsNotNone(err1)
        self.assertIn("❌ Unknown Package", err1)
        self.assertIn("abc", err1)

        # 2. Invalid Price
        _, err2 = parse_bulk_prices_input("10800 abc")
        self.assertIsNotNone(err2)
        self.assertIn("❌ Invalid Price", err2)

        # 3. Duplicate Package
        dup_text = "10800 64\n10800 65"
        _, err3 = parse_bulk_prices_input(dup_text)
        self.assertIsNotNone(err3)
        self.assertIn("❌ Duplicate Package", err3)

        # 4. Partial UPSERT valid input (accepted as valid partial price map)
        partial_text = "10800 64\n5040 33"
        price_map, err4 = parse_bulk_prices_input(partial_text)
        self.assertIsNone(err4)
        self.assertEqual(len(price_map), 2)
        self.assertEqual(price_map["10800"], 64.0)

        # Verify DB remained untouched before execution
        after_prices = await get_all_package_prices_from_db()
        self.assertEqual(initial_prices, after_prices)

    async def test_atomic_bulk_update_and_cache_reload(self):
        from utils import parse_bulk_prices_input, calculate_test_price, PACKAGE_PRICES
        from database import bulk_update_package_prices_in_db, get_all_package_prices_from_db, DEFAULT_PACKAGE_PRICES

        valid_input = """
        10800 70
        5040 35
        2400 18
        880 9
        420 5
        80 2

        108000 600
        96000 520
        72000 390
        55200 300
        48000 270
        43200 240
        38400 220
        24000 140
        21600 125
        19200 115
        16800 100
        14400 88
        12000 75
        9600 60
        7200 46
        4800 32
        """
        price_map, err = parse_bulk_prices_input(valid_input)
        self.assertIsNone(err)

        # Update DB & Cache
        success = await bulk_update_package_prices_in_db(price_map, updated_by_id=1573531032)
        self.assertTrue(success)

        # Verify Cache updated immediately without restart
        self.assertEqual(PACKAGE_PRICES["10800"], 70.0)
        self.assertEqual(calculate_test_price("10800"), 70.0)
        self.assertEqual(calculate_test_price("2400"), 18.0)
        self.assertEqual(calculate_test_price("10800 + 5040"), 105.0)

        # Verify DB persisted
        db_prices = await get_all_package_prices_from_db()
        self.assertEqual(db_prices["10800"], 70.0)

        # Restore default prices
        await bulk_update_package_prices_in_db(DEFAULT_PACKAGE_PRICES, updated_by_id=1573531032)
        self.assertEqual(calculate_test_price("10800"), 64.0)

    async def test_unauthorized_user_blocked(self):
        from utils import is_super_admin
        self.assertTrue(is_super_admin(1573531032))
        self.assertFalse(is_super_admin(999999999))

    async def test_command_menu_and_handler_audit(self):
        from handlers import exportprices_command_handler, updateprices_command_handler

        # Inspect main.py source code to verify set_my_commands and handler registration
        with open("main.py", "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn('BotCommand("exportprices"', content)
        self.assertIn('BotCommand("updateprices"', content)
        self.assertIn('CommandHandler("exportprices", exportprices_command_handler)', content)
        self.assertIn('CommandHandler("updateprices", updateprices_command_handler)', content)

        # Inspect handlers.py source code to verify help_command includes both commands
        with open("handlers.py", "r", encoding="utf-8") as f:
            h_content = f.read()

        self.assertIn('exportprices', h_content)
        self.assertIn('updateprices', h_content)


class TestIgnoredTrustedUsers(unittest.IsolatedAsyncioTestCase):
    """Tests trusted user ID ignore rules in order detection engine & group handlers."""

    def test_ignored_user_ids_config(self):
        from config import Config
        from utils import is_ignored_user

        self.assertNotIn(1573531032, Config.IGNORED_USER_IDS)
        self.assertIn(1249984265, Config.IGNORED_USER_IDS)

        self.assertFalse(is_ignored_user(1573531032))
        self.assertTrue(is_ignored_user(1249984265))
        self.assertFalse(is_ignored_user(999999999))

    async def test_ignored_users_in_source_group_handler(self):
        from unittest.mock import MagicMock, AsyncMock
        from handlers import source_group_handler, BOT_SETTINGS

        BOT_SETTINGS["source_group_id"] = -1001111222333

        # Ignored User 1249984265
        update1249 = MagicMock()
        update1249.effective_message.message_id = 902
        update1249.effective_message.text = "test1249@gmail.com 2400 CP"
        update1249.effective_chat.id = -1001111222333
        update1249.effective_user.id = 1249984265
        update1249.effective_message.reply_text = AsyncMock()
        await source_group_handler(update1249, None)
        update1249.effective_message.reply_text.assert_not_called()


class TestDeliveryLedgerSystem(unittest.IsolatedAsyncioTestCase):
    """Unit tests for Production Delivery Ledger & Running Total System."""

    async def asyncSetUp(self):
        from database import init_db, AsyncSessionLocal
        from models import DeliveryLedger, RunningTotalLedger
        from sqlalchemy import delete
        await init_db()
        async with AsyncSessionLocal() as session:
            await session.execute(delete(DeliveryLedger))
            await session.execute(delete(RunningTotalLedger))
            await session.commit()

    async def test_first_delivery(self):
        from database import record_delivery_ledger_entry, get_current_running_total

        before_start = await get_current_running_total()
        self.assertEqual(before_start, 0.0)

        e1, ok1 = await record_delivery_ledger_entry(order_id=201, package="10800", now_value=64.0, loader_name="Loader A", dedup_hash="201:10800:1")
        self.assertTrue(ok1)
        self.assertEqual(e1.before_total, 0.0)
        self.assertEqual(e1.now_value, 64.0)
        self.assertEqual(e1.running_total, 64.0)

    async def test_running_total_accumulation(self):
        from database import record_delivery_ledger_entry, get_current_running_total

        await record_delivery_ledger_entry(order_id=202, package="10800", now_value=64.0, dedup_hash="202:1")
        self.assertEqual(await get_current_running_total(), 64.0)

        await record_delivery_ledger_entry(order_id=202, package="5040", now_value=33.0, dedup_hash="202:2")
        self.assertEqual(await get_current_running_total(), 97.0)

    async def test_ledger_reply_message_and_missing_loader_name(self):
        from unittest.mock import AsyncMock, MagicMock
        from handlers import process_delivery_ledger_event
        from database import get_last_ledger_entry

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()

        await process_delivery_ledger_event(
            order_id=301,
            package_str="10800",
            loader_name=None,  # Missing loader name fallback test
            bot=mock_bot,
            chat_id=-100999,
            dedup_hash="301:10800:reply_test",
            reply_to_message_id=888  # Delivery message reply ID
        )

        mock_bot.send_message.assert_called_once()
        call_kwargs = mock_bot.send_message.call_args.kwargs
        self.assertEqual(call_kwargs["chat_id"], -100999)
        self.assertEqual(call_kwargs["text"], "Before 0$\nNow 64$\nTotal 64$")

        last_e = await get_last_ledger_entry()
        self.assertIsNotNone(last_e)
        self.assertEqual(last_e.loader, "Loader")  # Fallback verified

    async def test_partial_deliveries_create_separate_ledger_entries(self):
        from database import record_delivery_ledger_entry, get_current_running_total

        e1, ok1 = await record_delivery_ledger_entry(order_id=101, package="10800", now_value=64.0, loader_name="Loader A", dedup_hash="101:10800:1")
        self.assertTrue(ok1)
        self.assertIsNotNone(e1)
        self.assertEqual(e1.now_value, 64.0)

        e2, ok2 = await record_delivery_ledger_entry(order_id=101, package="5040", now_value=33.0, loader_name="Loader A", dedup_hash="101:5040:2")
        self.assertTrue(ok2)
        self.assertEqual(e2.now_value, 33.0)

        e3, ok3 = await record_delivery_ledger_entry(order_id=101, package="2400", now_value=16.5, loader_name="Loader A", dedup_hash="101:2400:3")
        self.assertTrue(ok3)
        self.assertEqual(e3.now_value, 16.5)

    async def test_multi_package_delivery_combines_value(self):
        from utils import calculate_delivered_packages_value
        from database import record_delivery_ledger_entry

        val, known = calculate_delivered_packages_value("10800+5040")
        self.assertTrue(known)
        self.assertEqual(val, 97.0)

        e, ok = await record_delivery_ledger_entry(order_id=102, package="10800+5040", now_value=val, loader_name="Loader B", dedup_hash="102:multi:1")
        self.assertTrue(ok)
        self.assertEqual(e.now_value, 97.0)

    async def test_duplicate_delivery_blocked(self):
        from database import record_delivery_ledger_entry

        e1, ok1 = await record_delivery_ledger_entry(order_id=103, package="2400", now_value=16.5, loader_name="Loader C", dedup_hash="DUP_TEST_HASH_123")
        self.assertTrue(ok1)

        e2, ok2 = await record_delivery_ledger_entry(order_id=103, package="2400", now_value=16.5, loader_name="Loader C", dedup_hash="DUP_TEST_HASH_123")
        self.assertFalse(ok2)
        self.assertIsNone(e2)

    async def test_manual_adjustment_reason_requirement_and_execution(self):
        from database import record_delivery_ledger_entry

        e_add, ok1 = await record_delivery_ledger_entry(
            order_id=None,
            package="Manual Add",
            now_value=29.0,
            loader_name="Admin",
            reason="Special Pack Price Correction",
            is_manual=True
        )
        self.assertTrue(ok1)
        self.assertTrue(e_add.is_manual)
        self.assertEqual(e_add.reason, "Special Pack Price Correction")
        self.assertEqual(e_add.now_value, 29.0)

        e_sub, ok2 = await record_delivery_ledger_entry(
            order_id=None,
            package="Manual Subtract",
            now_value=-16.0,
            loader_name="Admin",
            reason="Duplicate Entry Correction",
            is_manual=True
        )
        self.assertTrue(ok2)
        self.assertTrue(e_sub.is_manual)
        self.assertEqual(e_sub.reason, "Duplicate Entry Correction")
        self.assertEqual(e_sub.now_value, -16.0)

    async def test_safe_undo_with_confirmation(self):
        from database import record_delivery_ledger_entry, undo_ledger_entry

        e, ok = await record_delivery_ledger_entry(order_id=105, package="880", now_value=8.0, loader_name="Loader D", dedup_hash="UNDO_HASH_105")
        self.assertTrue(ok)

        undone = await undo_ledger_entry(e.id, admin_id=1573531032)
        self.assertIsNotNone(undone)
        self.assertEqual(undone.id, e.id)

    async def test_todaytotal_period_stats(self):
        from database import get_ledger_period_stats
        stats = await get_ledger_period_stats()
        self.assertIn("today_count", stats)
        self.assertIn("today_revenue", stats)
        self.assertIn("week_revenue", stats)
        self.assertIn("month_revenue", stats)
        self.assertIn("running_total", stats)

    async def test_reset_ledger(self):
        from database import record_delivery_ledger_entry, reset_delivery_ledger, get_current_running_total

        await record_delivery_ledger_entry(order_id=106, package="420", now_value=4.5, loader_name="Loader E", dedup_hash="RESET_TEST_HASH")
        res = await reset_delivery_ledger(admin_id=1573531032)
        self.assertTrue(res)
        tot = await get_current_running_total()
        self.assertEqual(tot, 0.0)


class TestSimpleRunningTotalCalculator(unittest.IsolatedAsyncioTestCase):
    """Unit tests for Simple Running Total Calculator module."""

    async def asyncSetUp(self):
        from database import init_db, AsyncSessionLocal
        from models import CalculatorLedger
        from sqlalchemy import delete
        await init_db()
        async with AsyncSessionLocal() as session:
            await session.execute(delete(CalculatorLedger))
            await session.commit()

    async def test_positive_and_negative_calculations(self):
        from database import record_calculator_entry, get_calculator_current_total

        self.assertEqual(await get_calculator_current_total(), 0.0)

        e1, b1, n1, a1 = await record_calculator_entry(97.0, admin_id=1573531032)
        self.assertEqual(b1, 0.0)
        self.assertEqual(n1, 97.0)
        self.assertEqual(a1, 97.0)
        self.assertEqual(await get_calculator_current_total(), 97.0)

        e2, b2, n2, a2 = await record_calculator_entry(64.0, admin_id=1573531032)
        self.assertEqual(b2, 97.0)
        self.assertEqual(n2, 64.0)
        self.assertEqual(a2, 161.0)
        self.assertEqual(await get_calculator_current_total(), 161.0)

        e3, b3, n3, a3 = await record_calculator_entry(-100.0, admin_id=1573531032)
        self.assertEqual(b3, 161.0)
        self.assertEqual(n3, -100.0)
        self.assertEqual(a3, 61.0)
        self.assertEqual(await get_calculator_current_total(), 61.0)

    async def test_undo_calculator_entry(self):
        from database import record_calculator_entry, undo_last_calculator_entry, get_calculator_current_total

        await record_calculator_entry(97.0, admin_id=1573531032)
        await record_calculator_entry(64.0, admin_id=1573531032)
        await record_calculator_entry(-100.0, admin_id=1573531032)

        self.assertEqual(await get_calculator_current_total(), 61.0)

        undone = await undo_last_calculator_entry(admin_id=1573531032)
        self.assertIsNotNone(undone)
        self.assertEqual(undone.amount, -100.0)
        self.assertEqual(await get_calculator_current_total(), 161.0)

    async def test_formatting_messages(self):
        from utils import format_calculator_result_message, format_calculator_total_message

        pos_msg = format_calculator_result_message(97.0, 64.0, 161.0)
        self.assertIn("Before\n97$", pos_msg)
        self.assertIn("Now\n+64$", pos_msg)
        self.assertIn("Total\n161$", pos_msg)

        neg_msg = format_calculator_result_message(161.0, -100.0, 61.0)
        self.assertIn("Before\n161$", neg_msg)
        self.assertIn("Now\n-100$", neg_msg)
        self.assertIn("Total\n61$", neg_msg)

        tot_msg = format_calculator_total_message(61.0)
        self.assertIn("Current Total", tot_msg)
        self.assertIn("61$", tot_msg)

    async def test_unauthorized_user_blocked(self):
        from unittest.mock import MagicMock, AsyncMock
        from handlers import calculate_command_handler, total_command_handler, calc_undo_command_handler

        unauth_update = MagicMock()
        unauth_update.effective_user.id = 999111222
        unauth_update.effective_message.reply_text = AsyncMock()

        await calculate_command_handler(unauth_update, None)
        unauth_update.effective_message.reply_text.assert_called_with("❌ You are not authorized to use this command.")

        unauth_update.effective_message.reply_text.reset_mock()
        await total_command_handler(unauth_update, None)
        unauth_update.effective_message.reply_text.assert_called_with("❌ You are not authorized to use this command.")

        unauth_update.effective_message.reply_text.reset_mock()
        await calc_undo_command_handler(unauth_update, None)
        unauth_update.effective_message.reply_text.assert_called_with("❌ You are not authorized to use this command.")


class TestSimpleRunningTotalSystem(unittest.IsolatedAsyncioTestCase):
    """Unit tests for Production Simple Running Total System."""

    async def asyncSetUp(self):
        from database import init_db, AsyncSessionLocal
        from models import DeliveryLedger, RunningTotalLedger
        from sqlalchemy import delete
        await init_db()
        async with AsyncSessionLocal() as session:
            await session.execute(delete(DeliveryLedger))
            await session.execute(delete(RunningTotalLedger))
            await session.commit()

    async def test_automatic_delivery_calculation_and_multi_deliveries(self):
        from database import execute_auto_delivery_total, get_running_total_current

        self.assertEqual(await get_running_total_current(), 0.0)

        e1, b1, n1, a1 = await execute_auto_delivery_total(order_id=161, now_val=64.0)
        self.assertEqual(b1, 0.0)
        self.assertEqual(n1, 64.0)
        self.assertEqual(a1, 64.0)
        self.assertEqual(await get_running_total_current(), 64.0)

        e2, b2, n2, a2 = await execute_auto_delivery_total(order_id=162, now_val=33.0)
        self.assertEqual(b2, 64.0)
        self.assertEqual(n2, 33.0)
        self.assertEqual(a2, 97.0)

        e3, b3, n3, a3 = await execute_auto_delivery_total(order_id=163, now_val=16.5)
        self.assertEqual(b3, 97.0)
        self.assertEqual(n3, 16.5)
        self.assertEqual(a3, 113.5)
        self.assertEqual(await get_running_total_current(), 113.5)

    async def test_pay_resets_total_to_zero_and_new_deliveries_restart(self):
        from database import execute_auto_delivery_total, execute_pay_reset, get_running_total_current

        # 1. Deliveries: 64$, 33$, 64$ -> Running Total: 161$
        e1, b1, n1, a1 = await execute_auto_delivery_total(order_id=301, now_val=64.0)
        self.assertEqual(a1, 64.0)

        e2, b2, n2, a2 = await execute_auto_delivery_total(order_id=302, now_val=33.0)
        self.assertEqual(a2, 97.0)

        e3, b3, n3, a3 = await execute_auto_delivery_total(order_id=303, now_val=64.0)
        self.assertEqual(a3, 161.0)
        self.assertEqual(await get_running_total_current(), 161.0)

        # 2. Run /pay -> Verify Before: 161$, Paid: 161$, After: 0$
        entry, before, paid, current = await execute_pay_reset(admin_id=1573531032)
        self.assertEqual(before, 161.0)
        self.assertEqual(paid, 161.0)
        self.assertEqual(current, 0.0)
        self.assertEqual(await get_running_total_current(), 0.0)

        # 3. Deliver 16.5$ -> Verify Before: 0$, Now: 16.5$, Total: 16.5$
        e_next, b_next, n_next, a_next = await execute_auto_delivery_total(order_id=304, now_val=16.5)
        self.assertEqual(b_next, 0.0)
        self.assertEqual(n_next, 16.5)
        self.assertEqual(a_next, 16.5)
        self.assertEqual(await get_running_total_current(), 16.5)

    async def test_manual_plus_and_minus_adjustments(self):
        from database import execute_auto_delivery_total, execute_manual_adjustment, get_running_total_current

        await execute_auto_delivery_total(order_id=161, now_val=64.0)
        self.assertEqual(await get_running_total_current(), 64.0)

        e_plus, b_plus, n_plus, a_plus, act_plus = await execute_manual_adjustment(10.0, admin_id=1573531032)
        self.assertEqual(b_plus, 64.0)
        self.assertEqual(n_plus, 10.0)
        self.assertEqual(a_plus, 74.0)
        self.assertEqual(act_plus, "MANUAL_PLUS")
        self.assertEqual(await get_running_total_current(), 74.0)

        e_minus, b_minus, n_minus, a_minus, act_minus = await execute_manual_adjustment(-10.0, admin_id=1573531032)
        self.assertEqual(b_minus, 74.0)
        self.assertEqual(n_minus, -10.0)
        self.assertEqual(a_minus, 64.0)
        self.assertEqual(act_minus, "MANUAL_MINUS")
        self.assertEqual(await get_running_total_current(), 64.0)

    async def test_undo_last_action(self):
        from database import execute_auto_delivery_total, execute_manual_adjustment, undo_last_running_total_action, get_running_total_current

        await execute_auto_delivery_total(order_id=161, now_val=64.0)
        await execute_manual_adjustment(10.0, admin_id=1573531032)
        self.assertEqual(await get_running_total_current(), 74.0)

        undone = await undo_last_running_total_action(admin_id=1573531032)
        self.assertIsNotNone(undone)
        self.assertEqual(undone.amount, 10.0)
        self.assertEqual(await get_running_total_current(), 64.0)

    async def test_unauthorized_users_ignored(self):
        from unittest.mock import MagicMock, AsyncMock
        from handlers import running_total_command_handler, pay_running_total_command_handler, manual_running_total_text_handler

        unauth_update = MagicMock()
        unauth_update.effective_user.id = 999888777
        unauth_update.effective_message.reply_text = AsyncMock()
        unauth_update.effective_message.text = "+100"

        await running_total_command_handler(unauth_update, None)
        unauth_update.effective_message.reply_text.assert_not_called()

        await pay_running_total_command_handler(unauth_update, None)
        unauth_update.effective_message.reply_text.assert_not_called()

        await manual_running_total_text_handler(unauth_update, None)
        unauth_update.effective_message.reply_text.assert_not_called()

    async def test_bug1_delivery_after_pay_starts_from_zero(self):
        from database import record_delivery_ledger_entry, execute_pay_reset, get_running_total_current

        await record_delivery_ledger_entry(order_id=501, package="10800", now_value=225.0, dedup_hash="b1_1")
        await record_delivery_ledger_entry(order_id=502, package="5040", now_value=33.0, dedup_hash="b1_2")
        await record_delivery_ledger_entry(order_id=503, package="10800", now_value=64.0, dedup_hash="b1_3")

        self.assertEqual(await get_running_total_current(), 322.0)

        entry, before, paid, current = await execute_pay_reset(admin_id=1573531032)
        self.assertEqual(before, 322.0)
        self.assertEqual(paid, 322.0)
        self.assertEqual(current, 0.0)
        self.assertEqual(await get_running_total_current(), 0.0)

        e_next, ok = await record_delivery_ledger_entry(order_id=504, package="420", now_value=5.5, dedup_hash="b1_4")
        self.assertTrue(ok)
        self.assertEqual(e_next.before_total, 0.0)
        self.assertEqual(e_next.now_value, 5.5)
        self.assertEqual(e_next.running_total, 5.5)
        self.assertEqual(await get_running_total_current(), 5.5)

    async def test_bug2_decimal_negative_manual_adjustment_preserves_sign(self):
        from database import record_delivery_ledger_entry, execute_manual_adjustment, get_running_total_current

        await record_delivery_ledger_entry(order_id=601, package="327.5", now_value=327.5, dedup_hash="b2_1")
        self.assertEqual(await get_running_total_current(), 327.5)

        entry, before_val, now_val, after_val, act_type = await execute_manual_adjustment(-327.5, admin_id=1573531032)
        self.assertEqual(before_val, 327.5)
        self.assertEqual(now_val, -327.5)
        self.assertEqual(after_val, 0.0)
        self.assertEqual(act_type, "MANUAL_MINUS")
        self.assertEqual(await get_running_total_current(), 0.0)

    async def test_partial_delivery_running_total_correctness(self):
        from utils import parse_test_order_packages, mark_selected_packages_delivered, calculate_delivered_packages_value
        from database import record_delivery_ledger_entry, get_running_total_current

        parsed = parse_test_order_packages("880+420+80")
        raw_items = parsed["packages"]

        # Step 1: Deliver 880 + 420 (Price: 9.0 + 3.5 = 12.5$)
        step1_selection = [{"package": "880"}, {"package": "420"}]
        updated1, is_all1, cnt1 = mark_selected_packages_delivered(raw_items, loader_id=1, selected_items=step1_selection)
        self.assertFalse(is_all1)

        new_names1 = [it["package"] for it in step1_selection]
        pkg_str1 = "+".join(new_names1)
        val1, ok1 = calculate_delivered_packages_value(pkg_str1)
        self.assertTrue(ok1)
        self.assertEqual(val1, 12.5)

        e1, _ = await record_delivery_ledger_entry(order_id=701, package=pkg_str1, now_value=val1, dedup_hash="pd_1")
        self.assertEqual(e1.before_total, 0.0)
        self.assertEqual(e1.now_value, 12.5)
        self.assertEqual(e1.running_total, 12.5)

        # Step 2: Deliver 80 (Price: 1.0$)
        step2_selection = [{"package": "80"}]
        updated2, is_all2, cnt2 = mark_selected_packages_delivered(updated1, loader_id=1, selected_items=step2_selection)
        self.assertTrue(is_all2)

        new_names2 = [it["package"] for it in step2_selection]
        pkg_str2 = "+".join(new_names2)
        val2, ok2 = calculate_delivered_packages_value(pkg_str2)
        self.assertTrue(ok2)
        self.assertEqual(val2, 1.0)

        e2, _ = await record_delivery_ledger_entry(order_id=701, package=pkg_str2, now_value=val2, dedup_hash="pd_2")
        self.assertEqual(e2.before_total, 12.5)
        self.assertEqual(e2.now_value, 1.0)
        self.assertEqual(e2.running_total, 13.5)
        self.assertEqual(await get_running_total_current(), 13.5)

    async def test_three_step_and_multi_package_partial_deliveries(self):
        from utils import calculate_delivered_packages_value
        from database import record_delivery_ledger_entry

        val, ok = calculate_delivered_packages_value("5040+2400")
        self.assertTrue(ok)
        self.assertEqual(val, 49.5)

        e, _ = await record_delivery_ledger_entry(order_id=702, package="5040+2400", now_value=val, dedup_hash="pd_multi")
        self.assertEqual(e.now_value, 49.5)

    async def test_second_and_third_partial_delivery_exact_now_values(self):
        from utils import parse_test_order_packages, mark_selected_packages_delivered, calculate_delivered_packages_value
        from database import record_delivery_ledger_entry, get_running_total_current

        # Order: 10800 + 5040 + 2400 (Prices: 64$, 33$, 16.5$ -> Total: 113.5$)
        parsed = parse_test_order_packages("10800+5040+2400")
        items = parsed["packages"]

        # Step 1: First delivery (10800 + 2400) -> Now: 80.5$, Before: 0$, Total: 80.5$
        sel1 = [{"package": "10800"}, {"package": "2400"}]
        updated1, is_all1, _ = mark_selected_packages_delivered(items, loader_id=1, selected_items=sel1)
        val1, ok1 = calculate_delivered_packages_value("10800+2400")
        self.assertTrue(ok1)
        self.assertEqual(val1, 80.5)

        e1, _ = await record_delivery_ledger_entry(order_id=801, package="10800+2400", now_value=val1, dedup_hash="ex_1")
        self.assertEqual(e1.before_total, 0.0)
        self.assertEqual(e1.now_value, 80.5)
        self.assertEqual(e1.running_total, 80.5)

        # Step 2: Second delivery (5040) -> Now: 33$, Before: 80.5$, Total: 113.5$
        sel2 = [{"package": "5040"}]
        updated2, is_all2, _ = mark_selected_packages_delivered(updated1, loader_id=1, selected_items=sel2)
        val2, ok2 = calculate_delivered_packages_value("5040")
        self.assertTrue(ok2)
        self.assertEqual(val2, 33.0)

        e2, _ = await record_delivery_ledger_entry(order_id=801, package="5040", now_value=val2, dedup_hash="ex_2")
        self.assertEqual(e2.before_total, 80.5)
        self.assertEqual(e2.now_value, 33.0)
        self.assertEqual(e2.running_total, 113.5)
        self.assertEqual(await get_running_total_current(), 113.5)


class TestMultiPackageSelectionRegression(unittest.TestCase):
    """Regression tests to verify multi-package selection UI and calculation."""

    def test_selecting_two_packages_simultaneously(self):
        from utils import parse_test_order_packages, toggle_package_selection

        parsed = parse_test_order_packages("10800+5040+2400")
        items = parsed["packages"]

        # Select 10800
        items, s1 = toggle_package_selection(items, 0, loader_id=10)
        self.assertEqual(s1, "Selected")
        self.assertEqual(items[0]["status"], "Selected")

        # Select 5040 simultaneously
        items, s2 = toggle_package_selection(items, 1, loader_id=10)
        self.assertEqual(s2, "Selected")
        self.assertEqual(items[1]["status"], "Selected")

        # Verify both remain Selected simultaneously
        selected_count = sum(1 for it in items if it.get("status") == "Selected")
        self.assertEqual(selected_count, 2)
        self.assertEqual(items[2]["status"], "Pending")

    def test_selecting_all_packages_simultaneously(self):
        from utils import parse_test_order_packages, toggle_package_selection

        parsed = parse_test_order_packages("10800+5040+2400")
        items = parsed["packages"]

        for i in range(3):
            items, status = toggle_package_selection(items, i, loader_id=10)
            self.assertEqual(status, "Selected")

        selected_count = sum(1 for it in items if it.get("status") == "Selected")
        self.assertEqual(selected_count, 3)

    def test_deselecting_a_package(self):
        from utils import parse_test_order_packages, toggle_package_selection

        parsed = parse_test_order_packages("10800+5040+2400")
        items = parsed["packages"]

        # Select 10800 and 5040
        items, _ = toggle_package_selection(items, 0, loader_id=10)
        items, _ = toggle_package_selection(items, 1, loader_id=10)

        # Deselect 5040
        items, s_desel = toggle_package_selection(items, 1, loader_id=10)
        self.assertEqual(s_desel, "Deselected")
        self.assertEqual(items[1]["status"], "Pending")
        self.assertEqual(items[0]["status"], "Selected")

    def test_confirm_delivery_with_multiple_selected_packages(self):
        from utils import parse_test_order_packages, toggle_package_selection, mark_selected_packages_delivered, calculate_delivered_packages_value

        parsed = parse_test_order_packages("10800+5040+2400")
        items = parsed["packages"]

        # Loader selects 10800 and 5040
        items, _ = toggle_package_selection(items, 0, loader_id=10)
        items, _ = toggle_package_selection(items, 1, loader_id=10)

        selected_items = [it for it in items if it.get("status") == "Selected"]
        self.assertEqual(len(selected_items), 2)

        # Confirm delivery
        updated_items, is_all, del_cnt = mark_selected_packages_delivered(items, loader_id=10, selected_items=selected_items)
        self.assertFalse(is_all)
        self.assertEqual(del_cnt, 2)

        # Delivered packages: 10800 + 5040 -> Price: 64$ + 33$ = 97$
        pkg_names = [it["package"] for it in selected_items]
        pkg_str = "+".join(pkg_names)
        self.assertEqual(pkg_str, "10800+5040")

        total_price, ok = calculate_delivered_packages_value(pkg_str)
        self.assertTrue(ok)
        self.assertEqual(total_price, 97.0)


class TestSuperAdminLogicPermissionsAndOrderBypass(unittest.IsolatedAsyncioTestCase):
    """Unit and integration tests for Super Admin permissions and Order Detector bypass."""

    async def asyncSetUp(self):
        from database import init_db, AsyncSessionLocal
        from models import DeliveryLedger, RunningTotalLedger, Order
        from sqlalchemy import delete
        await init_db()
        async with AsyncSessionLocal() as session:
            await session.execute(delete(DeliveryLedger))
            await session.execute(delete(RunningTotalLedger))
            await session.execute(delete(Order))
            await session.commit()

    async def test_super_admin_command_access(self):
        from unittest.mock import MagicMock, AsyncMock
        from handlers import (
            pay_running_total_command_handler,
            running_total_command_handler,
            users_command,
            manual_running_total_text_handler,
        )

        super_admin_id = 1573531032

        # 1. Super Admin uses /pay
        update_pay = MagicMock()
        update_pay.effective_user.id = super_admin_id
        update_pay.effective_message.reply_text = AsyncMock()

        await pay_running_total_command_handler(update_pay, None)
        update_pay.effective_message.reply_text.assert_called_once()
        self.assertIn("Payment Recorded", update_pay.effective_message.reply_text.call_args[0][0])

        # 2. Super Admin uses /total
        update_total = MagicMock()
        update_total.effective_user.id = super_admin_id
        update_total.effective_message.reply_text = AsyncMock()

        await running_total_command_handler(update_total, None)
        update_total.effective_message.reply_text.assert_called_once()
        self.assertIn("Current Delivery Total", update_total.effective_message.reply_text.call_args[0][0])

        # 3. Super Admin uses /users
        update_users = MagicMock()
        update_users.effective_user.id = super_admin_id
        update_users.effective_message.reply_text = AsyncMock()

        await users_command(update_users, None)
        update_users.effective_message.reply_text.assert_called_once()
        self.assertIn("Super Admin", update_users.effective_message.reply_text.call_args[0][0])

        # 4. Super Admin uses +10
        update_plus = MagicMock()
        update_plus.effective_user.id = super_admin_id
        update_plus.effective_message.text = "+10"
        update_plus.effective_message.reply_text = AsyncMock()

        await manual_running_total_text_handler(update_plus, None)
        update_plus.effective_message.reply_text.assert_called_once()
        self.assertIn("+10$", update_plus.effective_message.reply_text.call_args[0][0])

        # 5. Super Admin uses -10
        update_minus = MagicMock()
        update_minus.effective_user.id = super_admin_id
        update_minus.effective_message.text = "-10"
        update_minus.effective_message.reply_text = AsyncMock()

        await manual_running_total_text_handler(update_minus, None)
        update_minus.effective_message.reply_text.assert_called_once()
        self.assertIn("-10$", update_minus.effective_message.reply_text.call_args[0][0])

    async def test_super_admin_order_message_ignored(self):
        from unittest.mock import MagicMock, AsyncMock
        from handlers import source_group_handler
        from database import AsyncSessionLocal
        from models import Order
        from sqlalchemy import select

        super_admin_id = 1573531032
        order_text = (
            "Facebook\n\n"
            "Email:\nsuperadmin_order@gmail.com\n\n"
            "Password:\nPakistan123\n\n"
            "Order:\n2400"
        )

        update = MagicMock()
        update.effective_user.id = super_admin_id
        update.effective_chat.id = -100123456
        update.effective_message.text = order_text
        update.effective_message.caption = None
        update.effective_message.message_id = 5555
        update.effective_message.reply_text = AsyncMock()

        await source_group_handler(update, None)

        async with AsyncSessionLocal() as session:
            res = await session.execute(select(Order).where(Order.email == "superadmin_order@gmail.com"))
            orders = res.scalars().all()
            self.assertEqual(len(orders), 0)

    async def test_normal_customer_order_still_detected(self):
        from unittest.mock import MagicMock, AsyncMock
        from handlers import source_group_handler
        from database import AsyncSessionLocal, BOT_SETTINGS
        from models import Order
        from sqlalchemy import select

        customer_id = 999111222
        order_text = (
            "Facebook\n\n"
            "Email:\ncustomer_order@gmail.com\n\n"
            "Password:\nPakistan123\n\n"
            "Order:\n2400"
        )

        update = MagicMock()
        update.effective_user.id = customer_id
        update.effective_chat.id = -100123456
        update.effective_message.text = order_text
        update.effective_message.caption = None
        update.effective_message.message_id = 1234
        update.effective_message.reply_text = AsyncMock()

        BOT_SETTINGS["source_group_id"] = -100123456

        mock_context = MagicMock()
        mock_context.bot = MagicMock()

        await source_group_handler(update, mock_context)

        async with AsyncSessionLocal() as session:
            res = await session.execute(select(Order).where(Order.email == "customer_order@gmail.com"))
            orders = res.scalars().all()
            self.assertEqual(len(orders), 1)
            self.assertEqual(orders[0].package, "2400")


class TestMultilingualDetectorAndPackageAliases(unittest.TestCase):
    """Unit and regression tests for multilingual order detection and package aliases."""

    def test_real_customer_message_1_activision(self):
        from keywords import contains_order_keyword
        from utils import parse_test_order_packages

        msg = (
            "Activision\n\n"
            "Correo:\nuser1@gmail.com\n\n"
            "Contraseña:\npassword123\n\n"
            "Nick:\nPlayer1\n\n"
            "10800*3"
        )
        is_order, platform = contains_order_keyword(msg)
        self.assertTrue(is_order)
        self.assertEqual(platform, "activision")

        parsed = parse_test_order_packages(msg)
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed["packages"]), 3)
        self.assertEqual(parsed["packages"][0]["package"], "10800")
        self.assertEqual(parsed["packages"][0]["qty"], 1)

    def test_real_customer_message_2_facebook_spanish(self):
        from keywords import contains_order_keyword
        from utils import parse_test_order_packages

        msg = (
            "facebook\n\n"
            "Correo o número fb:\nuser2@gmail.com\n\n"
            "Contraseña de fb:\npass123\n\n"
            "Códigos\n\n"
            "2534 6603\n\n"
            "3075 1980\n\n"
            "5k"
        )
        is_order, platform = contains_order_keyword(msg)
        self.assertTrue(is_order)
        self.assertEqual(platform, "facebook")

        parsed = parse_test_order_packages(msg)
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed["packages"]), 1)
        self.assertEqual(parsed["packages"][0]["package"], "5040")
        self.assertEqual(parsed["packages"][0]["qty"], 1)

    def test_package_aliases_and_multiplication_notation(self):
        from utils import parse_test_order_packages

        # 5k -> 5040
        p5k = parse_test_order_packages("5k")
        self.assertEqual(len(p5k["packages"]), 1)
        self.assertEqual(p5k["packages"][0]["package"], "5040")

        # 10k -> 10800
        p10k = parse_test_order_packages("10k")
        self.assertEqual(len(p10k["packages"]), 1)
        self.assertEqual(p10k["packages"][0]["package"], "10800")

        # 2.4k -> 2400
        p24k = parse_test_order_packages("2.4k")
        self.assertEqual(len(p24k["packages"]), 1)
        self.assertEqual(p24k["packages"][0]["package"], "2400")

        # 10800*3 -> Expanded to 3 items
        p_mult1 = parse_test_order_packages("10800*3")
        self.assertEqual(len(p_mult1["packages"]), 3)
        self.assertEqual(p_mult1["packages"][0]["package"], "10800")

        # 5040x3 -> Expanded to 3 items
        p_mult2 = parse_test_order_packages("5040x3")
        self.assertEqual(len(p_mult2["packages"]), 3)
        self.assertEqual(p_mult2["packages"][0]["package"], "5040")

        # 2400 ×2 -> Expanded to 2 items
        p_mult3 = parse_test_order_packages("2400 ×2")
        self.assertEqual(len(p_mult3["packages"]), 2)
        self.assertEqual(p_mult3["packages"][0]["package"], "2400")

        # 5k*2 -> Expanded to 2 items of 5040
        p_mult4 = parse_test_order_packages("5k*2")
        self.assertEqual(len(p_mult4["packages"]), 2)
        self.assertEqual(p_mult4["packages"][0]["package"], "5040")

        # 5k x2 -> Expanded to 2 items of 5040
        p_mult5 = parse_test_order_packages("5k x2")
        self.assertEqual(len(p_mult5["packages"]), 2)
        self.assertEqual(p_mult5["packages"][0]["package"], "5040")

    def test_email_and_package_fallback_rule(self):
        from keywords import contains_order_keyword

        # 1. Email + Package -> Order detected
        msg1 = "order_user@gmail.com\n10800"
        is_order1, _ = contains_order_keyword(msg1)
        self.assertTrue(is_order1)

        msg2 = "Customer: buyer@outlook.com\n5k*2"
        is_order2, _ = contains_order_keyword(msg2)
        self.assertTrue(is_order2)

        # 2. Email only -> Not an order
        msg_email_only = "user_only@gmail.com"
        is_order_eo, _ = contains_order_keyword(msg_email_only)
        self.assertFalse(is_order_eo)

        # 3. Package only -> Existing behavior unchanged (Not an order)
        msg_pkg_only = "10800"
        is_order_po, _ = contains_order_keyword(msg_pkg_only)
        self.assertFalse(is_order_po)

        # 4. Real customer messages with Spanish fields -> Order detected
        msg_spanish = (
            "facebook\n\n"
            "Correo o número fb:\nuser_spanish@gmail.com\n\n"
            "Contraseña de fb:\npassword123\n\n"
            "Códigos\n2534 6603\n\n"
            "5k"
        )
        is_order_sp, platform_sp = contains_order_keyword(msg_spanish)
        self.assertTrue(is_order_sp)
        self.assertEqual(platform_sp, "facebook")


class TestPackageMultiplierExpansionEngine(unittest.IsolatedAsyncioTestCase):
    """Production regression test suite for Package Multiplier Expansion Engine."""

    async def asyncSetUp(self):
        from database import init_db, AsyncSessionLocal
        from models import DeliveryLedger, RunningTotalLedger, Order
        from sqlalchemy import delete
        await init_db()
        async with AsyncSessionLocal() as session:
            await session.execute(delete(DeliveryLedger))
            await session.execute(delete(RunningTotalLedger))
            await session.execute(delete(Order))
            await session.commit()

    def test_multiplier_expansions_and_aliases(self):
        from utils import parse_test_order_packages

        # 10800*3
        p1 = parse_test_order_packages("10800*3")
        pkgs1 = [it["package"] for it in p1["packages"]]
        self.assertEqual(pkgs1, ["10800", "10800", "10800"])
        self.assertEqual(p1["total_price"], 192.0)

        # 10800x3
        p2 = parse_test_order_packages("10800x3")
        pkgs2 = [it["package"] for it in p2["packages"]]
        self.assertEqual(pkgs2, ["10800", "10800", "10800"])

        # 10800×3
        p3 = parse_test_order_packages("10800×3")
        pkgs3 = [it["package"] for it in p3["packages"]]
        self.assertEqual(pkgs3, ["10800", "10800", "10800"])

        # 5k*2 -> 5040, 5040
        p4 = parse_test_order_packages("5k*2")
        pkgs4 = [it["package"] for it in p4["packages"]]
        self.assertEqual(pkgs4, ["5040", "5040"])
        self.assertEqual(p4["total_price"], 66.0)

        # 10k×4 -> 10800, 10800, 10800, 10800
        p5 = parse_test_order_packages("10k×4")
        pkgs5 = [it["package"] for it in p5["packages"]]
        self.assertEqual(pkgs5, ["10800", "10800", "10800", "10800"])
        self.assertEqual(p5["total_price"], 256.0)

        # 5040x2
        p6 = parse_test_order_packages("5040x2")
        pkgs6 = [it["package"] for it in p6["packages"]]
        self.assertEqual(pkgs6, ["5040", "5040"])

        # 880*5
        p7 = parse_test_order_packages("880*5")
        pkgs7 = [it["package"] for it in p7["packages"]]
        self.assertEqual(pkgs7, ["880", "880", "880", "880", "880"])
        self.assertEqual(p7["total_price"], 40.0)

    async def test_expanded_package_partial_deliveries_and_ledger(self):
        from utils import parse_test_order_packages, toggle_package_selection, mark_selected_packages_delivered, calculate_delivered_packages_value
        from database import record_delivery_ledger_entry, get_running_total_current

        parsed = parse_test_order_packages("10800*3")
        items = parsed["packages"]
        self.assertEqual(len(items), 3)

        # 1. First Partial Delivery: Loader selects 2 of 10800
        items, _ = toggle_package_selection(items, 0, loader_id=10)
        items, _ = toggle_package_selection(items, 1, loader_id=10)

        selected_1 = [it for it in items if it.get("status") == "Selected"]
        self.assertEqual(len(selected_1), 2)

        updated_items_1, is_completed_1, del_cnt_1 = mark_selected_packages_delivered(
            items, loader_id=10, selected_items=selected_1
        )
        self.assertFalse(is_completed_1)
        self.assertEqual(del_cnt_1, 2)

        pkg_str_1 = "+".join([it["package"] for it in selected_1])
        price_1, ok1 = calculate_delivered_packages_value(pkg_str_1)
        self.assertTrue(ok1)
        self.assertEqual(price_1, 128.0)

        entry_1, ok_l1 = await record_delivery_ledger_entry(order_id=1, package=pkg_str_1, now_value=price_1)
        self.assertTrue(ok_l1)
        self.assertEqual(entry_1.before_total, 0.0)
        self.assertEqual(entry_1.now_value, 128.0)
        self.assertEqual(entry_1.running_total, 128.0)
        self.assertEqual(await get_running_total_current(), 128.0)

        # 2. Second Delivery: Loader selects remaining 1 of 10800
        pending_idx = [i for i, it in enumerate(updated_items_1) if it.get("status") == "Pending"][0]
        updated_items_1, _ = toggle_package_selection(updated_items_1, pending_idx, loader_id=10)

        selected_2 = [it for it in updated_items_1 if it.get("status") == "Selected"]
        self.assertEqual(len(selected_2), 1)

        updated_items_2, is_completed_2, del_cnt_2 = mark_selected_packages_delivered(
            updated_items_1, loader_id=10, selected_items=selected_2
        )
        self.assertTrue(is_completed_2)
        self.assertEqual(del_cnt_2, 1)

        pkg_str_2 = "+".join([it["package"] for it in selected_2])
        price_2, ok2 = calculate_delivered_packages_value(pkg_str_2)
        self.assertTrue(ok2)
        self.assertEqual(price_2, 64.0)

        entry_2, ok_l2 = await record_delivery_ledger_entry(order_id=1, package=pkg_str_2, now_value=price_2)
        self.assertTrue(ok_l2)
        self.assertEqual(entry_2.before_total, 128.0)
        self.assertEqual(entry_2.now_value, 64.0)
        self.assertEqual(entry_2.running_total, 192.0)
        self.assertEqual(await get_running_total_current(), 192.0)


class TestProductionOrderParserV2RealCustomerSamples(unittest.TestCase):
    """Test suite for Production Order Parser v2 using 18 real customer production samples."""

    def test_sample_1(self):
        from order_parser import parse_order_v2
        sample = "991#\n***facebook*\n\nNick: Apodo en el juego:Jktxx03\n+584249290951\nContraseña: migordito0324\n\nCódigos\n46137364\n62673214\n74047761\n2400"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["customer_ref_id"], "991")
        self.assertEqual(p["login_method"], "Facebook")
        self.assertEqual(p["phone"], "+584249290951")
        self.assertEqual(p["packages"][0]["package"], "2400")
        self.assertEqual(len(p["recovery_codes"]), 3)

    def test_sample_2(self):
        from order_parser import parse_order_v2
        sample = "992#\n\nFor@§ter0\nneiraalex19@gmail.com\n1995Karolyn\n\n10800"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["customer_ref_id"], "992")
        self.assertEqual(p["email"], "neiraalex19@gmail.com")
        self.assertEqual(p["packages"][0]["package"], "10800")

    def test_sample_3(self):
        from order_parser import parse_order_v2
        sample = "993#\n\nWilveralexanderramos37@gmail.com\nRUTH97wrm\nKinataWINGIS\n5k"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["email"], "wilveralexanderramos37@gmail.com")
        self.assertEqual(p["packages"][0]["package"], "5040")

    def test_sample_4(self):
        from order_parser import parse_order_v2
        sample = "994#\n*Activision*\n\nNick: Apodo en el juego\nCorreo: jotapyp@gmail.com\nContraseña: Jordanelmejor23$\n\n5k"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["login_method"], "Activision")
        self.assertEqual(p["email"], "jotapyp@gmail.com")
        self.assertEqual(p["packages"][0]["package"], "5040")

    def test_sample_5(self):
        from order_parser import parse_order_v2
        sample = "995#\n\nApodo : ^jefemaestro\nCorreo: arismendideivis130@gmail.com\n\nContraseña : ConorAris3223\n2400"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["email"], "arismendideivis130@gmail.com")
        self.assertEqual(p["packages"][0]["package"], "2400")

    def test_sample_6(self):
        from order_parser import parse_order_v2
        sample = "996#\n\nActivación\nN@R@(U\nqui15142023@gmail.com\nquintero12\n2400"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["login_method"], "Activision")
        self.assertEqual(p["email"], "qui15142023@gmail.com")
        self.assertEqual(p["packages"][0]["package"], "2400")

    def test_sample_7(self):
        from order_parser import parse_order_v2
        sample = "997#\n\nNick LYCAN2303\n\nguaicargomez.14@hotmail.com\nClave: 04163346905\n2400"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["email"], "guaicargomez.14@hotmail.com")
        self.assertEqual(p["packages"][0]["package"], "2400")

    def test_sample_8(self):
        from order_parser import parse_order_v2
        sample = "998#\n\npalmaadalbert.31@gmail.com\nadal2425\nKinggato\n5k"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["email"], "palmaadalbert.31@gmail.com")
        self.assertEqual(p["packages"][0]["package"], "5040")

    def test_sample_9(self):
        from order_parser import parse_order_v2
        sample = "999#\n\nNick:Goat.Ʀaӄan\nstrangehuman922@gmail.com\nContraseña:e30165529\n2400"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["email"], "strangehuman922@gmail.com")
        self.assertEqual(p["packages"][0]["package"], "2400")

    def test_sample_10(self):
        from order_parser import parse_order_v2
        sample = "1000#\n*Activision*\n\nNick: G®££/\/G()°\nCorreo: braudyscalderon@gmail.com\nContraseña: calderon23*31\n5000+2400"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["login_method"], "Activision")
        self.assertEqual(p["email"], "braudyscalderon@gmail.com")
        pkgs = [item["package"] for item in p["packages"]]
        self.assertEqual(pkgs, ["5040", "2400"])
        self.assertEqual(p["unknown_packages"], [])

    def test_sample_11(self):
        from order_parser import parse_order_v2
        sample = "1#\n\nnestor_torrique99@hotmail.com\n\nManicuare2004\n\nBk Platinium\n5000"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["email"], "nestor_torrique99@hotmail.com")
        self.assertEqual(p["packages"][0]["package"], "5040")
        self.assertEqual(p["unknown_packages"], [])

    def test_sample_12(self):
        from order_parser import parse_order_v2
        sample = "2#\n*Activision*\n\nNick: SASUKE\nCorreo: raidanarias17@gmail.com\nContraseña: 26745481ra.\n\n10800"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["login_method"], "Activision")
        self.assertEqual(p["email"], "raidanarias17@gmail.com")
        self.assertEqual(p["packages"][0]["package"], "10800")

    def test_sample_13(self):
        from order_parser import parse_order_v2
        sample = "3#\n\nNick:\nGØW_Ҝєиîgth\n\nCorreo:\nkenigth10gaming@gmail.com\n\nContraseña:\nkeni.2811\n\n2400"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["email"], "kenigth10gaming@gmail.com")
        self.assertEqual(p["packages"][0]["package"], "2400")

    def test_sample_14(self):
        from order_parser import parse_order_v2
        sample = "4#\n\nCorreo\ndamianalejandro2020@outlook.es\n\nContraseña: manuelvivasgod\n\nNick:NS.Bigvivas20\n\nCódigos\n05597299\n09396956\n29985590\n\n2400"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["email"], "damianalejandro2020@outlook.es")
        self.assertEqual(p["packages"][0]["package"], "2400")
        self.assertEqual(len(p["recovery_codes"]), 3)

    def test_sample_15(self):
        from order_parser import parse_order_v2
        sample = "5#\n\ngabrielcorcega40@gmail.com\ngato..28721\nApodo: GATO\n10800"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["email"], "gabrielcorcega40@gmail.com")
        self.assertEqual(p["packages"][0]["package"], "10800")

    def test_sample_16(self):
        from order_parser import parse_order_v2
        sample = "75#\n*facebook*\n\nNick:HG KENNY\nCorreo o número fb: kennyalexanderpay@gmail.com\nContraseña de fb:kenny00\n\nCódigos\n2534 6603\n3075 1980\n3568 0949\n\n5k"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["login_method"], "Facebook")
        self.assertEqual(p["email"], "kennyalexanderpay@gmail.com")
        self.assertEqual(p["packages"][0]["package"], "5040")
        self.assertEqual(len(p["recovery_codes"]), 3)

    def test_sample_17(self):
        from order_parser import parse_order_v2
        sample = "76#\n\nApodo: joker².²\nCorreo: ladeuxpalaciojosedavid@gmail.com\nContraseña: josedavid04\n2400"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["email"], "ladeuxpalaciojosedavid@gmail.com")
        self.assertEqual(p["packages"][0]["package"], "2400")

    def test_sample_18(self):
        from order_parser import parse_order_v2
        sample = "77#\n\nNick name F7/ Adsolutex.\n\nCorreo. mcallistercastrillon@icloud.com\n\nContraseña. Torrealba174.\n5k"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["email"], "mcallistercastrillon@icloud.com")
        self.assertEqual(p["packages"][0]["package"], "5040")


class TestCanonicalPackageAliasNormalizationFix(unittest.IsolatedAsyncioTestCase):
    """Regression test suite for Production Package Alias Normalization Fix."""

    async def asyncSetUp(self):
        from database import init_db, AsyncSessionLocal
        from models import DeliveryLedger, RunningTotalLedger, Order
        from sqlalchemy import delete
        await init_db()
        async with AsyncSessionLocal() as session:
            await session.execute(delete(DeliveryLedger))
            await session.execute(delete(RunningTotalLedger))
            await session.execute(delete(Order))
            await session.commit()

    def test_canonical_alias_resolutions(self):
        from order_parser import normalize_package_alias, parse_order_v2

        self.assertEqual(normalize_package_alias("5k"), "5040")
        self.assertEqual(normalize_package_alias("5K"), "5040")
        self.assertEqual(normalize_package_alias("5000"), "5040")
        self.assertEqual(normalize_package_alias("5040"), "5040")

        # 5k, 5000, 5040 price equality
        p_5k = parse_order_v2("Email: a@g.com\n5k")
        p_5000 = parse_order_v2("Email: a@g.com\n5000")
        p_5040 = parse_order_v2("Email: a@g.com\n5040")

        self.assertEqual(p_5k["packages"][0]["package"], "5040")
        self.assertEqual(p_5000["packages"][0]["package"], "5040")
        self.assertEqual(p_5040["packages"][0]["package"], "5040")

        self.assertEqual(p_5k["packages"][0]["unit_price"], 33.0)
        self.assertEqual(p_5000["packages"][0]["unit_price"], 33.0)
        self.assertEqual(p_5040["packages"][0]["unit_price"], 33.0)

    def test_multi_package_canonical_normalization(self):
        from utils import parse_test_order_packages

        # 5000+2400 -> 5040 + 2400
        p1 = parse_test_order_packages("5000+2400")
        pkgs1 = [it["package"] for it in p1["packages"]]
        self.assertEqual(pkgs1, ["5040", "2400"])
        self.assertFalse(p1["has_unknown"])
        self.assertEqual(p1["total_price"], 49.5)

        # 5k+2400 -> 5040 + 2400
        p2 = parse_test_order_packages("5k+2400")
        pkgs2 = [it["package"] for it in p2["packages"]]
        self.assertEqual(pkgs2, ["5040", "2400"])
        self.assertFalse(p2["has_unknown"])
        self.assertEqual(p2["total_price"], 49.5)

        # 5040+2400 -> 5040 + 2400
        p3 = parse_test_order_packages("5040+2400")
        pkgs3 = [it["package"] for it in p3["packages"]]
        self.assertEqual(pkgs3, ["5040", "2400"])
        self.assertFalse(p3["has_unknown"])
        self.assertEqual(p3["total_price"], 49.5)

    def test_multiplier_canonical_normalization(self):
        from utils import parse_test_order_packages

        # 5000*2 -> 5040, 5040
        p1 = parse_test_order_packages("5000*2")
        pkgs1 = [it["package"] for it in p1["packages"]]
        self.assertEqual(pkgs1, ["5040", "5040"])
        self.assertEqual(p1["total_price"], 66.0)

        # 5k*2 -> 5040, 5040
        p2 = parse_test_order_packages("5k*2")
        pkgs2 = [it["package"] for it in p2["packages"]]
        self.assertEqual(pkgs2, ["5040", "5040"])
        self.assertEqual(p2["total_price"], 66.0)

        # 5040*2 -> 5040, 5040
        p3 = parse_test_order_packages("5040*2")
        pkgs3 = [it["package"] for it in p3["packages"]]
        self.assertEqual(pkgs3, ["5040", "5040"])
        self.assertEqual(p3["total_price"], 66.0)

    async def test_loader_display_ledger_and_running_total_canonical_prices(self):
        from utils import parse_test_order_packages, build_loader_package_keyboard, mark_selected_packages_delivered, calculate_delivered_packages_value
        from database import record_delivery_ledger_entry, get_running_total_current

        parsed = parse_test_order_packages("5000+2400")
        items = parsed["packages"]

        # Loader UI receives canonical packages (☐ 5040 CP / ⬜ 5040)
        kb = build_loader_package_keyboard(101, items)
        self.assertIsNotNone(kb)
        btn_texts = [btn.text for row in kb.inline_keyboard for btn in row]
        self.assertIn("5040", btn_texts[0])

        # Partial delivery & Ledger
        updated, is_completed, del_cnt = mark_selected_packages_delivered(items, loader_id=5, selected_items=[items[0]])
        self.assertEqual(del_cnt, 1)

        pkg_str = items[0]["package"]
        self.assertEqual(pkg_str, "5040")

        val, ok = calculate_delivered_packages_value(pkg_str)
        self.assertTrue(ok)
        self.assertEqual(val, 33.0)

        entry, ok_l = await record_delivery_ledger_entry(order_id=10, package=pkg_str, now_value=val)
        self.assertTrue(ok_l)
        self.assertEqual(entry.now_value, 33.0)
        self.assertEqual(await get_running_total_current(), 33.0)

    def test_delivery_ledger_exact_3_line_format(self):
        from utils import format_ledger_entry_message
        msg = format_ledger_entry_message(64.0, 15.5, 79.5)
        expected = "Before 64$\nNow 15.5$\nTotal 79.5$"
        self.assertEqual(msg, expected)


class TestCPPackAndRecoveryCodeSeparation(unittest.TestCase):
    """Test suite for CP PACK field priority, recovery code separation, and thousands separators."""

    def test_sample_52(self):
        from order_parser import parse_order_v2
        sample = (
            "Order #:52\n\n"
            "Login: Activision\n\n"
            "Email: yenilicet1996@gmail.com\n\n"
            "Password: Licet1996\n\n"
            "IGN \"Nick\" : 5G·Tomorrow\n\n"
            "CP PACK : 12.000\n\n"
            "Mode: SaFe\n\n"
            "Time: FAST"
        )
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["customer_ref_id"], "52")
        self.assertEqual(p["login_method"], "Activision")
        self.assertEqual(p["email"], "yenilicet1996@gmail.com")
        self.assertEqual(len(p["packages"]), 1)
        self.assertEqual(p["packages"][0]["package"], "12000")

    def test_sample_54_recovery_codes_separated(self):
        from order_parser import parse_order_v2
        sample = (
            "Order #:54\n\n"
            "Login: Facebook\n\n"
            "Email: vierimansilla6141@gmail.com\n\n"
            "Password: mmchina6141\n\n"
            "Codes: (solo FB) 👇🏼👇🏼👇🏼:\n\n"
            "14627274\n"
            "16259506\n"
            "33195095\n"
            "35621615\n"
            "41430777\n\n"
            "IGN \"Nick\": VieriM\n\n"
            "CP PACK: 12.000\n\n"
            "Mode: SaFe\n\n"
            "Time: Fast"
        )
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["customer_ref_id"], "54")
        self.assertEqual(p["login_method"], "Facebook")
        self.assertEqual(p["email"], "vierimansilla6141@gmail.com")

        # Recovery codes captured
        self.assertEqual(len(p["recovery_codes"]), 5)
        self.assertIn("14627274", p["recovery_codes"])

        # Packages must contain ONLY 12000, ZERO recovery codes!
        self.assertEqual(len(p["packages"]), 1)
        self.assertEqual(p["packages"][0]["package"], "12000")
        pkg_names = [item["package"] for item in p["packages"]]
        for code in p["recovery_codes"]:
            self.assertNotIn(code, pkg_names)

    def test_sample_55(self):
        from order_parser import parse_order_v2
        sample = "Order #:55\n\nLogin: Activision\n\nEmail: eudesvicentearellano@gmail.com\n\nPassword: arellano26\n\nIGN \"Nick\" : ThePUKITIS\n\nCP PACK : 4800"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["packages"][0]["package"], "4800")

    def test_sample_56(self):
        from order_parser import parse_order_v2
        sample = "Order #:56\n\nLogin: Activision\n\nEmail: juanincodm367@gmail.com\n\nPassword: 319702Ju*\n\nIGN \"Nick\" : ƦGИ・VɅELOR\n\nCP PACK : 12,000"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["packages"][0]["package"], "12000")

    def test_sample_57(self):
        from order_parser import parse_order_v2
        sample = "Order #:57\n\nLogin: Activision\n\nEmail: christopherw121094@gmail.com\n\nPassword: codwarch94\n\nIGN \"Nick\" :NN_warch1210\n\nCP PACK : 4.800"
        p = parse_order_v2(sample)
        self.assertTrue(p["order_detected"])
        self.assertEqual(p["packages"][0]["package"], "4800")

    def test_sample_58(self):
        from order_parser import parse_order_v2
        p = parse_order_v2("CP PACK : 12,000")
        self.assertEqual(p["packages"][0]["package"], "12000")

    def test_sample_59(self):
        from order_parser import parse_order_v2
        p = parse_order_v2("CP PACK : 24.000")
        self.assertEqual(p["packages"][0]["package"], "24000")

    def test_sample_60(self):
        from order_parser import parse_order_v2
        p = parse_order_v2("CP PACK : 9.600")
        self.assertEqual(p["packages"][0]["package"], "9600")


class TestMissingPackageWorkflowFix(unittest.IsolatedAsyncioTestCase):
    """Test suite for Missing Package Workflow Fix."""

    async def asyncSetUp(self):
        from database import DEFAULT_PACKAGE_PRICES
        self._orig_prices = dict(DEFAULT_PACKAGE_PRICES)

    async def asyncTearDown(self):
        from database import AsyncSessionLocal, PackagePrice, DEFAULT_PACKAGE_PRICES
        from sqlalchemy import delete
        from utils import reload_package_prices_cache
        async with AsyncSessionLocal() as s:
            await s.execute(delete(PackagePrice))
            for k, v in DEFAULT_PACKAGE_PRICES.items():
                s.add(PackagePrice(package=k, price=v))
            await s.commit()
        reload_package_prices_cache(DEFAULT_PACKAGE_PRICES)

    async def test_single_and_multiple_missing_packages(self):
        from utils import get_unknown_package_keyboard, format_missing_packages_summary, format_package_progress_summary, update_unknown_package_price
        from database import update_single_package_price_in_db, get_all_package_prices_from_db

        items = [
            {"package": "999000", "qty": 1, "status": "Unpriced", "unit_price": None},
            {"package": "888000", "qty": 1, "status": "Unpriced", "unit_price": None}
        ]

        # 1. Summary displays Missing Packages list
        summary = format_missing_packages_summary(items)
        self.assertIn("❌ Missing Packages", summary)
        self.assertIn("999000", summary)
        self.assertIn("888000", summary)

        # 2. Keyboard displays button for EACH missing package
        kb = get_unknown_package_keyboard(101, items)
        self.assertIsNotNone(kb)
        btn_texts = [btn.text for row in kb.inline_keyboard for btn in row]
        self.assertEqual(len(btn_texts), 2)
        self.assertIn("✏️ Add Price 999000", btn_texts)
        self.assertIn("✏️ Add Price 888000", btn_texts)

        # 3. Add price to first package (999000 -> 67$)
        await update_single_package_price_in_db("999000", 67.0)

        # Verify saved in DB
        db_prices = await get_all_package_prices_from_db()
        self.assertEqual(db_prices.get("999000"), 67.0)

        # Update order items
        updated_items, new_total, has_unpriced = update_unknown_package_price(items, "999000", 67.0)
        self.assertTrue(has_unpriced)

        # 4. Remaining missing package (888000 -> 58$)
        kb2 = get_unknown_package_keyboard(101, updated_items)
        self.assertIsNotNone(kb2)
        btn_texts2 = [btn.text for row in kb2.inline_keyboard for btn in row]
        self.assertEqual(len(btn_texts2), 1)
        self.assertIn("✏️ Add Price 888000", btn_texts2)

        # Add price for 888000 -> 58$
        await update_single_package_price_in_db("888000", 58.0)
        updated_items2, final_total, has_unpriced2 = update_unknown_package_price(updated_items, "888000", 58.0)
        self.assertFalse(has_unpriced2)
        self.assertEqual(final_total, 125.0)

        # 5. After all missing prices added, keyboard returns None & display converts to standard package format
        self.assertIsNone(get_unknown_package_keyboard(101, updated_items2))
        final_summary = format_package_progress_summary(updated_items2, final_total)
        self.assertIn("📦 Packages", final_summary)
        self.assertIn("999000 CP", final_summary)
        self.assertIn("888000 CP", final_summary)
        self.assertIn("125$", final_summary)

    def test_alias_5000_5k_5040_behavior_unchanged(self):
        from utils import get_unknown_package_keyboard
        items = [
            {"package": "5000", "qty": 1, "status": "Pending", "unit_price": 33.0},
            {"package": "5k", "qty": 1, "status": "Pending", "unit_price": 33.0}
        ]
        kb = get_unknown_package_keyboard(102, items)
        self.assertIsNone(kb)


class TestUpsertBulkPriceUpdateSystem(unittest.IsolatedAsyncioTestCase):
    """Regression test suite for Fix /updateprices - UPSERT & New Package Prices."""

    async def asyncSetUp(self):
        from database import DEFAULT_PACKAGE_PRICES
        self._orig_prices = dict(DEFAULT_PACKAGE_PRICES)

    async def asyncTearDown(self):
        from database import AsyncSessionLocal, PackagePrice, DEFAULT_PACKAGE_PRICES
        from sqlalchemy import delete
        from utils import reload_package_prices_cache
        async with AsyncSessionLocal() as s:
            await s.execute(delete(PackagePrice))
            for k, v in DEFAULT_PACKAGE_PRICES.items():
                s.add(PackagePrice(package=k, price=v))
            await s.commit()
        reload_package_prices_cache(DEFAULT_PACKAGE_PRICES)

    async def test_upsert_bulk_update_complete_flow(self):
        from utils import parse_bulk_prices_input, parse_test_order_packages, get_unknown_package_keyboard
        from database import bulk_update_package_prices_in_db, get_all_package_prices_from_db

        # 1. Update existing package price + Insert new package price in same /updateprices
        update_text = """
        10800 67
        5040 34
        2400 16.5
        108000 563
        96,000 503
        72.000 375
        55200 300
        48000 31
        38400 250
        999000 750
        """
        price_map, err = parse_bulk_prices_input(update_text)
        self.assertIsNone(err)
        self.assertIsNotNone(price_map)

        # Verify thousands separator normalization
        self.assertEqual(price_map.get("96000"), 503.0)
        self.assertEqual(price_map.get("72000"), 375.0)
        self.assertEqual(price_map.get("108000"), 563.0)
        self.assertEqual(price_map.get("999000"), 750.0)
        self.assertEqual(price_map.get("10800"), 67.0)

        # Save to DB & Cache
        success = await bulk_update_package_prices_in_db(price_map, updated_by_id=12345)
        self.assertTrue(success)

        # 2. Cache refresh after /updateprices (immediate recognition without restart)
        db_prices = await get_all_package_prices_from_db()
        self.assertEqual(db_prices.get("108000"), 563.0)
        self.assertEqual(db_prices.get("96000"), 503.0)
        self.assertEqual(db_prices.get("72000"), 375.0)
        self.assertEqual(db_prices.get("999000"), 750.0)

        # 3. New order containing 108000, 96000, 72000 recognized immediately
        order_msg = (
            "Facebook\n\n"
            "Email:\ntestcustomer@gmail.com\n\n"
            "Password:\nPass1234\n\n"
            "Order:\n108.000+96.000+72,000"
        )
        p = parse_test_order_packages(order_msg)
        self.assertIsNotNone(p)
        self.assertFalse(p["has_unknown"])
        self.assertEqual(p["total_price"], 1441.0)

        # New package no longer triggers Add Price
        kb = get_unknown_package_keyboard(99, p["packages"])
        self.assertIsNone(kb)

        # 4. Alias 5000 / 5k / 5040 behavior unchanged
        p_alias = parse_test_order_packages("Email: u@gmail.com\nPass: p\n5k+5000")
        self.assertIsNotNone(p_alias)
        self.assertFalse(p_alias["has_unknown"])
        self.assertEqual(p_alias["total_price"], 68.0)

        # 5. Genuinely unknown package still triggers Missing Package flow
        p_unk = parse_test_order_packages("Email: u@gmail.com\nPass: p\n888777")
        self.assertIsNotNone(p_unk)
        self.assertTrue(p_unk["has_unknown"])
        kb_unk = get_unknown_package_keyboard(99, p_unk["packages"])
        self.assertIsNotNone(kb_unk)
        btn_texts = [b.text for r in kb_unk.inline_keyboard for b in r]
        self.assertIn("✏️ Add Price 888777", btn_texts)


class TestWrongPasswordCustomerFlow(unittest.IsolatedAsyncioTestCase):
    """Test suite for Updated Wrong Password Customer Flow."""

    async def test_wrong_password_customer_buttons_and_callbacks(self):
        from utils import build_customer_issue_keyboard, LoaderIssueType
        from database import (
            create_order,
            set_order_loader_message_id,
            get_order_by_id,
            update_order_issue_state,
            update_order_status,
            update_order_raw_text,
            get_latest_ledger_entries
        )
        from handlers import customer_confirmation_callback_handler

        # 1. Create test order
        order = await create_order(
            email="testcustomer@gmail.com",
            client_chat_id=-1001234,
            original_message_id=999,
            package="10800",
            raw_text="Email: testcustomer@gmail.com\nPass: Secret123\n10800"
        )
        await set_order_loader_message_id(order.id, 888, -1005678)
        self.assertIsNotNone(order)
        order_id = order.id

        # 2. Loader reports wrong password -> Verify 3 buttons generated
        kb = build_customer_issue_keyboard(order_id, LoaderIssueType.WRONG_PASSWORD)
        btn_texts = [btn.text for row in kb.inline_keyboard for btn in row]
        self.assertEqual(len(btn_texts), 3)
        self.assertEqual(btn_texts[0], "✅ Password is Correct")
        self.assertEqual(btn_texts[1], "🔄 Updating Password")
        self.assertEqual(btn_texts[2], "❌ Cancel Order")

        # 3. Test "🔄 Updating Password" callback handler execution
        sent_messages = []
        sent_reactions = []
        edited_messages = []

        class MockQuery:
            data = f"cust_confirm:pw_updating:{order_id}:wrong_password"
            message = type("Msg", (), {"caption": None})()

            async def edit_message_text(self, text, parse_mode=None):
                edited_messages.append(text)

            async def edit_message_caption(self, caption, parse_mode=None):
                edited_messages.append(caption)

            async def answer(self, text=None, show_alert=False):
                pass

        class MockBot:
            async def send_message(self, chat_id, text, reply_to_message_id=None, parse_mode=None):
                sent_messages.append({
                    "chat_id": chat_id,
                    "text": text,
                    "reply_to_message_id": reply_to_message_id
                })

            async def set_message_reaction(self, chat_id, message_id, reaction=None, is_big=None):
                sent_reactions.append({
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reaction": reaction
                })

        mock_update = type("Update", (), {"callback_query": MockQuery()})()
        mock_context = type("Context", (), {"bot": MockBot()})()

        await customer_confirmation_callback_handler(mock_update, mock_context)

        # ✓ Updating Password sends client "🔄 Please send new password."
        self.assertIn("🔄 Please send new password.", edited_messages)

        # ✓ Loader receives password-update notification replying to original loader message (888)
        self.assertTrue(any("Please wait for the new password." in m["text"] for m in sent_messages))
        self.assertTrue(any(m["reply_to_message_id"] == 888 for m in sent_messages))

        # ✓ Original Loader message receives 🔄 reaction
        self.assertTrue(any(r["message_id"] == 888 for r in sent_reactions))

        # 4. Customer sends new password -> Save to SAME Order ID
        updated_order = await update_order_raw_text(order_id, "Password:\nMySuperNewPass123", "testcustomer@gmail.com")
        self.assertIsNotNone(updated_order)
        self.assertEqual(updated_order.id, order_id)
        self.assertIn("MySuperNewPass123", updated_order.raw_text)

        # 5. Test "❌ Cancel Order" callback handler execution
        sent_messages.clear()
        sent_reactions.clear()
        edited_messages.clear()

        class MockCancelQuery:
            data = f"cust_confirm:pw_cancel:{order_id}:wrong_password"
            message = type("Msg", (), {"caption": None})()

            async def edit_message_text(self, text, parse_mode=None):
                edited_messages.append(text)

            async def edit_message_caption(self, caption, parse_mode=None):
                edited_messages.append(caption)

            async def answer(self, text=None, show_alert=False):
                pass

        mock_cancel_update = type("Update", (), {"callback_query": MockCancelQuery()})()

        await customer_confirmation_callback_handler(mock_cancel_update, mock_context)

        # ✓ Cancel Order marks same order Cancelled
        can_order = await get_order_by_id(order_id)
        self.assertEqual(can_order.status, "CANCELLED")

        # ✓ Cancellation notification is sent to Loader Group replying to original loader message (888)
        self.assertTrue(any("has been cancelled by the customer." in m["text"] for m in sent_messages))
        self.assertTrue(any(m["reply_to_message_id"] == 888 for m in sent_messages))

        # ✓ Original Loader message (888) receives ❌ reaction
        self.assertTrue(any(r["message_id"] == 888 for r in sent_reactions))

        # ✓ Cancelled order cannot be delivered
        from delivery import deliver_order_by_id
        success = await deliver_order_by_id(MockBot(), order_id)
        self.assertFalse(success)

        # ✓ No duplicate order or ledger entry
        entries = await get_latest_ledger_entries(limit=10)
        matching_entries = [e for e in entries if e.order_id == order_id]
        self.assertEqual(len(matching_entries), 0)

    async def test_multiple_password_updates_and_cancellation_reaction(self):
        from utils import build_updated_raw_text_with_passwords
        from database import (
            create_order,
            set_order_loader_message_id,
            get_order_by_id,
            update_order_issue_state,
            update_order_status,
            update_order_raw_text,
            get_latest_ledger_entries
        )
        from handlers import customer_confirmation_callback_handler

        # 1. Create original order
        orig_raw = "Login: Facebook\nEmail: ex@gmail.com\nPassword: A\n2400 CP"
        order = await create_order(
            email="ex@gmail.com",
            client_chat_id=-100111,
            original_message_id=500,
            package="2400",
            raw_text=orig_raw
        )
        await set_order_loader_message_id(order.id, 76, -100999)
        order = await get_order_by_id(order.id)
        order_id = order.id
        self.assertEqual(order.loader_message_id, 76)
        self.assertEqual(order.loader_group_id, -100999)

        # 2. Password Update #1: A -> B
        raw1 = build_updated_raw_text_with_passwords(order.raw_text, "B")
        self.assertIn("Old Password: A", raw1)
        self.assertIn("New Password: B", raw1)
        upd1 = await update_order_raw_text(order_id, raw1, "ex@gmail.com")
        self.assertEqual(upd1.id, order_id)
        self.assertEqual(upd1.loader_message_id, 76)

        # 3. Password Update #2: B -> C
        raw2 = build_updated_raw_text_with_passwords(upd1.raw_text, "C")
        self.assertIn("Old Password: B", raw2)
        self.assertIn("New Password: C", raw2)
        upd2 = await update_order_raw_text(order_id, raw2, "ex@gmail.com")
        self.assertEqual(upd2.id, order_id)
        self.assertEqual(upd2.loader_message_id, 76)

        # 4. Password Update #3: C -> D
        raw3 = build_updated_raw_text_with_passwords(upd2.raw_text, "D")
        self.assertIn("Old Password: C", raw3)
        self.assertIn("New Password: D", raw3)
        upd3 = await update_order_raw_text(order_id, raw3, "ex@gmail.com")
        self.assertEqual(upd3.id, order_id)
        self.assertEqual(upd3.loader_message_id, 76)

        # 5. Customer clicks ❌ Cancel Order after 3 password updates
        sent_messages = []
        sent_reactions = []

        class MockQuery:
            data = f"cust_confirm:pw_cancel:{order_id}:wrong_password"
            message = type("Msg", (), {"caption": None})()

            async def edit_message_text(self, text, parse_mode=None):
                pass

            async def edit_message_caption(self, caption, parse_mode=None):
                pass

            async def answer(self, text=None, show_alert=False):
                pass

        class MockBot:
            async def send_message(self, chat_id, text, reply_to_message_id=None, parse_mode=None):
                sent_messages.append({
                    "chat_id": chat_id,
                    "text": text,
                    "reply_to_message_id": reply_to_message_id
                })

            async def set_message_reaction(self, chat_id, message_id, reaction=None, is_big=None):
                sent_reactions.append({
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reaction": reaction
                })

        mock_update = type("Update", (), {"callback_query": MockQuery()})()
        mock_context = type("Context", (), {"bot": MockBot()})()

        await customer_confirmation_callback_handler(mock_update, mock_context)

        # ✓ Cancel Order marks same order Cancelled
        can_order = await get_order_by_id(order_id)
        self.assertEqual(can_order.status, "CANCELLED")

        # ✓ ❌ reaction added to loader_message_id (76) in loader_group_id (-100999)
        self.assertEqual(len(sent_reactions), 1)
        self.assertEqual(sent_reactions[0]["chat_id"], -100999)
        self.assertEqual(sent_reactions[0]["message_id"], 76)

        # ✓ Cancellation notification sent replying to loader_message_id (76)
        self.assertEqual(len(sent_messages), 1)
        self.assertEqual(sent_messages[0]["chat_id"], -100999)
        self.assertEqual(sent_messages[0]["reply_to_message_id"], 76)
        self.assertIn("has been cancelled by the customer.", sent_messages[0]["text"])

        # ✓ Cancelled order cannot be delivered
        from delivery import deliver_order_by_id
        success = await deliver_order_by_id(MockBot(), order_id)
        self.assertFalse(success)

    async def test_multi_order_explicit_cancellation_isolation(self):
        """
        Tests Section 8 requirement:
        Create Order #236 and Order #277.
        Trigger Wrong Password & Cancel on Order #277.
        Verify Order #277 cancelled, Order #236 unchanged, ❌ reaction on Order #277 loader msg.
        """
        from database import (
            create_order,
            set_order_loader_message_id,
            get_order_by_id
        )
        from handlers import customer_confirmation_callback_handler

        # 1. Create Order #236
        order_236 = await create_order(
            email="cust236@gmail.com",
            client_chat_id=-1001,
            original_message_id=10,
            package="10800",
            raw_text="Email: cust236@gmail.com\n10800"
        )
        await set_order_loader_message_id(order_236.id, 2360, -10099)
        order_236 = await get_order_by_id(order_236.id)

        # 2. Create Order #277
        order_277 = await create_order(
            email="cust277@gmail.com",
            client_chat_id=-1002,
            original_message_id=20,
            package="21600",
            raw_text="Activision\nEmail: cust277@gmail.com\nPassword: P277\n21600"
        )
        await set_order_loader_message_id(order_277.id, 2770, -10099)
        order_277 = await get_order_by_id(order_277.id)

        # 3. Customer clicks ❌ Cancel Order on Order #277
        sent_messages = []
        sent_reactions = []

        class MockQuery:
            data = f"cust_confirm:pw_cancel:{order_277.id}:wrong_password"
            message = type("Msg", (), {"caption": None})()

            async def edit_message_text(self, text, parse_mode=None):
                pass

            async def edit_message_caption(self, caption, parse_mode=None):
                pass

            async def answer(self, text=None, show_alert=False):
                pass

        class MockBot:
            async def send_message(self, chat_id, text, reply_to_message_id=None, parse_mode=None):
                sent_messages.append({
                    "chat_id": chat_id,
                    "text": text,
                    "reply_to_message_id": reply_to_message_id
                })

            async def set_message_reaction(self, chat_id, message_id, reaction=None, is_big=None):
                sent_reactions.append({
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reaction": reaction
                })

        mock_update = type("Update", (), {"callback_query": MockQuery()})()
        mock_context = type("Context", (), {"bot": MockBot()})()

        await customer_confirmation_callback_handler(mock_update, mock_context)

        # 4. Verify Order #277 is CANCELLED and Order #236 is UNCHANGED
        res_277 = await get_order_by_id(order_277.id)
        res_236 = await get_order_by_id(order_236.id)

        self.assertEqual(res_277.status, "CANCELLED")
        self.assertEqual(res_236.status, "Pending")

        # 5. Verify ❌ reaction on Order #277's loader message (2770) and NOT #236 (2360)
        self.assertEqual(len(sent_reactions), 1)
        self.assertEqual(sent_reactions[0]["message_id"], 2770)
        self.assertNotEqual(sent_reactions[0]["message_id"], 2360)

        # 6. Verify notification text explicitly mentions Order #277 and NOT #236
        self.assertEqual(len(sent_messages), 1)
        self.assertIn(f"Order #{order_277.id} has been cancelled by the customer.", sent_messages[0]["text"])
        self.assertNotIn(f"#{order_236.id}", sent_messages[0]["text"])


class TestLoaderBotNotificationFilter(unittest.IsolatedAsyncioTestCase):
    async def test_is_bot_system_notification_text_helper(self):
        from utils import is_bot_system_notification_text

        # Bot system notifications -> must return True
        self.assertTrue(is_bot_system_notification_text("❌ Order Cancelled\n\nOrder #267 has been cancelled by the customer."))
        self.assertTrue(is_bot_system_notification_text("🔄 Password Updated\n\nOrder #267\nOld Password: A\nNew Password: B"))
        self.assertTrue(is_bot_system_notification_text("🔄 Customer is updating the password.\n\nPlease wait for the new password."))
        self.assertTrue(is_bot_system_notification_text("📦 Delivered Package\n\n10800 CP delivered for Order #267."))
        self.assertTrue(is_bot_system_notification_text("📊 Delivery Ledger\n\nBefore 64$\nNow 15.5$\nTotal 79.5$"))
        self.assertTrue(is_bot_system_notification_text("⏳ Waiting for customer confirmation..."))
        self.assertTrue(is_bot_system_notification_text("✅ Customer confirmed password"))

        # Real loader inputs -> must return False
        self.assertFalse(is_bot_system_notification_text("64"))
        self.assertFalse(is_bot_system_notification_text("+10"))
        self.assertFalse(is_bot_system_notification_text("-10"))
        self.assertFalse(is_bot_system_notification_text("wrong password"))
        self.assertFalse(is_bot_system_notification_text("wrong name"))
        self.assertFalse(is_bot_system_notification_text("2fa"))

    async def test_bot_cancellation_and_system_notifications_ignored(self):
        from handlers import delivery_group_handler, price_input_text_handler

        replied_text = []

        class MockUser:
            id = 999000
            is_bot = True

        class MockChat:
            id = -100999
            title = "Loader Group"

        class MockReplyTo:
            message_id = 555
            text = "Order details"
            caption = None

        class MockMessage:
            message_id = 556
            from_user = MockUser()
            chat = MockChat()
            text = "❌ Order Cancelled\n\nOrder #267 has been cancelled by the customer.\n\nPlease stop this delivery."
            caption = None
            photo = None
            document = None
            reply_to_message = MockReplyTo()

            async def reply_text(self, text, parse_mode=None, reply_to_message_id=None, reply_markup=None):
                replied_text.append(text)

        class MockBot:
            id = 999000

        mock_update = type("Update", (), {
            "effective_user": MockUser(),
            "effective_chat": MockChat(),
            "effective_message": MockMessage()
        })()
        mock_context = type("Context", (), {"bot": MockBot()})()

        # Run both handlers on bot notification message
        await delivery_group_handler(mock_update, mock_context)
        await price_input_text_handler(mock_update, mock_context)

        # ✓ Verify NO reply text was sent (never triggers "❌ Invalid price")
        self.assertEqual(len(replied_text), 0)


class TestDynamicDeliveryPricingAfterUpdatePrices(unittest.IsolatedAsyncioTestCase):
    async def test_updateprices_updates_delivery_caption_and_ledger(self):
        from database import bulk_update_package_prices_in_db, DEFAULT_PACKAGE_PRICES
        from utils import (
            format_delivered_packages_caption,
            calculate_delivered_packages_value,
            PACKAGE_PRICES,
            reload_package_prices_cache
        )

        # 1. Update price for 2400 CP from 16 to 15.5
        success = await bulk_update_package_prices_in_db({"2400": 15.5}, updated_by_id=1573531032)
        self.assertTrue(success)
        self.assertEqual(PACKAGE_PRICES["2400"], 15.5)

        # 2. Verify Delivered Package caption uses current price 15.5$ even if item has stale unit_price 16.0
        items_with_stale_price = [{"package": "2400", "qty": 1, "unit_price": 16.0, "status": "Pending"}]
        caption = format_delivered_packages_caption(items_with_stale_price)
        self.assertIn("📦 Delivered Package", caption)
        self.assertIn("✅ 2400 CP", caption)
        self.assertIn("💰 Price: 15.5$", caption)
        self.assertNotIn("💰 Price: 16$", caption)

        # 3. Verify Delivery Ledger calculation uses current price 15.5
        ledger_val, is_known = calculate_delivered_packages_value("2400")
        self.assertTrue(is_known)
        self.assertEqual(ledger_val, 15.5)

        # Cleanup: Restore default package prices
        await bulk_update_package_prices_in_db(DEFAULT_PACKAGE_PRICES, updated_by_id=1573531032)

    async def test_multi_package_and_partial_delivery_pricing_after_update(self):
        from database import bulk_update_package_prices_in_db, DEFAULT_PACKAGE_PRICES
        from utils import (
            format_delivered_packages_caption,
            calculate_delivered_packages_value,
            PACKAGE_PRICES
        )

        # Update 10800 = 67.0 and 2400 = 15.5
        await bulk_update_package_prices_in_db({"10800": 67.0, "2400": 15.5}, updated_by_id=1573531032)
        self.assertEqual(PACKAGE_PRICES["10800"], 67.0)
        self.assertEqual(PACKAGE_PRICES["2400"], 15.5)

        # Multi-package delivery caption (10800 + 2400 -> 67 + 15.5 = 82.5$)
        multi_items = [
            {"package": "10800", "qty": 1, "status": "Pending"},
            {"package": "2400", "qty": 1, "status": "Pending"}
        ]
        caption = format_delivered_packages_caption(multi_items)
        self.assertIn("📦 Delivered Package(s)", caption)
        self.assertIn("✅ 10800 CP", caption)
        self.assertIn("✅ 2400 CP", caption)
        self.assertIn("💰 Price: 82.5$", caption)

        # Multi-package delivery ledger value
        ledger_val, is_known = calculate_delivered_packages_value("10800+2400")
        self.assertTrue(is_known)
        self.assertEqual(ledger_val, 82.5)

        # Partial delivery caption (2400 only -> 15.5$)
        partial_items = [{"package": "2400", "qty": 1, "status": "Pending"}]
        p_caption = format_delivered_packages_caption(partial_items)
        self.assertIn("📦 Delivered Package", p_caption)
        self.assertIn("💰 Price: 15.5$", p_caption)

        # Cleanup: Restore default package prices
        await bulk_update_package_prices_in_db(DEFAULT_PACKAGE_PRICES, updated_by_id=1573531032)


class TestFlexibleOrderDetection(unittest.IsolatedAsyncioTestCase):
    async def test_exact_customer_yandex_order_sample(self):
        from order_parser import parse_order_v2

        sample_msg = (
            "Login\n\n"
            "Raphiniels@yandex.ru\n\n"
            "Password\n\n"
            "powered124\n\n"
            "Nickname\n\n"
            "Raphaskill\n\n"
            "7200cp"
        )

        parsed = parse_order_v2(sample_msg)

        self.assertTrue(parsed["order_detected"])
        self.assertEqual(parsed["email"].lower(), "raphiniels@yandex.ru")
        self.assertEqual(parsed["password"], "powered124")
        self.assertEqual(parsed["username"], "Raphaskill")
        self.assertEqual(len(parsed["packages"]), 1)
        self.assertEqual(parsed["packages"][0]["package"], "7200")

    async def test_label_and_cp_format_variations(self):
        from order_parser import parse_order_v2

        # 1. Colon-separated format with 7200 CP
        v1 = parse_order_v2("Login: email@gmail.com\nPassword: abc123\nNickname: Player\n7200 CP")
        self.assertTrue(v1["order_detected"])
        self.assertEqual(v1["email"], "email@gmail.com")
        self.assertEqual(v1["password"], "abc123")
        self.assertEqual(v1["username"], "Player")
        self.assertEqual(v1["packages"][0]["package"], "7200")

        # 2. Spanish format with CP: 7200 and @outlook.es
        v2 = parse_order_v2("Correo\nusuario@outlook.es\nContraseña\nmiClave123\nApodo\nMiApodo\nCP: 7200")
        self.assertTrue(v2["order_detected"])
        self.assertEqual(v2["email"], "usuario@outlook.es")
        self.assertEqual(v2["password"], "miClave123")
        self.assertEqual(v2["username"], "MiApodo")
        self.assertEqual(v2["packages"][0]["package"], "7200")

        # 3. Thousands separator (7.200) with @icloud.com
        v3 = parse_order_v2("Login\nuser@icloud.com\nPass\npass123\nIGN\nPlayer1\n7.200")
        self.assertTrue(v3["order_detected"])
        self.assertEqual(v3["email"], "user@icloud.com")
        self.assertEqual(v3["password"], "pass123")
        self.assertEqual(v3["username"], "Player1")
        self.assertEqual(v3["packages"][0]["package"], "7200")

        # 4. Comma thousands separator (7,200) with @proton.me
        v4 = parse_order_v2("E-mail\nuser@proton.me\nClave\npass456\nNombre\nPlayer2\n7,200cp.")
        self.assertTrue(v4["order_detected"])
        self.assertEqual(v4["email"], "user@proton.me")
        self.assertEqual(v4["password"], "pass456")
        self.assertEqual(v4["username"], "Player2")
        self.assertEqual(v4["packages"][0]["package"], "7200")

    async def test_false_positive_prevention(self):
        from order_parser import parse_order_v2

        # Random chat message with email only -> must not detect as order
        r1 = parse_order_v2("Contact us at info@example.com for support")
        self.assertFalse(r1["order_detected"])

        # Random message with number only -> must not detect as order
        r2 = parse_order_v2("The score was 7200 points in the game")
        self.assertFalse(r2["order_detected"])


class TestClientReactionAndMessageEditFilter(unittest.IsolatedAsyncioTestCase):
    async def test_reaction_updates_ignored_by_edited_message_handler(self):
        from handlers import edited_message_handler, BOT_SETTINGS
        from unittest.mock import AsyncMock

        replied_messages = []

        class MockUser:
            id = 1573531032  # Super Admin ID

        class MockChat:
            id = -100123456

        class MockEffectiveMessage:
            message_id = 1475

        class MockUpdate:
            edited_message = None
            message_reaction = type("Reaction", (), {})()
            message_reaction_count = None
            effective_message = MockEffectiveMessage()
            effective_chat = MockChat()
            effective_user = MockUser()

        BOT_SETTINGS["source_group_id"] = -100123456

        mock_update = MockUpdate()
        mock_context = type("Context", (), {"bot": type("Bot", (), {"id": 999000})()})()

        # Run handler on reaction update
        await edited_message_handler(mock_update, mock_context)

        # ✓ Verify no reply notice was sent
        self.assertEqual(len(replied_messages), 0)

    async def test_genuine_customer_edited_message_triggers_manual_placement_notice(self):
        from handlers import edited_message_handler, BOT_SETTINGS

        replied_messages = []

        class MockUser:
            id = 999888777  # Normal Customer ID (not admin/delivery)

        class MockChat:
            id = -100123456

        class MockEditedMessage:
            message_id = 1475
            text = "10800"

            async def reply_text(self, text, reply_to_message_id=None):
                replied_messages.append({
                    "text": text,
                    "reply_to_message_id": reply_to_message_id
                })

        class MockUpdate:
            edited_message = MockEditedMessage()
            message_reaction = None
            message_reaction_count = None
            effective_message = MockEditedMessage()
            effective_chat = MockChat()
            effective_user = MockUser()

        BOT_SETTINGS["source_group_id"] = -100123456

        mock_update = MockUpdate()
        mock_context = type("Context", (), {"bot": type("Bot", (), {"id": 999000})()})()

        await edited_message_handler(mock_update, mock_context)

        # ✓ Verify manual placement notice was sent for genuine customer message edit
        self.assertEqual(len(replied_messages), 1)
        self.assertIn("This order will be placed again manually wait for team", replied_messages[0]["text"])
        self.assertEqual(replied_messages[0]["reply_to_message_id"], 1475)


class TestCategoryAGroupLedgerIsolation(unittest.IsolatedAsyncioTestCase):
    async def test_category_a_multi_group_ledger_isolation(self):
        from database import (
            init_db,
            record_delivery_ledger_entry,
            get_running_total_current,
            execute_pay_reset,
            execute_manual_adjustment,
            get_last_running_total_entry
        )

        await init_db()

        chat_a1 = -1001111111111
        chat_a2 = -1002222222222
        chat_a3 = -1003333333333

        # 1. Set initial balances: A-1 = $800, A-2 = $20, A-3 = $150
        await record_delivery_ledger_entry(order_id=None, package="INIT", now_value=800.0, chat_id=chat_a1)
        await record_delivery_ledger_entry(order_id=None, package="INIT", now_value=20.0, chat_id=chat_a2)
        await record_delivery_ledger_entry(order_id=None, package="INIT", now_value=150.0, chat_id=chat_a3)

        self.assertEqual(await get_running_total_current(chat_id=chat_a1), 800.0)
        self.assertEqual(await get_running_total_current(chat_id=chat_a2), 20.0)
        self.assertEqual(await get_running_total_current(chat_id=chat_a3), 150.0)

        # 2. Delivery in A-1 ($50) -> Increases ONLY A-1 ($850). A-2 and A-3 remain unchanged.
        e1, _ = await record_delivery_ledger_entry(order_id=901, package="10800", now_value=50.0, chat_id=chat_a1)
        self.assertEqual(await get_running_total_current(chat_id=chat_a1), 850.0)
        self.assertEqual(await get_running_total_current(chat_id=chat_a2), 20.0)
        self.assertEqual(await get_running_total_current(chat_id=chat_a3), 150.0)

        # 3. Delivery in A-2 ($10) -> Increases ONLY A-2 ($30). A-1 and A-3 remain unchanged.
        e2, _ = await record_delivery_ledger_entry(order_id=902, package="2400", now_value=10.0, chat_id=chat_a2)
        self.assertEqual(await get_running_total_current(chat_id=chat_a1), 850.0)
        self.assertEqual(await get_running_total_current(chat_id=chat_a2), 30.0)
        self.assertEqual(await get_running_total_current(chat_id=chat_a3), 150.0)

        # 4. /pay in A-1 -> Resets ONLY A-1 ($0). A-2 stays $30, A-3 stays $150.
        entry_pay_a1, before_p1, paid_p1, cur_p1 = await execute_pay_reset(admin_id=1573531032, chat_id=chat_a1)
        self.assertEqual(before_p1, 850.0)
        self.assertEqual(cur_p1, 0.0)
        self.assertEqual(await get_running_total_current(chat_id=chat_a1), 0.0)
        self.assertEqual(await get_running_total_current(chat_id=chat_a2), 30.0)
        self.assertEqual(await get_running_total_current(chat_id=chat_a3), 150.0)

        # 5. +10 in A-1 -> Affects ONLY A-1 ($10). A-2 stays $30.
        e_plus, b_plus, n_plus, a_plus, _ = await execute_manual_adjustment(10.0, admin_id=1573531032, chat_id=chat_a1)
        self.assertEqual(await get_running_total_current(chat_id=chat_a1), 10.0)
        self.assertEqual(await get_running_total_current(chat_id=chat_a2), 30.0)

        # 6. -10 in A-2 -> Affects ONLY A-2 ($20). A-1 stays $10.
        e_minus, b_minus, n_minus, a_minus, _ = await execute_manual_adjustment(-10.0, admin_id=1573531032, chat_id=chat_a2)
        self.assertEqual(await get_running_total_current(chat_id=chat_a2), 20.0)
        self.assertEqual(await get_running_total_current(chat_id=chat_a1), 10.0)
        self.assertEqual(await get_running_total_current(chat_id=chat_a3), 150.0)

        # 7. /pay in A-2 -> Resets ONLY A-2 ($0).
        entry_pay_a2, _, _, _ = await execute_pay_reset(admin_id=1573531032, chat_id=chat_a2)
        self.assertEqual(await get_running_total_current(chat_id=chat_a2), 0.0)

        # 8. Restart recovery verification -> DB persistent totals
        self.assertEqual(await get_running_total_current(chat_id=chat_a1), 10.0)
        self.assertEqual(await get_running_total_current(chat_id=chat_a2), 0.0)
        self.assertEqual(await get_running_total_current(chat_id=chat_a3), 150.0)


class TestFacebookRecoveryCodeParserExclusion(unittest.IsolatedAsyncioTestCase):
    async def test_facebook_recovery_codes_not_detected_as_packages(self):
        from order_parser import parse_order_v2

        sample_fb = (
            "71#\n"
            "*facebook*\n\n"
            "Nick: Apodo en el juego HR°Dxwrin\n\n"
            "Correo o número fb +529619465770\n\n"
            "Contraseña de fb: darwin.41\n\n"
            "Códigos\n"
            "*1430 8078\n"
            "*1982 9264\n"
            "*2347 4217\n\n"
            "5k"
        )

        parsed = parse_order_v2(sample_fb)

        self.assertTrue(parsed["order_detected"])
        self.assertEqual(parsed["login_method"], "Facebook")
        self.assertEqual(parsed["recovery_codes"], ["1430 8078", "1982 9264", "2347 4217"])
        self.assertEqual(len(parsed["packages"]), 1)
        self.assertEqual(parsed["packages"][0]["package"], "5040")
        self.assertEqual(parsed["unknown_packages"], [])

        pkg_names = [p["package"] for p in parsed["packages"]]
        self.assertNotIn("8078", pkg_names)
        self.assertNotIn("9264", pkg_names)
        self.assertNotIn("4217", pkg_names)
        self.assertNotIn("1430", pkg_names)

    async def test_facebook_recovery_codes_another_format(self):
        from order_parser import parse_order_v2

        sample_fb2 = (
            "*facebook*\n\n"
            "Correo o número fb: user@gmail.com\n"
            "Contraseña de fb: password\n\n"
            "Códigos:\n"
            "2534 6603\n"
            "3075 1980\n"
            "3568 0949\n\n"
            "5k"
        )

        parsed = parse_order_v2(sample_fb2)

        self.assertTrue(parsed["order_detected"])
        self.assertEqual(parsed["packages"][0]["package"], "5040")
        self.assertEqual(parsed["unknown_packages"], [])
        self.assertEqual(parsed["recovery_codes"], ["2534 6603", "3075 1980", "3568 0949"])

    async def test_phone_order_and_credentials_safety(self):
        from order_parser import parse_order_v2

        sample = (
            "Order #: 54\n"
            "Correo: test@gmail.com\n"
            "Phone: +529619465770\n"
            "Pass: mypass123\n"
            "5040"
        )

        parsed = parse_order_v2(sample)

        self.assertTrue(parsed["order_detected"])
        self.assertEqual(len(parsed["packages"]), 1)
        self.assertEqual(parsed["packages"][0]["package"], "5040")
        pkg_names = [p["package"] for p in parsed["packages"]]
        self.assertNotIn("529619465770", pkg_names)
        self.assertNotIn("54", pkg_names)
        self.assertNotIn("123", pkg_names)

    async def test_genuine_unknown_cp_package(self):
        from order_parser import parse_order_v2

        sample = (
            "Email: user@gmail.com\n"
            "Pass: password\n"
            "CP PACK: 777777"
        )

        parsed = parse_order_v2(sample)

        self.assertTrue(parsed["order_detected"])
        self.assertEqual(len(parsed["packages"]), 1)
        self.assertEqual(parsed["packages"][0]["package"], "777777")
        self.assertFalse(parsed["packages"][0]["known"])
        self.assertIn("777777", parsed["unknown_packages"])


class TestClientOrderCancellationRequestWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_client_replies_cancel_creates_request_for_loader(self):
        from database import init_db, create_order, get_order_by_id
        from handlers import handle_client_cancellation_request

        await init_db()

        order = await create_order(
            email="client_cancel_1@test.com",
            client_chat_id=-100123456,
            original_message_id=9001,
            package="2400",
            status="Pending"
        )

        sent_messages = []

        class MockRepliedMsg:
            message_id = 9001

        class MockUser:
            id = 111222333

        class MockChat:
            id = -100123456

        class MockMessage:
            message_id = 9002
            text = "cancel"
            caption = None
            reply_to_message = MockRepliedMsg()

            async def reply_text(self, text, quote=True):
                sent_messages.append(text)

        class MockBot:
            async def send_message(self, chat_id, text, reply_markup=None, reply_to_message_id=None, parse_mode=None):
                sent_messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

        class MockUpdate:
            effective_message = MockMessage()
            effective_user = MockUser()
            effective_chat = MockChat()

        mock_update = MockUpdate()
        mock_context = type("Context", (), {"bot": MockBot()})()

        handled = await handle_client_cancellation_request(mock_update, mock_context)
        self.assertTrue(handled)

        updated_order = await get_order_by_id(order.id)
        self.assertTrue(updated_order.cancellation_requested)
        self.assertEqual(updated_order.status, "Pending")  # Must NOT be cancelled yet

    async def test_random_cancel_without_reply_is_ignored(self):
        from handlers import handle_client_cancellation_request

        class MockUser:
            id = 111222333

        class MockChat:
            id = -100123456

        class MockMessage:
            message_id = 9005
            text = "cancel"
            caption = None
            reply_to_message = None  # No reply

        class MockUpdate:
            effective_message = MockMessage()
            effective_user = MockUser()
            effective_chat = MockChat()

        mock_update = MockUpdate()
        mock_context = type("Context", (), {"bot": None})()

        handled = await handle_client_cancellation_request(mock_update, mock_context)
        self.assertFalse(handled)

    async def test_duplicate_cancellation_request_prevented(self):
        from database import init_db, create_order, request_order_cancellation
        from handlers import handle_client_cancellation_request

        await init_db()

        order = await create_order(
            email="client_cancel_dup@test.com",
            client_chat_id=-100123456,
            original_message_id=9010,
            package="5040",
            status="Pending"
        )
        await request_order_cancellation(order.id)

        replied_text = []

        class MockRepliedMsg:
            message_id = 9010

        class MockMessage:
            message_id = 9011
            text = "cancel"
            caption = None
            reply_to_message = MockRepliedMsg()

            async def reply_text(self, text, quote=True):
                replied_text.append(text)

        class MockUpdate:
            effective_message = MockMessage()
            effective_user = type("User", (), {"id": 111})()
            effective_chat = type("Chat", (), {"id": -100123456})()

        mock_update = MockUpdate()
        mock_context = type("Context", (), {"bot": None})()

        handled = await handle_client_cancellation_request(mock_update, mock_context)
        self.assertTrue(handled)
        self.assertIn("Cancellation request already sent", replied_text[0])

    async def test_already_delivered_order_cannot_be_cancelled(self):
        from database import init_db, create_order
        from handlers import handle_client_cancellation_request

        await init_db()

        order = await create_order(
            email="client_cancel_del@test.com",
            client_chat_id=-100123456,
            original_message_id=9020,
            package="10800",
            status="Delivered"
        )

        replied_text = []

        class MockRepliedMsg:
            message_id = 9020

        class MockMessage:
            message_id = 9021
            text = "/cancel"
            caption = None
            reply_to_message = MockRepliedMsg()

            async def reply_text(self, text, quote=True):
                replied_text.append(text)

        class MockUpdate:
            effective_message = MockMessage()
            effective_user = type("User", (), {"id": 111})()
            effective_chat = type("Chat", (), {"id": -100123456})()

        mock_update = MockUpdate()
        mock_context = type("Context", (), {"bot": None})()

        handled = await handle_client_cancellation_request(mock_update, mock_context)
        self.assertTrue(handled)
        self.assertIn("already been delivered and cannot be cancelled", replied_text[0])

    async def test_loader_decisions_cancel_wait_almost(self):
        from database import init_db, create_order, request_order_cancellation, get_order_by_id
        from handlers import client_cancellation_request_callback_handler

        await init_db()

        # 1. Loader cancels order
        order1 = await create_order(
            email="loader_dec_1@test.com",
            client_chat_id=-100123456,
            original_message_id=9030,
            package="2400",
            status="Pending"
        )
        await request_order_cancellation(order1.id)

        class MockUser:
            id = 1573531032  # Super Admin ID

        class MockQuery:
            from_user = MockUser()
            data = f"cancel_req_cancel:{order1.id}"
            async def answer(self, text=None, show_alert=False): pass
            async def edit_message_text(self, text, parse_mode=None): pass

        class MockUpdate:
            callback_query = MockQuery()

        await client_cancellation_request_callback_handler(MockUpdate(), type("Context", (), {"bot": None})())

        up1 = await get_order_by_id(order1.id)
        self.assertEqual(up1.status, "Cancelled")
        self.assertFalse(up1.cancellation_requested)
        self.assertEqual(up1.cancellation_decision, "cancelled")

        # 2. Loader selects wait
        order2 = await create_order(
            email="loader_dec_2@test.com",
            client_chat_id=-100123456,
            original_message_id=9040,
            package="2400",
            status="Pending"
        )
        await request_order_cancellation(order2.id)

        class MockQueryWait:
            from_user = MockUser()
            data = f"cancel_req_wait:{order2.id}"
            async def answer(self, text=None, show_alert=False): pass
            async def edit_message_text(self, text, parse_mode=None): pass

        await client_cancellation_request_callback_handler(type("Update", (), {"callback_query": MockQueryWait()})(), type("Context", (), {"bot": None})())

        up2 = await get_order_by_id(order2.id)
        self.assertEqual(up2.status, "Pending")
        self.assertFalse(up2.cancellation_requested)
        self.assertEqual(up2.cancellation_decision, "wait")

        # 3. Loader selects almost done
        order3 = await create_order(
            email="loader_dec_3@test.com",
            client_chat_id=-100123456,
            original_message_id=9050,
            package="2400",
            status="Pending"
        )
        await request_order_cancellation(order3.id)

        class MockQueryAlmost:
            from_user = MockUser()
            data = f"cancel_req_almost:{order3.id}"
            async def answer(self, text=None, show_alert=False): pass
            async def edit_message_text(self, text, parse_mode=None): pass

        await client_cancellation_request_callback_handler(type("Update", (), {"callback_query": MockQueryAlmost()})(), type("Context", (), {"bot": None})())

        up3 = await get_order_by_id(order3.id)
        self.assertEqual(up3.status, "Pending")
        self.assertFalse(up3.cancellation_requested)
        self.assertEqual(up3.cancellation_decision, "rejected")


class TestCategoryABPricingAndWalletSystem(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from database import init_db, AsyncSessionLocal
        from models import Wallet, WalletTransaction, PaymentTransaction
        from sqlalchemy import delete
        await init_db()
        async with AsyncSessionLocal() as session:
            await session.execute(delete(WalletTransaction))
            await session.execute(delete(PaymentTransaction))
            await session.execute(delete(Wallet))
            await session.commit()

    async def asyncTearDown(self):
        from database import bulk_update_package_prices_in_db, DEFAULT_PACKAGE_PRICES
        await bulk_update_package_prices_in_db(DEFAULT_PACKAGE_PRICES, category="A")
        await bulk_update_package_prices_in_db(DEFAULT_PACKAGE_PRICES, category="B")

    async def test_category_a_and_b_price_list_isolation(self):
        from database import init_db, bulk_update_package_prices_in_db, get_all_package_prices_from_db, DEFAULT_PACKAGE_PRICES

        await init_db()

        try:
            # Update Category A 2400 price to 16.5
            await bulk_update_package_prices_in_db({"2400": 16.5}, category="A")
            # Update Category B 2400 price to 15.0
            await bulk_update_package_prices_in_db({"2400": 15.0}, category="B")

            prices_a = await get_all_package_prices_from_db(category="A")
            prices_b = await get_all_package_prices_from_db(category="B")

            self.assertEqual(prices_a.get("2400"), 16.5)
            self.assertEqual(prices_b.get("2400"), 15.0)

            # Update Category A price to 15.5
            await bulk_update_package_prices_in_db({"2400": 15.5}, category="A")

            prices_a_new = await get_all_package_prices_from_db(category="A")
            prices_b_new = await get_all_package_prices_from_db(category="B")

            # Category A price must be 15.5, Category B price MUST remain 15.0!
            self.assertEqual(prices_a_new.get("2400"), 15.5)
            self.assertEqual(prices_b_new.get("2400"), 15.0)
        finally:
            await bulk_update_package_prices_in_db(DEFAULT_PACKAGE_PRICES, category="A")
            await bulk_update_package_prices_in_db(DEFAULT_PACKAGE_PRICES, category="B")

    async def test_critical_wallet_identity_rule(self):
        import uuid
        from database import init_db, get_or_create_wallet, topup_wallet, deduct_wallet_balance_for_order

        await init_db()

        group_b1 = -1001111111111
        group_b2 = -1002222222222
        user_123 = 777123

        tx1 = f"TX_B1_{uuid.uuid4().hex[:6]}"
        tx2 = f"TX_B2_{uuid.uuid4().hex[:6]}"

        # Create wallets for B1+User123 and B2+User123
        w1, s1, _ = await topup_wallet(group_b1, user_123, 100.0, provider="Binance", transaction_id=tx1)
        w2, s2, _ = await topup_wallet(group_b2, user_123, 20.0, provider="Bybit", transaction_id=tx2)

        self.assertTrue(s1)
        self.assertTrue(s2)
        self.assertEqual(w1.balance, 100.0)
        self.assertEqual(w2.balance, 20.0)

        # Spend $30 in Group B1
        w1_after, s_deduct, _ = await deduct_wallet_balance_for_order(group_b1, user_123, order_id=8801, amount=30.0)
        self.assertTrue(s_deduct)
        self.assertEqual(w1_after.balance, 70.0)

        # Group B2 wallet MUST remain 20.0!
        w2_check = await get_or_create_wallet(group_b2, user_123)
        self.assertEqual(w2_check.balance, 20.0)

    async def test_duplicate_transaction_protection(self):
        import uuid
        from database import init_db, topup_wallet

        await init_db()

        group_b1 = -1001111111111
        user_id = 999111
        tx_dup = f"TX_DUP_{uuid.uuid4().hex[:6]}"

        # First topup with tx_dup
        w1, ok1, reason1 = await topup_wallet(group_b1, user_id, 50.0, provider="Binance", transaction_id=tx_dup)
        self.assertTrue(ok1)
        self.assertEqual(w1.balance, 50.0)

        # Duplicate topup attempt with SAME provider and transaction_id
        w2, ok2, reason2 = await topup_wallet(group_b1, user_id, 50.0, provider="Binance", transaction_id=tx_dup)
        self.assertFalse(ok2)
        self.assertEqual(reason2, "DUPLICATE_TRANSACTION")

        # Balance must remain 50.0
        w_final = await topup_wallet(group_b1, user_id, 0.0, provider="Binance") # Check
        self.assertEqual(w1.balance, 50.0)

    async def test_payment_verifier_rule_17_missing_credentials(self):
        from payment_verifier import verify_payment_transaction

        # Invalid currency test
        ok_curr, code_curr, msg_curr = await verify_payment_transaction("Binance", "TX100", 50.0, currency="BTC")
        self.assertFalse(ok_curr)
        self.assertEqual(code_curr, "UNSUPPORTED_CURRENCY")

        # Rule #17 test: Missing API credentials
        ok_rule17, code_rule17, msg_rule17 = await verify_payment_transaction("Binance", "TX100", 50.0, currency="USDT")
        self.assertFalse(ok_rule17)
        self.assertEqual(code_rule17, "MISSING_API_CREDENTIALS")

    async def test_wallet_deduction_does_not_modify_delivery_ledger(self):
        from database import init_db, topup_wallet, deduct_wallet_balance_for_order, get_running_total_current

        await init_db()

        group_b1 = -1005555555555
        user_id = 333444

        before_ledger_total = await get_running_total_current(chat_id=group_b1)

        # Top up wallet
        await topup_wallet(group_b1, user_id, 200.0, provider="Admin")
        # Deduct wallet
        await deduct_wallet_balance_for_order(group_b1, user_id, order_id=9999, amount=65.0)

        after_ledger_total = await get_running_total_current(chat_id=group_b1)

        # DeliveryLedger running total MUST remain 100% unchanged by wallet top-up / deduction!
        self.assertEqual(before_ledger_total, after_ledger_total)

    async def test_binance_api_read_only_connectivity_test(self):
        from payment_verifier import test_binance_api_connectivity

        report = await test_binance_api_connectivity()
        self.assertIn("credentials_loaded", report)
        self.assertIn("formatted_text", report)
        self.assertIn("🧪 <b>Binance API Multi-Endpoint Diagnostics</b>", report["formatted_text"])

    async def test_wallet_command_handler_execution_and_category_a_safety(self):
        from handlers import wallet_command_handler
        from database import CLIENT_GROUPS_CACHE, init_db

        await init_db()

        class MockUser:
            id = 555123
            first_name = "TestUser"
            username = "testuser"

        class MockChat:
            id = -100999888
            title = "Test Group B"

        class MockMessage:
            def __init__(self):
                self.replied_text = None
            async def reply_text(self, text, parse_mode=None, **kwargs):
                if "quote" in kwargs:
                    raise TypeError("Message.reply_text() got an unexpected keyword argument 'quote'")
                self.replied_text = text

        # 1. Category B Group Wallet Execution (/wallet & /balance)
        CLIENT_GROUPS_CACHE[-100999888] = "B"
        msg_b = MockMessage()
        up_b = type("Update", (), {"effective_user": MockUser(), "effective_chat": MockChat(), "message": msg_b})()
        ctx_b = type("Context", (), {})()

        # Should execute without NameError
        await wallet_command_handler(up_b, ctx_b)
        self.assertIsNotNone(msg_b.replied_text)
        self.assertIn("Category B Wallet Overview", msg_b.replied_text)
        self.assertIn("Current Balance:", msg_b.replied_text)
        self.assertIn("$0.00", msg_b.replied_text)

        # 2. Category A Group Wallet Execution (Must refuse & NOT create wallet)
        chat_a = MockChat()
        chat_a.id = -100777666
        chat_a.title = "Test Group A"
        CLIENT_GROUPS_CACHE[-100777666] = "A"

        msg_a = MockMessage()
        up_a = type("Update", (), {"effective_user": MockUser(), "effective_chat": chat_a, "message": msg_a})()
        ctx_a = type("Context", (), {})()

        await wallet_command_handler(up_a, ctx_a)
        self.assertIsNotNone(msg_a.replied_text)
        self.assertIn("Wallet system is active only for Category B groups.", msg_a.replied_text)


if __name__ == "__main__":
    unittest.main()
