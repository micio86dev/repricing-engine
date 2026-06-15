"""Enumerations used across the repricing engine."""

from enum import StrEnum


class Market(StrEnum):
    """Supported marketplaces / countries (ISO 3166-1 alpha-2 codes)."""

    IT = "IT"
    DE = "DE"
    FR = "FR"
    ES = "ES"
    UK = "UK"
    US = "US"
    NL = "NL"
    PT = "PT"
    BE = "BE"
    AT = "AT"


class MatchMethod(StrEnum):
    """How a candidate match was produced."""

    EXACT_EAN = "EXACT_EAN"
    EXACT_GTIN = "EXACT_GTIN"
    SKU_BRAND = "SKU_BRAND"
    SEMANTIC = "SEMANTIC"
    AI_VERIFIED = "AI_VERIFIED"


class ConfidenceLevel(StrEnum):
    """Bucketed confidence level for a match."""

    HIGH = "HIGH"  # > 0.90
    MEDIUM = "MEDIUM"  # 0.70 - 0.90
    LOW = "LOW"  # < 0.70


class Availability(StrEnum):
    """Stock availability of a competitor offer."""

    IN_STOCK = "IN_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    PREORDER = "PREORDER"
    UNKNOWN = "UNKNOWN"
