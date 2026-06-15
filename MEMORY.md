# MEMORY.md — Repricing Engine

## Project Status

- **Phase:** MVP (CLI-only matching engine)
- **Client:** Gotour Agency (Catania) — web agency building repricing platform
- **Goal:** Prove superior matching accuracy vs OxyLabs/PriceShape

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-06-15 | Python 3.12 + uv | Fast package management, modern Python features |
| 2026-06-15 | polars over pandas | Performance for 100k+ product datasets |
| 2026-06-15 | Cascade matching (4 layers) | Single-identifier matching fails (client's pain point) |
| 2026-06-15 | Groq/Llama for AI gate | Fast inference, low cost vs OpenAI |
| 2026-06-15 | Groq model `llama-3.3-70b-versatile` | `llama-3.1-70b-versatile` was decommissioned by Groq |
| 2026-06-15 | sentence-transformers local | No API costs for embeddings, runs on CPU |
| 2026-06-15 | CLI-first, no web UI | MVP validation before building infrastructure |
| 2026-06-15 | Dependency-inject encoder + Groq client | Keeps unit tests offline; ≥80% coverage without model download |

## Known Issues

- Waiting for OxyLabs export sample from client (Marco) to validate ingestor
- Embedding model (~90MB) downloads on first run — document this in README
- EAN databases have duplicates/errors — Layer 4 AI gate essential

## Future Phases (post-MVP)

- [ ] PostgreSQL storage for catalogs + price history
- [ ] Celery + Redis job queue for async processing
- [ ] Additional sources: DataForSEO, TrovaPrezzi RSS, Google Shopping API
- [ ] Self-healing HTML scraper with LLM extraction fallback
- [ ] Market-aware scheduling (timezone-based cron)
- [ ] REST API + dashboard for client access
- [ ] Smart scheduling (frequency based on product volatility)
- [ ] Deduplication across clients (same EAN = fetch once)
