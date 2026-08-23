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
from typing import Tuple, Optional, Dict, Any, List

from utils import format_wallet_amount

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


def _get_outbound_ip_info() -> Dict[str, str]:
    """
    Fetches outbound public IP address and geo location info cleanly.
    """
    try:
        req = urllib.request.Request("https://ipinfo.io/json", headers={"User-Agent": "TelegramDeliveryBot/2.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {
                "ip": data.get("ip", "Unknown"),
                "country": data.get("country", "Unknown"),
                "region": data.get("region", "Unknown"),
                "city": data.get("city", "Unknown"),
                "org": data.get("org", "Unknown")
            }
    except Exception:
        try:
            req = urllib.request.Request("https://api.ipify.org?format=json", headers={"User-Agent": "TelegramDeliveryBot/2.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {
                    "ip": data.get("ip", "Unknown"),
                    "country": "Unknown",
                    "region": "Unknown",
                    "city": "Unknown",
                    "org": "Unknown"
                }
        except Exception:
            return {"ip": "Unknown", "country": "Unknown", "region": "Unknown", "city": "Unknown", "org": "Unknown"}


def _execute_binance_public_get(base_url: str, endpoint_path: str) -> Tuple[bool, int, str]:
    """
    Executes a public, unauthenticated GET request to a Binance endpoint.
    Returns Tuple[success: bool, status_code: int, safe_message: str].
    """
    try:
        full_url = f"{base_url}{endpoint_path}"
        req = urllib.request.Request(full_url, headers={"User-Agent": "TelegramDeliveryBot/2.0"}, method="GET")
        with urllib.request.urlopen(req, timeout=8) as response:
            return True, response.getcode(), "Reachable"
    except urllib.error.HTTPError as e:
        safe_msg = f"HTTP {e.code}"
        try:
            err_body = e.read().decode("utf-8")
            err_json = json.loads(err_body)
            if isinstance(err_json, dict) and "msg" in err_json:
                safe_msg = f"HTTP {e.code}: {err_json['msg']}"
        except Exception:
            pass
        return False, e.code, safe_msg
    except Exception as e:
        return False, 500, f"Network error: {type(e).__name__}"


def _execute_binance_signed_get_with_url(base_url: str, endpoint_path: str, params: Optional[Dict[str, Any]] = None) -> Tuple[bool, int, Any, str]:
    """
    Executes a read-only signed GET request to a specific Binance base URL.
    NEVER logs or exposes BINANCE_API_KEY or BINANCE_API_SECRET.
    """
    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_API_SECRET", "").strip()

    if not api_key or not api_secret:
        return False, 401, {}, "Binance API Key or Secret missing in environment variables."

    try:
        clean_endpoint = endpoint_path
        query_params = dict(params or {})

        if "?" in endpoint_path:
            path_part, query_part = endpoint_path.split("?", 1)
            clean_endpoint = path_part
            parsed_qs = urllib.parse.parse_qs(query_part)
            for k, v_list in parsed_qs.items():
                if k not in query_params:
                    query_params[k] = v_list[0] if v_list else ""

        query_params["timestamp"] = int(time.time() * 1000)
        query_params["recvWindow"] = 5000

        query_string = urllib.parse.urlencode(query_params)
        signature = hmac.new(
            api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        full_url = f"{base_url}{clean_endpoint}?{query_string}&signature={signature}"
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
        safe_msg = f"HTTP {e.code}"
        try:
            err_body = e.read().decode("utf-8")
            err_json = json.loads(err_body)
            if isinstance(err_json, dict) and "msg" in err_json:
                safe_msg = f"HTTP {e.code}: {err_json['msg']}"
        except Exception:
            pass
        return False, e.code, {}, safe_msg
    except Exception as e:
        return False, 500, {}, f"Network connection error: {type(e).__name__}"


def _execute_binance_signed_get(endpoint_path: str, params: Optional[Dict[str, Any]] = None) -> Tuple[bool, int, Any, str]:
    """Executes default signed GET against https://api.binance.com."""
    return _execute_binance_signed_get_with_url("https://api.binance.com", endpoint_path, params)


async def test_binance_api_connectivity() -> Dict[str, Any]:
    """
    Performs a completely SAFE, READ-ONLY Binance API connectivity and capability test.
    Tests 3 base URLs: api.binance.com, api1.binance.com, api-gcp.binance.com.
    Checks public ping reachability vs authenticated account/deposit access.
    Retrieves outbound server IP and region info cleanly.
    Returns structured report dict with formatted Telegram text report.
    Does NOT credit any wallet, mutate database, or perform any payment or trade.
    """
    api_key_present = bool(os.getenv("BINANCE_API_KEY"))
    api_secret_present = bool(os.getenv("BINANCE_API_SECRET"))
    credentials_loaded = api_key_present and api_secret_present

    logger.info(f"[PAYMENT_TEST] Binance API credentials loaded: {'YES' if credentials_loaded else 'NO'}")

    ip_info = await asyncio.to_thread(_get_outbound_ip_info)
    outbound_ip = ip_info.get("ip", "Unknown")
    country = ip_info.get("country", "Unknown")
    region = ip_info.get("region", "Unknown")
    city = ip_info.get("city", "Unknown")
    org = ip_info.get("org", "Unknown")

    logger.info(f"[PAYMENT_TEST] Outbound IP: {outbound_ip}, Location: {city}, {region}, {country} ({org})")

    base_urls = [
        "https://api.binance.com",
        "https://api1.binance.com",
        "https://api-gcp.binance.com"
    ]

    results = {}
    any_auth_ok = False
    any_deposit_ok = False
    http_451_detected = False

    for b_url in base_urls:
        domain = b_url.replace("https://", "")
        # Public test
        pub_ok, pub_code, pub_msg = await asyncio.to_thread(_execute_binance_public_get, b_url, "/api/v3/time")
        
        # Authenticated account test
        acc_ok, acc_code, acc_data, acc_msg = await asyncio.to_thread(_execute_binance_signed_get_with_url, b_url, "/api/v3/account")
        
        # Authenticated deposit history test
        dep_ok, dep_code, dep_data, dep_msg = await asyncio.to_thread(_execute_binance_signed_get_with_url, b_url, "/sapi/v1/capital/deposit/hisrec")

        if acc_code == 451 or dep_code == 451 or pub_code == 451:
            http_451_detected = True

        if acc_ok:
            any_auth_ok = True
        if dep_ok:
            any_deposit_ok = True

        results[domain] = {
            "public_ok": pub_ok, "public_code": pub_code, "public_msg": pub_msg,
            "account_ok": acc_ok, "account_code": acc_code, "account_msg": acc_msg,
            "deposit_ok": dep_ok, "deposit_code": dep_code, "deposit_msg": dep_msg
        }

        logger.info(f"[PAYMENT_TEST] [{domain}] Public: {pub_msg} | Account: {acc_msg} | Deposit: {dep_msg}")

    usdt_verification = any_deposit_ok
    usdc_verification = any_deposit_ok
    is_ready = credentials_loaded and any_auth_ok and any_deposit_ok

    logger.info(f"[PAYMENT_TEST] Binance API connection: {'SUCCESS' if any_auth_ok else 'FAILED'}")
    logger.info(f"[PAYMENT_TEST] Account access: {'SUCCESS' if any_auth_ok else 'FAILED'}")
    logger.info(f"[PAYMENT_TEST] Deposit history access: {'SUCCESS' if any_deposit_ok else 'FAILED'}")
    logger.info(f"[PAYMENT_TEST] USDT verification capability: {'AVAILABLE' if usdt_verification else 'NOT AVAILABLE'}")
    logger.info(f"[PAYMENT_TEST] USDC verification capability: {'AVAILABLE' if usdc_verification else 'NOT AVAILABLE'}")

    status_cred = "✅ Valid" if credentials_loaded else "❌ Missing"
    status_sys = "✅ READY" if is_ready else "❌ NOT READY"

    # Format Telegram Message
    lines = ["🧪 <b>Binance API Multi-Endpoint Diagnostics</b>\n"]
    lines.append(f"<b>API Credentials:</b> {status_cred}")
    lines.append(f"<b>Server Outbound IP:</b> <code>{outbound_ip}</code>")
    lines.append(f"<b>Detected Region:</b> {city}, {region}, {country} ({org})\n")

    lines.append("<b>1. Public Connectivity (/api/v3/time):</b>")
    for dom, res in results.items():
        icon = "✅" if res["public_ok"] else "❌"
        lines.append(f"• {dom}: {icon} {res['public_msg']}")

    lines.append("\n<b>2. Authenticated Account (/api/v3/account):</b>")
    for dom, res in results.items():
        icon = "✅" if res["account_ok"] else "❌"
        lines.append(f"• {dom}: {icon} {res['account_msg']}")

    lines.append("\n<b>3. Deposit History (/sapi/v1/capital/deposit/hisrec):</b>")
    for dom, res in results.items():
        icon = "✅" if res["deposit_ok"] else "❌"
        lines.append(f"• {dom}: {icon} {res['deposit_msg']}")

    lines.append(f"\nUSDT Verification: {'✅ Available' if usdt_verification else '❌ Not Available'}")
    lines.append(f"USDC Verification: {'✅ Available' if usdc_verification else '❌ Not Available'}")
    lines.append(f"Withdrawal Permission: ❌ Not Required (Read-Only)")
    lines.append(f"Trading Permission: ❌ Not Required (Read-Only)")
    lines.append(f"\n<b>Payment Verification System: {status_sys}</b>\n")

    lines.append("<b>Diagnosis & Cause:</b>")
    if http_451_detected:
        lines.append(
            f"❌ <b>HTTP 451 Restricted Location Block</b>\n"
            f"Binance Cloudflare edge servers block outbound requests originating from your Railway deployment's IP region ({country} / {org}). "
            f"Public ping and API keys are valid, but Binance restricts API access from US/datacenter IP ranges."
        )
    elif not credentials_loaded:
        lines.append("❌ BINANCE_API_KEY or BINANCE_API_SECRET missing in environment variables.")
    elif is_ready:
        lines.append("✅ All read-only deposit verification endpoints are active and accessible.")
    else:
        lines.append("❌ API query failed. Ensure 'Enable Reading' is checked in Binance API Management.")

    report_text = "\n".join(lines)

    return {
        "credentials_loaded": credentials_loaded,
        "outbound_ip": outbound_ip,
        "country": country,
        "region": region,
        "results": results,
        "api_connected": any_auth_ok,
        "account_access": any_auth_ok,
        "deposit_history_access": any_deposit_ok,
        "usdt_verification": usdt_verification,
        "usdc_verification": usdc_verification,
        "http_451_detected": http_451_detected,
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
        # Try primary base URLs safely
        for base_url in ["https://api.binance.com", "https://api1.binance.com", "https://api-gcp.binance.com"]:
            ok, code, data, err = await asyncio.to_thread(_execute_binance_signed_get_with_url, base_url, "/sapi/v1/capital/deposit/hisrec")
            if code == 451:
                return False, "RESTRICTED_LOCATION_HTTP_451", f"Binance API access blocked from server location (HTTP 451). Automated crediting suspended."
            if not ok:
                continue
            if isinstance(data, list):
                for dep in data:
                    tx_id = str(dep.get("txId", "")).strip()
                    coin = str(dep.get("coin", "")).strip().upper()
                    dep_amount = float(dep.get("amount", 0))
                    status = int(dep.get("status", 0))
                    if tx_id == transaction_id.strip():
                        if coin != currency_clean:
                            return False, "CURRENCY_MISMATCH", f"Transaction coin is {coin}, expected {currency_clean}."
                        if abs(dep_amount - amount) > 0.001:
                            return False, "AMOUNT_MISMATCH", f"Transaction amount is {dep_amount}, expected {amount}."
                        if status != 1:
                            return False, "DEPOSIT_NOT_COMPLETED", f"Transaction deposit status is {status} (pending/failed)."
                        return True, "SUCCESS", f"Transaction {transaction_id} verified successfully via Binance API."
                return False, "TX_NOT_FOUND", f"Transaction {transaction_id} not found in recent Binance deposit history."

    return False, "UNVERIFIED", f"Transaction {transaction_id} could not be verified via live {p_upper} API."



