# Telegram Email Image Delivery Bot (v1.3.0) 🚀

[![Python CI](https://github.com/pubgn960/telegram-email-delivery-bot/actions/workflows/python.yml/badge.svg)](https://github.com/pubgn960/telegram-email-delivery-bot/actions/workflows/python.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Railway Deploy](https://railway.app/button.svg)](https://railway.app/)

A self-configuring, privacy-protected production Telegram bot built with **Python 3.12** and **python-telegram-bot v22+**. Operates on a production **Two-Group Reply-Based Workflow** with **Telegram Reactions** (`📥` order received, `❤️` delivery completed) and **Complete Privacy Protection** (zero customer names/usernames exposed).

---

## 🏗 Two-Group Architecture & Workflow

```text
+-----------------------+              +-----------------------+
|    1. CLIENT GROUP    |              |    2. LOADER GROUP    |
| (Customers post info) |              | (Loaders reply media) |
+-----------+-----------+              +-----------+-----------+
            |                                      |
     Customer sends order                    Bot forwards order
 (Email: abc@gmail.com)                      (Order ID: #10025)
  Bot adds 📥 reaction                             |
            |                                      v
            v                          +-----------+-----------+
+-----------+-----------+              | Loader Replies to Order   |
| Bot Registers Order   |------------->| (Photos / Albums / Docs)  |
| (Status: Pending)     |              +-----------+-----------+
+-----------------------+                          |
            ^                                      v
            |                          Bot adds ❤️ reaction to
   Bot delivers albums                 Loader delivery message
   to Client Group                                 |
  Bot adds ❤️ reaction                             |
  to customer message                              |
            |                                      |
            +--------------------------------------+
                               |
                  Updates Status to 'Delivered'
                  Edits Loader Msg in Loader Group
```

---

## 🔒 Privacy & Telegram Reaction Features

1. **Complete Customer Privacy**: Customer usernames, first names, last names, and Telegram User IDs are **NEVER** displayed or forwarded anywhere.
2. **Order Received Reaction (`📥` / `✅`)**: Placed on the original customer message in the Client Group as soon as the order is registered and forwarded.
3. **Delivery Completed Reaction (`❤️`)**: Placed on both the original customer order message in the Client Group and the Loader's delivery message in the Loader Group upon successful delivery.
4. **Graceful Fallbacks**: If reactions are disabled or unsupported in a group, the bot logs `Reaction not supported` and continues operating smoothly without crashing.

---

## 🔄 Step-by-Step Business Workflow

### Step 1: Customer Order (Client Group)
A customer posts an order message in **Group 1 (Client Group)** containing text and email:
```text
10800 CP
Email: abc@gmail.com
```
The bot creates an order record (`Order ID: #1`), adds an `📥` reaction to the customer's message, and forwards the clean order format to **Group 2 (Loader Group)**:
```text
📦 NEW ORDER

Order ID:
#1

Package:
10800 CP

Email:
abc@gmail.com

Time:
2026-08-08 12:42 UTC
```

### Step 2: Loader Reply (Loader Group)
The loader **MUST reply directly** to the bot's Order Message in the Loader Group with photos, photo-documents, or albums.

- Loaders **never type emails manually** or search by email.
- If a loader sends images without replying, the bot rejects the upload:
  ```text
  ❌ Please reply to the original order message.
  ```

### Step 3: Automated Delivery & Confirmations
Upon album completion, the bot automatically dispatches the image albums to the **Client Group**:
```text
📧 Email
abc@gmail.com

📦 Order ID
#1

✅ Delivery Completed
```
The bot adds a `❤️` reaction to both the customer order message in the Client Group and the Loader delivery message in the Loader Group, then sends a confirmation to the Loader Group:
```text
✅ DELIVERED

Order ID:
#1

Images:
8

Delivered:
2026-08-08 17:45
```

---

## ⚙️ Environment Variables (Zero Group IDs Required)

Only **3 environment variables** are required:

| Variable | Description | Example |
| :--- | :--- | :--- |
| `BOT_TOKEN` | Bot API Token from Telegram `@BotFather` | `1234567890:ABCdefGHIjkl...` |
| `ADMIN_IDS` | Comma-separated list of Admin Telegram User IDs | `123456789,987654321` |
| `DATABASE_URL` | Async SQLAlchemy Connection String | `sqlite+aiosqlite:///bot_database.db` |

---

## 🛠 Admin Management Commands

- `/source` - Configure current group as **Client Group**.
- `/delivery` - Configure current group as **Loader Group**.
- `/groups` - Display group configuration status.
- `/status` - View bot status, DB engine, uptime, and RAM memory usage.
- `/pending` - List all pending orders waiting for loader reply.
- `/delivered` - List latest delivered orders.
- `/find <order_id_or_email>` - Search order details by Order ID (e.g. `10025`) or customer email.
- `/order <order_id>` - Display detailed order information.
- `/cancel <order_id>` - Cancel a pending order.
- `/resend <order_id>` - Re-deliver an order to the Client Group.
- `/stats` - View rich dashboard metrics (Total, Pending, Delivered, Cancelled, Today's Orders, Today's Deliveries, Avg Delivery Time).
- `/export` - Download CSV report export.
- `/backup` - Download SQLite database backup file.
- `/restore` - Restore SQLite database from attached `.db` file.
- `/resetgroups` - Clear group configurations in DB.
