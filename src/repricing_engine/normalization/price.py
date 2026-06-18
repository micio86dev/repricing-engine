"""Price and shipping normalization.

Different markets format numbers differently (``1.299,90`` in IT/DE vs
``1,299.90`` in UK/US). These helpers return a precise :class:`~decimal.Decimal`
regardless of the source formatting.
"""

import re
from decimal import Decimal, InvalidOperation

from repricing_engine.exceptions import NormalizationError
from repricing_engine.models.enums import Market

# Markets that use a comma as the decimal separator (and dot/space for thousands).
_COMMA_DECIMAL_MARKETS: frozenset[Market] = frozenset(
    {
        Market.IT,
        Market.DE,
        Market.FR,
        Market.ES,
        Market.NL,
        Market.PT,
        Market.BE,
        Market.AT,
    }
)

_CURRENCY_NOISE_RE = re.compile(r"[^\d.,\-]")
# Keys we consider to hold a shipping/delivery cost, by source then generic.
_SHIPPING_KEYS: dict[str, tuple[str, ...]] = {
    "oxylabs": ("shipping", "shipping_cost", "delivery", "delivery_cost"),
}
_GENERIC_SHIPPING_KEYS: tuple[str, ...] = (
    "shipping_cost",
    "shipping",
    "delivery_cost",
    "delivery",
)


def normalize_price(price: str | int | float | Decimal, currency: str, market: Market) -> Decimal:
    """Parse a price into a :class:`~decimal.Decimal` using market conventions.

    Args:
        price: Raw price (string with separators/symbols, or a number).
        currency: Currency code (used only to strip symbols); not validated.
        market: Market whose number formatting rules to apply.

    Returns:
        The parsed price as a ``Decimal``.

    Raises:
        NormalizationError: If the value cannot be parsed into a number.
    """
    if isinstance(price, Decimal):
        return price
    if isinstance(price, int | float):
        return Decimal(str(price))

    raw = (price or "").strip()
    if not raw:
        msg = f"Empty price value (currency={currency!r}, market={market})"
        raise NormalizationError(msg)

    cleaned = _CURRENCY_NOISE_RE.sub("", raw)

    if market in _COMMA_DECIMAL_MARKETS:
        # Comma is the decimal separator; dot is a thousands separator.
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        # Dot is the decimal separator; comma is a thousands separator.
        cleaned = cleaned.replace(",", "")

    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError) as exc:
        msg = f"Cannot parse price {price!r} for market {market}"
        raise NormalizationError(msg) from exc


def _parse_decimal_loose(value: str) -> Decimal | None:
    """Parse a number of unknown locale, inferring the decimal separator."""
    cleaned = _CURRENCY_NOISE_RE.sub("", value.strip())
    if not cleaned:
        return None

    has_comma = "," in cleaned
    has_dot = "." in cleaned
    if has_comma and has_dot:
        # The right-most separator is the decimal one.
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif has_comma:
        decimals = len(cleaned.rsplit(",", 1)[1])
        cleaned = cleaned.replace(",", "." if decimals <= 2 else "")
    elif has_dot:
        decimals = len(cleaned.rsplit(".", 1)[1])
        if decimals == 3:  # treat as a thousands separator
            cleaned = cleaned.replace(".", "")

    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def parse_price_loose(value: str | int | float | Decimal | None) -> Decimal | None:
    """Best-effort parse of a machine/loose-formatted number, inferring the locale.

    Use this for values that arrive in canonical or near-canonical form (JSON-LD,
    an LLM extraction, a structured API) rather than a known market's human text:
    ``789.0`` stays ``789.0`` (not ``7890``), ``789,00`` becomes ``789.00``, and
    ``1.299,90`` becomes ``1299.90``. Returns ``None`` when unparseable.

    Args:
        value: The raw value (number or string), possibly ``None``.

    Returns:
        The parsed :class:`~decimal.Decimal`, or ``None``.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))
    return _parse_decimal_loose(value)


def extract_shipping(raw_data: dict[str, str], source: str) -> Decimal | None:
    """Extract a shipping cost from a source's raw row, if present.

    Args:
        raw_data: The source-specific raw row (string values).
        source: The source name (selects known key names first).

    Returns:
        The shipping cost as a ``Decimal``, or ``None`` if absent/empty.
    """
    if not raw_data:
        return None

    lowered = {key.lower(): val for key, val in raw_data.items()}
    candidate_keys = _SHIPPING_KEYS.get(source.lower(), ()) + _GENERIC_SHIPPING_KEYS

    for key in candidate_keys:
        value = lowered.get(key)
        if value is None or str(value).strip() == "":
            continue
        return _parse_decimal_loose(str(value))
    return None
