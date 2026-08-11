# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.23.0] - 2026-08-09

### Fixed & Changed
- **Price Workflow Enhancements (`models.py`, `database.py`, `handlers.py`, `delivery.py`)**:
  - **Bug 1 Fix - Single Active Prompt per Order**:
    - Stores `price_prompt_msg_id` in database and `PRICE_INPUT_SESSION[order_id]`.
    - If admin clicks `💰 Price` button while a prompt is already active, answers callback alert: `⚠️ Please enter the price in the existing reply.` without sending duplicate prompt messages.
  - **Bug 2 Fix - Strict Reply-Based Price Entry**:
    - Requires admin text entry to be sent strictly as a **reply to the prompt message** (`Enter order price:` or `Enter new price:`).
    - Ignores unreplied text or non-matching replies completely.
  - **Strict Format Validation (`is_valid_price_string`)**:
    - Accepts exact numbers/decimals (e.g. `15`, `15.5`, `2500`, `2999.99`). Rejects `abc`, `15rs`, `price 20` with reply: `❌ Invalid price.\nPlease enter numbers only.`.
  - **Edit Price Workflow (`✏️ Edit Price`)**:
    - Changes button to `[✏️ Edit Price]` after price is set.
    - Admin clicking `✏️ Edit Price` prompts `Enter new price:`.
    - Admin reply posts a NEW message `💰 Price Updated: 30` for calculator bot detection.

## [1.22.0] - 2026-08-09

### Fixed & Changed
- **Price Workflow Client Group Routing Fix (`delivery.py`, `handlers.py`)**:
  - **Loader Group Isolation**: Loader Group receives **NO Price UI** (only plain delivery completion notice).
  - **Client Group Routing**: All Price callbacks, prompts (`Enter order price:`), and final calculator price messages (`💰 Price: 2500`) are routed strictly to the **Client Group** (`order.client_chat_id` & `order.original_message_id`).

## [1.21.0] - 2026-08-09

### Added & Changed
- **Category A Only Price Workflow (`models.py`, `database.py`, `delivery.py`, `handlers.py`, `main.py`)**:
  - Added `category` and `price` columns to `orders` database model.

## [1.20.0] - 2026-08-09

### Fixed & Changed
- **Loader Add Wizard State Isolation Fix (`loader_text_wizard_handler`)**:
  - Restricts `loader_text_wizard_handler` execution strictly to admin users with an active wizard session in `LOADERS_ADD_SESSION[user_id]`.

## [1.19.0] - 2026-08-09

### Fixed & Changed
- **Bug 1 Fix - Duplicate Order "Place Again" (`duplicate_order_callback_handler`)**:
  - Pressing `✅ Place Again` creates a brand new `Order` in database with a new Order ID.
- **Bug 2 Fix - Category B Loader Selection (`category_b_approval_callback_handler`)**:
  - Copies original customer message to selected loader group, updates DB status and loader IDs.

## [1.18.0] - 2026-08-09

### Fixed & Changed
- **Telegram BotCommand Menu Registration Fix**:
  - Replaced uppercase commands in `BotCommand` registration (`"A"` and `"B"`) with lowercase commands (`"a"` and `"b"`).

## [1.17.0] - 2026-08-09

### Added & Changed
- **Multi-Loader Approval System (Category B v2)**:
  - Implemented `Loader` declarative model and `loaders` database table.

## [1.16.0] - 2026-08-09

### Changed
- **Fixed Payment Review Group Constant (`-1004441603990`)**:
  - Configured fixed default Payment Review Group Chat ID `PAYMENT_REVIEW_GROUP_ID = -1004441603990` in `config.py`.

## [1.15.0] - 2026-08-09

### Added & Changed
- **Group Category Routing System (v1.2)**:
  - Added `ClientGroup` model and `client_groups` database table.

## [1.14.0] - 2026-08-09

### Changed
- **Removed Non-Reply & Unmatched Reply Warning Messages in Loader Group**:
  - Replaced error cards with silent logging: `[LOADER] Ignored non-reply message.` and `[LOADER] Ignored reply that does not match any active order.`

## [1.13.0] - 2026-08-09

### Added & Changed
- **Ignore Super Admin & Delivery User Messages in Client Group**:
  - Messages from Super Admins and Delivery Users in Client Group are ignored completely without order processing.

## [1.12.0] - 2026-08-09

### Added & Changed
- **Role-Based User Management (`authorized_users`)**:
  - Implemented `AuthorizedUser` model and user management commands (`/user`, `/users`).

## [1.11.0] - 2026-08-09

### Added & Changed
- **Customer Edited Order Message Handling**: Direct reply: `This order will be placed again manually wait for team`.

## [1.10.0] - 2026-08-09

### Added & Changed
- **Duplicate Order Prompt & Confirmation**: Interactive inline keyboard prompt (`⚠️ Duplicate Order Detected`).

## [1.9.0] - 2026-08-08

### Added & Changed
- **Email as First Image Album Caption**: Email address set as caption of the **FIRST image** in delivered albums.

## [1.8.0] - 2026-08-08

### Added & Changed
- **Removed Delivery Summary Card**: Removed extra completion summary card.
- **Wrong Details Workflow**: Support for Loader text reply `wrong` to notify customer.

## [1.7.0] - 2026-08-08

### Refactored & Changed
- **Global In-Memory `BOT_SETTINGS` Cache**: Zero-database-query message filtering in RAM.

## [1.6.0] - 2026-08-08

### Added & Changed
- **Keyword-Based Order Detection**: Configurable keyword list in `keywords.py`.

## [1.5.0] - 2026-08-08

### Added & Changed
- **Exact Message Copying**: `copy_message` without added metadata.

## [1.4.0] - 2026-08-08

### Added & Changed
- **Updated Reaction Rules**: `👍` on order received, `❤️` on loader reply, `❤️` on customer delivery.

## [1.3.0] - 2026-08-08

### Added & Changed
- **Privacy Protection**: Removed customer names/usernames.

## [1.2.0] - 2026-08-08

### Added & Changed
- **Two-Group Architecture**: Group 1 (Client Group) and Group 2 (Loader Group).

## [1.1.0] - 2026-08-08

### Added & Changed
- **Reply-Based Delivery Workflow**: Explicit Reply-Based Order Mapping.

## [1.0.0] - 2026-08-08

### Added
- **Initial Bot Release**: SQLAlchemy 2.0 Async layer, Railway deployment configuration.
