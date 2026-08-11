"""
Order Detection Keywords Configuration.
Contains dedicated keyword definitions for detecting customer orders in Client Group messages,
photo captions, and document captions.
"""

from typing import List, Tuple, Optional

# Dedicated order detection keywords (case-insensitive)
ORDER_KEYWORDS: List[str] = [
    ".com",
    ".co",
    ".net",
    ".org",
    ".pk",
    ".io",
    ".gg",
    "gmail",
    "gma",
    "hotmail",
    "hotmail.com",
    "outlook",
    "outlook.com",
    "yahoo",
    "icloud",
    "proton",
    "+",
    "email"
]


def contains_order_keyword(text: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    Checks if a given message text or caption contains at least one order keyword.
    Matching is case-insensitive.

    Args:
        text (Optional[str]): Message text, photo caption, or document caption.

    Returns:
        Tuple[bool, Optional[str]]: (is_matched, matched_keyword)
    """
    if not text:
        return False, None

    text_lower = text.lower()
    for kw in ORDER_KEYWORDS:
        if kw.lower() in text_lower:
            return True, kw

    return False, None
