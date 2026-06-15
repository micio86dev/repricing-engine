"""Text normalization: titles and brand names.

These helpers produce comparable, lower-cased strings so fuzzy and exact
comparisons are not defeated by casing, punctuation, or trivial variations.
"""

import re
import unicodedata

# Whole-word abbreviation expansions applied to titles.
ABBREVIATIONS: dict[str, str] = {
    "&": "and",
    "w/": "with",
    "pcs": "pieces",
    "pkg": "package",
    "qty": "quantity",
}

# Canonical brand names keyed by common variations (all lower-cased).
BRAND_ALIASES: dict[str, str] = {
    "hewlett-packard": "hp",
    "hewlett packard": "hp",
    "hp inc": "hp",
    "samsung electronics": "samsung",
    "sony corporation": "sony",
    "apple inc": "apple",
}

# Brands we recognise inside a free-text title (canonical, lower-cased).
KNOWN_BRANDS: frozenset[str] = frozenset(
    {
        "apple",
        "samsung",
        "sony",
        "hp",
        "dell",
        "lenovo",
        "logitech",
        "bosch",
        "philips",
        "lg",
        "asus",
        "acer",
        "canon",
        "nikon",
    }
)

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")


def _strip_accents(value: str) -> str:
    """Remove diacritics so "café" and "cafe" compare equal."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_title(title: str) -> str:
    """Lower-case, de-accent, expand abbreviations and strip noise from a title.

    Args:
        title: Raw product title.

    Returns:
        A normalized, whitespace-collapsed title. Empty input yields ``""``.
    """
    if not title:
        return ""

    text = _strip_accents(title).lower().strip()

    # Expand abbreviations on whole-word boundaries before stripping symbols.
    for abbr, full in ABBREVIATIONS.items():
        pattern = rf"(?<!\w){re.escape(abbr)}(?!\w)"
        text = re.sub(pattern, f" {full} ", text)

    text = _NON_ALNUM_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalize_brand(brand: str | None) -> str | None:
    """Return a canonical, lower-cased brand string.

    Maps known variations (e.g. "Hewlett-Packard" -> "hp") via ``BRAND_ALIASES``.

    Args:
        brand: Raw brand string, possibly ``None``.

    Returns:
        Canonical brand, or ``None`` if the input was empty/``None``.
    """
    if not brand or not brand.strip():
        return None

    cleaned = _WHITESPACE_RE.sub(" ", _strip_accents(brand).lower().strip())
    return BRAND_ALIASES.get(cleaned, cleaned)


def extract_brand(title: str) -> str | None:
    """Best-effort extraction of a known brand from a free-text title.

    Args:
        title: Raw product title.

    Returns:
        The canonical brand if a known brand token is found, else ``None``.
    """
    normalized = normalize_title(title)
    if not normalized:
        return None

    tokens = normalized.split(" ")
    for token in tokens:
        canonical = BRAND_ALIASES.get(token, token)
        if canonical in KNOWN_BRANDS:
            return canonical
    return None
