"""
Production Order Parser v2 — Real Customer Pattern Support.
Parses complex, multilingual customer order messages, extracts structured fields,
handles package multipliers and aliases, ignores customer reference prefixes (e.g. 991#),
and classifies orders while enforcing security logging (no passwords/credentials logged).
"""

import re
import logging
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)

# Default Alias Mapping (Configurable)
DEFAULT_PACKAGE_ALIASES: Dict[str, str] = {
    "5k": "5040",
    "5k": "5040",
    "5000": "5040",
    "5040": "5040",
    "10k": "10800",
    "10k": "10800",
    "10000": "10800",
    "10800": "10800",
    "2.4k": "2400",
    "2.4k": "2400",
    "2,4k": "2400",
    "2,4k": "2400",
    "2400": "2400",
    "10.8k": "10800",
    "10.8k": "10800",
    "10,8k": "10800",
    "10,8k": "10800",
    "5.04k": "5040",
    "5.04k": "5040",
    "5,04k": "5040",
    "5,04k": "5040",
}

# Supported Platforms (case-insensitive)
SUPPORTED_PLATFORMS: List[str] = [
    "facebook",
    "fb",
    "meta",
    "activision",
    "activacion",
    "activación",
    "activision id"
]

# Standard Email Regex (Supports .es, .com, .me, etc.)
EMAIL_REGEX = re.compile(
    r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
    re.IGNORECASE
)

# International Phone Regex (e.g. +584249290951)
PHONE_REGEX = re.compile(
    r'(?:\+|\b00)\d{1,3}[\s.-]?\d{6,14}\b'
)

# Customer Reference / Order ID Prefix Regex (e.g. "991#", "1000#", "75#", "1#")
CUSTOMER_REF_REGEX = re.compile(
    r'^\s*(\d+)#',
    re.IGNORECASE | re.MULTILINE
)

# Recovery Codes Header Regex
RECOVERY_HEADER_REGEX = re.compile(
    r'^(?:código|códigos|codigo|codigos|recovery code|recovery codes|backup codes)\b',
    re.IGNORECASE
)

# Recovery Code Pattern (8 digits or 4+4 digits)
RECOVERY_CODE_PATTERN = re.compile(
    r'^\s*(\d{8}|\d{4}\s*\d{4})\s*$'
)


def get_dynamic_package_prices() -> Dict[str, float]:
    """Dynamically loads package prices from utils.PACKAGE_PRICES."""
    try:
        from utils import PACKAGE_PRICES
        return dict(PACKAGE_PRICES)
    except Exception:
        return {
            "108000": 600.0, "96000": 530.0, "72000": 400.0, "48000": 270.0,
            "43200": 240.0, "38400": 215.0, "24000": 135.0, "21600": 120.0,
            "19200": 110.0, "16800": 95.0, "14400": 80.0, "12000": 70.0,
            "10800": 64.0, "9600": 57.0, "7200": 45.0, "5040": 33.0,
            "2400": 16.5, "880": 8.0, "420": 4.5, "80": 1.0
        }


def normalize_package_alias(pkg_name: Optional[str], alias_map: Optional[Dict[str, str]] = None) -> str:
    """
    Normalizes package alias to canonical package string.
    Case-insensitive, e.g.:
    "5k" -> "5040"
    "5K" -> "5040"
    "5000" -> "5040"
    "5040" -> "5040"
    "10k" -> "10800"
    "10000" -> "10800"
    """
    if not pkg_name:
        return ""
    pkg_str = str(pkg_name).strip()
    pkg_lower = pkg_str.lower()

    aliases = dict(DEFAULT_PACKAGE_ALIASES)
    if alias_map:
        aliases.update(alias_map)

    return aliases.get(pkg_lower, pkg_str)


def extract_customer_ref_id(text: Optional[str]) -> Optional[str]:
    """Extracts customer reference ID prefix like '991#' from message start."""
    if not text:
        return None
    match = CUSTOMER_REF_REGEX.search(text)
    if match:
        return match.group(1)
    return None


