"""
Strict Order Detection Keywords & Decision Logic.
Eliminates false positives by enforcing ALL 4 required conditions:
1. Platform Detection (Facebook, FB, Meta, Activision, etc.)
2. Login / Email Information (26+ supported email domains or standard regex)
3. Password / Login Details (Password, Pass, Pwd, Login, Recovery, 2FA, etc.)
4. Package Detection (Catalog packages, quantities, or unknown packages)
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

# 1. Supported Platform Keywords (case-insensitive)
SUPPORTED_PLATFORMS: List[str] = [
    "facebook",
    "fb",
    "meta",
    "activision",
    "activision id"
]

# 2. Supported Email Keywords & Domains (case-insensitive, English & Spanish)
EMAIL_KEYWORDS: List[str] = [
    "email",
    "correo",
    "correo electrónico",
    "correo electronico",
    "correo o número",
    "correo o numero",
    "correo o número fb",
    "correo o numero fb",
    "mail"
]

SUPPORTED_EMAIL_DOMAINS: List[str] = [
    "gmail.com",
    "googlemail.com",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "msn.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "yahoo.com",
    "ymail.com",
    "rocketmail.com",
    "proton.me",
    "protonmail.com",
    "aol.com",
    "gmx.com",
    "mail.com",
    "zoho.com",
    "yandex.com",
    "yandex.ru",
    "qq.com",
    "163.com",
    "126.com",
    "naver.com",
    "daum.net",
    "rediffmail.com"
]

# Standard Email Regex
EMAIL_REGEX = re.compile(
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
    re.IGNORECASE
)

# 3. Password / Login Details / Credential Keywords (case-insensitive, English & Spanish)
PASSWORD_KEYWORDS: List[str] = [
    "password",
    "pass",
    "pwd",
    "login",
    "contraseña",
    "contrasena",
    "clave",
    "contraseña de fb",
    "contrasena de fb",
    "código",
    "códigos",
    "codigo",
    "codigos",
    "recovery",
    "recovery code",
    "recovery codes",
    "backup codes",
    "2fa",
    "authenticator",
    "nick",
    "ign",
    "usuario",
    "nombre"
]

# 5. International Phone Number Regex (Optional)
PHONE_REGEX = re.compile(
    r'(?:\+|\b00)(?:92|91|971|966|1|44|49|33|61|81|82|86|90|7|20|\d{1,3})[\s.-]?\d{3,}'
)


def contains_order_keyword(text: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    Production Order Detection logic v2:
    Delegates to order_parser.parse_order_v2 for comprehensive detection.

    Returns:
        Tuple[bool, Optional[str]]: (is_valid, matched_platform_keyword)
    """
    if not text:
        return False, None

    from order_parser import parse_order_v2
    parsed = parse_order_v2(text)

    if parsed.get("order_detected"):
        platform = parsed.get("login_method") or "facebook"
        return True, platform.lower()

    return False, None
