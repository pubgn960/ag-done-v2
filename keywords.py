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

# 2. Supported Email Domains (case-insensitive)
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

# 3. Password / Login Details Keywords (case-insensitive)
PASSWORD_KEYWORDS: List[str] = [
    "password",
    "pass",
    "pwd",
    "login",
    "recovery",
    "recovery code",
    "recovery codes",
    "backup codes",
    "2fa",
    "authenticator"
]

# 5. International Phone Number Regex (Optional)
PHONE_REGEX = re.compile(
    r'(?:\+|\b00)(?:92|91|971|966|1|44|49|33|61|81|82|86|90|7|20|\d{1,3})[\s.-]?\d{3,}'
)


def contains_order_keyword(text: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    Strictly checks if a given message is a valid customer order.
    Requires ALL 4 conditions to be satisfied:
    - Platform Detection
    - Login / Email Information
    - Password / Login Details
    - Package Detection

    Returns:
        Tuple[bool, Optional[str]]: (is_valid, matched_platform_keyword)
    """
    if not text:
        return False, None

    from utils import parse_test_order_packages

    text_lower = text.lower()

    # 1. Platform Detection (Required)
    matched_platform = None
    for p in SUPPORTED_PLATFORMS:
        if re.search(r'\b' + re.escape(p) + r'\b', text_lower) or p in text_lower:
            matched_platform = p
            break

    if not matched_platform:
        return False, None

    # 2. Login Information (Required: Email domain or Email address regex)
    has_email = bool(EMAIL_REGEX.search(text)) or any(d in text_lower for d in SUPPORTED_EMAIL_DOMAINS)
    if not has_email:
        return False, None

    # 3. Password / Login Details (Required)
    has_pwd = any(
        re.search(r'\b' + re.escape(kw) + r'\b', text_lower) or kw in text_lower
        for kw in PASSWORD_KEYWORDS
    )
    if not has_pwd:
        return False, None

    # 4. Package Detection (Required)
    parsed_pkg = parse_test_order_packages(text)
    has_pkg = parsed_pkg is not None and len(parsed_pkg.get("packages", [])) > 0
    if not has_pkg:
        return False, None

    return True, matched_platform
