# CLAUDE.md — Repricing Engine

## Project Overview

Intelligent repricing/price monitoring engine for e-commerce. Multi-layer product matching with AI quality gate. Python 3.12+, CLI-based MVP.

## Commands

- `uv sync` — Install all dependencies
- `docker compose up -d` — Start the local SearXNG (primary `--fetch` source; JSON API on :8888)
- `uv run repricing match --catalog <file> --output <file>` — **Default = full online pipeline**: discovers competitors (`--fetch`), reads each offer's real price + shipping from its PDP, keeps only proven-same-product offers (`--strict-match`), and drops any offer lacking a known price **and** shipping cost (`--require-shipping`). Stock qty/status captured when present, never required.
- `uv run repricing match --catalog <file> --competitors <file> --no-fetch --no-require-shipping` — Offline **CSV-only** run (the legacy path; loosen the default filters explicitly).
- `uv run repricing match --catalog <file> --verify-pdp` — Also force full page verification (beyond the default price/shipping read).
- Opt-outs: `--no-fetch` (CSV-only), `--no-strict-match` (allow title-similarity matches), `--no-require-shipping` (keep price-less/shipping-less offers). Set `FETCH_READ_PRICES=false` for URLs-only.
- `uv sync --extra playwright && uv run playwright install chromium` — JS/Cloudflare-page fallback (enable with `PDP_PLAYWRIGHT_ENABLED=true`)
- `uv run pytest` — Run all tests
- `uv run pytest tests/unit/` — Run unit tests only
- `uv run pytest -k "test_exact_id"` — Run specific test pattern
- `uv run ruff check .` — Lint
- `uv run ruff format .` — Format
- `uv run pre-commit run --all-files` — Pre-commit hooks

## Architecture

### Matching Pipeline (cascade with confidence scoring)

```
Layer 1: Exact ID Match (EAN/GTIN)    → confidence 0.95+
   ↓ (always continues to cross-validate)
Layer 2: SKU + Brand Fuzzy Match       → confidence 0.80+
   ↓ (if no high-confidence match yet)
Layer 3: Semantic Match (embeddings)   → confidence 0.60+
   ↓ (all candidates above threshold)
Layer 4: AI Quality Gate (Groq/Llama)  → confirms or rejects each match
```

Key principle: **Never trust a single identifier.** Even if EAN matches, cross-validate brand + category. Layer 4 (AI) is the final arbiter for ambiguous cases.

### Source Fetching (opt-in, `--fetch`) — `src/repricing_engine/sources/`

Discovers *more* competitor offers than a static CSV holds, via free providers run
concurrently then deduplicated:

- **SearXNG** (primary, free, self-hosted metasearch — `SEARXNG_BASE_URL`). Bundled via
  `docker-compose.yml` + `searxng/settings.yml` (JSON API enabled, limiter off for localhost).
  It aggregates Google/Bing/Brave/Qwant/... server-side, so it reaches Cloudflare-protected
  retailers a plain scraper can't. Each product is queried with several strategies — quoted SKU,
  a *valid* EAN/GTIN, brand+title, title — over the first `SEARXNG_MAX_PAGES` result pages.
- **TrovaPrezzi** (IT-only public price-comparison pages; bs4, AI fallback; often Cloudflare-blocked
  on plain httpx — SearXNG surfaces its URLs instead, then PDP reads the price).
- **DuckDuckGo** (free HTML-endpoint SERP fallback, per-instance rate-limited; brand+title and
  quoted-SKU queries).
- **CSV file** (`CsvFileProvider` wrapping `OxyLabsIngestor`).

