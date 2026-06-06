"""
Marktplaats.nl Bike Market Price Scraper
Uses plain HTTP + JSON-LD/regex extraction — no browser needed.

Usage:
    python -m execution.scrape_marktplaats --brand Cannondale --model SystemSix
    python -m execution.scrape_marktplaats --brand Trek
"""

import argparse
import json
import re
import sys
from statistics import median
from typing import Optional

import httpx

from execution.config import SCRAPING_DELAY_SECONDS
from execution.rate_limiter import RateLimiter
from execution.db_repository import Repository

BASE_URL = "https://www.marktplaats.nl"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "nl-NL,nl;q=0.9",
}


def _parse_dutch_price(text: str) -> Optional[float]:
    """Parse Dutch price: €1.999,- or €2.600,00 or €850"""
    if not text:
        return None
    text = re.sub(r'[€\s\xa0]', '', str(text))
    text = text.rstrip(',-').strip()
    # Dutch format: dot=thousands, comma=decimal
    m = re.match(r'^(\d{1,3})(\.\d{3})+(,\d{0,2})?$', text)
    if m:
        text = text.replace('.', '').replace(',', '.')
    else:
        text = text.replace(',', '.')
    try:
        val = float(text)
        return val if 50 < val < 50000 else None
    except ValueError:
        return None


def _build_search_url(brand: str, model: Optional[str] = None) -> str:
    """Build Marktplaats search URL for a bike brand/model."""
    query = brand
    if model:
        query = f"{brand} {model}"
    # URL-encode: replace spaces with +
    encoded = re.sub(r'\s+', '+', query.lower().strip())
    return f"{BASE_URL}/q/{encoded}/"


def _extract_prices_from_jsonld(html: str) -> list[float]:
    """Extract prices from JSON-LD script tags."""
    prices = []
    for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                             html, re.DOTALL | re.IGNORECASE):
        try:
            data = json.loads(block)
            prices.extend(_prices_from_obj(data))
        except (json.JSONDecodeError, TypeError):
            continue
    return prices


def _prices_from_obj(obj) -> list[float]:
    """Recursively find price values in a JSON-LD object."""
    results = []
    if isinstance(obj, list):
        for item in obj:
            results.extend(_prices_from_obj(item))
    elif isinstance(obj, dict):
        # Direct offer
        if obj.get("@type") in ("Offer", "Product"):
            price = obj.get("price") or (obj.get("offers") or {}).get("price")
            if price is not None:
                p = _parse_dutch_price(str(price))
                if p:
                    results.append(p)
        # Recurse into all values
        for v in obj.values():
            if isinstance(v, (dict, list)):
                results.extend(_prices_from_obj(v))
    return results


def _extract_prices_from_html(html: str) -> list[float]:
    """Regex fallback: extract all price patterns from raw HTML."""
    prices = []
    for m in re.finditer(r'€[\s\xa0]*([\d][.\d]*[\d](?:[,\d]{0,3})?)', html):
        p = _parse_dutch_price("€" + m.group(1))
        if p:
            prices.append(p)
    return prices


def run(brand: str, model: Optional[str] = None, bike_type: Optional[str] = None,
        progress_callback=None) -> dict:
    """Fetch bike market prices from Marktplaats and store in DB."""
    if not brand or not brand.strip():
        return {"fetched": 0, "error": "brand is required"}

    repo = Repository()
    rate_limiter = RateLimiter(SCRAPING_DELAY_SECONDS)
    client = httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True)

    try:
        url = _build_search_url(brand, model)
        print(f"Fetching Marktplaats: {url}")

        rate_limiter.wait()
        resp = client.get(url)
        resp.raise_for_status()

        # Try JSON-LD first, fall back to regex
        prices = _extract_prices_from_jsonld(resp.text)
        if len(prices) < 3:
            prices = _extract_prices_from_html(resp.text)

        # If brand+model yields nothing, try brand-only
        if len(prices) < 3 and model:
            print(f"  Few results for brand+model, retrying brand-only...")
            rate_limiter.wait()
            fallback_url = _build_search_url(brand)
            resp2 = client.get(fallback_url)
            if resp2.status_code == 200:
                prices = _extract_prices_from_jsonld(resp2.text)
                if len(prices) < 3:
                    prices = _extract_prices_from_html(resp2.text)

        print(f"  Found {len(prices)} price listings")

        if prices:
            med = median(prices)
            print(f"  Median price: €{med:,.0f} (range €{min(prices):,.0f}–€{max(prices):,.0f})")

        # Clear old prices and store new ones
        repo.clear_bike_market_prices(brand=brand, model=model)
        stored = 0
        for price in prices:
            repo.upsert_bike_market_price(
                brand=brand,
                model=model,
                bike_type=bike_type,
                asking_price=price,
                source="marktplaats",
                source_url=url,
            )
            stored += 1

        repo.close()
        return {"brand": brand, "model": model, "fetched": stored, "prices": prices}

    except Exception as e:
        print(f"Error fetching Marktplaats: {e}", file=sys.stderr)
        repo.close()
        return {"fetched": 0, "error": str(e)}
    finally:
        client.close()


def run_for_all_bikes(auction_name: Optional[str] = None, progress_callback=None) -> dict:
    """Fetch market prices for all unique brand/model combos in the bikes table."""
    repo = Repository()
    bikes = repo.list_bikes(limit=9999, auction_name=auction_name)
    repo.close()

    seen = set()
    pairs = []
    for b in bikes:
        if b.brand:
            key = (b.brand, b.model)
            if key not in seen:
                seen.add(key)
                pairs.append((b.brand, b.model, b.bike_type))

    total = len(pairs)
    total_fetched = 0

    for i, (brand, model, bike_type) in enumerate(pairs, 1):
        if progress_callback:
            progress_callback(i, total, f"Looking up {brand} {model or ''}...")
        result = run(brand=brand, model=model, bike_type=bike_type)
        total_fetched += result.get("fetched", 0)

    return {"pairs_looked_up": total, "prices_fetched": total_fetched}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch bike market prices from Marktplaats")
    parser.add_argument("--brand", required=True)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()
    result = run(brand=args.brand, model=args.model)
    print(json.dumps(result, indent=2, default=str))
