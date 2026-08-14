"""
Production Order Parser v2 — Real Customer Pattern Support.
Parses complex, multilingual customer order messages, extracts structured fields,
handles package multipliers and aliases, ignores customer reference prefixes (e.g. 991#, Order #:54),
gives explicit priority to CP PACK fields, isolates recovery codes, and classifies orders
while enforcing security logging (no passwords/credentials logged).
"""

import re
import logging
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)

# Default Alias Mapping (Configurable)
DEFAULT_PACKAGE_ALIASES: Dict[str, str] = {
    "5k": "5040",
    "5000": "5040",
    "5040": "5040",
    "10k": "10800",
    "10000": "10800",
    "10800": "10800",
    "2.4k": "2400",
    "2,4k": "2400",
    "2400": "2400",
    "4.8k": "4800",
    "4,8k": "4800",
    "4800": "4800",
    "9.6k": "9600",
    "9,6k": "9600",
    "9600": "9600",
    "12k": "12000",
    "12000": "12000",
    "24k": "24000",
    "24000": "24000",
    "10.8k": "10800",
    "10,8k": "10800",
    "5.04k": "5040",
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

# Customer Reference / Order ID Prefix Regex (e.g. "Order #:54", "Order #54", "Order: #54", "991#", "#54")
CUSTOMER_REF_REGEX = re.compile(
    r'\border\s*#?\s*[:=\-]?\s*#?\s*(\d+)\b|^\s*#?(\d+)#',
    re.IGNORECASE | re.MULTILINE
)

# Explicit CP PACK Header Regex (e.g. "CP PACK: 12.000", "CP PACK : 4.800")
CP_PACK_REGEX = re.compile(
    r'\b(?:cp\s*pack|cp\s*package|pack\s*cp)\s*[:=\.\-]?\s*(?P<val>[^\n\r]+)',
    re.IGNORECASE
)

# Recovery Codes Header Regex (e.g. "Codes:", "Codes: (solo FB)", "Códigos:", "Recovery Codes:")
RECOVERY_HEADER_REGEX = re.compile(
    r'^\s*(?:codes?|código|códigos|codigo|codigos|recovery\s*codes?|backup\s*codes?)\b',
    re.IGNORECASE
)

# Recovery Code Pattern (8 digits or 4+4 digits)
RECOVERY_CODE_PATTERN = re.compile(
    r'^\s*(\d{6,12}|\d{4}\s*\d{4})\s*$'
)


def get_dynamic_package_prices() -> Dict[str, float]:
    """Dynamically loads package prices from utils.PACKAGE_PRICES with fallback."""
    fallback = {
        "108000": 563.0, "96000": 503.0, "72000": 375.0, "55200": 291.0,
        "48000": 254.0, "43200": 229.0, "38400": 211.0, "24000": 132.0,
        "21600": 119.0, "19200": 109.0, "16800": 95.0, "14400": 82.0,
        "12000": 69.0, "10800": 64.0, "9600": 55.0, "7200": 42.0,
        "5040": 33.0, "4800": 29.0, "2400": 16.5, "880": 8.0,
        "420": 4.5, "80": 1.0
    }
    try:
        from utils import PACKAGE_PRICES, TEST_PACKAGE_PRICES
        prices = dict(PACKAGE_PRICES)
        if not prices:
            prices = dict(TEST_PACKAGE_PRICES)
        if not prices:
            prices = fallback
        return prices
    except Exception:
        return fallback


def normalize_package_alias(pkg_name: Optional[str], alias_map: Optional[Dict[str, str]] = None) -> str:
    """
    Normalizes package alias to canonical package string.
    Case-insensitive, e.g.:
    "5k" -> "5040"
    "5000" -> "5040"
    "12.000" -> "12000"
    "4.800" -> "4800"
    """
    if not pkg_name:
        return ""
    pkg_str = str(pkg_name).strip()

    # Strip dots/commas from thousands formatted numbers (e.g. 12.000 -> 12000, 4.800 -> 4800)
    if re.match(r'^\d{1,3}[.,]\d{3}$', pkg_str):
        pkg_str = re.sub(r'[.,]', '', pkg_str)

    pkg_lower = pkg_str.lower()
    aliases = dict(DEFAULT_PACKAGE_ALIASES)
    if alias_map:
        aliases.update(alias_map)

    return aliases.get(pkg_lower, pkg_str)


def extract_customer_ref_id(text: Optional[str]) -> Optional[str]:
    """Extracts customer reference ID prefix like '54', '991', etc."""
    if not text:
        return None
    match = CUSTOMER_REF_REGEX.search(text)
    if match:
        return match.group(1) or match.group(2)
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

    # 1. Customer Ref ID (e.g. "Order #:54", "991#", "#54")
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

    # 4. Line-by-line Classification (Username, Password, Recovery Codes, CP PACK, Package Candidate Lines)
    username = None
    password = None
    recovery_codes: List[str] = []
    explicit_cp_pack_candidates: List[str] = []
    package_candidate_lines: List[str] = []
    ignored_lines: Set[int] = set()

    in_recovery_sec = False
    next_line_is_username = False
    next_line_is_password = False
    next_line_is_email = False
    has_explicit_cp_pack = False

    for idx, line in enumerate(lines):
        l_strip = line.strip()
        l_lower = l_strip.lower()

        # Ignore Customer Ref Line like "Order #:54", "991#", "#54"
        if re.search(r'\border\s*#?\s*[:=\-]?\s*#?\s*\d+\b', l_lower, re.IGNORECASE) or CUSTOMER_REF_REGEX.match(l_strip):
            ignored_lines.add(idx)
            continue

        # Ignore standalone platform headers like "*facebook*", "*Activision*", "Activación"
        if l_strip.startswith("*") and l_strip.endswith("*"):
            ignored_lines.add(idx)
            continue
        if l_lower in ("facebook", "fb", "meta", "activision", "activacion", "activación", "activision id"):
            ignored_lines.add(idx)
            continue

        # Check Explicit CP PACK Header (Highest Priority for Package Extraction)
        cp_match = CP_PACK_REGEX.search(l_strip)
        if cp_match:
            cp_val = cp_match.group("val").strip()
            # Clean trailing details if present on line (e.g. "12.000 Mode: Safe")
            if "mode:" in cp_val.lower():
                cp_val = cp_val.lower().split("mode:", 1)[0].strip()
            explicit_cp_pack_candidates.append(cp_val)
            has_explicit_cp_pack = True
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

        # Check Recovery Code Section Header
        if RECOVERY_HEADER_REGEX.search(l_strip) or l_lower.startswith("codes") or l_lower.startswith("código") or l_lower.startswith("codigo"):
            in_recovery_sec = True
            ignored_lines.add(idx)
            continue

        if in_recovery_sec:
            # Check if line is a recovery code line
            if RECOVERY_CODE_PATTERN.match(l_strip) or re.match(r'^\d{6,12}$', l_strip):
                recovery_codes.append(l_strip)
                ignored_lines.add(idx)
                continue
            else:
                # End recovery code section if line is a new field header
                if any(h in l_lower for h in ("ign", "nick", "cp pack", "mode", "time", "email", "password", "clave")):
                    in_recovery_sec = False

        # Email Headers or Email address line
        if EMAIL_REGEX.search(l_strip):
            ignored_lines.add(idx)
            continue
        if re.match(r'^(?:login|email|e-mail|mail|correo\s*o\s*número\s*fb|correo\s*o\s*numero\s*fb|correo\s*o\s*número|correo\s*o\s*numero|correo\s*electrónico|correo\s*electronico|correo)\s*[:=\.\-]?$', l_lower) or any(l_lower.startswith(h) for h in ("login:", "email:", "e-mail:", "correo:", "mail:")):
            ignored_lines.add(idx)
            val = line.split(":", 1)[-1].strip() if ":" in line else ""
            if val and EMAIL_REGEX.search(val):
                email = EMAIL_REGEX.search(val).group(0).lower().rstrip(".,;!")
            elif not val or val.lower() in ("login", "email", "e-mail", "mail", "correo"):
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
        if re.match(r'^(?:nickname|nick\s*name|nick|apodo\s*en\s*el\s*juego|apodo|username|user\s*name|usuario|ign|nombre|ign\s*"nick")\s*[:=\.\-]?$', l_lower) or "ign" in l_lower or any(l_lower.startswith(h) for h in ("nickname:", "nick:", "nick name:", "ign:", "username:", "apodo:", "nombre:", "usuario:", "nickname ", "nick ")):
            ignored_lines.add(idx)
            val = line.split(":", 1)[-1].strip() if ":" in line else line
            if "ign" in l_lower and ":" in line:
                val = line.split(":", 1)[-1].strip()
            elif l_lower.startswith("nick ") and ":" not in line:
                val = line[5:].strip()
            elif l_lower.startswith("nickname ") and ":" not in line:
                val = line[9:].strip()

            header_kw = (
                "nickname", "nick", "nick name", "apodo", "apodo en el juego", "username", "user name",
                "usuario", "ign", "nombre", "ign \"nick\"", "nickname:", "nick:", "apodo:", "username:", "ign:"
            )

            if val and val.lower() not in header_kw:
                if "apodo en el juego:" in val.lower():
                    val = val.split(":", 1)[-1].strip()
                if val:
                    username = val
            else:
                next_line_is_username = True
            continue

        # Password Headers
        if re.match(r'^(?:password|pass|pwd|contraseña\s*de\s*fb|contrasena\s*de\s*fb|contraseña|contrasena|clave)\s*[:=\.\-]?$', l_lower) or any(l_lower.startswith(h) for h in ("password:", "pass:", "pwd:", "contraseña:", "contrasena:", "clave:")):
            ignored_lines.add(idx)
            val = line.split(":", 1)[-1].strip() if ":" in line else line
            header_pass_kw = (
                "password", "pass", "pwd", "contraseña", "contrasena", "clave",
                "password:", "pass:", "pwd:", "contraseña:", "contrasena:", "clave:",
                "contraseña de fb", "contrasena de fb"
            )
            if val and val.lower() not in header_pass_kw:
                password = val
            else:
                next_line_is_password = True
            continue

        # Check if line is a package candidate line
        has_pkg_token = False

        if re.search(r'\bcp\s*[:=\-]?\s*\d+', l_lower, re.IGNORECASE):
            has_pkg_token = True
        elif re.search(r'\b\d+(?:[.,]\d{3})*\s*(?:cp)?\b', l_lower, re.IGNORECASE):
            nums = re.findall(r'\b(?:\d{1,3}(?:[.,]\d{3})+|\d+)\b', l_lower)
            for n in nums:
                n_clean = re.sub(r'[.,]', '', n)
                n_norm = normalize_package_alias(n_clean, aliases)
                if n_norm in price_db or n_clean in price_db or (n_clean.isdigit() and int(n_clean) >= 400):
                    has_pkg_token = True
                    break

        if not has_pkg_token:
            for alias in aliases.keys():
                if re.search(r'\b' + re.escape(alias) + r'\b', l_lower, re.IGNORECASE):
                    has_pkg_token = True
                    break

        if not has_pkg_token:
            if re.search(r'\b\d+(?:cp)?[*xX×]\d+', l_lower) or re.search(r'^\d+\s*[\+\,\&\/]\s*\d+', l_lower):
                has_pkg_token = True

        if has_pkg_token:
            package_candidate_lines.append(l_strip)
            continue

    # Priority 1: Explicit CP PACK field takes absolute priority
    if has_explicit_cp_pack and explicit_cp_pack_candidates:
        package_candidate_lines = explicit_cp_pack_candidates
    elif not package_candidate_lines:
        # Priority 3: Fallback to non-ignored lines
        for idx, line in enumerate(lines):
            if idx not in ignored_lines and line.strip():
                package_candidate_lines.append(line.strip())

    # 5. Package Segment Extraction & Multiplier Expansion Engine
    packages: List[Dict[str, Any]] = []
    unknown_packages: List[str] = []
    known_total = 0.0
    has_unknown = False

    raw_pkg_text = "\n".join(package_candidate_lines)

    # Clean CP prefixes and suffixes e.g. "CP: 7200" -> "7200", "7200cp." -> "7200"
    raw_pkg_text = re.sub(r'\bcp\s*[:=\-]?\s*', '', raw_pkg_text, flags=re.IGNORECASE)
    raw_pkg_text = re.sub(r'(\d+)\s*cp\.?\b', r'\1', raw_pkg_text, flags=re.IGNORECASE)

    # Normalize thousands separators (12.000 -> 12000, 4.800 -> 4800, 7.200 -> 7200, 7,200 -> 7200)
    raw_pkg_text = re.sub(r'\b(\d{1,3})[.,](\d{3})\b', r'\1\2', raw_pkg_text)

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
        r'(?P<pkg_standalone>\d+)(?:cp)?[\.]?'
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
