"""Ping every configured data-source credential and report OK/FAIL.

Fast pre-flight check before a full import: each service is hit with a minimal,
mostly zero-cost call (account/token endpoint where one exists) using the keys
from your ``.env`` (loaded via :class:`Settings`). Unconfigured services are
skipped. Run it with::

    uv run python scripts/check_keys.py
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from urllib.parse import quote

import httpx

from repricing_engine.config import Settings

_TIMEOUT = 20.0


async def _dataforseo(s: Settings, client: httpx.AsyncClient) -> tuple[str, str]:
    if not (s.dataforseo_enabled and s.dataforseo_login and s.dataforseo_password):
        return "—", "not configured (DATAFORSEO_ENABLED + LOGIN + PASSWORD)"
    auth = base64.b64encode(f"{s.dataforseo_login}:{s.dataforseo_password}".encode()).decode()
    r = await client.get(
        "https://api.dataforseo.com/v3/appendix/user_data",
        headers={"Authorization": f"Basic {auth}"},
        timeout=_TIMEOUT,
    )
    data = r.json()
    if r.status_code == 200 and data.get("status_code") == 20000:
        try:
            balance = data["tasks"][0]["result"][0]["money"]["balance"]
        except (KeyError, IndexError, TypeError):
            balance = "?"
        return "OK", f"auth valid, balance={balance}"
    msg = f"HTTP {r.status_code}, status={data.get('status_code')} {data.get('status_message', '')}"
    return "FAIL", msg


async def _ebay(s: Settings, client: httpx.AsyncClient) -> tuple[str, str]:
    if not (s.ebay_enabled and s.ebay_client_id and s.ebay_client_secret):
        return "—", "not configured (EBAY_ENABLED + CLIENT_ID + CLIENT_SECRET)"
    basic = base64.b64encode(f"{s.ebay_client_id}:{s.ebay_client_secret}".encode()).decode()
    r = await client.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
        timeout=_TIMEOUT,
    )
    if r.status_code == 200 and r.json().get("access_token"):
        return "OK", "OAuth token obtained (Production keyset valid)"
    return "FAIL", f"HTTP {r.status_code} — {r.text[:140]}"


async def _serper(s: Settings, client: httpx.AsyncClient) -> tuple[str, str]:
    if not (s.serper_enabled and s.serper_api_key):
        return "—", "not configured (SERPER_ENABLED + API_KEY)"
    r = await client.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": s.serper_api_key, "Content-Type": "application/json"},
        json={"q": "test", "gl": "it", "hl": "it"},
        timeout=_TIMEOUT,
    )
    if r.status_code == 200:
        return "OK", "key valid"
    return "FAIL", f"HTTP {r.status_code} — {r.text[:140]}"


async def _unblocker(s: Settings, client: httpx.AsyncClient) -> tuple[str, str]:
    if not s.pdp_unblocker_url_template:
        return "—", "not configured (PDP_UNBLOCKER_URL_TEMPLATE)"
    url = s.pdp_unblocker_url_template.replace("{url}", quote("https://httpbin.org/ip", safe=""))
    r = await client.get(url, timeout=_TIMEOUT + 15)
    return (
        ("OK", "fetched a test page through the unblocker")
        if r.status_code == 200
        else ("FAIL", f"HTTP {r.status_code} — {r.text[:140]}")
    )


async def _keepa(s: Settings, client: httpx.AsyncClient) -> tuple[str, str]:
    if not (s.keepa_enabled and s.keepa_api_key):
        return "—", "not configured (KEEPA_ENABLED + API_KEY)"
    r = await client.get(f"https://api.keepa.com/token?key={s.keepa_api_key}", timeout=_TIMEOUT)
    data = r.json()
    if r.status_code == 200 and "tokensLeft" in data:
        return "OK", f"tokensLeft={data['tokensLeft']}"
    return "FAIL", f"HTTP {r.status_code} — {str(data)[:140]}"


async def _feed(s: Settings, client: httpx.AsyncClient) -> tuple[str, str]:
    if not s.feed_urls:
        return "—", "not configured (FEED_URLS)"
    first = s.feed_urls.split(",")[0].strip()
    r = await client.get(first, timeout=_TIMEOUT)
    return (
        ("OK", f"reachable ({len(r.content)} bytes)")
        if r.status_code == 200
        else ("FAIL", f"HTTP {r.status_code}")
    )


async def _searxng(s: Settings, client: httpx.AsyncClient) -> tuple[str, str]:
    if not (s.searxng_enabled and s.searxng_base_url):
        return "—", "not configured (SEARXNG_BASE_URL)"
    base = s.searxng_base_url.rstrip("/")
    r = await client.get(f"{base}/search", params={"q": "test", "format": "json"}, timeout=_TIMEOUT)
    data = r.json()
    n = len(data.get("results", []))
    down = [e[0] for e in data.get("unresponsive_engines", [])]
    status = "OK" if n > 0 else "WARN"
    return status, f"results={n}, down_engines={down[:5] or 'none'}"


_CHECKS = (
    ("DataForSEO", _dataforseo),
    ("eBay", _ebay),
    ("Serper", _serper),
    ("Unblocker", _unblocker),
    ("Keepa", _keepa),
    ("Feed", _feed),
    ("SearXNG", _searxng),
)
_ICON = {"OK": "✅", "FAIL": "❌", "WARN": "⚠️ ", "—": "➖"}


_Check = Callable[[Settings, httpx.AsyncClient], Awaitable[tuple[str, str]]]


async def _run_one(fn: _Check, s: Settings, client: httpx.AsyncClient) -> tuple[str, str]:
    try:
        return await fn(s, client)
    except Exception as exc:  # noqa: BLE001 — a ping failure must not crash the report
        return "FAIL", f"{type(exc).__name__}: {exc}"


async def main() -> None:
    """Ping every configured service and print a status table."""
    settings = Settings()
    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(*(_run_one(fn, settings, client) for _, fn in _CHECKS))
    print(f"{'SERVICE':<12} {'':<3} DETAIL")  # noqa: T201
    print("-" * 60)  # noqa: T201
    for (name, _), (status, detail) in zip(_CHECKS, results, strict=True):
        print(f"{name:<12} {_ICON.get(status, status):<3} {detail}")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
