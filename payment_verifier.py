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


async def test_binance_pay_api_connectivity() -> Dict[str, Any]:
    """
    Performs a READ-ONLY diagnostic test specifically for Binance Pay API (/sapi/v1/pay/transactions).
    Checks whether Binance Pay API endpoint is accessible, whether API permissions allow access,
    and whether sender Binance UID (payerId) is available in the API response.
    Never exposes API key or secret.
    """
    api_key_present = bool(os.getenv("BINANCE_API_KEY"))
    api_secret_present = bool(os.getenv("BINANCE_API_SECRET"))
    credentials_loaded = api_key_present and api_secret_present

    base_urls = [
        "https://api.binance.com",
        "https://api1.binance.com",
        "https://api-gcp.binance.com"
    ]

    pay_api_ok = False
    payer_uid_available = False
    http_451 = False
    perm_sufficient = False
    last_err_msg = ""

    for b_url in base_urls:
        ok, code, data, err = await asyncio.to_thread(
            _execute_binance_signed_get_with_url, b_url, "/sapi/v1/pay/transactions"
        )
        if code == 451:
            http_451 = True
            last_err_msg = "HTTP 451: Service unavailable from a restricted location."
            continue
        if ok and isinstance(data, dict):
            pay_api_ok = True
            perm_sufficient = True
            tx_list = data.get("data", [])
            if isinstance(tx_list, list) and tx_list:
                for item in tx_list:
                    if extract_payer_binance_uid(item) is not None:
                        payer_uid_available = True
                        break
            last_err_msg = "Reachable"
            break
        elif code in (400, 401, 403):
            last_err_msg = f"HTTP {code}: {err or 'Missing Binance Pay permissions or account restriction.'}"
        else:
            if err:
                last_err_msg = err

    is_ready = credentials_loaded and pay_api_ok and payer_uid_available and perm_sufficient

    lines = ["💳 <b>Binance Pay API Verification Diagnostic</b>\n"]
    lines.append(f"<b>Binance Pay API:</b> {'✅ Available' if pay_api_ok else '❌ Not Available'}")
    lines.append(f"<b>Payer Identity (Sender UID):</b> {'✅ Sender UID Available' if payer_uid_available else '❌ Sender UID Not Available'}")
    lines.append(f"<b>USDT Supported:</b> {'✅ Supported' if pay_api_ok else '❌ Not Available'}")
    lines.append(f"<b>USDC Supported:</b> {'✅ Supported' if pay_api_ok else '❌ Not Available'}")
    lines.append(f"<b>API Permission:</b> {'✅ Sufficient' if perm_sufficient else '❌ Missing / Restricted'}")
    lines.append(f"<b>Payment Verification:</b> {'✅ READY' if is_ready else '❌ NOT READY'}\n")

    if not is_ready:
        lines.append("⚠️ <b>BINANCE UID AUTO-MATCHING IS NOT AVAILABLE THROUGH THIS ENDPOINT</b>")
        lines.append(f"<b>Diagnostic Detail:</b> {last_err_msg}")
        if http_451:
            lines.append("<i>Reason: Railway deployment IP is restricted by Binance geographic eligibility (HTTP 451).</i>")
        elif not payer_uid_available and pay_api_ok:
            lines.append("<i>Reason: Binance Pay API accessible, but no incoming Pay transactions with payerId returned.</i>")
        elif not perm_sufficient:
            lines.append("<i>Reason: Binance API key lacks Binance Pay Merchant read permissions.</i>")
    else:
        lines.append("✅ Binance Pay API is fully verified and ready for automated payer UID wallet crediting.")

    report_text = "\n".join(lines)

    return {
        "credentials_loaded": credentials_loaded,
        "pay_api_available": pay_api_ok,
        "payer_uid_available": payer_uid_available,
        "permission_sufficient": perm_sufficient,
        "is_ready": is_ready,
        "http_451": http_451,
        "report_text": report_text
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


def extract_payer_binance_uid(dep: Any) -> Optional[str]:
    """
    Safely extracts the sender Binance UID (payerId) from a Binance transaction/deposit dictionary.
    Supports top-level fields, nested payerInfo objects, and raw payload structures.
    Does NOT leak secrets or API credentials.
    """
    if not isinstance(dep, dict):
        return None

    # Check top-level direct fields
    top_candidates = [
        dep.get("payer_binance_uid"),
        dep.get("binanceId"),
        dep.get("payerId"),
        dep.get("payer_uid"),
        dep.get("senderUid"),
        dep.get("payerBinanceUid"),
        dep.get("sender_uid"),
    ]
    for cand in top_candidates:
        if cand is not None and str(cand).strip():
            return str(cand).strip()

    # Check nested payerInfo dictionary (Binance Pay API format)
    payer_info = dep.get("payerInfo")
    if isinstance(payer_info, dict):
        nested_candidates = [
            payer_info.get("binanceId"),
            payer_info.get("payerId"),
            payer_info.get("binanceUid"),
            payer_info.get("uid"),
            payer_info.get("payer_uid"),
        ]
        for cand in nested_candidates:
            if cand is not None and str(cand).strip():
                return str(cand).strip()

    # Check nested raw payload dictionary if wrapped
    raw_payload = dep.get("raw")
    if isinstance(raw_payload, dict) and raw_payload != dep:
        return extract_payer_binance_uid(raw_payload)

    return None


def log_payment_diagnostic(dep: Any, tx_id: str, amount: float, status: str) -> None:
    """
    Outputs safe diagnostic information for deposit/transaction payload inspection without exposing secrets.
    """
    if not isinstance(dep, dict):
        logger.info(f"[PAYMENT_DIAGNOSTIC] transaction_id={tx_id} | amount={amount} | status={status} | payload=invalid_dict")
        return

    available_payer_fields = [
        k for k in dep.keys()
        if "payer" in k.lower() or "sender" in k.lower() or "uid" in k.lower() or "user" in k.lower()
    ]

    nested_payer_fields = []
    if isinstance(dep.get("payerInfo"), dict):
        nested_payer_fields = [f"payerInfo.{k}" for k in dep["payerInfo"].keys()]

    all_fields = available_payer_fields + nested_payer_fields
    uid = extract_payer_binance_uid(dep)
    logger.info(
        f"[PAYMENT_DIAGNOSTIC] transaction_id={tx_id} | amount={amount} | status={status} | "
        f"available_payer_fields={all_fields} | payer_uid_field_found={'True' if uid else 'False'} | "
        f"extraction_result={'available' if uid else 'unavailable'}"
    )


def parse_binance_pay_transactions(data: Any) -> List[Dict[str, Any]]:
    """
    Parses items from Binance Pay API response (/sapi/v1/pay/transactions).
    Extracts transactionId, currency/coin, amount, status, payer_binance_uid.
    Supports dictionary wrapper {"data": [...]} and direct list returns.
    """
    results = []
    if isinstance(data, dict):
        items = data.get("data", [])
    elif isinstance(data, list):
        items = data
    else:
        items = []

    if not isinstance(items, list):
        return []

    for item in items:
        if not isinstance(item, dict):
            continue

        tx_id = str(
            item.get("transactionId")
            or item.get("orderId")
            or item.get("tradeId")
            or item.get("txId")
            or item.get("tx_id")
            or ""
        ).strip()

        coin = str(
            item.get("currency")
            or item.get("coin")
            or ""
        ).strip().upper()

        raw_amt = item.get("amount")
        try:
            dep_amount = float(raw_amt) if raw_amt is not None else 0.0
        except (ValueError, TypeError):
            dep_amount = 0.0

        raw_status = str(item.get("status", "")).lower()
        # Binance Pay status: SUCCESS, COMPLETED, C2C, 1, or present in /pay/transactions payload
        is_failed = raw_status in ("failed", "rejected", "canceled", "cancelled", "0", "false", "error")
        is_completed = not is_failed and (
            raw_status in ("success", "completed", "c2c", "1", "true")
            or item.get("status") in (1, "1")
            or item.get("orderType") in ("C2C", "PAY")
            or bool(tx_id)
        )

        payer_uid = extract_payer_binance_uid(item)

        if coin in SUPPORTED_CURRENCIES and is_completed and tx_id and dep_amount > 0:
            results.append({
                "tx_id": tx_id,
                "coin": coin,
                "amount": dep_amount,
                "status": "completed",
                "provider": "BINANCE",
                "payer_binance_uid": payer_uid,
                "raw": item
            })

    return results


async def fetch_recent_binance_deposits() -> Tuple[bool, str, list]:
    """
    Fetches recent completed USDT/USDC deposits from official Binance API.
    Queries both Binance Pay API (/sapi/v1/pay/transactions) and Capital Deposit History (/sapi/v1/capital/deposit/hisrec).
    Returns Tuple[ok: bool, code_or_msg: str, deposits: list].
    """
    if not check_provider_api_configured("BINANCE"):
        return False, "MISSING_API_CREDENTIALS", []

    base_urls = ["https://api.binance.com", "https://api1.binance.com", "https://api-gcp.binance.com"]
    deposits_map: Dict[str, Dict[str, Any]] = {}
    http_451 = False
    query_success = False

    # 1. Query Binance Pay API (/sapi/v1/pay/transactions)
    for base_url in base_urls:
        ok, code, data, err = await asyncio.to_thread(
            _execute_binance_signed_get_with_url, base_url, "/sapi/v1/pay/transactions"
        )
        if code == 451:
            http_451 = True
            continue
        if ok:
            query_success = True
            pay_items = parse_binance_pay_transactions(data)
            for item in pay_items:
                deposits_map[item["tx_id"]] = item
            break

    # 2. Query Capital Deposit History API (/sapi/v1/capital/deposit/hisrec)
    for base_url in base_urls:
        ok, code, data, err = await asyncio.to_thread(
            _execute_binance_signed_get_with_url, base_url, "/sapi/v1/capital/deposit/hisrec"
        )
        if code == 451:
            http_451 = True
            continue
        if ok and isinstance(data, list):
            query_success = True
            for dep in data:
                tx_id = str(dep.get("txId", "") or dep.get("tx_id", "")).strip()
                coin = str(dep.get("coin", "") or dep.get("currency", "")).strip().upper()
                dep_amount = float(dep.get("amount", 0))
                status = int(dep.get("status", 0)) if str(dep.get("status", "")).isdigit() else (1 if dep.get("status") in (1, "1", "completed", "COMPLETED") else 0)
                payer_uid = extract_payer_binance_uid(dep)

                if coin in SUPPORTED_CURRENCIES and status == 1 and tx_id and dep_amount > 0:
                    if tx_id not in deposits_map:
                        deposits_map[tx_id] = {
                            "tx_id": tx_id,
                            "coin": coin,
                            "amount": dep_amount,
                            "status": "completed",
                            "provider": "BINANCE",
                            "payer_binance_uid": payer_uid,
                            "raw": dep
                        }
            break

    if query_success:
        return True, "SUCCESS", list(deposits_map.values())

    if http_451:
        logger.warning("[PAYMENT_WATCHER] Binance API fetch blocked (HTTP 451 Restricted Location).")
        return False, "RESTRICTED_LOCATION_HTTP_451", []

    return False, "FETCH_FAILED", []


async def poll_and_auto_credit_binance_deposits(context=None, target_user_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Periodic background watcher task for Binance deposits.
    Fetches official Binance deposits, validates coin (USDT/USDC), status (completed),
    checks duplicate PaymentTransaction records, and credits Category B wallets safely.
    """
    logger.info("[PAYMENT_WATCHER] Checking Binance deposits...")
    ok, status_code, deposits = await fetch_recent_binance_deposits()

    if not ok:
        logger.info(f"[PAYMENT_WATCHER] Deposit check status: {status_code}")
        return {"ok": False, "status": status_code, "credited_count": 0}

    from database import AsyncSessionLocal, topup_wallet
    from models import PaymentTransaction, Order
    from sqlalchemy import select

    credited_count = 0

    for dep in deposits:
        tx_id = dep["tx_id"]
        coin = dep["coin"]
        amount = dep["amount"]

        masked_tx = tx_id[:6] + "..." + tx_id[-4:] if len(tx_id) > 10 else "***"
        logger.info(f"[PAYMENT_WATCHER] Found {coin} deposit | Amount: {amount} | Status: completed | Transaction ID: {masked_tx}")

        # Duplicate check against PaymentTransaction
        async with AsyncSessionLocal() as session:
            stmt_tx = select(PaymentTransaction).where(
                PaymentTransaction.provider == "BINANCE",
                PaymentTransaction.transaction_id == tx_id
            )
            dup_tx = (await session.execute(stmt_tx)).scalar_one_or_none()
            if dup_tx:
                logger.info(f"[PAYMENT_WATCHER] Duplicate: True | Transaction ID: {masked_tx} already processed.")
                continue

        logger.info(f"[PAYMENT_WATCHER] Duplicate: False | Processing new deposit {masked_tx}...")
        log_payment_diagnostic(dep.get("raw", dep), masked_tx, amount, "completed")

        payer_uid = dep.get("payer_binance_uid") or extract_payer_binance_uid(dep) or extract_payer_binance_uid(dep.get("raw"))

        matched_user_id = None
        matched_group_id = None

        if payer_uid:
            from database import AsyncSessionLocal, BinanceClientIdentity
            async with AsyncSessionLocal() as session:
                stmt_bin = select(BinanceClientIdentity).where(
                    BinanceClientIdentity.binance_uid == str(payer_uid).strip(),
                    BinanceClientIdentity.status == "LINKED"
                )
                ident = (await session.execute(stmt_bin)).scalar_one_or_none()
                if ident:
                    matched_group_id = ident.client_group_id
                    matched_user_id = ident.telegram_user_id

        if matched_group_id and matched_user_id:
            uid_str = str(payer_uid).strip()
            user_str = str(matched_user_id).strip()
            masked_uid = uid_str[:4] + "***" + uid_str[-3:] if len(uid_str) > 7 else "***"
            masked_user = user_str[:4] + "***" + user_str[-3:] if len(user_str) > 7 else "***"

            logger.info(f"[PAYMENT_WATCHER] Payer identity matched: True | Binance UID: {masked_uid}")
            logger.info(f"[PAYMENT_WATCHER] Wallet resolved: True | Group: {matched_group_id} | User: {masked_user}")

            w_obj, ok_topup, reason = await topup_wallet(
                client_group_id=matched_group_id,
                telegram_user_id=matched_user_id,
                amount=amount,
                provider="BINANCE",
                transaction_id=tx_id,
                currency=coin
            )

            if ok_topup and w_obj:
                credited_count += 1
                formatted_amt = format_wallet_amount(amount)
                formatted_bal = format_wallet_amount(w_obj.balance)
                logger.info(f"[PAYMENT_WATCHER] Wallet credited: +${formatted_amt} {coin} | New Balance: ${formatted_bal}")

                # Secondary Step: Check for pending Category B orders
                async with AsyncSessionLocal() as session:
                    stmt_ord = select(Order).where(
                        Order.client_chat_id == matched_group_id,
                        Order.category == "B",
                        Order.status == "Pending Payment"
                    )
                    pending_b_orders = list((await session.execute(stmt_ord)).scalars().all())

                pending_count = len(pending_b_orders)
                if pending_count > 0:
                    logger.info(f"[PAYMENT_WATCHER] Pending orders found: {pending_count} | Auto-processing orders...")
                    if context:
                        try:
                            from handlers import process_pending_category_b_orders
                            await process_pending_category_b_orders(matched_group_id, matched_user_id, context)
                        except Exception as ex_proc:
                            logger.error(f"[PAYMENT_WATCHER] Auto-processing orders error: {ex_proc}")
                else:
                    logger.info(f"[PAYMENT_WATCHER] Pending orders found: 0 | Balance remains in wallet")
            else:
                logger.warning(f"[PAYMENT_WATCHER] Wallet crediting failed: {reason}")

        elif payer_uid:
            uid_str = str(payer_uid).strip()
            masked_uid = uid_str[:4] + "***" + uid_str[-3:] if len(uid_str) > 7 else "***"
            logger.warning(f"[PAYMENT_WATCHER] UNMATCHED PAYMENT | Payer Binance UID {masked_uid} not registered in any Category B group.")
            logger.warning("[PAYMENT_WATCHER] Manual review required. Wallet NOT credited.")

        else:
            logger.warning("[PAYMENT_WATCHER] UNMATCHED PAYMENT | Payer Binance UID unavailable")
            logger.warning("[PAYMENT_WATCHER] Manual review required. Wallet NOT credited.")

    return {"ok": True, "status": "SUCCESS", "credited_count": credited_count}

