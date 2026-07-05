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

    # Source fetching (opt-in via the CLI ``--fetch`` flag)
    searxng_base_url: str | None = Field(default=None)  # e.g. http://localhost:8888
    searxng_enabled: bool = Field(default=True)  # only used when a base URL is set
    searxng_max_pages: int = Field(default=3, ge=1)  # result pages fetched per query
    searxng_max_concurrency: int = Field(default=4, ge=1)  # in-flight requests (avoid CAPTCHAs)
    searxng_rate_limit_seconds: float = Field(default=0.5, ge=0.0)  # spacing between requests
    trovaprezzi_enabled: bool = Field(default=True)  # IT-only public comparison pages
    trovaprezzi_rate_limit_seconds: float = Field(
        default=1.5, ge=0.0
    )  # rate limiting to respect site limits
    duckduckgo_enabled: bool = Field(default=True)  # free HTML-endpoint SERP fallback
    duckduckgo_rate_limit_seconds: float = Field(
        default=1.0, ge=0.0
    )  # rate limiting with retry logic

    # Real-price reading: when ``--fetch`` is used, visit each discovered offer's
    # page to read its actual price/stock (reuses the PDP cascade). Off => URLs only.
    fetch_read_prices: bool = Field(default=True)

    # PDP verification (opt-in via the CLI ``--verify-pdp`` flag)
    pdp_fetch_timeout_seconds: float = Field(default=20.0, gt=0.0)  # increased for slow connections
    pdp_playwright_enabled: bool = Field(default=False)  # JS-rendering fallback (optional extra)
    pdp_max_concurrent_fetches: int = Field(default=8, ge=1)
    pdp_rate_limit_per_domain_seconds: float = Field(default=1.0, ge=0.0)
    pdp_ai_extraction_enabled: bool = Field(default=True)  # last-resort Groq extraction
    pdp_ai_confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0)

    # ------------------------------------------------------------------ #
    # Paid / at-scale discovery providers (opt-in). All disabled and empty
    # by default — the free SearXNG + DuckDuckGo path keeps working untouched.
    # See docs/INTEGRATIONS.md for how to register each service and get keys.
    # ------------------------------------------------------------------ #

    # DataForSEO — recommended primary at scale (Google Shopping "Merchant" API
    # returns priced offers directly; SERP API returns URLs). HTTP Basic auth.
    dataforseo_enabled: bool = Field(default=False)
    dataforseo_login: str | None = Field(default=None)  # account email
    dataforseo_password: str | None = Field(default=None)  # API password (not the UI password)
    dataforseo_mode: str = Field(default="shopping")  # "shopping" (priced) | "serp" (urls)

    # Serper.dev — cheapest Google SERP (URLs); good for the specialist long tail.
    serper_enabled: bool = Field(default=False)
    serper_api_key: str | None = Field(default=None)

    # Oxylabs — SERP + E-commerce Scraper API (sub-user credentials).
    oxylabs_enabled: bool = Field(default=False)
    oxylabs_username: str | None = Field(default=None)
    oxylabs_password: str | None = Field(default=None)

    # SearchApi.io / SerpApi — SERP/Shopping alternatives (single API key each).
    searchapi_enabled: bool = Field(default=False)
    searchapi_api_key: str | None = Field(default=None)
    serpapi_enabled: bool = Field(default=False)
    serpapi_api_key: str | None = Field(default=None)

    # ScraperAPI — proxy/renderer for fetching PDPs at scale (JS/Cloudflare).
    scraperapi_enabled: bool = Field(default=False)
    scraperapi_api_key: str | None = Field(default=None)

    # eBay Browse API (official, free tier) — new-item offers.
    ebay_enabled: bool = Field(default=False)
    ebay_client_id: str | None = Field(default=None)  # "App ID"
    ebay_client_secret: str | None = Field(default=None)  # "Cert ID"

    # Keepa — Amazon offers/price history.
    keepa_enabled: bool = Field(default=False)
    keepa_api_key: str | None = Field(default=None)

    # RSS / XML merchant feeds (comma-separated URLs) — e.g. Google Merchant or
    # comparison-site partner feeds you have access to.
    feed_urls: str | None = Field(default=None)

    # Logging
    log_level: str = Field(default="INFO")
