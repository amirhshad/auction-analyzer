"""
AutoScout24 Market Price Scraper
Uses Playwright for JavaScript-rendered content.

Usage:
    python -m execution.scrape_autoscout24 --make Audi --model "A1 Sportback" --year 2019
"""

import argparse
import json
import re
import sys
from typing import Optional

from execution.config import AUTOSCOUT24_DELAY_SECONDS
from execution.rate_limiter import RateLimiter
from execution.db_repository import Repository

BASE_URL = "https://www.autoscout24.nl"

# AutoScout24 uses simplified model slugs — map common full names
# Body type variants (Coupé, Sportback, Touring, etc.) map to the base model
MODEL_SLUG_OVERRIDES = {
    # Audi
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
    "1-serie": "1-er-reihe",
    "2-serie": "2-er-reihe",
    "3-serie": "3-er-reihe",
    "3-serie touring": "3-er-reihe",
    "4-serie": "4-er-reihe",
    "5-serie": "5-er-reihe",
    "5-serie touring": "5-er-reihe",
    "x1": "x1",
    "x3": "x3",
    "x5": "x5",
    # Mercedes
    "c-klasse": "c-klasse",
    "c-klasse estate": "c-klasse",
    "e-klasse": "e-klasse",
    "e-klasse estate": "e-klasse",
    # Volkswagen
    "golf variant": "golf",
    "golf plus": "golf-plus",
    "polo": "polo",
    "up!": "up",
    "caddy": "caddy",
    "tiguan": "tiguan",
    # Volvo
    "v40": "v40",
    "v60": "v60",
    "xc60": "xc60",
    "xc70": "xc70",
    "xc90": "xc90",
    # Ford — CC = Coupé-Cabriolet
    "focus cc": "focus-cc",
    "focus coupé-cabriolet": "focus-cc",
    "focus coupe-cabriolet": "focus-cc",
    # Peugeot
    "207 cc": "207",
    "308 cc": "308",
    # Citroen
    "jumpy": "jumpy",
    # Kia
    "soul": "soul",
}

# Body type words that appear in model names but are separate from the base model
# Used to filter search results to matching body type
BODY_TYPE_KEYWORDS = {
    "coupé", "coupe", "cabriolet", "cabrio", "cc", "sportback", "limousine",
    "avant", "touring", "estate", "variant", "stationwagen", "sedan",
    "hatchback", "suv", "crossover", "plus",
}

FUEL_MAP = {
    "b": "Benzine",
    "d": "Diesel",
    "e": "Elektrisch",
    "l": "LPG",
    "h": "Hybride",
    "2": "Hybride",
    "c": "CNG",
    "o": "Anders",
}


def _model_to_slug(model: str) -> str:
    """Convert a model name to an AutoScout24 URL slug."""
    lower = model.lower().strip()
    if lower in MODEL_SLUG_OVERRIDES:
        return MODEL_SLUG_OVERRIDES[lower]
    # Try progressively shorter prefixes for multi-word models
    # e.g. "A5 Coupé 2.0" → try "a5 coupé 2.0", "a5 coupé", "a5"
    words = lower.split()
    for i in range(len(words) - 1, 0, -1):
        prefix = " ".join(words[:i])
        if prefix in MODEL_SLUG_OVERRIDES:
            return MODEL_SLUG_OVERRIDES[prefix]
    return lower.replace(" ", "-")


def _extract_body_types(model: str) -> set:
    """Extract body type keywords from model name, including equivalents.

    'A5 Coupé' → {'coupé', 'coupe', 'cabriolet', 'cabrio', 'cc'}
    'Focus Coupé-Cabriolet' → same expanded set
    'Golf Variant' → {'variant'}
    """
    if not model:
        return set()
    found = set()
    for word in re.split(r'[\s-]+', model.lower()):
        if word in BODY_TYPE_KEYWORDS:
            found.add(word)
    if not found:
        return set()
    # Coupé/cabriolet/cc are used interchangeably on AutoScout24
    if found & {"coupé", "coupe", "cabriolet", "cabrio", "cc"}:
        found.update({"coupé", "coupe", "cabriolet", "cabrio", "cc"})
    return found


def build_search_url(make: str, model: Optional[str] = None,
                     year: Optional[int] = None, mileage_km: Optional[int] = None) -> str:
    """Build AutoScout24 search URL."""
    slug_make = make.lower().replace(" ", "-")
    if model:
        slug_model = _model_to_slug(model)
        url = f"{BASE_URL}/lst/{slug_make}/{slug_model}"
    else:
        url = f"{BASE_URL}/lst/{slug_make}"

    params = ["cy=NL", "atype=C"]
    if year:
        params.append(f"fregfrom={year - 1}")
        params.append(f"fregto={year + 1}")
    if mileage_km:
        params.append(f"kmto={mileage_km + 20000}")

    return url + "?" + "&".join(params)


