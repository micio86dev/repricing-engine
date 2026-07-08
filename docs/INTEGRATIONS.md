# Integrations Guide — paid discovery, shopping & scraping providers

This guide explains **every external service** you may register to scale the
repricing engine, how to sign up, where to find the credentials, which `.env`
variable each one maps to, and a quick `curl` test to confirm the key works
**before** wiring it into the engine.

> **Nothing here is required to run the engine.** The free path (self-hosted
> **SearXNG** + **DuckDuckGo** + **Groq**) works out of the box. These providers
> are opt-in and only matter at scale (thousands of products/day), where the free
> search engines rate-limit your IP.

---

## 1. How the pipeline uses external services (3 layers)

```
        DISCOVERY                 SHOPPING (offers+price)        PDP PROXY
  find the competitor URLs     structured offers with price   fetch a page's HTML
  ───────────────────────      ────────────────────────────   ─────────────────
  SearXNG (free, local)        DataForSEO Merchant (Shopping)  httpx (free)
  DuckDuckGo (free)            Serper /shopping                Playwright (free, local)
  Serper /search (SERP)        SerpApi / SearchApi Shopping    ScraperAPI (proxy)
  DataForSEO SERP              Oxylabs E-commerce Scraper      Oxylabs Web Scraper
  eBay / Keepa (marketplace)                                   Bright Data (proxy)
```

- **DISCOVERY** returns *URLs* → you still open each page (a **PDP**) to read the
  price. At 100k products/day the PDP fetching is the expensive part.
- **SHOPPING** returns *offers with prices* directly → skips most PDP work. **This
  is the cheapest approach at scale.**
- **PDP PROXY** is only needed for the SERP/discovery route on JS/Cloudflare shops.

### Cost recap (≈100k products/day ≈ 3M requests/month, 1 query per product)

| Service | Layer | Price | ≈ / month (3M) | Best for |
|---|---|---|--:|---|
| **DataForSEO — Merchant/Shopping** | Shopping (priced) | ~$1.00 / 1k | **~$3,000** | Primary at scale — offers+prices+GTIN |
| **Serper.dev** (Ultimate tier) | SERP (urls) | $0.30 / 1k | **~$900** | Cheapest discovery for the long tail |
| **DataForSEO — SERP** | SERP (urls) | $0.60 / 1k | ~$1,800 | Reliable pay-as-you-go discovery |
| **Oxylabs — SERP / E-commerce** | SERP + Shopping | ~$0.80–2.80 / 1k | ~$2,400–8,400 | Already in your stack; heavy sites |
| **SearchApi.io** | SERP/Shopping | ~$4 / 1k | ~$12,000 | Small/medium volumes |
| **SerpApi** | SERP/Shopping | ~$3.75 / 1k | ~$11,000 | ⚠️ too costly at this scale |
| **ScraperAPI** | PDP proxy | 1 credit/page (Google=25) | varies | PDP fetching; avoid for Google SERP |
| **eBay Browse API** | Marketplace | Free (~5k calls/day) | Free | New-item offers |
| **Keepa** | Amazon | Subscription (tokens) | ~€20–50/mo | Amazon price history |

**Recommended combo for 100k/day:** DataForSEO Merchant (primary, ~$3k) +
Serper SERP for the long tail (~$900) + PDP only the top-N cheapest per product.
Budget ≈ **$3,000–4,000/month**. Groq stays negligible (~$6/mo, or $0 with `--skip-ai`).

---

## 2. Per-service registration

Each section: **What → When → Pricing → Sign up → Credentials → `.env` → Test**.

### 2.1 DataForSEO ⭐ (recommended primary)

- **What:** REST API for Google Shopping ("Merchant") and Google Organic SERP.
  The Merchant endpoint returns competitor offers **with prices, sellers and GTIN**.
- **When:** Your main source at scale — cheapest way to get priced offers without
  fetching every page.
