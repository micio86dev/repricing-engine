"""Identifier normalization & validation: EAN, GTIN, SKU.

EAN/GTIN are validated with the standard GS1 mod-10 check digit so that
malformed identifiers are rejected (returned as ``None``) instead of silently
producing wrong matches.
"""

import re

_NON_DIGIT_RE = re.compile(r"\D")
_VALID_GTIN_LENGTHS: frozenset[int] = frozenset({8, 12, 13, 14})


def _digits_only(value: str | int | None) -> str:
    """Return only the digit characters of ``value`` ("" if none/None)."""
    if value is None:
        return ""
    return _NON_DIGIT_RE.sub("", str(value))


def _checksum_valid(digits: str) -> bool:
    """Validate a GS1 identifier's mod-10 check digit.

    Works for any GTIN length (8/12/13/14): weights alternate 3 and 1 from the
    rightmost payload digit.
    """
    payload, check = digits[:-1], digits[-1]
    total = 0
    # Weights alternate 3,1,3,1... starting from the rightmost payload digit.
    for position, char in enumerate(reversed(payload)):
        weight = 3 if position % 2 == 0 else 1
        total += int(char) * weight
    computed_check = (10 - (total % 10)) % 10
    return computed_check == int(check)


def normalize_ean(ean: str | int | None) -> str | None:
    """Validate and normalize an EAN-8 / EAN-13 code.

    A 12-digit UPC-A is zero-padded to 13 (its EAN-13 form).

    Args:
        ean: Raw EAN value (string or int), possibly ``None``.

    Returns:
        The canonical EAN digit string, or ``None`` if invalid.
    """
    digits = _digits_only(ean)
    if len(digits) == 12:  # UPC-A -> EAN-13
        digits = f"0{digits}"
    if len(digits) not in (8, 13):
        return None
    return digits if _checksum_valid(digits) else None


def normalize_gtin(gtin: str | int | None) -> str | None:
    """Validate and normalize a GTIN-8/12/13/14 code.

    Args:
        gtin: Raw GTIN value (string or int), possibly ``None``.

    Returns:
        The canonical GTIN digit string, or ``None`` if invalid.
    """
    digits = _digits_only(gtin)
    if len(digits) not in _VALID_GTIN_LENGTHS:
        return None
    return digits if _checksum_valid(digits) else None


def normalize_sku(sku: str | int | None) -> str:
    """Normalize a SKU: upper-case, drop all internal whitespace.

    Args:
        sku: Raw SKU value, possibly ``None``.

    Returns:
        The normalized SKU ("" for empty/``None`` input).
    """
    if sku is None:
        return ""
    return re.sub(r"\s+", "", str(sku)).upper()
