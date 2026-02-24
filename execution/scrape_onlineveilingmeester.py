"""
OnlineVeilingmeester Auction Scraper
Uses Playwright for JavaScript-rendered content.
Supports both Dutch and English URL formats.

Usage:
    python -m execution.scrape_onlineveilingmeester --url "https://www.onlineveilingmeester.nl/nl/veilingen/..."
"""

import argparse
import json
import re
import sys
from typing import Optional

from execution.config import SCRAPING_DELAY_SECONDS
from execution.rate_limiter import RateLimiter
from execution.db_repository import Repository

# URL pattern mappings (Dutch ↔ English)
URL_PATTERNS = {
    "/veilingen/": "/auctions/",
    "/kavels/": "/lots/",
    "/auctions/": "/veilingen/",
    "/lots/": "/kavels/",
}


def normalize_url(url: str) -> str:
    """Ensure URL is well-formed."""
    if not url.startswith("http"):
        url = "https://" + url
    return url.rstrip("/")


def get_lot_urls_pattern(url: str) -> str:
    """Convert auction URL to lots URL pattern."""
    # /veilingen/X → /kavels/X or /auctions/X → /lots/X
    for dutch, english in [("/veilingen/", "/kavels/"), ("/auctions/", "/lots/")]:
        if dutch in url:
            return url.replace(dutch, english.replace("/lots/", "/kavels/"))
    return url


def parse_price(text: str) -> Optional[float]:
    """Parse price from text."""
    if not text:
        return None
    cleaned = re.sub(r'[€\s]', '', text)
    if ',' in cleaned and '.' in cleaned:
        cleaned = cleaned.replace('.', '').replace(',', '.')
    elif ',' in cleaned:
        cleaned = cleaned.replace(',', '.')
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_int(text: str) -> Optional[int]:
    if not text:
        return None
    nums = re.sub(r'[^\d]', '', text)
    return int(nums) if nums else None


