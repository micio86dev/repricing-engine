"""Value-added-tax (VAT) normalization.

Competitor prices are compared **ex-VAT** so shops in different VAT regimes are
put on the same footing: a VAT-included IT B2C listing and a VAT-excluded B2B one
otherwise look cheaper/dearer purely by tax treatment. This module holds the
per-market standard rates and the split that turns a raw price into its
``(ex_vat, vat_amount, incl_vat)`` components.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from repricing_engine.models.enums import Market

# Standard VAT rates per supported market, as fractions (not percentages).
VAT_RATES: dict[Market, Decimal] = {
    Market.IT: Decimal("0.22"),
    Market.DE: Decimal("0.19"),
    Market.FR: Decimal("0.20"),
    Market.ES: Decimal("0.21"),
    Market.NL: Decimal("0.21"),
    Market.PT: Decimal("0.23"),
    Market.BE: Decimal("0.21"),
    Market.AT: Decimal("0.20"),
    Market.UK: Decimal("0.20"),
    Market.US: Decimal("0.00"),
}

# The catalog is Italian, so an unknown market defaults to the IT standard rate.
_DEFAULT_RATE: Decimal = Decimal("0.22")
_CENTS = Decimal("0.01")


def vat_rate_for(market: Market) -> Decimal:
    """Return the standard VAT rate for a market (IT 22% for an unknown one).

    Args:
        market: The market whose VAT rate to look up.

    Returns:
        The VAT rate as a ``Decimal`` fraction (e.g. ``Decimal("0.22")``).
    """
    return VAT_RATES.get(market, _DEFAULT_RATE)


def _q2(value: Decimal) -> Decimal:
    """Quantize a money value to 2 decimals, rounding half up."""
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def split_vat(price: Decimal, *, included: bool, rate: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """Split a price into its ``(ex_vat, vat_amount, incl_vat)`` components.

    The three components are derived from the full-precision ex-VAT base and then
    each quantized independently to 2 decimals (half up), so they stay internally
    consistent: a VAT-included ``211.75`` at 22% yields ``173.57 + 38.18 =
    211.75`` rather than an off-by-a-cent ``211.76``.

    Args:
        price: The raw price to split.
        included: Whether ``price`` already includes VAT.
        rate: The VAT rate as a fraction (e.g. ``Decimal("0.22")``).

    Returns:
        A ``(ex_vat, vat_amount, incl_vat)`` tuple, each quantized to 2 decimals.
    """
    ex_vat = price / (Decimal("1") + rate) if included else price
    vat_amount = ex_vat * rate
    incl_vat = ex_vat + vat_amount
    return _q2(ex_vat), _q2(vat_amount), _q2(incl_vat)
