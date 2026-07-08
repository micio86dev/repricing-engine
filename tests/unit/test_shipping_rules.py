"""Unit tests for the curated per-retailer free-shipping allow-list."""

import pytest

from repricing_engine.sources.shipping_rules import FREE_IT_SHIPPING_DOMAINS, free_shipping_for


class TestFreeShippingRules:
    @pytest.mark.parametrize("domain", sorted(FREE_IT_SHIPPING_DOMAINS))
    def test_seeded_domains_are_free(self, domain: str):
        assert free_shipping_for(domain) is True

    def test_normalizes_www_and_case(self):
        assert free_shipping_for("WWW.Idrocrimart.IT") is True
        assert free_shipping_for("  climaprice.it  ") is True

    @pytest.mark.parametrize(
        "domain",
        ["amazon.it", "unknown-shop.it", "idrocrimart.com", "", "sub.idrocrimart.it"],
    )
    def test_unlisted_domains_are_not_free(self, domain: str):
        assert free_shipping_for(domain) is False

    def test_seed_set_is_exactly_the_confirmed_five(self):
        assert set(FREE_IT_SHIPPING_DOMAINS) == {
            "idrocrimart.it",
            "complementiclimatici.it",
            "sovatem.it",
            "climaprice.it",
            "elmaxweb.it",
        }
