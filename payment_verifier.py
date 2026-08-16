"""
Verified Payment Verification Module for Binance and Bybit (Category B Wallet Top-ups).
Enforces Rule #12, #13, #14, #15, #16, #17, #18, #19.

- Authorized Binance Destination: Alyan-Gamer-Support (UID: 738316002)
- Authorized Bybit Destination: AlyanGamer (UID: 481517334)
- Supported Currencies: USDT, USDC ONLY
- Screenshots and text claims are NEVER accepted as proof of payment.
- Verifies API credentials in environment variables. Per Rule #17: If API keys are missing,
  automated wallet crediting is suspended and reported as UNVERIFIED.
"""

import os
import hmac
import time
import json
import logging
import hashlib
import asyncio
import urllib.parse
import urllib.request
from typing import Tuple, Optional, Dict, Any

logger = logging.getLogger(__name__)

# Authorized destinations configured in MASTER PROMPT
BINANCE_AUTHORIZED_DEST = {"ID": "Alyan-Gamer-Support", "UID": "738316002"}
BYBIT_AUTHORIZED_DEST = {"ID": "AlyanGamer", "UID": "481517334"}

SUPPORTED_CURRENCIES = {"USDT", "USDC"}
SUPPORTED_PROVIDERS = {"BINANCE", "BYBIT"}


def check_provider_api_configured(provider: str) -> bool:
    """Checks if live API keys for provider are present in environment variables."""
    p_upper = (provider or "").strip().upper()
    if p_upper == "BINANCE":
        return bool(os.getenv("BINANCE_API_KEY") and os.getenv("BINANCE_API_SECRET"))
    elif p_upper == "BYBIT":
        return bool(os.getenv("BYBIT_API_KEY") and os.getenv("BYBIT_API_SECRET"))
    return False


