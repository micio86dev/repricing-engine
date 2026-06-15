"""Application settings, loaded from environment / ``.env``."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the repricing engine.

    All values are overridable via environment variables or a ``.env`` file.
    Thresholds drive the matching cascade; nothing is hard-coded in the layers.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # AI Quality Gate
    groq_api_key: str | None = Field(default=None)
    groq_model: str = Field(default="llama-3.3-70b-versatile")

    # Matching thresholds
    exact_id_min_confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    sku_brand_min_confidence: float = Field(default=0.80, ge=0.0, le=1.0)
    semantic_min_confidence: float = Field(default=0.60, ge=0.0, le=1.0)
    ai_gate_min_confidence: float = Field(default=0.75, ge=0.0, le=1.0)

    # Embedding model (runs locally on CPU)
    embedding_model: str = Field(default="sentence-transformers/all-MiniLM-L6-v2")

    # Logging
    log_level: str = Field(default="INFO")