- **Pricing:** SERP ~$0.60 / 1k; Merchant/Shopping ~$1.00 / 1k task. Pay-as-you-go,
  no subscription, prepaid balance.
- **Sign up:**
  1. Go to <https://app.dataforseo.com/register> and create an account.
  2. Confirm your email and log in to the dashboard.
  3. Add a prepaid balance (Billing) — even $10 is enough to test.
  4. Open **Dashboard → API Access**: you'll see your **login** (your email) and an
     **API password** (a generated string — *not* your website password).
- **Credentials:** `login` (email) + `password` (API password). Auth is HTTP Basic.
- **`.env`:**
  ```env
  DATAFORSEO_ENABLED=true
  DATAFORSEO_LOGIN=you@example.com
  DATAFORSEO_PASSWORD=<api-password-from-dashboard>
  DATAFORSEO_MODE=shopping        # or serp
  ```
- **Test (Google Shopping, IT):**
  ```bash
  curl -s --user "you@example.com:API_PASSWORD" \
    -H "Content-Type: application/json" \
    -X POST "https://api.dataforseo.com/v3/merchant/google/products/live/advanced" \
    -d '[{"language_code":"it","location_code":2380,"keyword":"geberit sigma 8"}]' | head -c 800
  ```
  (`location_code` 2380 = Italy.) A `status_code: 20000` means the key works.

### 2.2 Serper.dev (cheapest SERP)

- **What:** The cheapest/fastest Google Search API (organic + `/shopping`).
- **When:** Discovery for the specialist long tail (shops not on Google Shopping).
- **Pricing:** $0.30–$1.00 / 1k (packs: 50k=$50, 500k=$375, 2.5M=$1,250, 12.5M=$3,750).
  2,500 free credits to start.
- **Sign up:**
  1. Go to <https://serper.dev> and sign in (Google login).
  2. The **Dashboard** shows your **API Key** immediately.
  3. Buy a credit pack when ready (credits last 6 months).
- **Credentials:** a single API key.
- **`.env`:**
  ```env
  SERPER_ENABLED=true
  SERPER_API_KEY=<your-serper-key>
  ```
- **Test:**
  ```bash
  curl -s -X POST "https://google.serper.dev/search" \
    -H "X-API-KEY: YOUR_KEY" -H "Content-Type: application/json" \
    -d '{"q":"geberit sigma 8 110.791.00.1","gl":"it","hl":"it"}' | head -c 800
  ```

### 2.3 Oxylabs (SERP + E-commerce Scraper)

- **What:** Enterprise SERP Scraper API and E-commerce Scraper API (Google Shopping,
  Amazon, etc.). You already reference "oxylabs" as a CSV source.
- **When:** Heavy/blocked sites, or if you standardize on one vendor.
- **Pricing:** SERP ~$0.80–2.80 / 1k results; Web/E-commerce Scraper from ~$49/mo.
- **Sign up:**
  1. Start a trial at <https://oxylabs.io/products/scraper-api/serp> (or the
     E-commerce Scraper) → create an account.
  2. In <https://dashboard.oxylabs.io> create a **Scraper API sub-user**
     (username + password) under your API access.
- **Credentials:** sub-user `username` + `password` (HTTP Basic).
- **`.env`:**
  ```env
  OXYLABS_ENABLED=true
  OXYLABS_USERNAME=<scraper-api-sub-user>
  OXYLABS_PASSWORD=<sub-user-password>
  ```
- **Test (Realtime E-commerce, Google Shopping IT):**
  ```bash
  curl -s --user "USER:PASS" -H "Content-Type: application/json" \
    -X POST "https://realtime.oxylabs.io/v1/queries" \
    -d '{"source":"google_shopping_search","query":"geberit sigma 8","geo_location":"Italy"}' | head -c 800
  ```

### 2.4 SearchApi.io (alternative)

