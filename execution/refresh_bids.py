"""
Refresh Bid Prices — lightweight scraper that updates only current_bid and bid_count
for existing goods items in the database.

Usage:
    python -m execution.refresh_bids
    python -m execution.refresh_bids --auction "Some Auction Name"
"""

import re
import sys
from typing import Optional

from execution.config import SCRAPING_DELAY_SECONDS
from execution.rate_limiter import RateLimiter
from execution.db_repository import Repository


def _parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    cleaned = re.sub(r'[€\s]', '', text)
    cleaned = re.sub(r',-$', '', cleaned)
    if ',' in cleaned and '.' in cleaned:
        cleaned = cleaned.replace('.', '').replace(',', '.')
    elif ',' in cleaned:
        cleaned = cleaned.replace(',', '.')
    elif '.' in cleaned:
        dot_parts = cleaned.split('.')
        if len(dot_parts) == 2 and len(dot_parts[1]) == 3:
            cleaned = cleaned.replace('.', '')
    try:
        val = float(cleaned)
        return val if val > 0 else None
    except ValueError:
        return None


# JS to extract current bid from a Troostwijk lot page
_TROOSTWIJK_BID_JS = """() => {
    const clean = (s) => s ? s.replace(/[\\u200B\\u200C\\u200D\\uFEFF\\u00AD]/g, '').trim().replace(/\\s+/g, ' ') : '';
    const result = { current_bid: null, bid_count: null };
    const dts = document.querySelectorAll('dt');
    for (const dt of dts) {
        const dtText = clean(dt.textContent);
        if (dtText.includes('Huidig bod') || dtText.includes('Huidig Bod') || dtText.includes('Current bid')) {
            const dd = dt.nextElementSibling;
            if (dd && dd.tagName === 'DD') {
                result.current_bid = clean(dd.textContent);
            }
            const countMatch = dtText.match(/(\\d+)/);
            if (countMatch) {
                result.bid_count = parseInt(countMatch[1]);
            }
            break;
        }
    }
    if (!result.bid_count) {
        const allText = document.body.innerText;
        const m = allText.match(/(\\d+)\\s*Biedingen?/i);
        if (m) result.bid_count = parseInt(m[1]);
    }
    return result;
}"""

# JS to extract current bid from an OVM lot page
_OVM_BID_JS = """() => {
    const clean = (s) => s ? s.trim().replace(/\\s+/g, ' ') : '';
    const result = { current_bid: null, bid_count: null };
    // OVM shows price in elements with € prefix
    const allText = document.body.innerText;
    const lines = allText.split('\\n').map(l => l.trim());
    for (const line of lines) {
        if (line.startsWith('€') && line.length < 30) {
            result.current_bid = clean(line);
            break;
        }
    }
    // Bid count
    for (const line of lines) {
        if (line.startsWith('Bids:') || line.startsWith('Biedingen:')) {
            const num = parseInt(line.split(':')[1]?.trim());
            if (!isNaN(num)) result.bid_count = num;
            break;
        }
    }
    return result;
}"""


def refresh_bids(auction_name: Optional[str] = None, progress_callback=None) -> dict:
    """Refresh bid prices for all goods items with URLs.

    Returns dict with 'total', 'updated', 'failed' counts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Error: playwright not installed.", file=sys.stderr)
        return {"total": 0, "updated": 0, "failed": 0}

    repo = Repository()
    items = repo.list_goods_items(limit=9999, auction_name=auction_name)
    items_with_url = [i for i in items if i.url]

    if not items_with_url:
        repo.close()
        return {"total": 0, "updated": 0, "failed": 0}

    total = len(items_with_url)
    updated = 0
    failed = 0
    rate_limiter = RateLimiter(SCRAPING_DELAY_SECONDS)

    print(f"Refreshing bids for {total} items...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            locale="nl-NL",
        )
        page = context.new_page()

        for i, item in enumerate(items_with_url, 1):
            if progress_callback:
                progress_callback(i, total, f"Refreshing bid {i}/{total}...")

            try:
                rate_limiter.wait()
                page.goto(item.url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)

                is_troostwijk = "troostwijk" in item.url
                js = _TROOSTWIJK_BID_JS if is_troostwijk else _OVM_BID_JS
                data = page.evaluate(js)

                new_bid = _parse_price(data.get("current_bid"))
                new_count = data.get("bid_count")

                if new_bid is not None:
                    repo.upsert_goods_item(
                        external_id=item.external_id,
                        source=item.source,
                        current_bid=new_bid,
                        bid_count=new_count,
                    )
                    updated += 1
                    print(f"  [{i}/{total}] {item.title[:40]} — €{new_bid:,.0f}")
                else:
                    print(f"  [{i}/{total}] {item.title[:40]} — no bid found")
                    failed += 1

            except Exception as e:
                print(f"  [{i}/{total}] {item.title[:40]} — error: {e}", file=sys.stderr)
                failed += 1

        browser.close()

    repo.close()
    summary = {"total": total, "updated": updated, "failed": failed}
    print(f"Done: {updated}/{total} updated, {failed} failed")
    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Refresh bid prices for goods items")
    parser.add_argument("--auction", help="Filter by auction name")
    args = parser.parse_args()
    refresh_bids(auction_name=args.auction)
