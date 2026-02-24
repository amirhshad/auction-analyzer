"""
Goods Auction Scraper (non-vehicle items)
Uses Playwright for JavaScript-rendered content.

Usage:
    python -m execution.scrape_goods --url "https://www.troostwijkauctions.com/a/..."
    python -m execution.scrape_goods --url "https://www.onlineveilingmeester.nl/nl/veilingen/..."
"""

import argparse
import json
import re
import sys
from typing import Optional

from execution.config import SCRAPING_DELAY_SECONDS
from execution.rate_limiter import RateLimiter
from execution.db_repository import Repository


MONTH_MAP = {
    # Dutch abbreviated
    "jan": 1, "feb": 2, "mrt": 3, "maa": 3, "apr": 4, "mei": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
    # English abbreviated / full
    "mar": 3, "may": 5, "oct": 10,
    "january": 1, "february": 2, "march": 3, "maart": 3, "april": 4,
    "june": 6, "july": 7, "august": 8, "september": 9,
    "oktober": 10, "october": 10, "november": 11, "december": 12,
}


def _parse_dutch_datetime(text: str):
    """Parse datetime string like '08 feb 2026 21:17' or '08 February 2026, 19:30'."""
    if not text:
        return None
    from datetime import datetime, timezone
    try:
        # Strip commas and extra whitespace
        cleaned = text.strip().replace(",", "")
        parts = cleaned.split()
        if len(parts) >= 4:
            day = int(parts[0])
            month_str = parts[1].lower()
            month = MONTH_MAP.get(month_str) or MONTH_MAP.get(month_str[:3])
            year = int(parts[2])
            time_parts = parts[3].split(":")
            hour = int(time_parts[0])
            minute = int(time_parts[1]) if len(time_parts) > 1 else 0
            if month:
                return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except (ValueError, IndexError):
        pass
    return None


def _format_auction_name(raw_name: str, location: Optional[str], auction_url: str) -> str:
    """Build a concise auction name: 'A1-38027 | Rotterdam | Bezorgveiling diverse retourgoederen'."""
    parts = []

    # Auction number from URL — Troostwijk: /a/.../38027/ or lot external_id A1-38027-123
    id_match = re.search(r'/(\d{4,6})(?:/|$)', auction_url)
    if id_match:
        parts.append(f"A1-{id_match.group(1)}")
    else:
        # Try extracting from lot external_id pattern
        id_match2 = re.search(r'(A\d+-\d+)', auction_url)
        if id_match2:
            parts.append(id_match2.group(1))

    # City from location (e.g. "Rotterdam" from "Rotterdam, NL")
    if location:
        city = location.split(",")[0].strip()
        if city:
            parts.append(city)

    # Brief auction name — first meaningful segment, max ~45 chars
    if raw_name:
        brief = raw_name.split(",")[0].strip()
        if len(brief) > 45:
            brief = brief[:42].rsplit(" ", 1)[0] + "..."
        parts.append(brief)

    return " | ".join(parts) if parts else "Goods Auction"


def normalize_url(url: str) -> str:
    if not url.startswith("http"):
        url = "https://" + url
    return url.rstrip("/")


def parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    cleaned = re.sub(r'[€\s]', '', text)
    # Handle Dutch ",-" suffix meaning zero cents (e.g. "330,-")
    cleaned = re.sub(r',-$', '', cleaned)
    if ',' in cleaned and '.' in cleaned:
        # "1.250,50" → thousands dot, decimal comma
        cleaned = cleaned.replace('.', '').replace(',', '.')
    elif ',' in cleaned:
        # "3450,50" → decimal comma
        cleaned = cleaned.replace(',', '.')
    elif '.' in cleaned:
        # "1.250" → could be thousands separator (Dutch) or decimal
        # If digits after dot are exactly 3, treat as thousands separator
        dot_parts = cleaned.split('.')
        if len(dot_parts) == 2 and len(dot_parts[1]) == 3:
            cleaned = cleaned.replace('.', '')  # thousands separator
    try:
        val = float(cleaned)
        return val if val > 0 else None
    except ValueError:
        return None


