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

# --- Free/paid shipping detection in unstructured page text ------------------
# Unconditional free-shipping promises across our markets. A nearby threshold
# qualifier (see ``_SHIPPING_THRESHOLD_RE``) disqualifies the promise, since a
# conditional "free over 99€" is not free for an arbitrary order.
_FREE_SHIPPING_RE = re.compile(
    r"(?:spedizione|consegna)\s+(?:gratuita|gratis|gratuito|inclusa|compresa)"  # IT
    r"|free\s+(?:shipping|delivery)"  # EN
    r"|kostenlose[rsmn]?\s+versand|gratis\s+versand|versandkostenfrei"  # DE
    r"|livraison\s+(?:gratuite|offerte)|frais\s+de\s+port\s+offerts"  # FR
    r"|env[íi]o\s+grat(?:is|uito)|gastos\s+de\s+env[íi]o\s+grat"  # ES
    r"|gratis\s+verzending|verzending\s+gratis"  # NL
    r"|frete\s+gr[áa]tis",  # PT
    re.IGNORECASE,
)
# A conditional/minimum-order threshold: a qualifier word *followed by an amount*
# (any market). The trailing number is what distinguishes a real threshold
# ("ab 25€") from an innocent phrase ("ab Lager" — from stock), so a bare
# qualifier never disqualifies a free-shipping promise on its own.
_SHIPPING_THRESHOLD_RE = re.compile(
    r"\b(?:sopra|oltre|superiore|superiori|a\s+partire(?:\s+da)?|min\.?|from|above|over"
    r"|ab|[uü]ber|desde|d[eè]s|[àa]\s+partir(?:\s+de)?|m[áa]s\s+de|superior)\b"
    r"\s*(?:a\s+|di\s+|de\s+)?[€$£]?\s*\d"
    # Italian/Portuguese "da €25" / "da 30€" minimum-order phrasing. The currency
    # symbol keeps this off innocent uses of "da" ("da magazzino", "da 5 stelle").
    r"|\bda\s*[\d.,]*\s*[€$£]",
    re.IGNORECASE,
)
# Shipping/delivery nouns to anchor a paid-amount search on (word-bounded, so
# "shipping" won't match inside "dropshipping"). "porto" is deliberately excluded:
# it is also a city/word ("Porto", "porto" = harbour) and too false-positive-prone.
_SHIPPING_TERM = (
    r"spedizione|consegna|shipping|delivery|versand|livraison|frais\s+de\s+port"
    r"|env[íi]o|gastos\s+de\s+env[íi]o|verzending|frete|portes"
)
# A currency-tagged money token: at most 3 integer digits (shipping is small) and,
# via the trailing negative lookahead on the caller, never a fragment of a longer
# number — so "€1234,00" (a product price) does not read as "€123".
_MONEY = r"(?:€|\$|£)\s?\d{1,3}(?:[.,]\d{2})?|\d{1,3}(?:[.,]\d{2})?\s?(?:€|\$|£)"
# A paid shipping cost is a shipping noun followed — with *only separators*, never
# an intervening word or number — by a money token. The tight gap is the precision
# guard: a product price elsewhere in the sentence (or in an HTML attribute like
# ``data-type="shipping">€239``) is never adjacent enough to be misread as shipping.
_PAID_SHIPPING_RE = re.compile(
    r"\b(?:" + _SHIPPING_TERM + r")\b[\s:–—-]{0,4}(" + _MONEY + r")(?!\d)(?![.,]\d)",
    re.IGNORECASE,
)

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


def _promises_free_shipping(text: str) -> bool:
    """True when the text carries an *unconditional* free-shipping promise."""
    for match in _FREE_SHIPPING_RE.finditer(text):
        window = text[max(0, match.start() - 20) : match.end() + 45]
        if not _SHIPPING_THRESHOLD_RE.search(window):
            return True
    return False


def _paid_shipping_from_text(text: str, market: Market) -> Decimal | None:
    """Read the cheapest paid shipping amount stated next to a shipping keyword.

    A page may list several tiers ("Spedizione: 9,99€ ... Consegna: 4,99€"); the
    lowest is the floor a buyer can actually select, so it is the one that matters
    for a landed-price comparison. Only amounts tightly adjacent to a shipping
    keyword count; a method-labelled variant ("Spedizione standard 4,99€") is left
    to JSON-LD/meta/AI rather than guessed. Returns ``None`` when none is found.
    """
    amounts: list[Decimal] = []
    for match in _PAID_SHIPPING_RE.finditer(text):
        # The currency arg is cosmetic: normalize_price strips the symbol and
        # parses by market, so a "$"/"£" token is handled the same as "€".
        try:
            amounts.append(normalize_price(match.group(1), "EUR", market))
        except NormalizationError:
            continue
    return min(amounts) if amounts else None


def extract_shipping_from_text(text: str, market: Market) -> Decimal | None:
    """Extract a shipping cost from unstructured page text (free pass, no tokens).

    Returns ``Decimal("0")`` for an unconditional free-shipping promise, a
    positive ``Decimal`` for a paid amount stated next to a shipping keyword, or
    ``None`` when shipping is unknown or only conditionally free. Precision is
    favoured over recall: an ambiguous or threshold-gated case yields ``None``
    rather than a guessed number that would poison the landed price.

    Args:
        text: Visible page text (or HTML) to scan.
        market: Market whose number formatting to apply to a paid amount.

    Returns:
        The shipping cost as a ``Decimal``, or ``None`` if unknown.
    """
    if not text:
        return None
    if _promises_free_shipping(text):
        return Decimal("0")
    return _paid_shipping_from_text(text, market)


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
