"""Shared pytest fixtures and lightweight test doubles."""

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from repricing_engine.config import Settings
from repricing_engine.models.enums import Market
from repricing_engine.models.product import CatalogProduct, CompetitorProduct

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class FakeEncoder:
    """Deterministic, offline stand-in for a sentence-transformer encoder.

    Builds a bag-of-words vector with a stable word -> index mapping, so texts
    that share words have a high cosine similarity (no model download).
    """

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def encode(self, sentences: list[str]) -> np.ndarray:
        vectors = np.zeros((len(sentences), self.dim), dtype=np.float64)
        for row, sentence in enumerate(sentences):
            for word in sentence.split():
                index = sum(ord(char) for char in word) % self.dim
                vectors[row, index] += 1.0
        return vectors


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, payload: dict | None, error: Exception | None) -> None:
        self._payload = payload
        self._error = error
        self.call_count = 0

    def create(self, **_kwargs: object) -> _FakeCompletion:
        self.call_count += 1
        if self._error is not None:
            raise self._error
        return _FakeCompletion(json.dumps(self._payload))


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class FakeGroqClient:
    """Minimal Groq-client double exposing ``chat.completions.create``."""

    def __init__(self, payload: dict | None = None, error: Exception | None = None) -> None:
        self.completions = _FakeCompletions(payload, error)
        self.chat = _FakeChat(self.completions)


class _PredicateCompletions:
    def __init__(self, decide: Callable[[str], bool]) -> None:
        self._decide = decide
        self.call_count = 0

    def create(self, **kwargs: object) -> _FakeCompletion:
        self.call_count += 1
        messages = kwargs["messages"]
        prompt = messages[-1]["content"]  # type: ignore[index]
        is_match = self._decide(prompt)
        payload = {
            "is_match": is_match,
            "confidence_adjustment": 0.0 if is_match else -0.3,
            "reason": "stubbed verdict",
        }
        return _FakeCompletion(json.dumps(payload))


class PredicateGroqClient:
    """Groq double whose verdict depends on the prompt content.

    ``decide(prompt) -> bool`` decides whether the candidate is a real match;
    rejected candidates receive a ``-0.3`` confidence adjustment. Lets tests
    simulate an AI gate that rejects, e.g., an accessory that happens to share a
    product's EAN, without any network call.
    """

    def __init__(self, decide: Callable[[str], bool]) -> None:
        self.completions = _PredicateCompletions(decide)
        self.chat = _FakeChat(self.completions)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def sample_catalog_path() -> Path:
    return FIXTURES_DIR / "sample_catalog.csv"


@pytest.fixture
def sample_oxylabs_path() -> Path:
    return FIXTURES_DIR / "sample_oxylabs_export.csv"


@pytest.fixture
def expected_output_path() -> Path:
    return FIXTURES_DIR / "expected_output.csv"


@pytest.fixture
def dirty_catalog_path() -> Path:
    return FIXTURES_DIR / "dirty_catalog.csv"


@pytest.fixture
def dirty_oxylabs_path() -> Path:
    return FIXTURES_DIR / "dirty_oxylabs_export.csv"


@pytest.fixture
def settings() -> Settings:
    """Settings with no Groq key (AI gate disabled by default)."""
    return Settings(groq_api_key=None, log_level="WARNING")


@pytest.fixture
def fake_encoder() -> FakeEncoder:
    return FakeEncoder()


@pytest.fixture
def catalog_product() -> CatalogProduct:
    return CatalogProduct(
        sku="APL-IPH13-128",
        ean="4006381333931",
        brand="Apple",
        title="Apple iPhone 13 128GB Blue",
        category="Smartphones",
        market=Market.IT,
    )


@pytest.fixture
def competitor_product() -> CompetitorProduct:
    return CompetitorProduct(
        source="oxylabs",
        source_id="OXY-001",
        title="Apple iPhone 13 128 GB Blue",
        price=Decimal("789.00"),
        currency="EUR",
        url="https://shop.example.com/p/oxy-001",
        market=Market.IT,
        ean="4006381333931",
        sku="APL-IPH13-128",
        brand="Apple",
        shipping_cost=Decimal("0.00"),
    )