`SourceFetcher` orders providers cheapest-first (`cost_tier`), runs the available ones
with `asyncio.gather` (each guarded so one failure can't sink the batch). It then **enriches**
raw results via `sources/enrichment.py` — stamping the catalog SKU/EAN/brand onto a result *only
when that value literally appears* in its title/snippet/URL, so the deterministic `exact_id` /
`sku_brand` layers (not just semantic) can confirm fetched offers with high confidence. Finally it
maps to `CompetitorProduct` and dedupes by normalized URL then one-per-domain (cheapest).

Because fetched offers must still clear matching, the sync `match_one` takes `full_landscape=True`
on the `--fetch` path: the semantic layer keeps scoring every competitor that isn't *itself*
already confidently matched, so a strong match elsewhere never silently truncates the landscape.
The default CSV-only path (`full_landscape=False`) preserves the legacy global semantic-skip
byte-for-byte.

**Prices on `--fetch`:** SERP results are URLs (mostly price-less), so `--fetch` runs each matched
offer through the same `PdpVerifier` cascade to read the real price/stock (controlled by
`FETCH_READ_PRICES`, default on). This reuses the PDP code below with no duplication.

### PDP Verification (opt-in, `--verify-pdp`) — `src/repricing_engine/pdp/`

After matching, visits each candidate's product page to turn the *heuristic* `PDP·…`
labels into facts and read the real price/shipping/stock. Cheapest signal first:

```
PageFetcher (httpx → optional Playwright)  → FetchResult
   ↓
IdentifierFinder: regex(EAN/GTIN/SKU) + meta + JSON-LD   (FREE — confirms + often prices)
   ↓ (only if free pass found neither an identifier nor a price, and confidence < threshold)
AiExtractor (Groq/Llama)                                 (LAST RESORT — tokens cost money)
```

`PdpVerifier` writes the verified offer back through `CompetitorProduct.model_copy` and
annotates `MatchCandidate.match_details` (`pdp_verified`, `confirmation_method`,
`extraction_method`, `source_provider`). Fetch/parse failures degrade to the original data
with `pdp_verified=False` (zero data loss). Capped at `--max-pdp-per-product` (default 15).

### Data Flow

```
catalog.csv → CatalogIngestor → [CatalogProduct]
competitors.csv → OxyLabsIngestor → csv_pool ─┐
                                              │  (--fetch) SourceFetcher → fetched competitors
                                              ↓
EnhancedPipeline (async) ── per product ──→ MatchingPipeline.match_one (Layer 1→4, sync)
                                              ↓
                                  (--verify-pdp) PdpVerifier  → verified [MatchCandidate]
                                              ↓
                    (--require-shipping) keep_complete_offers → complete-only [MatchResult]
                                              ↓
                              [MatchResult] → LandscapeCsvWriter → output.csv (+_summary +_stats)
```

The online pipeline (`--fetch`, on by default) runs `EnhancedPipeline`. The opt-in
CSV-only path (`--no-fetch --competitors <file>`) skips `EnhancedPipeline` and calls
`MatchingPipeline.run` directly — its matching output is **byte-for-byte unchanged**
from the legacy behaviour.

**Completeness filter (opt-in, `--require-shipping`)** — `output/completeness_filter.py`:
`keep_complete_offers` drops every candidate whose offer lacks a price *or* a shipping
cost (free/`0` counts as known), so the output holds only offers with a certain landed
cost. It recomputes `best_match` among the survivors (highest confidence, else `None`);
a product left with no complete offer becomes unmatched (placeholder row). Pure and
non-mutating — applied to `[MatchResult]` just before the writer. Since shipping is read
only by the PDP cascade, pair it with `--verify-pdp`.

## Code Conventions

- **Language:** All code, comments, docstrings, commit messages, branch names in English
- **Style:** ruff enforced, line length 100, double quotes, Google-style docstrings
- **Types:** Full type annotations on all public functions. Use Pydantic models for data.
- **SOLID:** Single responsibility per module. Depend on abstractions (ABC for layers/ingestors). Open for extension (new layers, new ingestors via subclassing).
- **Testing:** TDD — write failing test first, then implement. Minimum 80% coverage.
- **Test isolation:** Tests MUST NOT touch any external/production system — no writes to a primary/production database, no live network. Write only to pytest's `tmp_path`; inject stubs for heavy deps (encoder, Groq). A session-autouse guard in `tests/conftest.py` overwrites datastore env vars (`DATABASE_URL`, …) with sandbox values so a real store can never be reached, even once a DB is added.
- **Error handling:** Never silently swallow exceptions. Use custom exception classes in `exceptions.py`.
- **Git:** Git Flow. Branches: `feature/xxx`, `fix/xxx`. Conventional Commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`.
- **No mutable global state.** Pass config via dependency injection.

## Important Patterns

### Adding a new matching layer

1. Create `src/repricing_engine/matching/layers/my_layer.py`
2. Subclass `BaseMatchLayer`
3. Implement `match(catalog_product, competitor_products) -> list[MatchCandidate]`
4. Register in `MatchingPipeline.__init__`
5. Add unit tests in `tests/unit/test_my_layer.py`

### Adding a new data source ingestor

1. Create `src/repricing_engine/ingestion/my_source_ingestor.py`
2. Subclass `BaseIngestor`
3. Implement `ingest(file_path) -> list[CompetitorProduct]`
4. Add CLI option in `cli.py`

### Adding a new source provider (`--fetch`)

1. Create `src/repricing_engine/sources/providers/my_provider.py`
2. Subclass `BaseSourceProvider`; set `name` + `cost_tier`; take an injected `httpx.AsyncClient`
3. Implement `async search(product, market) -> list[RawSearchResult]` and `is_available()`
4. Register it in `SourceFetcher.from_settings`; add a config toggle in `config.py`
5. Add unit tests injecting `httpx.MockTransport` (never the live network)

### PDP cascade rule (cheapest → most expensive)

Always try regex/meta → JSON-LD (both free) **before** the Groq `AiExtractor`. Groq tokens
cost money, so AI extraction runs only when the free pass found neither an identifier nor a
price and the candidate is still below `PDP_AI_CONFIDENCE_THRESHOLD`.

## Environment

- Python 3.12+ via uv
- `.env` file for secrets (never commit) — see `.env.example`
- All thresholds configurable via env vars
- Embedding model downloads to `~/.cache/huggingface/` on first run
- **Source fetching** (`--fetch`): `SEARXNG_BASE_URL` (default `http://localhost:8888`),
  `SEARXNG_ENABLED`, `SEARXNG_MAX_PAGES` (pages per query, default 3), `TROVAPREZZI_ENABLED`,
  `DUCKDUCKGO_ENABLED`, `DUCKDUCKGO_RATE_LIMIT_SECONDS`, `FETCH_READ_PRICES` (read real prices on
  `--fetch`, default on)
- **PDP verification** (`--verify-pdp`, and price reading on `--fetch`): `PDP_FETCH_TIMEOUT_SECONDS`,
  `PDP_MAX_CONCURRENT_FETCHES`, `PDP_RATE_LIMIT_PER_DOMAIN_SECONDS`, `PDP_AI_EXTRACTION_ENABLED`,
  `PDP_AI_CONFIDENCE_THRESHOLD`, `PDP_PLAYWRIGHT_ENABLED`

## Implementation Notes

- **CLI options use `Annotated[...]`** (e.g. `Annotated[Path, typer.Option(...)]`) rather than
  call-in-default, to satisfy ruff `B008`.
- **Heavy dependencies are dependency-injected**: the sentence-transformers encoder
  (`SemanticLayer`), the Groq client (`AiQualityGateLayer` / `AiExtractor`), and every
  `httpx.AsyncClient` (providers + `PageFetcher`) are passed in, so unit tests stub them with
  `FakeEncoder` / `FakeGroqClient` / `httpx.MockTransport` and avoid model downloads / network.
- **Async bridge**: fetching and PDP scraping are async; `MatchingPipeline.match_one` is sync.
  `EnhancedPipeline` (in `orchestration/`) calls the sync matcher inline and wraps Groq calls in
  `asyncio.to_thread`. The sync CSV-only path stays unchanged.
- **`CompetitorProduct.price` is optional** (`Decimal | None`): a fetched offer may have no price
  until a PDP is read. The writer guards all landed-price math for `None`.
- **Groq model** defaults to `llama-3.3-70b-versatile` (set via `GROQ_MODEL`).
- **Playwright is an optional extra** (`[project.optional-dependencies] playwright`), lazily
  imported only when `PDP_PLAYWRIGHT_ENABLED=true`; httpx is the default fetcher.
- **Price parsing**: feed/scrape strings (locale-formatted) use `normalize_price(value, currency,
  market)`; JSON-LD / AI / API values (machine-formatted) use `parse_price_loose` so `789.0`
  stays `789.0` rather than being read as `7890` under IT thousands-separator rules.
