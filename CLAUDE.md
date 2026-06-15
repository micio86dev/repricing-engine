# CLAUDE.md — Repricing Engine

## Project Overview

Intelligent repricing/price monitoring engine for e-commerce. Multi-layer product matching with AI quality gate. Python 3.12+, CLI-based MVP.

## Commands

- `uv sync` — Install all dependencies
- `uv run repricing match --catalog <file> --competitors <file> --output <file>` — Run matching
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

### Data Flow

```
catalog.csv → CatalogIngestor → [CatalogProduct]
                                       ↓
oxylabs.csv → OxyLabsIngestor → [CompetitorProduct]
                                       ↓
                              MatchingPipeline
                              (Layer 1→2→3→4)
                                       ↓
                              [MatchResult]
                                       ↓
                              CsvWriter → output.csv
```

## Code Conventions

- **Language:** All code, comments, docstrings, commit messages, branch names in English
- **Style:** ruff enforced, line length 100, double quotes, Google-style docstrings
- **Types:** Full type annotations on all public functions. Use Pydantic models for data.
- **SOLID:** Single responsibility per module. Depend on abstractions (ABC for layers/ingestors). Open for extension (new layers, new ingestors via subclassing).
- **Testing:** TDD — write failing test first, then implement. Minimum 80% coverage.
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

## Environment

- Python 3.12+ via uv
- `.env` file for secrets (never commit)
- All thresholds configurable via env vars
- Embedding model downloads to `~/.cache/huggingface/` on first run

## Implementation Notes

- **CLI options use `Annotated[...]`** (e.g. `Annotated[Path, typer.Option(...)]`) rather than
  call-in-default, to satisfy ruff `B008`.
- **Heavy dependencies are dependency-injected**: the sentence-transformers encoder
  (`SemanticLayer`) and the Groq client (`AiQualityGateLayer`) are passed in, so unit tests stub
  them and avoid model downloads / network calls. Real-model checks live behind the `integration`
  pytest marker.
- **Groq model** defaults to `llama-3.3-70b-versatile` (set via `GROQ_MODEL`).
