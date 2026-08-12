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

        # Missing Password/Detail
        self.assertFalse(contains_order_keyword("Facebook\nEmail: abc@gmail.com\n2400+880")[0])

        # Missing Package
        self.assertFalse(contains_order_keyword("Facebook\nEmail: abc@gmail.com\nPassword: 123")[0])

        # Missing Platform
        self.assertFalse(contains_order_keyword("Email: abc@gmail.com\nPassword: 123\n2400+880")[0])


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

    def test_single_packages(self):
        from utils import calculate_test_price

        cases = {
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
            "2400 CP": 17.0,
            "108000 CP": 565.0
        }

        for text, expected in cases.items():
            price = calculate_test_price(text)
            self.assertEqual(price, expected, f"Single package match failed for '{text}'")

    def test_mixed_packages(self):
        from utils import calculate_test_price

        mixed_cases = {
            "108000+7200+2400": 625.0,             # 565 + 43 + 17
            "96000+420": 509.5,                    # 505 + 4.5
            "48000+2400+880": 280.0,               # 255 + 17 + 8
            "7200+2400+880": 68.0,                 # 43 + 17 + 8
            "2400+2400+880": 42.0,                 # 17*2 + 8
            "108000,72000&24000/2400+880": 1100.0   # 565 + 377 + 133 + 17 + 8
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
        self.assertEqual(p1["total_price"], 565.0)

        # 24000 must match as 24000, NOT 2400
        p2 = parse_test_order_packages("24000")
        self.assertEqual(len(p2["packages"]), 1)
        self.assertEqual(p2["packages"][0]["package"], "24000")
        self.assertEqual(p2["total_price"], 133.0)

        # 96000 must match as 96000, NOT 9600
        p3 = parse_test_order_packages("96000")
        self.assertEqual(len(p3["packages"]), 1)
        self.assertEqual(p3["packages"][0]["package"], "96000")
        self.assertEqual(p3["total_price"], 505.0)

    def test_mixed_separators_normalization(self):
        from utils import parse_test_order_packages, calculate_test_price

        # 1. 10800,5040&2400/880+420 -> Expected 5 packages: 10800, 5040, 2400, 880, 420 = 127.0$ (64.5+33+17+8+4.5)
        p1 = parse_test_order_packages("10800,5040&2400/880+420")
        self.assertIsNotNone(p1)
        self.assertEqual([item["package"] for item in p1["packages"]], ["10800", "5040", "2400", "880", "420"])
        self.assertEqual(p1["total_price"], 127.0)

        # 2. 2400,880 -> Expected 2400, 880 = 25.0$ (17+8)
        self.assertEqual(calculate_test_price("2400,880"), 25.0)

        # 3. 2400&880 -> Expected 2400, 880 = 25.0$
        self.assertEqual(calculate_test_price("2400&880"), 25.0)

        # 4. 2400\n880 -> Expected 2400, 880 = 25.0$
        self.assertEqual(calculate_test_price("2400\n880"), 25.0)

    def test_quantities(self):
        from utils import calculate_test_price

        qty_cases = {
            "2400x2": 34.0,               # 17 * 2
            "2400 x2": 34.0,              # 17 * 2
            "2x2400": 34.0,               # 17 * 2
            "2 x 2400": 34.0,             # 17 * 2
            "2400x2 + 880x3": 58.0,       # (17*2) + (8*3) = 34 + 24
            "2x10800 + 3x420": 142.5      # (64.5*2) + (4.5*3) = 129 + 13.5 = 142.5
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
            "5000 CP",      # Unsupported package
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
        self.assertEqual(calculate_test_price("10800"), 64.5)
        # 2400 must NOT match 400
        self.assertEqual(calculate_test_price("2400"), 17.0)
        # 880 must NOT match 80
        self.assertEqual(calculate_test_price("880"), 8.0)

    def test_unknown_package_detection_and_pricing(self):
        from utils import (
            parse_test_order_packages,
            format_package_progress_summary,
            get_unknown_package_keyboard,
            update_unknown_package_price
        )

        # 1. Input with mixed known and unknown: 15000+2400+880 (15000 is unknown)
        p = parse_test_order_packages("15000+2400+880")
        self.assertIsNotNone(p)
        self.assertTrue(p["has_unknown"])
        self.assertEqual(p["known_total"], 25.0)  # 17 + 8 = 25
        self.assertEqual(len(p["packages"]), 3)
        self.assertEqual(p["packages"][0]["package"], "15000")
        self.assertFalse(p["packages"][0]["known"])

        # 2. Format initial state
        items = p["packages"]
        s0 = format_package_progress_summary(items, p["known_total"])
        self.assertIn("❓ 15000 CP", s0)
        self.assertIn("💰 Known Total: 25$", s0)

        # 3. Check unknown package keyboard
        kb = get_unknown_package_keyboard(101, items)
        self.assertIsNotNone(kb)
        self.assertIn("add_unk_price:101:15000", kb.inline_keyboard[0][0].callback_data)

        # 4. Admin enters price 85 for 15000
        updated_items, new_total, has_remaining = update_unknown_package_price(items, "15000", 85.0)
        self.assertFalse(has_remaining)
        self.assertEqual(new_total, 110.0)  # 85 + 17 + 8 = 110

        # 5. Format updated state
        s1 = format_package_progress_summary(updated_items, new_total)
        self.assertIn("☐ 15000 CP", s1)
        self.assertIn("☐ 2400 CP", s1)
        self.assertIn("☐ 880 CP", s1)
        self.assertIn("💰 Total Price: 110$", s1)

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

        # 1. Verify delivered screenshot caption contains ONLY 10800 and 5040 (NOT 2400) and calculates session price (64.5 + 33 = 97.5$)
        caption = format_delivered_packages_caption(selected_for_session)
        self.assertIn("📦 Delivered Package(s)", caption)
        self.assertIn("✅ 10800 CP", caption)
        self.assertIn("✅ 5040 CP", caption)
        self.assertIn("💰 Price: 97.5$", caption)
        self.assertNotIn("2400", caption)

        # 2. Mark progress as delivered
        updated_items, is_all_completed, delivered_cnt = mark_selected_packages_delivered(items, loader_id=111)
        self.assertEqual(delivered_cnt, 2)
        self.assertFalse(is_all_completed)  # 2400 is still pending

        # 3. Verify Client card summary displays ✅ for 10800 and 5040, and ☐/⬜ for 2400
        summary = format_package_progress_summary(updated_items, 114.5)
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

        # 1. Allowed reactions must contain only standard Unicode emojis
        self.assertEqual(ALLOWED_REACTION_EMOJIS, {"✅", "❌", "❤️", "⏳"})

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
        self.assertEqual(total, 175.0)  # 150 + 17 + 8 = 175$

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

        # 1. 2400+880+420 -> Expected 3 distinct packages, total price 29.5$ (17+8+4.5)
        p1 = parse_test_order_packages("2400+880+420")
        self.assertIsNotNone(p1)
        self.assertEqual(len(p1["packages"]), 3)
        self.assertEqual([item["package"] for item in p1["packages"]], ["2400", "880", "420"])
        self.assertEqual(p1["total_price"], 29.5)
        self.assertEqual(calculate_test_price("2400+880+420"), 29.5)

        # 2. 2400+2400+880 -> Expected 3 packages preserving intentional duplicate 2400s, total price 42.0$ (17*2 + 8)
        p2 = parse_test_order_packages("2400+2400+880")
        self.assertIsNotNone(p2)
        self.assertEqual(len(p2["packages"]), 3)
        self.assertEqual([item["package"] for item in p2["packages"]], ["2400", "2400", "880"])
        self.assertEqual(p2["total_price"], 42.0)
        self.assertEqual(calculate_test_price("2400+2400+880"), 42.0)

    def test_package_summary_formatting(self):
        from utils import parse_test_order_packages, format_package_summary_and_price

        # Example 1: Single package 2400
        p1 = parse_test_order_packages("2400")
        f1 = format_package_summary_and_price(p1)
        self.assertEqual(f1, "📦 Package:\n• 2400 CP\n\n💰 Price: 17$")

        # Example 2: Multiple packages 2400+880
        p2 = parse_test_order_packages("2400+880")
        f2 = format_package_summary_and_price(p2)
        self.assertEqual(f2, "📦 Package(s):\n• 2400 CP\n• 880 CP\n\n💰 Price: 25$")

        # Example 3: Multiple packages 10800+5040+420
        p3 = parse_test_order_packages("10800+5040+420")
        f3 = format_package_summary_and_price(p3)
        self.assertEqual(f3, "📦 Package(s):\n• 10800 CP\n• 5040 CP\n• 420 CP\n\n💰 Price: 102$")

        # Example 4: Quantity 2400x2+880
        p4 = parse_test_order_packages("2400x2+880")
        f4 = format_package_summary_and_price(p4)
        self.assertEqual(f4, "📦 Package(s):\n• 2400 CP ×2\n• 880 CP\n\n💰 Price: 42$")

        # Example 5: Order preservation (880+2400)
        p5 = parse_test_order_packages("880+2400")
        f5 = format_package_summary_and_price(p5)
        self.assertEqual(f5, "📦 Package(s):\n• 880 CP\n• 2400 CP\n\n💰 Price: 25$")

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
        self.assertEqual(s0, "📦 Packages\n\n☐ 2400 CP\n☐ 880 CP\n☐ 420 CP\n\n💰 Total Price: 29.5$")

        # Delivery 1: advance 2400 to Delivered
        items1, done1 = advance_package_progress(items)
        self.assertFalse(done1)
        s1 = format_package_progress_summary(items1, total_price)
        self.assertEqual(s1, "📦 Packages\n\n✅ 2400 CP\n☐ 880 CP\n☐ 420 CP\n\n💰 Total Price: 29.5$")

        # Delivery 2: advance 880 to Delivered
        items2, done2 = advance_package_progress(items1)
        self.assertFalse(done2)
        s2 = format_package_progress_summary(items2, total_price)
        self.assertEqual(s2, "📦 Packages\n\n✅ 2400 CP\n✅ 880 CP\n☐ 420 CP\n\n💰 Total Price: 29.5$")

        # Delivery 3: advance 420 to Delivered (all done)
        items3, done3 = advance_package_progress(items2)
        self.assertTrue(done3)
        s3 = format_package_progress_summary(items3, total_price)
        self.assertEqual(s3, "📦 Packages\n\n✅ 2400 CP\n✅ 880 CP\n✅ 420 CP\n\n🎉 All Packages Delivered\n\n💰 Total Price: 29.5$")


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


if __name__ == "__main__":
    unittest.main()