- **What:** Google SERP + Shopping API, single key. Simpler than DataForSEO.
- **Pricing:** ~$40 / 10k requests (≈$4 / 1k) — good for small/medium volumes.
- **Sign up:** <https://www.searchapi.io> → register → **Dashboard → API Key**.
- **`.env`:**
  ```env
  SEARCHAPI_ENABLED=true
  SEARCHAPI_API_KEY=<your-searchapi-key>
  ```
- **Test:**
  ```bash
  curl -s "https://www.searchapi.io/api/v1/search?engine=google_shopping&q=geberit+sigma+8&gl=it&hl=it&api_key=YOUR_KEY" | head -c 800
  ```

### 2.5 SerpApi (alternative — costly at scale)

- **What:** Popular Google SERP/Shopping API.
- **Pricing:** from $75/mo for 5k searches (~$3.75 / 1k) — **too expensive** at 3M/mo.
- **Sign up:** <https://serpapi.com/users/sign_up> → **Dashboard → Your API Key**.
- **`.env`:**
  ```env
  SERPAPI_ENABLED=true
  SERPAPI_API_KEY=<your-serpapi-key>
  ```
- **Test:**
  ```bash
  curl -s "https://serpapi.com/search.json?engine=google_shopping&q=geberit+sigma+8&gl=it&hl=it&api_key=YOUR_KEY" | head -c 800
  ```

### 2.6 ScraperAPI (PDP proxy / renderer)

- **What:** Proxy + JS renderer to fetch product pages that block plain requests
  (Cloudflare/JS). Used for the **PDP** step, not discovery.
- **When:** SERP/discovery route on tough shops (amazon.it, ebay.it, idealo, …).
- **Pricing:** credit-based — 1 credit/standard page, more for JS render; plans from
  $49/mo. (Avoid it for Google *SERP*: 25 credits each.)
- **Sign up:** <https://www.scraperapi.com> → sign up → **Dashboard → API Key**.
- **`.env`:**
  ```env
  SCRAPERAPI_ENABLED=true
  SCRAPERAPI_API_KEY=<your-scraperapi-key>
  ```
- **Test (render a JS page):**
  ```bash
  curl -s "http://api.scraperapi.com?api_key=YOUR_KEY&render=true&country_code=it&url=https://www.amazon.it/dp/B08XXXX" | head -c 400
  ```

### 2.7 eBay Browse API (official, free)

- **What:** Official API for live eBay listings (new-item offers). Free tier ~5,000
  calls/day.
- **Sign up:**
  1. Create a developer account at <https://developer.ebay.com>.
  2. **Application Keysets** → create a **Production** keyset → you get an
     **App ID (Client ID)** and **Cert ID (Client Secret)**.
- **Credentials:** `client_id` + `client_secret` → exchange for an OAuth application
  token (client-credentials grant), then call the Browse API.
- **`.env`:**
  ```env
  EBAY_ENABLED=true
  EBAY_CLIENT_ID=<App-ID / Client-ID>
  EBAY_CLIENT_SECRET=<Cert-ID / Client-Secret>
  ```
- **Test (get a token):**
  ```bash
  curl -s -X POST "https://api.ebay.com/identity/v1/oauth2/token" \
    -u "CLIENT_ID:CLIENT_SECRET" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=client_credentials&scope=https://api.ebay.com/oauth/api_scope" | head -c 400
  ```
  Then search: `GET https://api.ebay.com/buy/browse/v1/item_summary/search?q=...`
  with header `X-EBAY-C-MARKETPLACE-ID: EBAY_IT` and `Authorization: Bearer <token>`.

### 2.8 Keepa (Amazon)

- **What:** Amazon offers and price history by EAN/ASIN.
- **Pricing:** token-based subscription (~€19–49/mo depending on request volume).
- **Sign up:** <https://keepa.com/#!api> → subscribe → copy the **API key**.
- **`.env`:**
  ```env
  KEEPA_ENABLED=true
  KEEPA_API_KEY=<your-keepa-key>
  ```
