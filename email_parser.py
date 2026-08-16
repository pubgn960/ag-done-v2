"""
Email, Order ID, and Package extraction module using regular expressions.
Parses, sanitizes, and normalizes email addresses, Order IDs, and package text from messages.
Includes extract_last_email helper for Loader caption email overrides.
"""

import re
import logging
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)

# Standard Regex pattern for detecting email addresses
EMAIL_REGEX = re.compile(
    r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
    re.IGNORECASE
)

# Regex pattern for extracting Order ID numbers (e.g. "Order ID: #10025", "Order #10025", "#10025", "Order ID 10025")
ORDER_ID_REGEX = re.compile(
    r'(?:order\s*id|order\s*#|order|#)\s*[:#\s]*(\d+)',
    re.IGNORECASE
)


def extract_email(text: Optional[str]) -> Optional[str]:
    """
    Extracts the first valid email address found in the provided text.
    Strips trailing punctuation and normalizes to lowercase.

    Args:
        text (str, optional): The input string to scan (message text or caption).

    Returns:
        Optional[str]: The normalized (lowercase, trimmed) email address if found, else None.
    """
    if not text:
        return None

    match = EMAIL_REGEX.search(text)
    if match:
        raw_email = match.group(0).strip()
        email = raw_email.rstrip(".,;!)]>").lower()
        logger.debug(f"Extracted email: '{email}' from text.")
        return email

    return None


def extract_last_email(text: Optional[str]) -> Optional[str]:
    """
    Scans text for all valid email addresses and returns the LAST valid email address found.
    Normalizes to lowercase and strips trailing punctuation.

    Args:
        text (str, optional): The input string to scan (loader caption/text).

    Returns:
        Optional[str]: The last normalized email address if found, else None.
    """
    if not text:
        return None

    matches = EMAIL_REGEX.findall(text)
    if not matches:
        return None

    last_raw = matches[-1].strip()
    email = last_raw.rstrip(".,;!)]>").lower()
    logger.debug(f"Extracted last email override: '{email}' from text.")
    return email


def extract_order_id(text: Optional[str]) -> Optional[int]:
    """
    Extracts an Order ID integer from text or message captions.

    Args:
        text (str, optional): Input text string.

    Returns:
        Optional[int]: Order ID integer if found, else None.
    """
    if not text:
        return None

    match = ORDER_ID_REGEX.search(text)
    if match:
        try:
            order_id = int(match.group(1))
            logger.debug(f"Extracted Order ID: {order_id} from text.")
            return order_id
        except ValueError:
            pass

    return None


def extract_order_section(text: Optional[str]) -> Optional[str]:
    """
    Locates and extracts ONLY the Order section text from a customer message.
    Looks for headers starting with: Order, Package, Packages, CP.
    Stops if another unrelated section header is encountered.

    Returns:
        Optional[str]: Extracted order text section, or None if no Order section is found.
    """
    if not text:
        return None

    OTHER_SECTION_HEADERS = (
        "email:", "mail:", "correo:", "correo electrónico:", "correo electronico:",
        "correo o número:", "correo o numero:", "correo o número fb:", "correo o numero fb:",
        "password:", "pass:", "pwd:", "contraseña:", "contrasena:", "clave:",
        "contraseña de fb:", "contrasena de fb:",
        "recovery:", "recovery code:", "recovery codes:", "backup codes:",
        "código:", "códigos:", "codigo:", "codigos:", "2fa:", "authenticator:",
        "phone:", "teléfono:", "telefono:", "celular:", "número:", "numero:",
        "uid:", "account id:", "nickname:", "username:", "nick:", "ign:", "usuario:", "nombre:",
        "platform:", "login:"
    )

    STANDALONE_HEADERS = (
        "order", "package", "packages", "cp", "order:", "package:", "packages:", "cp:",
        "códigos", "codigos", "código", "codigo", "contraseña", "contrasena", "clave",
        "correo", "email", "password", "pass", "pwd", "recovery", "nick", "ign", "usuario"
    )

    lines = text.splitlines()
    in_order_section = False
    extracted_lines = []

    for line in lines:
        line_strip = line.strip()
        line_lower = line_strip.lower()

        if not in_order_section:
            # Match explicit header like "Order:", "Package:", "Packages:", "CP:"
            if re.match(r'^(?:order|packages|package|cp)\s*[:=\-]', line_lower):
                in_order_section = True
                val_after = line_strip.split(":", 1)[-1].split("=", 1)[-1].strip()
                if val_after and val_after.lower() not in ("order", "package", "packages", "cp"):
                    extracted_lines.append(val_after)
                continue
            # Standalone line header like "Order", "Package", "Packages", "CP"
            elif line_lower in ("order", "package", "packages", "cp", "order:", "package:", "packages:", "cp:"):
                in_order_section = True
                continue
            # Package line containing catalog numbers or aliases when not under an unrelated header
            elif not any(line_lower.startswith(h) for h in OTHER_SECTION_HEADERS) and line_lower not in STANDALONE_HEADERS and not EMAIL_REGEX.search(line_strip):
                if re.search(r'\b(?:108000|96000|72000|48000|43200|38400|24000|21600|19200|16800|14400|12000|10800|9600|7200|5040|2400|880|420|80|5k|10k|2\.4k|2,4k)\b', line_lower) or re.search(r'\b\d+(?:cp)?[*xX×]\d+', line_lower):
                    in_order_section = True
                    extracted_lines.append(line_strip)
                    continue
        else:
            if any(line_lower.startswith(h) for h in OTHER_SECTION_HEADERS) or line_lower in STANDALONE_HEADERS or EMAIL_REGEX.search(line_strip):
                break
            if line_strip:
                extracted_lines.append(line_strip)

    if extracted_lines:
        return "\n".join(extracted_lines)

    return None


def extract_package(text: Optional[str]) -> str:
    """
    Extracts package description from customer message by isolating the Order section.

    Args:
        text (str, optional): Input order message text.

    Returns:
        str: Package text description or default fallback.
    """
    if not text:
        return "Standard Package"

    sec = extract_order_section(text)
    if sec:
        return sec.replace("\n", " | ").strip()

    return "Standard Package"
