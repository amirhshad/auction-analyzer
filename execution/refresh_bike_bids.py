"""
Refresh Bike Bid Prices
Uses OVM REST API — no browser needed (bikes are always from OVM).

Usage:
    python -m execution.refresh_bike_bids
    python -m execution.refresh_bike_bids --auction "Tweewielers"
"""

import argparse
import re
import sys
from typing import Optional

import httpx

from execution.config import SCRAPING_DELAY_SECONDS
from execution.rate_limiter import RateLimiter
from execution.db_repository import Repository

OVM_BASE = "https://www.onlineveilingmeester.nl"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}


def _parse_lot_info(url: str):
    """Extract (auction_id, lot_num) from a bike lot URL."""
    m = re.search(r'/(?:auctions|veilingen)/(\d+)/(?:lots|kavels)/(\d+)', url)
    if m:
        return m.group(1), m.group(2)
    return None, None


def refresh_bike_bids(auction_name: Optional[str] = None, progress_callback=None) -> dict:
    """Refresh current_bid and bid_count for all active bikes via OVM REST API."""
    repo = Repository()
    bikes = repo.list_bikes(limit=9999, auction_name=auction_name)
    bikes_with_url = [b for b in bikes if b.url]

    if not bikes_with_url:
        repo.close()
        return {"total": 0, "updated": 0, "failed": 0}

    total = len(bikes_with_url)
    updated = failed = 0
    rate_limiter = RateLimiter(SCRAPING_DELAY_SECONDS)

    print(f"Refreshing bids for {total} bikes via OVM API...")

    client = httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True)
    try:
        for i, bike in enumerate(bikes_with_url, 1):
            if progress_callback:
                progress_callback(i, total, f"Refreshing {i}/{total}...")

            auction_id, lot_num = _parse_lot_info(bike.url)
            if not auction_id:
                failed += 1
                continue

            try:
                rate_limiter.wait()
                resp = client.get(f"{OVM_BASE}/rest/nl/v2/veilingen/{auction_id}/kavels/{lot_num}")
                if resp.status_code != 200:
                    failed += 1
                    continue

                data = resp.json()
                new_bid = data.get("hoogsteBod")
                new_count = data.get("aantalBiedingen")

                repo.upsert_bike(
                    external_id=bike.external_id,
                    source=bike.source,
                    current_bid=new_bid,
                    bid_count=new_count,
                )
                updated += 1
                bid_str = f"€{new_bid:,.0f}" if new_bid else "no bid"
                print(f"  [{i}/{total}] {bike.brand} {bike.model} — {bid_str}")

            except Exception as e:
                print(f"  [{i}/{total}] error: {e}", file=sys.stderr)
                failed += 1

    finally:
        client.close()

    repo.close()
    summary = {"total": total, "updated": updated, "failed": failed}
    print(f"Done: {updated}/{total} updated, {failed} failed")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--auction", help="Filter by auction name")
    args = parser.parse_args()
    refresh_bike_bids(auction_name=args.auction)
