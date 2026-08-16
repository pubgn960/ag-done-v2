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
import logging
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

    # If live API credentials were provided, API verification call would happen here.
    return False, "UNVERIFIED", f"Transaction {transaction_id} could not be verified via live {p_upper} API."
