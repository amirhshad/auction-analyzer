"""
Gaspedaal.nl Market Price Scraper
Aggregates listings from AutoScout24, Marktplaats, AutoTrack, and others.

Uses plain HTTP + JSON-LD extraction — no browser needed.

Usage:
    python -m execution.scrape_gaspedaal --make BMW --model "X3" --year 2014
    python -m execution.scrape_gaspedaal --make Ford --model "Focus CC" --year 2010 --mileage 200000
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

BASE_URL = "https://www.gaspedaal.nl"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Map Troostwijk model names to Gaspedaal URL slugs
# Gaspedaal uses Dutch naming (3-serie, not 3-er-reihe)
MODEL_SLUG_MAP = {
    # Audi body variants → base model
    "a1 sportback": "a1",
    "a3 sportback": "a3",
    "a3 limousine": "a3",
    "a4 allroad quattro": "a4-allroad",
    "a4 avant": "a4",
    "a5 sportback": "a5",
    "a5 coupé": "a5",
    "a5 coupe": "a5",
    "a5 cabriolet": "a5",
    "a6 avant": "a6",
    "a6 limousine": "a6",
    "a7 sportback": "a7",
    # BMW
    "1-serie": "1-serie",
    "2-serie": "2-serie",
    "3-serie": "3-serie",
    "3-serie touring": "3-serie",
    "4-serie": "4-serie",
    "5-serie": "5-serie",
    "5-serie touring": "5-serie",
    # Mercedes
    "c-klasse": "c-klasse",
    "c-klasse estate": "c-klasse",
    "e-klasse": "e-klasse",
    "e-klasse estate": "e-klasse",
    # Volkswagen
    "golf variant": "golf",
    "golf plus": "golf-plus",
    "up!": "up",
    # Ford
    "focus cc": "focus",
    "focus coupé-cabriolet": "focus",
    "focus coupe-cabriolet": "focus",
    # Peugeot
    "207 cc": "207",
    "308 cc": "308",
}

# Body type keywords for filtering (same logic as autoscout24 scraper)
BODY_TYPE_KEYWORDS = {
    "coupé", "coupe", "cabriolet", "cabrio", "cc", "sportback", "limousine",
    "avant", "touring", "estate", "variant", "stationwagen", "sedan",
    "hatchback", "suv", "crossover", "plus",
}


def _model_to_slug(model: str) -> str:
    """Convert a model name to a Gaspedaal URL slug."""
    lower = model.lower().strip()
    if lower in MODEL_SLUG_MAP:
        return MODEL_SLUG_MAP[lower]
    # Try progressively shorter prefixes
    words = lower.split()
    for i in range(len(words) - 1, 0, -1):
        prefix = " ".join(words[:i])
        if prefix in MODEL_SLUG_MAP:
            return MODEL_SLUG_MAP[prefix]
    return lower.replace(" ", "-")


def _extract_body_types(model: str) -> set:
    """Extract body type keywords from model name, including equivalents."""
    if not model:
        return set()
    found = set()
    for word in re.split(r'[\s-]+', model.lower()):
        if word in BODY_TYPE_KEYWORDS:
            found.add(word)
    if not found:
        return set()
    if found & {"coupé", "coupe", "cabriolet", "cabrio", "cc"}:
        found.update({"coupé", "coupe", "cabriolet", "cabrio", "cc"})
    return found


def build_search_url(make: str, model: Optional[str] = None,
                     year: Optional[int] = None, mileage_km: Optional[int] = None) -> str:
    """Build Gaspedaal search URL."""
    slug_make = make.lower().replace(" ", "-")
    if model:
        slug_model = _model_to_slug(model)
        url = f"{BASE_URL}/{slug_make}/{slug_model}"
    else:
        url = f"{BASE_URL}/{slug_make}"

    params = []
    if year:
        params.append(f"bmin={year - 1}")
        params.append(f"bmax={year + 1}")
    if mileage_km:
        params.append(f"kmax={mileage_km + 30000}")

    if params:
        return url + "?" + "&".join(params)
    return url


def _extract_jsonld(html: str) -> list[dict]:
    """Extract car listings from JSON-LD in page HTML."""
    listings = []
    ld_matches = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>',
        html, re.DOTALL,
    )
    for raw in ld_matches:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        # JSON-LD ItemList contains the listings
        if isinstance(data, dict) and data.get("@type") == "ItemList":
            for item in data.get("itemListElement", []):
                car = item.get("item", {})
                car_types = car.get("@type", [])
                if "Car" not in car_types and "Product" not in car_types:
                    continue

                price = None
                offers = car.get("offers", {})
                if isinstance(offers, dict):
                    price = offers.get("price")

                mileage = None
                odometer = car.get("mileageFromOdometer", {})
                if isinstance(odometer, dict):
                    mileage = odometer.get("value")

                listings.append({
                    "name": car.get("name", ""),
                    "brand": car.get("brand", ""),
                    "model": car.get("model", ""),
                    "year": car.get("productionDate"),
                    "price": price,
                    "mileage": mileage,
                    "fuel_type": car.get("fuelType", ""),
                    "transmission": car.get("vehicleTransmission", ""),
                    "body_type": car.get("bodyType", ""),
                })

    return listings


def run(make: str, model: Optional[str] = None,
        year: Optional[int] = None, mileage_km: Optional[int] = None) -> list[dict]:
    """Main scraper entry point."""
    rate_limiter = RateLimiter(SCRAPING_DELAY_SECONDS)
    repo = Repository()
    results = []

    # Clear old market prices for this make/model before re-scraping
    cleared = repo.clear_market_prices(make, model or "")
    if cleared:
        print(f"  Cleared {cleared} old market price entries for {make} {model or ''}")

    search_url = build_search_url(make, model, year, mileage_km)
    print(f"Searching Gaspedaal: {search_url}")

    body_types = _extract_body_types(model) if model else set()
    core_model = model.lower().split()[0] if model else None

    try:
        rate_limiter.wait()
        resp = httpx.get(search_url, headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)

        if resp.status_code != 200:
            print(f"  HTTP {resp.status_code} — trying make-only search", file=sys.stderr)
            fallback_url = build_search_url(make, year=year, mileage_km=mileage_km)
            rate_limiter.wait()
            resp = httpx.get(fallback_url, headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)
            if resp.status_code != 200:
                print(f"  HTTP {resp.status_code} — giving up", file=sys.stderr)
                repo.close()
                return []

        listings = _extract_jsonld(resp.text)
        print(f"  Found {len(listings)} listings on page 1")

        # Page 2 if there are many results
        if len(listings) >= 90:
            sep = "&" if "?" in search_url else "?"
            rate_limiter.wait()
            resp2 = httpx.get(f"{search_url}{sep}page=2", headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)
            if resp2.status_code == 200:
                page2 = _extract_jsonld(resp2.text)
                print(f"  Found {len(page2)} listings on page 2")
                listings.extend(page2)

        for item in listings:
            try:
                price = item.get("price")
                if not price or float(price) <= 0:
                    continue
                asking_price = float(price)

                listing_year = item.get("year")
                if listing_year:
                    listing_year = int(listing_year)

                listing_mileage = item.get("mileage")
                if listing_mileage:
                    listing_mileage = int(listing_mileage)

                listing_name = (item.get("name") or "").lower()
                listing_model = (item.get("model") or "").lower()
                listing_body = (item.get("body_type") or "").lower()

                # Filter by model if specified
                if core_model:
                    name_match = (
                        core_model in listing_model
                        or core_model in listing_name
                    )
                    if not name_match:
                        continue

                # Filter by body type if model specifies one
                if body_types:
                    title_text = f"{listing_name} {listing_body}"
                    if not any(bt in title_text for bt in body_types):
                        continue

                fuel = item.get("fuel_type") or ""
                result = {
                    "make": make,
                    "model": model or item.get("model", ""),
                    "year": listing_year,
                    "mileage_km": listing_mileage,
                    "fuel_type": fuel,
                    "asking_price": asking_price,
                    "source_url": "",
                }
                results.append(result)

                repo.upsert_market_price(
                    make=make,
                    model=model or item.get("model", ""),
                    asking_price=asking_price,
                    year=listing_year,
                    mileage_km=listing_mileage,
                    fuel_type=fuel,
                    source="gaspedaal",
                    source_url="",
                )

            except Exception as e:
                print(f"    Error parsing listing: {e}", file=sys.stderr)
                continue

    except Exception as e:
        print(f"Error during scraping: {e}", file=sys.stderr)

    repo.close()

    if results:
        prices = [r["asking_price"] for r in results]
        med = median(prices)
        print(f"\nDone. Found {len(results)} market prices. Median: €{med:,.0f}")
    else:
        print(f"\nDone. Found 0 market prices.")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Gaspedaal market prices")
    parser.add_argument("--make", required=True, help="Vehicle make (e.g., BMW)")
    parser.add_argument("--model", help="Vehicle model (e.g., X3)")
    parser.add_argument("--year", type=int, help="Year to search around (±1)")
    parser.add_argument("--mileage", type=int, help="Mileage in km")
    args = parser.parse_args()

    results = run(make=args.make, model=args.model, year=args.year, mileage_km=args.mileage)
    print(json.dumps({"found": len(results), "prices": results}, indent=2, default=str))