- **Test (Amazon.it = domain 8, lookup by EAN):**
  ```bash
  curl -s "https://api.keepa.com/product?key=YOUR_KEY&domain=8&code=8025863058632" | head -c 400
  ```

### 2.9 RSS / XML merchant feeds

- **What:** Some sources publish product/price data as feeds (XML/CSV/RSS) rather
  than an API:
  - **Your own Google Merchant Center** product feed (useful to reconcile catalog).
  - **Comparison-site partner/affiliate feeds** (TrovaPrezzi, Idealo, Kelkoo,
    Trovaprezzi merchant programs) — you must be an approved merchant/affiliate to
    get feed access; ask their partner support for the feed URL/token.
  - **Amazon Product Advertising API** (requires an approved Associates account).
- **When:** Only if you already have feed access; not a general competitor-price
  source. RSS is rarely the right tool for live competitor prices — prefer the
  Shopping APIs above.
- **`.env`:** comma-separated feed URLs
  ```env
  FEED_URLS=https://partner.example.com/feed.xml,https://other.example/feed.csv
  ```
- **Note:** parsing feeds needs the `feedparser` (RSS/Atom) or `polars`/`csv` (XML/CSV)
  dependency — `feedparser` is **not** yet in the project; it'll be added with the
  feed provider when you have a real feed to target.

---

## 3. Already configured (free)

- **SearXNG** — self-hosted metasearch, primary free discovery. Start with
  `docker compose up -d` (config in `searxng/settings.yml`). Set
  `SEARXNG_BASE_URL=http://localhost:8888`. See README "SearXNG quick start".
- **Groq** — the AI quality gate / last-resort price extractor. Get a key at
  <https://console.groq.com/keys> → `GROQ_API_KEY`. Cost is negligible; use
  `--skip-ai` in strict mode to spend $0 on AI.

---

## 4. `.env` variable → service reference

| Variable | Service | Type |
|---|---|---|
| `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` | DataForSEO | Basic auth |
| `DATAFORSEO_MODE` | DataForSEO | `shopping` \| `serp` |
| `SERPER_API_KEY` | Serper.dev | API key header |
| `OXYLABS_USERNAME` / `OXYLABS_PASSWORD` | Oxylabs | Basic auth (sub-user) |
| `SEARCHAPI_API_KEY` | SearchApi.io | API key |
| `SERPAPI_API_KEY` | SerpApi | API key |
| `SCRAPERAPI_API_KEY` | ScraperAPI | API key (proxy) |
| `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` | eBay Browse API | OAuth client creds |
| `KEEPA_API_KEY` | Keepa | API key |
| `FEED_URLS` | RSS/XML feeds | comma-separated URLs |
| `GROQ_API_KEY` | Groq (AI) | API key |
| `SEARXNG_BASE_URL` | SearXNG (self-hosted) | URL |

Each service also has an `*_ENABLED` flag (default `false`). A provider only runs
when its flag is `true` **and** its credentials are present.

---

## 5. Security

- **Never commit `.env`** — it's git-ignored. Only `.env.example` (placeholders) is
  committed. Rotate any key that has been shared or pasted anywhere.
- Prefer a secrets manager (Vault, AWS/GCP Secrets Manager, Doppler) in production
  and inject the env vars at runtime.
- Set per-provider **spend limits/budgets** in each vendor's dashboard to cap cost.

---

## 6. Implementation status & next step

The **credentials and toggles above are already wired into `config.py`** (so the
env vars are read into `Settings`). The **provider adapters** that call each API
are added on demand, following the existing pattern in
`src/repricing_engine/sources/providers/` (subclass `BaseSourceProvider`, set
`name` + `cost_tier`, implement `async search(...)` and `is_available()`, register
in `SourceFetcher.from_settings`, add unit tests with `httpx.MockTransport`).

**Tell me which service(s) you registered** (e.g. DataForSEO + Serper) and I'll
implement and test those provider adapters end-to-end.