def _execute_binance_signed_get(endpoint_path: str, params: Optional[Dict[str, Any]] = None) -> Tuple[bool, int, Any, str]:
    """
    Executes a read-only GET request to Binance API.
    Returns Tuple[success: bool, status_code: int, response_data: Any, safe_error_message: str].
    NEVER logs or exposes BINANCE_API_KEY or BINANCE_API_SECRET.
    """
    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_API_SECRET", "").strip()

    if not api_key or not api_secret:
        return False, 401, {}, "Binance API Key or Secret missing in environment variables."

    try:
        base_url = "https://api.binance.com"
        query_params = dict(params or {})
        query_params["timestamp"] = int(time.time() * 1000)
        query_params["recvWindow"] = 5000

        query_string = urllib.parse.urlencode(query_params)
        signature = hmac.new(
            api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        full_url = f"{base_url}{endpoint_path}?{query_string}&signature={signature}"
        req = urllib.request.Request(
            full_url,
            headers={
                "User-Agent": "TelegramDeliveryBot/2.0",
                "X-MBX-APIKEY": api_key
            },
            method="GET"
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            status_code = response.getcode()
            body = response.read().decode("utf-8")
            data = json.loads(body)
            return True, status_code, data, ""
    except urllib.error.HTTPError as e:
        safe_msg = f"HTTP Error {e.code}"
        try:
            err_body = e.read().decode("utf-8")
            err_json = json.loads(err_body)
            if isinstance(err_json, dict) and "msg" in err_json:
                safe_msg = f"HTTP Error {e.code}: {err_json['msg']}"
        except Exception:
            pass
        return False, e.code, {}, safe_msg
    except Exception as e:
        return False, 500, {}, f"Network connection error: {type(e).__name__}"


async def test_binance_api_connectivity() -> Dict[str, Any]:
    """
    Performs a completely SAFE, READ-ONLY Binance API connectivity and capability test.
    Checks:
    - API Credentials Loaded
    - Authenticated API Connection
    - Account Access
    - Deposit History Access (/sapi/v1/capital/deposit/hisrec)
    - USDT & USDC verification capabilities
    Returns structured report dict with formatted Telegram text report.
    Does NOT credit any wallet, mutate database, or perform any payment or trade.
    """
    api_key_present = bool(os.getenv("BINANCE_API_KEY"))
    api_secret_present = bool(os.getenv("BINANCE_API_SECRET"))
    credentials_loaded = api_key_present and api_secret_present

    logger.info(f"[PAYMENT_TEST] Binance API credentials loaded: {'YES' if credentials_loaded else 'NO'}")

    if not credentials_loaded:
        logger.info("[PAYMENT_TEST] Binance API connection: FAILED")
        logger.info("[PAYMENT_TEST] Account access: FAILED")
        logger.info("[PAYMENT_TEST] Deposit history access: FAILED")
        logger.info("[PAYMENT_TEST] USDT verification capability: NOT AVAILABLE")
        logger.info("[PAYMENT_TEST] USDC verification capability: NOT AVAILABLE")

        report_text = (
            "🧪 <b>Binance API Test</b>\n\n"
            "API Credentials: ❌ Missing\n"
            "API Connection: ❌ Failed\n"
            "Account Access: ❌ Not Available\n"
            "Deposit History: ❌ Not Available\n\n"
            "USDT Verification: ❌ Not Available\n"
            "USDC Verification: ❌ Not Available\n\n"
            "Withdrawal Permission: ❌ Not Required (Read-Only Test)\n"
            "Trading Permission: ❌ Not Required (Read-Only Test)\n\n"
            "<b>Payment Verification System: ❌ NOT READY</b>\n\n"
            "<i>Reason: BINANCE_API_KEY or BINANCE_API_SECRET is missing in environment variables.</i>"
        )
        return {
            "credentials_loaded": False,
            "api_connected": False,
            "account_access": False,
            "deposit_history_access": False,
            "usdt_verification": False,
            "usdc_verification": False,
            "is_ready": False,
            "formatted_text": report_text
        }

    # 1. Test Authenticated Account Access
    acc_ok, acc_code, acc_data, acc_err = await asyncio.to_thread(_execute_binance_signed_get, "/api/v3/account")
    api_connected = acc_ok
    account_access = acc_ok

    logger.info(f"[PAYMENT_TEST] Binance API connection: {'SUCCESS' if api_connected else 'FAILED'}")
    logger.info(f"[PAYMENT_TEST] Account access: {'SUCCESS' if account_access else 'FAILED'}")

    # 2. Test Deposit History Access (/sapi/v1/capital/deposit/hisrec)
    dep_ok, dep_code, dep_data, dep_err = await asyncio.to_thread(_execute_binance_signed_get, "/sapi/v1/capital/deposit/hisrec")
    deposit_history_access = dep_ok

    logger.info(f"[PAYMENT_TEST] Deposit history access: {'SUCCESS' if deposit_history_access else 'FAILED'}")

    usdt_verification = deposit_history_access
    usdc_verification = deposit_history_access

    logger.info(f"[PAYMENT_TEST] USDT verification capability: {'AVAILABLE' if usdt_verification else 'NOT AVAILABLE'}")
    logger.info(f"[PAYMENT_TEST] USDC verification capability: {'AVAILABLE' if usdc_verification else 'NOT AVAILABLE'}")

    is_ready = credentials_loaded and api_connected and account_access and deposit_history_access

    status_cred = "✅ Valid" if credentials_loaded else "❌ Missing"
    status_conn = "✅ Connected" if api_connected else "❌ Failed"
    status_acc = "✅ Available" if account_access else "❌ Not Available"
    status_dep = "✅ Available" if deposit_history_access else "❌ Not Available"
    status_usdt = "✅ Available" if usdt_verification else "❌ Not Available"
    status_usdc = "✅ Available" if usdc_verification else "❌ Not Available"
    status_sys = "✅ READY" if is_ready else "❌ NOT READY"

    reason_lines = []
    if not api_connected:
        reason_lines.append(f"Account API check failed ({acc_err})")
    if not deposit_history_access:
        reason_lines.append(f"Deposit history endpoint failed ({dep_err}). Ensure 'Enable Reading' is checked on Binance API key management.")

    reason_str = "\n".join(reason_lines) if reason_lines else "None. All read-only deposit verification checks passed successfully."

    report_text = (
        "🧪 <b>Binance API Test</b>\n\n"
        f"API Credentials: {status_cred}\n"
        f"API Connection: {status_conn}\n"
        f"Account Access: {status_acc}\n"
        f"Deposit History: {status_dep}\n\n"
        f"USDT Verification: {status_usdt}\n"
        f"USDC Verification: {status_usdc}\n\n"
        "Withdrawal Permission: ❌ Not Required (Read-Only Test)\n"
        "Trading Permission: ❌ Not Required (Read-Only Test)\n\n"
        f"<b>Payment Verification System: {status_sys}</b>\n\n"
        f"<i>Details / Reason:</i>\n{reason_str}"
    )

    return {
        "credentials_loaded": credentials_loaded,
        "api_connected": api_connected,
        "account_access": account_access,
        "deposit_history_access": deposit_history_access,
        "usdt_verification": usdt_verification,
        "usdc_verification": usdc_verification,
        "is_ready": is_ready,
        "formatted_text": report_text
    }


async def verify_payment_transaction(
    provider: str,
    transaction_id: str,
    amount: float,
    currency: str = "USDT"
) -> Tuple[bool, str, str]:
    """
    Verifies payment transaction against official provider data.
    Returns Tuple[is_verified: bool, status_code: str, message: str].
    """
    if not provider or provider.strip().upper() not in SUPPORTED_PROVIDERS:
        return False, "UNSUPPORTED_PROVIDER", f"Provider '{provider}' is not supported. Supported: Binance, Bybit."

    currency_clean = (currency or "").strip().upper()
    if currency_clean not in SUPPORTED_CURRENCIES:
        return False, "UNSUPPORTED_CURRENCY", f"Currency '{currency}' is not supported. Supported: USDT, USDC."

    if not transaction_id or not transaction_id.strip():
        return False, "INVALID_TX_ID", "Transaction ID is required."

    if amount <= 0:
        return False, "INVALID_AMOUNT", "Payment amount must be greater than zero."

    p_upper = provider.strip().upper()

    # Rule #17 Check: Are live API credentials configured?
    if not check_provider_api_configured(p_upper):
        msg = f"Live API credentials for {p_upper} are missing. Automated API crediting suspended per Rule 17."
        logger.warning(f"[PAYMENT_VERIFIER] {msg}")
        return False, "MISSING_API_CREDENTIALS", msg

    if p_upper == "BINANCE":
        # Check deposit history using read-only API
        ok, code, data, err = await asyncio.to_thread(_execute_binance_signed_get, "/sapi/v1/capital/deposit/hisrec")
        if not ok:
            return False, "API_ERROR", f"Binance API query failed: {err}"
        if isinstance(data, list):
            for dep in data:
                tx_id = str(dep.get("txId", "")).strip()
                coin = str(dep.get("coin", "")).strip().upper()
                dep_amount = float(dep.get("amount", 0))
                status = int(dep.get("status", 0))
                if tx_id == transaction_id.strip():
                    if coin != currency_clean:
                        return False, "CURRENCY_MISMATCH", f"Transaction coin is {coin}, expected {currency_clean}."
                    if abs(dep_amount - amount) > 0.01:
                        return False, "AMOUNT_MISMATCH", f"Transaction amount is {dep_amount}, expected {amount}."
                    if status != 1:
                        return False, "DEPOSIT_NOT_COMPLETED", f"Transaction deposit status is {status} (pending/failed)."
                    return True, "SUCCESS", f"Transaction {transaction_id} verified successfully via Binance API."
            return False, "TX_NOT_FOUND", f"Transaction {transaction_id} not found in recent Binance deposit history."

    return False, "UNVERIFIED", f"Transaction {transaction_id} could not be verified via live {p_upper} API."
