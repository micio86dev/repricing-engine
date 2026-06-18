# TODO — Future Source Providers & Follow-ups

The current `--fetch` providers (SearXNG, TrovaPrezzi, DuckDuckGo) are **free**. When free
coverage isn't enough, these **paid** SERP / e-commerce data APIs are the next candidates. Each
would be a new `BaseSourceProvider` (see CLAUDE.md → "Adding a new source provider").

## Paid provider pricing (researched 2026-06)

Estimates assume **~10,000 product lookups/day ≈ 300,000/month**, 1 query per product per day.
Verify on the linked pricing page before committing — rates change.

| Provider | Free tier | Cheapest rate | ~10k/day (≈300k/mo) | CC for free tier | Pricing |
|----------|-----------|---------------|---------------------|------------------|---------|
| **Serper** (serper.dev) | 2,500 credits one-time (6 mo) | $1.00 / 1k (→ $0.30/1k at scale) | **~$90–300/mo** | No | <https://serper.dev/> |
| **DataForSEO** (Google Shopping/Merchant) | signup credit only (unconfirmed) | $0.001/product std ($1.00/1k) | **~$300/mo** | unconfirmed | <https://dataforseo.com/pricing/merchant/google-shopping-api> |
| **OxyLabs** (Web Scraper API, live) | trial ~1–2k results | ~$0.50–1.6 / 1k results | **~$150–480/mo** | unconfirmed | <https://oxylabs.io/products/scraper-api/web/pricing> |
| **Brave Search API** | $5 metered/mo (~1k queries) | $5.00 / 1k ($0.005/q) | **~$1,500/mo** | Yes | <https://api-dashboard.search.brave.com/documentation/pricing> |
| **SerpApi** (serpapi.com) | 250 searches/mo | $0.025/search ($25/1k) | **~$7,500/mo** | unconfirmed | <https://serpapi.com/pricing> |

**Cheapest for ~10k/day:** **Serper** at volume (~$90–150/mo) edges out OxyLabs Micro (~$150/mo)
and DataForSEO (~$300/mo). Brave (~$1,500) and SerpApi (~$7,500) are far costlier. DataForSEO and
OxyLabs return *shopping/product* data (price + merchant) directly; Brave/SerpApi/Serper are SERP
queries that still need PDP verification to extract a price.

**Recommendation:** add **Serper** (cheap SERP, `cost_tier=1`) and **DataForSEO Merchant**
(structured shopping data, `cost_tier=1`) first; reserve OxyLabs live API for hard-to-scrape sites.

## Future comparison-shopping integrations

- **Idealo** — large DE/EU price-comparison coverage; partner/affiliate feed (no public API).
- **Kelkoo** — EU comparison network; merchant feed via partnership.
- **Google Merchant Center (GMC)** — Content API / price-competitiveness reports for *own* listings.

## Engineering follow-ups

- **TrovaPrezzi selectors** are best-effort constants; tune `_OFFER_ROW_SELECTORS` / price /
  shipping selectors against live HTML, with the Groq AI fallback as the safety net.
- **Playwright path** is lazy-imported and behind `PDP_PLAYWRIGHT_ENABLED`; add a real-fetch
  integration check once a CI runner with `playwright install chromium` is available.
- **Daily cron** still needs an automated competitor feed to run unattended (see auto-memory
  `repricing-production-and-output-gap`) — `--fetch` now provides one path to that.
- **Per-domain politeness**: revisit rate limits / UA rotation if providers start returning 429s.