def run(url: str) -> list[dict]:
    """Main scraper entry point."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Error: playwright not installed. Run: pip install playwright && playwright install chromium", file=sys.stderr)
        return []

    url = normalize_url(url)
    rate_limiter = RateLimiter(SCRAPING_DELAY_SECONDS)
    repo = Repository()
    results = []

    print(f"Scraping OnlineVeilingmeester: {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="nl-NL",
        )
        page = context.new_page()

        try:
            rate_limiter.wait()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # Handle cookie consent
            try:
                consent = page.locator("button:has-text('Akkoord'), button:has-text('Accept'), .cookie-accept")
                if consent.count() > 0:
                    consent.first.click(timeout=5000)
                    page.wait_for_timeout(1000)
            except Exception:
                pass

            # Extract auction name
            auction_name = ""
            title_el = page.locator("h1, .auction-title, .veilingtitel")
            if title_el.count() > 0:
                auction_name = title_el.first.text_content().strip()

            # Find lot links
            lot_links = page.locator("a[href*='/kavels/'], a[href*='/lots/'], a[href*='/kavel/'], a[href*='/lot/']")
            lot_urls = set()
            for i in range(lot_links.count()):
                href = lot_links.nth(i).get_attribute("href")
                if href:
                    if href.startswith("/"):
                        # Get base domain from url
                        domain = re.match(r'https?://[^/]+', url)
                        href = (domain.group(0) if domain else "") + href
                    lot_urls.add(href)

            print(f"  Auction: {auction_name}")
            print(f"  Found {len(lot_urls)} lots")

            # Scrape each lot
            for i, lot_url in enumerate(sorted(lot_urls), 1):
                rate_limiter.wait()
                print(f"  [{i}/{len(lot_urls)}] {lot_url}")

                try:
                    page.goto(lot_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2000)

                    # Extract lot ID
                    lot_id = re.search(r'/(?:kavel|lot|kavels|lots)/(\d+)', lot_url)
                    external_id = lot_id.group(1) if lot_id else lot_url.split("/")[-1]

                    # Title
                    lot_title = page.locator("h1, .lot-title, .kaveltitel")
                    title_text = lot_title.first.text_content().strip() if lot_title.count() > 0 else ""
                    parts = title_text.split(None, 1)
                    vehicle_make = parts[0] if len(parts) >= 1 else None
                    vehicle_model = parts[1] if len(parts) >= 2 else None

                    # Specs table
                    specs = {}
                    rows = page.locator("table tr, .specs-row, .kavel-specs tr, dl dt")
                    for j in range(rows.count()):
                        row_text = rows.nth(j).text_content().strip()
                        # Try to split on common delimiters
                        for sep in [":", "\t", "  "]:
                            if sep in row_text:
                                k, v = row_text.split(sep, 1)
                                specs[k.strip().lower()] = v.strip()
                                break

                    year = None
                    mileage_km = None
                    fuel_type = None
                    power_hp = None
                    transmission = None
                    color = None
                    condition_notes = None

                    for key, val in specs.items():
                        if any(w in key for w in ("bouwjaar", "year", "jaar")):
                            year = parse_int(val)
                        elif any(w in key for w in ("km", "mileage", "kilometerstand")):
                            mileage_km = parse_int(val)
                        elif any(w in key for w in ("brandstof", "fuel")):
                            fuel_type = val
                        elif any(w in key for w in ("vermogen", "power", "pk")):
                            power_hp = parse_int(val)
                        elif any(w in key for w in ("transmissie", "transmission")):
                            transmission = val
                        elif any(w in key for w in ("kleur", "color", "colour")):
                            color = val
                        elif any(w in key for w in ("staat", "condition", "beschrijving")):
                            condition_notes = val

                    # Images
                    image_els = page.locator("img[src*='lot'], img[src*='kavel'], .gallery img, .lot-images img")
                    image_urls = []
                    for j in range(min(image_els.count(), 20)):
                        src = image_els.nth(j).get_attribute("src") or image_els.nth(j).get_attribute("data-src") or ""
                        if src and "http" in src:
                            image_urls.append(src)

                    # Current bid
                    bid_el = page.locator(".current-bid, .huidige-bieding, [data-current-bid], .bid-amount")
                    current_bid = None
                    if bid_el.count() > 0:
                        current_bid = parse_price(bid_el.first.text_content())

                    # Bid count
                    count_el = page.locator(".bid-count, .aantal-biedingen, [data-bid-count]")
                    bid_count = None
                    if count_el.count() > 0:
                        bid_count = parse_int(count_el.first.text_content())

                    # End time
                    end_el = page.locator(".end-time, .sluitingstijd, time[datetime], [data-end-time]")
                    end_time = None
                    if end_el.count() > 0:
                        end_time = end_el.first.get_attribute("datetime") or end_el.first.text_content().strip()

                    # Store in DB
                    db_vehicle = repo.upsert_vehicle(
                        external_id=external_id,
                        source="onlineveilingmeester",
                        url=lot_url,
                        make=vehicle_make,
                        model=vehicle_model,
                        year=year,
                        mileage_km=mileage_km,
                        fuel_type=fuel_type,
                        power_hp=power_hp,
                        transmission=transmission,
                        color=color,
                        condition_notes=condition_notes,
                        image_urls_json=json.dumps(image_urls),
                    )

                    repo.upsert_auction(
                        vehicle_id=db_vehicle.id,
                        auction_name=auction_name or "OnlineVeilingmeester",
                        current_bid=current_bid,
                        bid_count=bid_count,
                        end_time=end_time,
                    )

                    if current_bid is not None:
                        repo.add_price_history(
                            vehicle_id=db_vehicle.id,
                            bid_amount=current_bid,
                            bid_count=bid_count,
                        )

                    result = {
                        "external_id": external_id,
                        "make": vehicle_make,
                        "model": vehicle_model,
                        "year": year,
                        "mileage_km": mileage_km,
                        "current_bid": current_bid,
                        "bid_count": bid_count,
                        "url": lot_url,
                    }
                    results.append(result)
                    print(f"    Stored: {vehicle_make} {vehicle_model} ({year})")

                except Exception as e:
                    print(f"    Error scraping lot: {e}", file=sys.stderr)
                    continue

        except Exception as e:
            print(f"Error during scraping: {e}", file=sys.stderr)
        finally:
            browser.close()

    repo.close()
    print(f"\nDone. Scraped {len(results)} vehicles.")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape OnlineVeilingmeester auctions")
    parser.add_argument("--url", required=True, help="Auction URL (Dutch or English format)")
    args = parser.parse_args()

    results = run(url=args.url)
    print(json.dumps({"scraped": len(results), "vehicles": results}, indent=2, default=str))