def parse_int(text: str) -> Optional[int]:
    if not text:
        return None
    nums = re.sub(r'[^\d]', '', text)
    return int(nums) if nums else None


# Category detection keywords (Dutch + English)
CAT_KEYWORDS = {
    "laptop": "Electronics", "chromebook": "Electronics", "notebook": "Electronics",
    "monitor": "Electronics", "tablet": "Electronics", "iphone": "Electronics",
    "samsung": "Electronics", "smartphone": "Electronics", "telefoon": "Electronics",
    "speaker": "Electronics", "headphone": "Electronics", "koptelefoon": "Electronics",
    "tv": "Electronics", "televisie": "Electronics", "camera": "Electronics",
    "printer": "Electronics", "computer": "Electronics", "gaming": "Electronics",
    "elektr": "Electronics", "electronic": "Electronics",
    "keuken": "Kitchen", "kitchen": "Kitchen", "oven": "Kitchen",
    "koelkast": "Kitchen", "vaatwas": "Kitchen", "blender": "Kitchen",
    "meubel": "Furniture", "furniture": "Furniture", "stoel": "Furniture",
    "tafel": "Furniture", "kast": "Furniture", "bed": "Furniture",
    "gereedschap": "Tools", "tool": "Tools", "boor": "Tools",
    "machine": "Machinery", "machinery": "Machinery",
    "fiets": "Sports", "bike": "Sports", "sport": "Sports",
    "tuin": "Garden", "garden": "Garden",
    "kleding": "Clothing", "schoenen": "Clothing", "fashion": "Clothing",
    "speelgoed": "Toys", "toy": "Toys", "lego": "Toys",
}


def _detect_category(text: str) -> Optional[str]:
    """Detect item category from text."""
    lower = text.lower()
    for keyword, cat in CAT_KEYWORDS.items():
        if keyword in lower:
            return cat
    return None


def _dismiss_cookies(page):
    """Dismiss cookie consent popups."""
    try:
        consent = page.locator(
            "#onetrust-accept-btn-handler, "
            "button:has-text('Alles accepteren'), "
            "button:has-text('Akkoord'), "
            "button:has-text('Accept'), "
            ".cookie-accept"
        )
        if consent.count() > 0:
            consent.first.click(timeout=5000)
            page.wait_for_timeout(1000)
    except Exception:
        pass


def _collect_troostwijk_lot_urls(page, url: str, rate_limiter, max_lots: int) -> list[str]:
    """Collect lot URLs from a Troostwijk auction page with pagination."""
    rate_limiter.wait()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    _dismiss_cookies(page)

    all_urls = set()
    page_num = 1

    while True:
        urls = page.evaluate("""() => {
            const links = document.querySelectorAll('a[href*="/l/"]');
            return [...new Set([...links].map(a => a.href))].filter(h => h.includes('/l/'));
        }""")
        all_urls.update(urls)
        print(f"    Page {page_num}: found {len(urls)} lot links ({len(all_urls)} total)")

        if len(all_urls) >= max_lots:
            break

        # Try next page
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

    return sorted(all_urls)[:max_lots]


