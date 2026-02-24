"""
Troostwijk Vehicle Auction Scraper
Uses Playwright for the JS-rendered troostwijkauctions.com site.

Usage:
    python -m execution.scrape_troostwijk --url "https://www.troostwijkauctions.com/a/..."
    python -m execution.scrape_troostwijk --pages 2 --lots 20
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

from execution.config import SCRAPING_DELAY_SECONDS
from execution.rate_limiter import RateLimiter
from execution.db_repository import Repository

BASE_URL = "https://www.troostwijkauctions.com"
CATEGORY_URL = f"{BASE_URL}/c/transport-en-logistiek/auto%27s/5196727d-c14f-48dc-a2f0-e75f50094a52"


@dataclass
class ScrapedVehicle:
    external_id: str
    url: str
    make: Optional[str] = None
    model: Optional[str] = None
    year: Optional[int] = None
    mileage_km: Optional[int] = None
    fuel_type: Optional[str] = None
    power_hp: Optional[int] = None
    transmission: Optional[str] = None
    body_type: Optional[str] = None
    color: Optional[str] = None
    location: Optional[str] = None
    condition_notes: Optional[str] = None
    mot_expiry: Optional[str] = None
    image_urls: list[str] = None
    auction_name: Optional[str] = None
    current_bid: Optional[float] = None
    bid_count: Optional[int] = None
    end_time: Optional[datetime] = None

    def __post_init__(self):
        if self.image_urls is None:
            self.image_urls = []


DUTCH_MONTHS = {
    "jan": 1, "feb": 2, "mrt": 3, "apr": 4, "mei": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
    "maart": 3, "juni": 6, "juli": 7,
}


def _parse_end_time(text: str) -> Optional[datetime]:
    """Parse Dutch date like '09 feb 2026 14:47' into datetime."""
    if not text:
        return None
    text = text.strip().lower()
    # Pattern: DD month YYYY HH:MM
    m = re.match(r'(\d{1,2})\s+(\w+)\s+(\d{4})\s+(\d{1,2}):(\d{2})', text)
    if not m:
        return None
    day, month_str, year, hour, minute = m.groups()
    month = DUTCH_MONTHS.get(month_str)
    if not month:
        return None
    try:
        return datetime(int(year), month, int(day), int(hour), int(minute))
    except ValueError:
        return None


def extract_lot_id(url: str) -> str:
    """Extract lot ID from URL like /l/...-A1-41416-129."""
    match = re.search(r'(A\d+-\d+-\d+)$', url.rstrip("/"))
    if match:
        return match.group(1)
    match = re.search(r'/(?:lot|kavel|l)/(\d+)', url)
    if match:
        return match.group(1)
    return url.rstrip("/").split("/")[-1]


def parse_int(text: str) -> Optional[int]:
    """Parse integer from text like '123.456 km' or '150 pk'."""
    if not text:
        return None
    nums = re.sub(r'[^\d]', '', text)
    return int(nums) if nums else None


def parse_price(text: str) -> Optional[float]:
    """Parse price from text like '€ 4.900,00' or '€ 26'.

    Dutch convention: dots as thousand separators, commas as decimal.
    """
    if not text:
        return None
    cleaned = re.sub(r'[€\s]', '', text)
    if ',' in cleaned and '.' in cleaned:
        cleaned = cleaned.replace('.', '').replace(',', '.')
    elif ',' in cleaned:
        cleaned = cleaned.replace(',', '.')
    elif '.' in cleaned:
        parts = cleaned.split('.')
        if len(parts) == 2 and len(parts[1]) == 3:
            cleaned = cleaned.replace('.', '')
    try:
        return float(cleaned)
    except ValueError:
        return None


def _dismiss_cookies(page):
    """Dismiss cookie consent dialog."""
    try:
        page.evaluate("document.querySelector('#onetrust-accept-btn-handler')?.click()")
        page.wait_for_timeout(1000)
    except Exception:
        pass


def _collect_lot_urls_from_auction(page, url: str, rate_limiter: RateLimiter, max_lots: int) -> list[str]:
    """Collect lot URLs from an auction page, handling pagination."""
    rate_limiter.wait()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    _dismiss_cookies(page)

    all_urls = set()
    page_num = 1

    while True:
        # Extract lot links from current page (links with /l/ path)
        urls = page.evaluate("""() => {
            const links = document.querySelectorAll('a[href*="/l/"]');
            return [...new Set([...links].map(a => a.href))].filter(h => h.includes('/l/'));
        }""")
        before = len(all_urls)
        all_urls.update(urls)
        print(f"    Page {page_num}: found {len(urls)} lot links ({len(all_urls)} total)")

        if len(all_urls) >= max_lots:
            break

        # Try next page
        next_btn = page.locator('a[aria-label="Next page"], a:has-text("›"), nav a').last
        try:
            # Check if there's a "next" link in pagination
            next_page_url = page.evaluate(f"""() => {{
                const links = document.querySelectorAll('nav a, [class*="pagination"] a');
                for (const link of links) {{
                    const text = link.textContent.trim();
                    if (text === '{page_num + 1}') return link.href;
                }}
                return null;
            }}""")

            if not next_page_url:
                break

            page_num += 1
            rate_limiter.wait()
            page.goto(next_page_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
        except Exception:
            break

    return sorted(all_urls)[:max_lots]


def _scrape_lot_detail(page, url: str, rate_limiter: RateLimiter) -> Optional[ScrapedVehicle]:
    """Scrape a single lot detail page."""
    rate_limiter.wait()

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
    except Exception as e:
        print(f"    Error loading {url}: {e}", file=sys.stderr)
        return None

    lot_id = extract_lot_id(url)

    # Extract all data via JS for speed
    data = page.evaluate("""() => {
        const result = {};

        // Helper: strip zero-width and invisible Unicode chars
        const clean = (s) => s.replace(/[\\u200B\\u200C\\u200D\\uFEFF\\u00AD]/g, '').trim().replace(/\\s+/g, ' ');

        // Title
        const h1 = document.querySelector('h1');
        result.title = h1 ? clean(h1.textContent) : '';

        // Specs (dt/dd pairs)
        const specs = {};
        const dts = document.querySelectorAll('dt');
        dts.forEach(dt => {
            const dd = dt.nextElementSibling;
            if (dd && dd.tagName === 'DD') {
                specs[clean(dt.textContent)] = clean(dd.textContent);
            }
        });
        result.specs = specs;

        // Auction name from breadcrumb or link
        const auctionLink = document.querySelector('a[href*="/a/"]');
        result.auction_name = auctionLink ? clean(auctionLink.textContent) : '';

        // Location
        const locLink = document.querySelector('a[href*="maps.google"]');
        result.location = locLink ? clean(locLink.textContent) : '';

        // Current bid — find dt containing bid keywords (active or closed auctions)
        result.current_bid = null;
        result.bid_count = null;
        for (const dt of dts) {
            const dtText = clean(dt.textContent);
            if (dtText.includes('Huidig bod') || dtText.includes('Definitief bod') ||
                dtText.includes('Current bid') || dtText.includes('Final bid')) {
                const dd = dt.nextElementSibling;
                if (dd && dd.tagName === 'DD') {
                    result.current_bid = clean(dd.textContent);
                }
                const countMatch = dtText.match(/(\\d+)/);
                if (countMatch) result.bid_count = countMatch[1];
                break;
            }
        }

        // Fallback: bid count from "X Biedingen" text
        if (!result.bid_count) {
            const allText = document.body.innerText;
            const bidMatch = allText.match(/(\\d+)\\s*Biedingen?/i);
            result.bid_count = bidMatch ? bidMatch[1] : null;
        }

        // End time / closing time — handle both active ("Sluit in:") and closed ("Kavel gesloten:") auctions
        result.end_time = null;
        const allElements = document.querySelectorAll('*');
        for (const el of allElements) {
            for (const child of el.childNodes) {
                const txt = child.nodeType === 3 ? clean(child.textContent) : '';
                if (txt.startsWith('Sluit in') || txt.startsWith('Kavel gesloten')) {
                    const p = el.querySelector('p');
                    if (p) {
                        result.end_time = clean(p.textContent);
                    } else {
                        const next = child.nextElementSibling || child.nextSibling;
                        if (next) result.end_time = clean(next.textContent || '');
                    }
                    break;
                }
            }
            if (result.end_time) break;
        }

        // Images (exclude tracking pixels)
        const images = [];
        document.querySelectorAll('img[src*="media.tbauctions"], picture source[srcset*="media.tbauctions"]').forEach(el => {
            const src = el.src || el.srcset?.split(' ')[0] || '';
            if (src && !images.includes(src) && !src.includes('bat.bing.com')) images.push(src);
        });
        result.images = images.slice(0, 20);

        // Description
        const descHeading = [...document.querySelectorAll('h5')].find(h => clean(h.textContent).includes('Beschrijving'));
        if (descHeading) {
            const descDiv = descHeading.parentElement?.querySelector('div:last-child');
            result.description = descDiv ? clean(descDiv.textContent) : '';
        }

        // Opmerkingen / Aanvullende details (notes)
        const notesHeading = [...document.querySelectorAll('h5')].find(h =>
            clean(h.textContent).includes('Opmerkingen') || clean(h.textContent).includes('Aanvullende')
        );
        if (notesHeading) {
            const notesDiv = notesHeading.parentElement?.querySelector('div:last-child');
            result.notes = notesDiv ? clean(notesDiv.textContent).substring(0, 500) : '';
        }

        return result;
    }""")

    if not data:
        return None

    vehicle = ScrapedVehicle(external_id=lot_id, url=url)

    # Parse title for make/model
    title = data.get("title", "")
    # Title format: "Mercedes Benz B-klasse 200 Ambition | Car | 2015 | K-330-GP | IAW"
    if "|" in title:
        parts = [p.strip() for p in title.split("|")]
        name_part = parts[0] if parts else ""
    else:
        name_part = title

    # Split first word as make, rest as model
    name_words = name_part.split(None, 1)
    if name_words:
        vehicle.make = name_words[0]
        if len(name_words) > 1:
            vehicle.model = name_words[1]

    vehicle.auction_name = data.get("auction_name") or None
    vehicle.location = data.get("location") or None
    vehicle.current_bid = parse_price(data.get("current_bid"))
    vehicle.bid_count = parse_int(data.get("bid_count"))
    vehicle.end_time = _parse_end_time(data.get("end_time"))
    vehicle.image_urls = data.get("images", [])

    # Condition notes from description + notes
    notes_parts = []
    if data.get("description"):
        notes_parts.append(data["description"])
    if data.get("notes"):
        notes_parts.append(data["notes"][:300])
    vehicle.condition_notes = " | ".join(notes_parts) if notes_parts else None

    # Map specs to fields — use lowercase prefix matching to handle
    # invisible chars or minor text differences from the site
    specs = data.get("specs", {})
    spec_map = {
        "merk": "make",
        "model": "model",
        "bouwjaar": "year",
        "afgelezen kilometerstand": "mileage_km",
        "kilometerstand": "mileage_km",
        "brandstof": "fuel_type",
        "vermogen": "power_hp",
        "transmissie": "transmission",
        "carrosserie": "body_type",
        "kleur": "color",
        "apk": "mot_expiry",
    }

    for spec_key, spec_val in specs.items():
        key_lower = spec_key.lower().strip()
        field = None
        for prefix, f in spec_map.items():
            if key_lower.startswith(prefix):
                field = f
                break
        if not field or not spec_val:
            continue
        val = spec_val
        if field in ("year", "mileage_km", "power_hp"):
            parsed = parse_int(val)
            if parsed:
                setattr(vehicle, field, parsed)
        elif field == "make" and val:
            vehicle.make = val
        elif field == "model" and val:
            vehicle.model = val
        else:
            setattr(vehicle, field, val)

    # Year fallback: extract from "Datum eerste toelating" (e.g. "2013-04-02")
    if not vehicle.year:
        for spec_key, spec_val in specs.items():
            if "datum eerste toelating" in spec_key.lower() and "(nl)" not in spec_key.lower():
                year_match = re.search(r'(19|20)\d{2}', spec_val)
                if year_match:
                    vehicle.year = int(year_match.group(0))
                    break

    # Year fallback: extract from title pipe-separated parts like "... | 2015 | ..."
    if not vehicle.year and "|" in title:
        for part in title.split("|"):
            part = part.strip()
            if re.match(r'^(19|20)\d{2}$', part):
                vehicle.year = int(part)
                break

    return vehicle


def _format_auction_name(raw_name: str, location: Optional[str], auction_id: str) -> str:
    """Build a concise auction name: 'A1-41416 | Alblasserdam | Globe Car Auctions'."""
    parts = []

    # Auction number (e.g. "A1-41416" from external_id "A1-41416-214")
    id_match = re.match(r'(A\d+-\d+)', auction_id)
    if id_match:
        parts.append(id_match.group(1))

    # City from location (e.g. "Alblasserdam" from "Alblasserdam, NL")
    if location:
        city = location.split(",")[0].strip()
        if city:
            parts.append(city)

    # Brief auction name — first meaningful segment, max ~40 chars
    if raw_name:
        brief = raw_name.split(",")[0].strip()
        if len(brief) > 45:
            brief = brief[:42].rsplit(" ", 1)[0] + "..."
        parts.append(brief)

    return " | ".join(parts) if parts else "Troostwijk"


def run(url: Optional[str] = None, pages: int = 2, max_lots: int = 20,
        progress_callback=None) -> list[dict]:
    """Main scraper entry point.

    Args:
        url: Specific auction URL to scrape. If None, scrapes the cars category.
        pages: Number of pages to scrape (only for category browsing).
        max_lots: Maximum lots to scrape.
        progress_callback: Optional callable(current, total, message) for progress updates.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Error: playwright not installed. Run: pip install playwright && playwright install chromium", file=sys.stderr)
        return []

    rate_limiter = RateLimiter(SCRAPING_DELAY_SECONDS)
    repo = Repository()
    results = []

    target_url = url or CATEGORY_URL
    print(f"Scraping Troostwijk: {target_url}")
    print(f"  Max lots: {max_lots}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="nl-NL",
        )
        page = context.new_page()

        try:
            # Step 1: Collect lot URLs
            print("  Collecting lot URLs...")
            lot_urls = _collect_lot_urls_from_auction(page, target_url, rate_limiter, max_lots)
            print(f"  Found {len(lot_urls)} lots to scrape")
            if progress_callback:
                progress_callback(0, len(lot_urls), f"Found {len(lot_urls)} lots to scrape...")

            # Step 2: Scrape each lot detail page
            for i, lot_url in enumerate(lot_urls, 1):
                print(f"  [{i}/{len(lot_urls)}] {lot_url[:80]}...")
                if progress_callback:
                    progress_callback(i, len(lot_urls), f"Scraping lot {i}/{len(lot_urls)}...")
                vehicle = _scrape_lot_detail(page, lot_url, rate_limiter)
                if not vehicle:
                    continue

                # Store in database
                db_vehicle = repo.upsert_vehicle(
                    external_id=vehicle.external_id,
                    source="troostwijk",
                    url=vehicle.url,
                    make=vehicle.make,
                    model=vehicle.model,
                    year=vehicle.year,
                    mileage_km=vehicle.mileage_km,
                    fuel_type=vehicle.fuel_type,
                    power_hp=vehicle.power_hp,
                    transmission=vehicle.transmission,
                    body_type=vehicle.body_type,
                    color=vehicle.color,
                    location=vehicle.location,
                    condition_notes=vehicle.condition_notes,
                    mot_expiry=vehicle.mot_expiry,
                    image_urls_json=json.dumps(vehicle.image_urls),
                )

                if vehicle.auction_name or vehicle.current_bid is not None:
                    formatted_name = _format_auction_name(
                        vehicle.auction_name, vehicle.location, vehicle.external_id
                    )
                    vehicle.auction_name = formatted_name
                    repo.upsert_auction(
                        vehicle_id=db_vehicle.id,
                        auction_name=formatted_name,
                        current_bid=vehicle.current_bid,
                        bid_count=vehicle.bid_count,
                        end_time=vehicle.end_time,
                    )

                if vehicle.current_bid is not None:
                    repo.add_price_history(
                        vehicle_id=db_vehicle.id,
                        bid_amount=vehicle.current_bid,
                        bid_count=vehicle.bid_count,
                    )

                results.append(asdict(vehicle))
                print(f"    Stored: {vehicle.make} {vehicle.model} ({vehicle.year}) - €{vehicle.current_bid or 0:,.0f}")

        except Exception as e:
            print(f"Error during scraping: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
        finally:
            browser.close()

    repo.close()
    print(f"\nDone. Scraped {len(results)} vehicles.")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Troostwijk vehicle auctions")
    parser.add_argument("--url", help="Auction URL to scrape")
    parser.add_argument("--pages", type=int, default=2, help="Number of category pages to scrape")
    parser.add_argument("--lots", type=int, default=20, help="Maximum number of lots to scrape")
    args = parser.parse_args()

    results = run(url=args.url, pages=args.pages, max_lots=args.lots)
    print(json.dumps({"scraped": len(results), "vehicles": results}, indent=2, default=str))