def run(make: str, model: Optional[str] = None,
        year: Optional[int] = None, mileage_km: Optional[int] = None) -> list[dict]:
    """Main scraper entry point."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Error: playwright not installed.", file=sys.stderr)
        return []

    rate_limiter = RateLimiter(AUTOSCOUT24_DELAY_SECONDS)
    repo = Repository()
    results = []

    # Clear old market prices for this make/model before re-scraping
    cleared = repo.clear_market_prices(make, model or "")
    if cleared:
        print(f"  Cleared {cleared} old market price entries for {make} {model or ''}")

    search_url = build_search_url(make, model, year, mileage_km)
    print(f"Searching AutoScout24: {search_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="nl-NL",
        )
        page = context.new_page()

        try:
            rate_limiter.wait()
            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

            # Handle cookie consent
            try:
                consent_btn = page.locator("#onetrust-accept-btn-handler, button:has-text('Alles accepteren')")
                if consent_btn.count() > 0:
                    consent_btn.first.click(timeout=5000)
                    page.wait_for_timeout(1000)
            except Exception:
                pass

            # Dismiss survey popup if present
            try:
                survey_btn = page.locator("button:has-text('Nee, bedankt')")
                if survey_btn.count() > 0:
                    survey_btn.first.click(timeout=3000)
                    page.wait_for_timeout(500)
            except Exception:
                pass

            page.wait_for_timeout(2000)

            # Check for 404 / error page — try base model name, then make-only
            is_error = "Error Pages" in page.title() or page.locator("h1:has-text('bestaat helaas niet')").count() > 0
            if is_error and model:
                # Try with just the base model word (e.g. "Focus" from "Focus Coupé-Cabriolet")
                base_word = model.split()[0] if model else None
                if base_word and base_word.lower() != _model_to_slug(model):
                    print(f"  No results for {make} {model}, trying base model '{base_word}'...")
                    fallback_url = build_search_url(make, model=base_word, year=year, mileage_km=mileage_km)
                    rate_limiter.wait()
                    page.goto(fallback_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2000)
                    is_error = "Error Pages" in page.title() or page.locator("h1:has-text('bestaat helaas niet')").count() > 0

                if is_error:
                    print(f"  No results for {make} {base_word or model}, trying make-only search...")
                    fallback_url = build_search_url(make, year=year, mileage_km=mileage_km)
                    rate_limiter.wait()
                    page.goto(fallback_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2000)

            # Extract listings using data-* attributes on article elements
            listings_data = page.evaluate("""() => {
                const articles = document.querySelectorAll(
                    'article[data-testid="decluttered-list-item"], article[data-price]'
                );
                return Array.from(articles).slice(0, 30).map(a => ({
                    price: a.dataset.price || null,
                    make: a.dataset.make || null,
                    model: a.dataset.model || null,
                    mileage: a.dataset.mileage || null,
                    fuelType: a.dataset.fuelType || null,
                    firstReg: a.dataset.firstRegistration || null,
                    title: a.querySelector('h2')?.textContent?.trim() || '',
                }));
            }""")

            count = len(listings_data)
            print(f"  Found {count} listings")

            for i, item in enumerate(listings_data):
                try:
                    price_raw = item.get("price")
                    if not price_raw:
                        continue
                    asking_price = float(price_raw)
                    if asking_price <= 0:
                        continue

                    # Year from first registration "08-2019"
                    listing_year = None
                    first_reg = item.get("firstReg") or ""
                    year_match = re.search(r'(20\d{2}|19\d{2})', first_reg)
                    if year_match:
                        listing_year = int(year_match.group(1))

                    listing_mileage = int(item["mileage"]) if item.get("mileage") else None
                    fuel_code = item.get("fuelType") or ""
                    listing_fuel = FUEL_MAP.get(fuel_code, fuel_code)
                    title = item.get("title") or ""
                    title_lower = title.lower()

                    # Always filter by model — check base model name is in title or data
                    if model:
                        base_slug = _model_to_slug(model)
                        data_model = (item.get("model") or "").lower()

                        # Extract the core model name (first word, before body type keywords)
                        # e.g. "Focus Coupé-Cabriolet" → "focus", "A5 Coupé" → "a5"
                        model_words = model.lower().split()
                        core_model = model_words[0] if model_words else base_slug

                        # Match: base_slug in data_model (e.g. "a5" in "a5-sportback")
                        # OR core model matches data_model start
                        # OR model name appears in listing title
                        base_match = (
                            base_slug in data_model
                            or data_model.startswith(core_model)
                            or core_model in title_lower
                        )
                        if not base_match:
                            continue

                        # Filter by body type if the model specifies one
                        # e.g. "A5 Coupé" → only include listings with "coupé"/"cc" in title
                        # e.g. "Focus Coupé-Cabriolet" → same expanded set
                        body_types = _extract_body_types(model)
                        if body_types and not any(bt in title_lower for bt in body_types):
                            continue

                    result = {
                        "make": make,
                        "model": model or title,
                        "year": listing_year,
                        "mileage_km": listing_mileage,
                        "fuel_type": listing_fuel,
                        "asking_price": asking_price,
                        "source_url": "",
                    }
                    results.append(result)

                    repo.upsert_market_price(
                        make=make,
                        model=model or title,
                        asking_price=asking_price,
                        year=listing_year,
                        mileage_km=listing_mileage,
                        fuel_type=listing_fuel,
                        source="autoscout24",
                        source_url="",
                    )

                    print(f"    {make} {model or title} ({listing_year}) - €{asking_price:,.0f} - {listing_mileage or '?'} km")

                except Exception as e:
                    print(f"    Error parsing listing {i}: {e}", file=sys.stderr)
                    continue

        except Exception as e:
            print(f"Error during scraping: {e}", file=sys.stderr)
        finally:
            browser.close()

    repo.close()
    print(f"\nDone. Found {len(results)} market prices.")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape AutoScout24 market prices")
    parser.add_argument("--make", required=True, help="Vehicle make (e.g., BMW)")
    parser.add_argument("--model", help="Vehicle model (e.g., 3-serie)")
    parser.add_argument("--year", type=int, help="Year to search around (±1)")
    parser.add_argument("--mileage", type=int, help="Mileage in km")
    args = parser.parse_args()

    results = run(make=args.make, model=args.model, year=args.year, mileage_km=args.mileage)
    print(json.dumps({"found": len(results), "prices": results}, indent=2, default=str))