def _scrape_troostwijk_lot(page, lot_url: str, rate_limiter) -> Optional[dict]:
    """Scrape a single Troostwijk goods lot detail page using JS evaluate."""
    rate_limiter.wait()
    page.goto(lot_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    data = page.evaluate("""() => {
        const result = {};

        // Helper: strip zero-width and invisible Unicode chars
        const clean = (s) => s.replace(/[\\u200B\\u200C\\u200D\\uFEFF\\u00AD]/g, '').trim().replace(/\\s+/g, ' ');

        // Title
        const h1 = document.querySelector('h1');
        result.title = h1 ? clean(h1.textContent) : '';

        // Specs from dt/dd pairs (all on page)
        const dts = document.querySelectorAll('dt');
        const specs = {};
        for (const dt of dts) {
            const dd = dt.nextElementSibling;
            if (dd && dd.tagName === 'DD') {
                const key = clean(dt.textContent);
                specs[key] = clean(dd.textContent);
            }
        }
        result.specs = specs;

        // Auction name from link
        const auctionLink = document.querySelector('a[href*="/a/"]');
        result.auction_name = auctionLink ? clean(auctionLink.textContent) : '';

        // Current bid — find dt containing "Huidig bod", price is in the next dd
        result.current_bid = null;
        result.bid_count = null;
        for (const dt of dts) {
            const dtText = clean(dt.textContent);
            if (dtText.includes('Huidig bod') || dtText.includes('Huidig Bod') || dtText.includes('Current bid')) {
                const dd = dt.nextElementSibling;
                if (dd && dd.tagName === 'DD') {
                    result.current_bid = clean(dd.textContent);
                }
                // Bid count is inside the dt text as a number (e.g. "Huidig bod (6)")
                const countMatch = dtText.match(/(\\d+)/);
                if (countMatch) {
                    result.bid_count = countMatch[1];
                }
                break;
            }
        }

        // Fallback: bid count from "X Biedingen" text anywhere on page
        if (!result.bid_count) {
            const allText = document.body.innerText;
            const bidCountMatch = allText.match(/(\\d+)\\s*Biedingen?/i);
            if (bidCountMatch) result.bid_count = bidCountMatch[1];
        }

        // End time — find element containing "Sluit in:" and get sibling/child p text
        result.end_time = null;
        const allElements = document.querySelectorAll('*');
        for (const el of allElements) {
            for (const child of el.childNodes) {
                if (child.nodeType === 3 && clean(child.textContent).startsWith('Sluit in')) {
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

        // Location
        result.location = null;
        const locLink = document.querySelector('a[href*="maps.google"]');
        if (locLink) {
            result.location = clean(locLink.textContent);
        } else {
            // Check specs for location keys
            for (const [k, v] of Object.entries(specs)) {
                if (k.includes('Locatie') || k.includes('Location')) {
                    result.location = v;
                    break;
                }
            }
        }

        // Images (exclude tracking pixels)
        const imgs = document.querySelectorAll('img[src*="media.tbauctions"], img[src*="troostwijk"]');
        result.images = [...new Set([...imgs].map(i => i.src).filter(
            s => s && s.startsWith('http') && !s.includes('bat.bing.com') && !s.includes('datocms-assets')
        ))].slice(0, 20);

        // Description — find heading then get content from its sibling
        result.description = '';
        result.notes = '';
        for (const tag of ['h5', 'h4', 'h3', 'h2', 'h6']) {
            const heading = [...document.querySelectorAll(tag)].find(h => {
                const t = clean(h.textContent);
                return t === 'Beschrijving' || t === 'BESCHRIJVING' || t === 'Description';
            });
            if (heading) {
                // Structure: heading is inside a wrapper div, description is in the next sibling div
                const wrapper = heading.parentElement;
                if (wrapper && wrapper.nextElementSibling) {
                    result.description = clean(wrapper.nextElementSibling.textContent).substring(0, 2000);
                } else if (heading.nextElementSibling) {
                    result.description = clean(heading.nextElementSibling.textContent).substring(0, 2000);
                }
                break;
            }
        }

        // Notes / extra details (Aanvullende informatie / Aanvullende details)
        for (const tag of ['h5', 'h4', 'h3', 'h2', 'h6']) {
            const heading = [...document.querySelectorAll(tag)].find(h => {
                const t = clean(h.textContent).toLowerCase();
                return t.includes('aanvullende');
            });
            if (heading) {
                const wrapper = heading.parentElement;
                if (wrapper && wrapper.nextElementSibling) {
                    result.notes = clean(wrapper.nextElementSibling.textContent).substring(0, 1000);
                } else if (heading.nextElementSibling) {
                    result.notes = clean(heading.nextElementSibling.textContent).substring(0, 1000);
                }
                break;
            }
        }

        return result;
    }""")

    if not data or not data.get("title"):
        return None

    raw_specs = data.get("specs", {})
    # Filter out bid/status keys from specs — keep only item-related specs
    skip_keys = {"Huidig bod", "Status", "Minimumprijs"}
    specs = {}
    for k, v in raw_specs.items():
        if not any(sk.lower() in k.lower() for sk in skip_keys):
            specs[k] = v

    title = data.get("title", "")
    description = data.get("description", "")

    # Extract lot ID from URL
    id_match = re.search(r'(A\d+-\d+-\d+)$', lot_url.rstrip("/"))
    external_id = id_match.group(1) if id_match else lot_url.split("/")[-1]

    # Brand from specs or title
    brand = specs.get("Merk") or specs.get("Brand")
    if not brand:
        brand_match = re.match(r'^([A-Z][a-zA-Z]+)', title)
        if brand_match:
            brand = brand_match.group(1)

    # Quantity
    quantity = parse_int(specs.get("Hoeveelheid") or specs.get("Quantity")) or 1

    # Category
    category = _detect_category(title + " " + description)

    # Condition from description keywords
    condition = None
    text_lower = (description + " " + (data.get("notes") or "")).lower()
    if "nieuw" in text_lower or "new" in text_lower:
        condition = "New"
    elif "retour" in text_lower or "return" in text_lower:
        condition = "Return"
    elif "gebruikt" in text_lower or "used" in text_lower:
        condition = "Used"
    elif "beschadigd" in text_lower or "damaged" in text_lower:
        condition = "Damaged"

    return {
        "external_id": external_id,
        "url": lot_url,
        "title": title,
        "description": description,
        "category": category,
        "brand": brand,
        "condition": condition,
        "quantity": quantity,
        "current_bid": parse_price(data.get("current_bid")),
        "bid_count": parse_int(data.get("bid_count")),
        "end_time": data.get("end_time"),
        "location": data.get("location"),
        "auction_name": data.get("auction_name") or "",
        "images": data.get("images", []),
        "specs": specs,
        "notes": data.get("notes"),
    }


def _scrape_ovm_listing_page(page) -> list[dict]:
    """Extract all lot data from the current OVM listing page via JS evaluate.

    OVM is a React/MUI SPA — no stable CSS classes.  We extract data from the
    structured text of each lot card on the listing page, which is much faster
    than visiting each lot detail page individually.
    """
    return page.evaluate("""() => {
        const items = [];
        const allLinks = document.querySelectorAll('a[href*="/lots/"]');
        const seen = new Set();

        for (const a of allLinks) {
            const href = a.href;
            const lotMatch = href.match(/\\/lots\\/(\\d+)$/);
            if (!lotMatch || seen.has(lotMatch[1])) continue;
            seen.add(lotMatch[1]);

            // Walk up to MuiGrid container
            let el = a;
            for (let i = 0; i < 6; i++) {
                el = el.parentElement;
                if (!el) break;
                if (el.className?.includes('MuiGrid')) break;
            }
            if (!el) continue;

            const text = el.innerText;
            const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);

            const title = lines[0] || '';
            let lotNum = '';
            let bids = null;
            let closingTime = '';
            let price = null;

            for (const line of lines) {
                if (line.startsWith('Lot:') || line.startsWith('Kavel:')) {
                    lotNum = line.split(':')[1]?.trim() || '';
                }
                if (line.startsWith('Bids:') || line.startsWith('Biedingen:')) {
                    bids = parseInt(line.split(':')[1]?.trim()) || 0;
                }
                if (line.startsWith('Closing time:') || line.startsWith('Sluitingstijd:')) {
                    closingTime = line.split(':').slice(1).join(':').trim();
                }
                if (line.startsWith('€')) {
                    price = line;
                }
            }

            // Get thumbnail image (prefer 800x600 over 150x150)
            const img = el.querySelector('img');
            let imgSrc = img?.src || '';
            if (imgSrc.includes('150x150')) {
                imgSrc = imgSrc.replace('150x150', '800x600');
            }

            items.push({
                lotNum,
                href,
                title,
                bids,
                closingTime,
                price,
                imgSrc,
            });
        }

        // Auction title (h4 on OVM)
        const auctionTitle = document.querySelector('h4')?.textContent?.trim() || '';

        return { items, auctionTitle };
    }""")


def _scrape_ovm_lot_detail(page, lot_url: str, rate_limiter) -> Optional[dict]:
    """Scrape specs and description from an OVM lot detail page.

    Returns dict with 'description', 'specs', 'condition', 'category', 'notes'
    or None on failure.
    """
    rate_limiter.wait()
    page.goto(lot_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2500)

    data = page.evaluate("""() => {
        const clean = (s) => s ? s.replace(/[\\u200B\\u200C\\u200D\\uFEFF\\u00AD]/g, '').trim().replace(/\\s+/g, ' ') : '';
        const result = { description: '', specs: {}, category: '', notes: '' };

        // Find all dt elements — OVM uses dt for section headers
        const dts = document.querySelectorAll('dt');
        for (const dt of dts) {
            const key = clean(dt.textContent);
            const dd = dt.nextElementSibling;
            if (!dd) continue;
            const value = clean(dd.textContent);

            if (key === 'Specifications' || key === 'Specificaties') {
                // This contains condition/damage notes
                result.description = value.substring(0, 2000);
            } else if (key === 'Details') {
                // Parse key-value pairs from the details text
                // Each line is "Key: Value" or "Key Value"
                const lines = dd.innerText.split('\\n').map(l => l.trim()).filter(l => l.length > 2);
                for (const line of lines) {
                    const colonIdx = line.indexOf(':');
                    if (colonIdx > 0 && colonIdx < 60) {
                        const k = line.substring(0, colonIdx).trim();
                        const v = line.substring(colonIdx + 1).trim();
                        if (k && v) result.specs[k] = v;
                    }
                }
            } else if (key === 'Category' || key === 'Categorie') {
                result.category = value;
            }
        }

        // Also capture shipping/auction notes from bold text
        const strongs = document.querySelectorAll('strong');
        let notesParts = [];
        for (const s of strongs) {
            const txt = clean(s.textContent);
            if (txt.includes('shipped') || txt.includes('verzonden') || txt.includes('Please note') || txt.includes('Let op')) {
                // Get the parent paragraph text
                const parent = s.parentElement;
                if (parent) notesParts.push(clean(parent.textContent));
            }
        }
        if (notesParts.length > 0) {
            result.notes = notesParts.join(' ').substring(0, 1000);
        }

        return result;
    }""")

    if not data:
        return None

    return data


def _collect_ovm_lots(page, url: str, rate_limiter, max_lots: int) -> tuple[list[dict], str]:
    """Collect all lot data from OVM listing pages (with pagination).

    Returns (lots_data, auction_name) extracted directly from listing pages.
    Much faster than visiting each lot detail page individually.
    """
    rate_limiter.wait()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    _dismiss_cookies(page)

    all_lots = []
    auction_name = ""
    page_num = 1

    while True:
        result = _scrape_ovm_listing_page(page)
        items = result.get("items", [])
        if not auction_name:
            auction_name = result.get("auctionTitle", "")

        all_lots.extend(items)
        print(f"    Page {page_num}: found {len(items)} lots ({len(all_lots)} total)")

        if len(all_lots) >= max_lots:
            break

        # Try next page — click the next page number button
        next_page = page_num + 1
        next_btn = page.locator(f"button:text-is('{next_page}'), a:text-is('{next_page}')")
        if next_btn.count() == 0:
            # Also try the ">" / next arrow button
            arrow = page.locator("button:has-text('›'), a:has-text('›')")
            if arrow.count() == 0:
                break
            try:
                arrow.first.click(timeout=5000)
            except Exception:
                break
        else:
            try:
                next_btn.first.click(timeout=5000)
            except Exception:
                break

        page_num += 1
        rate_limiter.wait()
        page.wait_for_timeout(2000)

    # Convert raw listing data to the standard lot dict format
    lots = []
    for item in all_lots[:max_lots]:
        lot_num = item.get("lotNum", "")
        # External ID from lot number (e.g. "8617-1") or URL
        external_id = lot_num or item.get("href", "").split("/")[-1]

        title = item.get("title", "")
        category = _detect_category(title)
        brand = None
        brand_match = re.match(r'^([A-Z][a-zA-Z&]+)', title)
        if brand_match:
            brand = brand_match.group(1)

        img_url = item.get("imgSrc", "")

        lots.append({
            "external_id": external_id,
            "url": item.get("href", ""),
            "title": title,
            "description": "",
            "category": category,
            "brand": brand,
            "condition": "Return",  # OVM home delivery auctions are typically returns
            "quantity": 1,
            "current_bid": parse_price(item.get("price")),
            "bid_count": item.get("bids"),
            "end_time": item.get("closingTime"),
            "location": None,
            "auction_name": auction_name,
            "images": [img_url] if img_url else [],
            "specs": {},
            "notes": None,
        })

    return lots, auction_name


def run(url: str, max_lots: int = 50, progress_callback=None) -> list[dict]:
    """Main scraper entry point.

    Args:
        url: Auction URL to scrape.
        max_lots: Maximum number of lots to scrape.
        progress_callback: Optional callable(current, total, message) for progress updates.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Error: playwright not installed. Run: pip install playwright && playwright install chromium", file=sys.stderr)
        return []

    url = normalize_url(url)
    is_troostwijk = "troostwijk" in url
    source = "troostwijk" if is_troostwijk else "onlineveilingmeester"

    rate_limiter = RateLimiter(SCRAPING_DELAY_SECONDS)
    repo = Repository()
    results = []

    print(f"Scraping goods auction ({source}): {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="nl-NL",
        )
        page = context.new_page()

        try:
            if is_troostwijk:
                # Troostwijk: collect URLs then visit each lot detail page
                print("  Collecting lot URLs...")
                lot_urls = _collect_troostwijk_lot_urls(page, url, rate_limiter, max_lots)

                print(f"  Found {len(lot_urls)} lots to scrape")
                if progress_callback:
                    progress_callback(0, len(lot_urls), f"Found {len(lot_urls)} items to scrape...")

                # Get auction name + location from auction page
                raw_auction_name = ""
                auction_location = None
                rate_limiter.wait()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
                title_el = page.locator("h1")
                if title_el.count() > 0:
                    raw_auction_name = title_el.first.text_content().strip()
                loc_link = page.locator("a[href*='maps.google']")
                if loc_link.count() > 0:
                    auction_location = loc_link.first.text_content().strip()
                auction_name = _format_auction_name(raw_auction_name, auction_location, url)

                all_lots_data = []
                for i, lot_url in enumerate(lot_urls, 1):
                    print(f"  [{i}/{len(lot_urls)}] {lot_url[:80]}...")
                    if progress_callback:
                        progress_callback(i, len(lot_urls), f"Scraping item {i}/{len(lot_urls)}...")
                    try:
                        data = _scrape_troostwijk_lot(page, lot_url, rate_limiter)
                        if data:
                            all_lots_data.append(data)
                    except Exception as e:
                        print(f"    Error scraping item: {e}", file=sys.stderr)
                        continue
            else:
                # OVM: extract listing data then enrich each lot with detail page
                print("  Extracting lots from listing pages...")
                all_lots_data, raw_auction_name = _collect_ovm_lots(page, url, rate_limiter, max_lots)
                auction_location = None
                auction_name = _format_auction_name(raw_auction_name, auction_location, url)

                print(f"  Found {len(all_lots_data)} lots — enriching with detail pages...")
                if progress_callback:
                    progress_callback(0, len(all_lots_data), f"Found {len(all_lots_data)} items, fetching details...")

                # Visit each lot detail page to get specs + description
                for i, lot_data in enumerate(all_lots_data):
                    lot_url = lot_data.get("url", "")
                    if not lot_url:
                        continue
                    print(f"    [{i+1}/{len(all_lots_data)}] Enriching {lot_data['title'][:50]}...")
                    if progress_callback:
                        progress_callback(i + 1, len(all_lots_data), f"Fetching details {i+1}/{len(all_lots_data)}...")
                    try:
                        detail = _scrape_ovm_lot_detail(page, lot_url, rate_limiter)
                        if detail:
                            if detail.get("description"):
                                lot_data["description"] = detail["description"]
                                # Update condition based on description keywords
                                desc_lower = detail["description"].lower()
                                if "defect" in desc_lower or "niet werkend" in desc_lower or "not working" in desc_lower:
                                    lot_data["condition"] = "Defect"
                                elif "damaged" in desc_lower or "beschadigd" in desc_lower:
                                    lot_data["condition"] = "Damaged"
                                elif "not included" in desc_lower or "missing" in desc_lower or "zonder" in desc_lower:
                                    lot_data["condition"] = "Incomplete"
                                elif "new" in desc_lower or "nieuw" in desc_lower:
                                    lot_data["condition"] = "New"
                            if detail.get("specs"):
                                lot_data["specs"] = detail["specs"]
                            if detail.get("category"):
                                lot_data["category"] = detail["category"]
                            if detail.get("notes"):
                                lot_data["notes"] = detail["notes"]
                    except Exception as e:
                        print(f"      Error enriching: {e}", file=sys.stderr)

            # Step 2: Store all lots in DB
            for i, data in enumerate(all_lots_data, 1):
                try:
                    # Update location/auction name from lot data if needed
                    lot_location = data.get("location")
                    if lot_location and not auction_location:
                        auction_location = lot_location
                        auction_name = _format_auction_name(raw_auction_name, auction_location, url)
                    item_auction_name = auction_name

                    if progress_callback:
                        progress_callback(i, len(all_lots_data), f"Storing item {i}/{len(all_lots_data)}...")

                    repo.upsert_goods_item(
                        external_id=data["external_id"],
                        source=source,
                        auction_id=None,
                        auction_name=item_auction_name,
                        url=data["url"],
                        title=data["title"],
                        description=data.get("description"),
                        category=data.get("category"),
                        brand=data.get("brand"),
                        condition=data.get("condition"),
                        quantity=data.get("quantity", 1),
                        current_bid=data.get("current_bid"),
                        bid_count=data.get("bid_count"),
                        estimated_value=None,
                        recommended_max_bid=None,
                        end_time=_parse_dutch_datetime(data.get("end_time")),
                        location=data.get("location"),
                        image_url=data["images"][0] if data.get("images") else None,
                        image_urls_json=json.dumps(data.get("images", [])),
                        specs_json=json.dumps(data.get("specs", {})),
                    )

                    result = {
                        "external_id": data["external_id"],
                        "title": data["title"],
                        "category": data.get("category"),
                        "brand": data.get("brand"),
                        "current_bid": data.get("current_bid"),
                        "url": data["url"],
                    }
                    results.append(result)
                    bid_str = f"€{data['current_bid']:,.0f}" if data.get("current_bid") else "no bid"
                    print(f"    Stored: {data['title'][:60]} ({bid_str})")

                except Exception as e:
                    print(f"    Error storing item: {e}", file=sys.stderr)
                    try:
                        repo.session.rollback()
                    except Exception:
                        pass
                    continue

        except Exception as e:
            print(f"Error during scraping: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
        finally:
            browser.close()

    repo.close()
    print(f"\nDone. Scraped {len(results)} items.")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape goods auctions")
    parser.add_argument("--url", required=True, help="Auction URL")
    parser.add_argument("--max-lots", type=int, default=50, help="Max lots to scrape")
    args = parser.parse_args()

    results = run(url=args.url, max_lots=args.max_lots)
    print(json.dumps({"scraped": len(results), "items": results}, indent=2, default=str))