def parse_order_v2(text: Optional[str], alias_map: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Production Order Parser v2 - Real Customer Pattern Support.

    Returns structured dictionary:
    {
        "order_detected": bool,
        "customer_ref_id": Optional[str],
        "email": Optional[str],
        "phone": Optional[str],
        "login_method": Optional[str],
        "username": Optional[str],
        "password": Optional[str],
        "recovery_codes": List[str],
        "packages": List[Dict[str, Any]],
        "unknown_packages": List[str],
        "total_price": Optional[float]
    }
    """
    if not text:
        return {
            "order_detected": False,
            "customer_ref_id": None,
            "email": None,
            "phone": None,
            "login_method": None,
            "username": None,
            "password": None,
            "recovery_codes": [],
            "packages": [],
            "unknown_packages": [],
            "total_price": None
        }

    price_db = get_dynamic_package_prices()
    aliases = dict(DEFAULT_PACKAGE_ALIASES)
    if alias_map:
        aliases.update(alias_map)

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # 1. Customer Ref ID (e.g. "991#", "1000#")
    customer_ref_id = extract_customer_ref_id(text)

    # 2. Email & Phone Extraction
    email_match = EMAIL_REGEX.search(text)
    email = email_match.group(0).lower().rstrip(".,;!") if email_match else None

    phone_match = PHONE_REGEX.search(text)
    phone = phone_match.group(0).strip() if phone_match else None

    # 3. Login Method Detection
    login_method = None
    text_lower = text.lower()

    if any(k in text_lower for k in ("activision", "activación", "activacion")):
        login_method = "Activision"
    elif any(k in text_lower for k in ("facebook", "fb", "meta")):
        login_method = "Facebook"

    # 4. Line-by-line Classification (Username, Password, Recovery Codes, Package Candidate Lines)
    username = None
    password = None
    recovery_codes: List[str] = []
    package_candidate_lines: List[str] = []
    ignored_lines: Set[int] = set()

    in_recovery_sec = False
    next_line_is_username = False
    next_line_is_password = False
    next_line_is_email = False

    for idx, line in enumerate(lines):
        l_strip = line.strip()
        l_lower = l_strip.lower()

        # Ignore Customer Ref Line like "991#", "1000#"
        if CUSTOMER_REF_REGEX.match(l_strip):
            ignored_lines.add(idx)
            continue

        # Ignore standalone platform headers like "*facebook*", "*Activision*", "Activación"
        if l_strip.startswith("*") and l_strip.endswith("*"):
            ignored_lines.add(idx)
            continue
        if l_lower in ("facebook", "fb", "meta", "activision", "activacion", "activación", "activision id"):
            ignored_lines.add(idx)
            continue

        # Multiline Header state consumption
        if next_line_is_username:
            if not username:
                username = l_strip
            next_line_is_username = False
            ignored_lines.add(idx)
            continue
        if next_line_is_password:
            if not password:
                password = l_strip
            next_line_is_password = False
            ignored_lines.add(idx)
            continue
        if next_line_is_email:
            next_line_is_email = False
            ignored_lines.add(idx)
            continue

        # Recovery Code Section Header
        if RECOVERY_HEADER_REGEX.match(l_strip):
            in_recovery_sec = True
            ignored_lines.add(idx)
            continue

        if in_recovery_sec:
            if RECOVERY_CODE_PATTERN.match(l_strip):
                recovery_codes.append(l_strip)
                ignored_lines.add(idx)
                continue
            else:
                in_recovery_sec = False

        # Email Headers or Email address line
        if EMAIL_REGEX.search(l_strip):
            ignored_lines.add(idx)
            continue
        if re.match(r'^(?:correo\s*o\s*número\s*fb|correo\s*o\s*numero\s*fb|correo\s*o\s*número|correo\s*o\s*numero|correo\s*electrónico|correo\s*electronico|correo|email|e-mail|mail)\s*[:=\.\-]?$', l_lower):
            ignored_lines.add(idx)
            val = line.split(":", 1)[-1].strip() if ":" in line else ""
            if not val:
                next_line_is_email = True
            continue

        # Phone line (e.g. +584249290951)
        if PHONE_REGEX.search(l_strip):
            ignored_lines.add(idx)
            continue

        # UID / Account ID Headers
        if re.match(r'^(?:uid|account\s*id|id)\s*[:=\.\-]?$', l_lower):
            ignored_lines.add(idx)
            val = line.split(":", 1)[-1].strip() if ":" in line else ""
            if not val:
                next_line_is_email = True
            continue

        # Username Headers
        if re.match(r'^(?:nick\s*name|nick|apodo\s*en\s*el\s*juego|apodo|usuario|ign|nombre)\s*[:=\.\-]?$', l_lower) or l_lower.startswith("nick"):
            ignored_lines.add(idx)
            val = line.split(":", 1)[-1].strip() if ":" in line else line
            if l_lower.startswith("nick ") and ":" not in line:
                val = line[5:].strip()
            if val and val.lower() not in ("nick", "apodo", "apodo en el juego", "nick name", "nick:"):
                if "apodo en el juego:" in val.lower():
                    val = val.split(":", 1)[-1].strip()
                if val:
                    username = val
            else:
                next_line_is_username = True
            continue

        # Password Headers
        if re.match(r'^(?:password|pass|pwd|contraseña\s*de\s*fb|contrasena\s*de\s*fb|contraseña|contrasena|clave)\s*[:=\.\-]?$', l_lower):
            ignored_lines.add(idx)
            val = line.split(":", 1)[-1].strip() if ":" in line else line
            if val and val.lower() not in ("password", "pass", "pwd", "contraseña", "contrasena", "clave", "contraseña:", "contrasena:", "clave:"):
                password = val
            else:
                next_line_is_password = True
            continue

        # Check if line is a package candidate line
        has_pkg_token = (
            any(re.search(r'\b' + re.escape(k) + r'\b', l_lower) for k in price_db.keys())
            or any(re.search(r'\b' + re.escape(alias) + r'\b', l_lower) for alias in aliases.keys())
            or re.search(r'\b\d+(?:cp)?[*xX×]\d+', l_lower)
            or re.search(r'^\d+\s*[\+\,\&\/]\s*\d+', l_lower)
        )

        if has_pkg_token:
            package_candidate_lines.append(l_strip)
            continue

    # Fallback to non-ignored lines if no explicit package candidate was found
    if not package_candidate_lines:
        for idx, line in enumerate(lines):
            if idx not in ignored_lines and line.strip():
                package_candidate_lines.append(line.strip())

    # 5. Package Segment Extraction & Multiplier Expansion Engine
    packages: List[Dict[str, Any]] = []
    unknown_packages: List[str] = []
    known_total = 0.0
    has_unknown = False

    raw_pkg_text = "\n".join(package_candidate_lines)

    # Normalize aliases first in pkg text (case-insensitive)
    for alias, target_pkg in sorted(aliases.items(), key=lambda x: -len(x[0])):
        raw_pkg_text = re.sub(r'\b' + re.escape(alias) + r'\b', target_pkg, raw_pkg_text, flags=re.IGNORECASE)

    # Normalize multiplier spaces
    raw_pkg_text = re.sub(r'\s*([*xX×])\s*', r'\1', raw_pkg_text)
    # Normalize separators into +
    raw_pkg_text = re.sub(r'[,&/\n\r+|]+', '+', raw_pkg_text)
    raw_pkg_text = re.sub(r'\s+', '+', raw_pkg_text)

    segments = [s.strip() for s in raw_pkg_text.split('+') if s.strip()]

    segment_regex = re.compile(
        r'^(?:'
        r'(?P<num1>\d+)(?:cp)?[*xX×](?P<num2>\d+)(?:cp)?'
        r'|'
        r'(?P<pkg_standalone>\d+)(?:cp)?'
        r')$',
        re.IGNORECASE
    )

    for seg in segments:
        if CUSTOMER_REF_REGEX.match(seg) or seg.endswith("#"):
            continue

        match = segment_regex.match(seg)
        if not match:
            continue

        gd = match.groupdict()
        qty = 1
        pkg = None

        if gd.get("pkg_standalone"):
            pkg = gd["pkg_standalone"]
            qty = 1
        elif gd.get("num1") and gd.get("num2"):
            n1 = int(gd["num1"])
            n2 = int(gd["num2"])

            n1_norm = normalize_package_alias(str(n1), aliases)
            n2_norm = normalize_package_alias(str(n2), aliases)

            if n2_norm in price_db:
                pkg = n2_norm
                qty = n1
            elif n1_norm in price_db:
                pkg = n1_norm
                qty = n2
            else:
                if n1 >= n2:
                    pkg = n1_norm
                    qty = n2
                else:
                    pkg = n2_norm
                    qty = n1

        if not pkg:
            continue

        # Always normalize pkg to its canonical package name
        pkg = normalize_package_alias(pkg, aliases)

        pkg_int = int(pkg)
        has_cp = bool(re.search(r'cp', seg, re.IGNORECASE))
        if not has_cp and pkg not in price_db and pkg_int < 400:
            continue

        unit_price = price_db.get(pkg)
        is_known = (unit_price is not None)

        for _ in range(qty):
            if is_known:
                known_total += unit_price
                packages.append({
                    "package": pkg,
                    "qty": 1,
                    "known": True,
                    "unit_price": unit_price,
                    "total": unit_price,
                    "status": "Pending"
                })
            else:
                has_unknown = True
                if pkg not in unknown_packages:
                    unknown_packages.append(pkg)
                packages.append({
                    "package": pkg,
                    "qty": 1,
                    "known": False,
                    "unit_price": None,
                    "total": None,
                    "status": "Unpriced"
                })

    # 6. Order Detection Decision
    has_identifier = bool(email or phone)
    has_creds = bool(password or username or recovery_codes)
    has_pkg = len(packages) > 0

    order_detected = False
    if has_identifier and has_pkg:
        order_detected = True
    elif login_method and has_creds and has_pkg:
        order_detected = True
    elif has_pkg and (has_identifier or has_creds):
        order_detected = True

    total_price = round(known_total, 2) if (packages and not has_unknown) else (round(known_total, 2) if known_total > 0 else None)

    # 7. Detector Logging (Security Enforcement: Passwords & Credentials are NEVER logged!)
    logger.info(f"[DETECTOR] Customer Ref ID: {customer_ref_id or 'None'}")
    logger.info(f"[DETECTOR] Email detected: {'YES' if email else ('PHONE' if phone else 'NO')}")
    logger.info(f"[DETECTOR] Login method: {login_method or 'Unknown'}")
    logger.info(f"[DETECTOR] Packages detected: {[p['package'] for p in packages]}")
    logger.info(f"[DETECTOR] Unknown packages: {unknown_packages}")
    logger.info(f"[DETECTOR] Order detected: {'YES' if order_detected else 'NO'}")

    return {
        "order_detected": order_detected,
        "customer_ref_id": customer_ref_id,
        "email": email,
        "phone": phone,
        "login_method": login_method,
        "username": username,
        "password": password,
        "recovery_codes": recovery_codes,
        "packages": packages,
        "unknown_packages": unknown_packages,
        "total_price": total_price
    }
