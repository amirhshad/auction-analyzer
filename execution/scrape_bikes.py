"""
OnlineVeilingmeester Bike Auction Scraper
Uses the OVM REST API — no browser needed.

Usage:
    python -m execution.scrape_bikes --url "https://onlineveilingmeester.nl/en/auctions/9086/lots"
"""

import argparse
import json
import re
import sys
from datetime import datetime
from typing import Optional

import httpx

from execution.config import SCRAPING_DELAY_SECONDS
from execution.rate_limiter import RateLimiter
from execution.db_repository import Repository

OVM_BASE = "https://www.onlineveilingmeester.nl"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}
IMAGE_BASE = "https://www.onlineveilingmeester.nl/images/800x600"


def _extract_auction_id(url: str) -> Optional[str]:
    m = re.search(r'/(?:auctions|veilingen)/(\d+)', url)
    return m.group(1) if m else None


def _strip_html(html: str) -> str:
    """Strip HTML tags, converting <br> to newlines."""
    if not html:
        return ""
    text = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _extract_bike_fields(kavel_data: dict) -> dict:
    """Map OVM kavelData fields to bike model fields."""
    naam = kavel_data.get("naam", "") or ""
    bike_type = kavel_data.get("product") or None
    brand = kavel_data.get("merk") or None
    model = kavel_data.get("productType") or None

    # Fallback brand/type from naam (format: "Racefiets, Cannondale, SystemSix, Color")
    if not brand and naam:
        parts = [p.strip() for p in naam.split(",") if p.strip()]
        if not bike_type and parts:
            bike_type = parts[0]
        if len(parts) >= 2 and not brand:
            brand = parts[1]
        if len(parts) >= 3 and not model:
            model = parts[2]

    return {
        "bike_type": bike_type,
        "brand": brand,
        "model": model,
        "frame_size": kavel_data.get("maatvoering") or None,
        "color": kavel_data.get("kleur") or None,
        "condition": kavel_data.get("conditie") or None,
        "components": _strip_html(kavel_data.get("specificaties", "") or "") or None,
        "notes": _strip_html(kavel_data.get("bijzonderheden", "") or "") or None,
    }


def run(url: str, progress_callback=None) -> list[dict]:
    """Scrape a bike auction from OVM REST API and store in DB."""
    auction_id = _extract_auction_id(url)
    if not auction_id:
        print(f"Error: could not extract auction ID from URL: {url}", file=sys.stderr)
        return []

    rate_limiter = RateLimiter(SCRAPING_DELAY_SECONDS)
    repo = Repository()
    results = []

    print(f"Scraping OVM bike auction {auction_id}...")

    client = httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True)
    try:
        rate_limiter.wait()
        resp = client.get(f"{OVM_BASE}/rest/en/veilingen/{auction_id}/kavelVolgNummers")
        resp.raise_for_status()
        lot_numbers = resp.json().get("volgNummers", [])

        # Get auction name from first lot
        auction_name = "OnlineVeilingmeester"
        if lot_numbers:
            rate_limiter.wait()
            r = client.get(f"{OVM_BASE}/rest/nl/v2/veilingen/{auction_id}/kavels/{lot_numbers[0]}")
            if r.status_code == 200:
                auction_name = r.json().get("veiling", {}).get("naam", auction_name)

        total = len(lot_numbers)
        print(f"  Auction: {auction_name} — {total} lots")

        if progress_callback:
            progress_callback(0, total, f"Found {total} lots...")

        for i, lot_num in enumerate(lot_numbers, 1):
            if progress_callback:
                progress_callback(i, total, f"Fetching lot {i}/{total}...")

            try:
                rate_limiter.wait()
                resp = client.get(f"{OVM_BASE}/rest/nl/v2/veilingen/{auction_id}/kavels/{lot_num}")
                if resp.status_code != 200:
                    continue

                data = resp.json()
                kd = data.get("kavelData", {})
                fields = _extract_bike_fields(kd)

                current_bid = data.get("hoogsteBod")
                bid_count = data.get("aantalBiedingen")
                end_time = None
                end_str = data.get("sluitingsDatumISO")
                if end_str:
                    try:
                        end_time = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pass

                image_list = data.get("imageList", [])[:20]
                image_urls = [f"{IMAGE_BASE}/{img}" for img in image_list]
                lot_url = f"{OVM_BASE}/en/auctions/{auction_id}/lots/{lot_num}"

                bike = repo.upsert_bike(
                    external_id=str(lot_num),
                    source="onlineveilingmeester",
                    url=lot_url,
                    auction_name=auction_name,
                    current_bid=current_bid,
                    bid_count=bid_count,
                    end_time=end_time,
                    image_urls_json=json.dumps(image_urls) if image_urls else None,
                    **fields,
                )

                result = {
                    "external_id": str(lot_num),
                    "bike_type": fields["bike_type"],
                    "brand": fields["brand"],
                    "model": fields["model"],
                    "current_bid": current_bid,
                    "url": lot_url,
                }
                results.append(result)
                bid_str = f"€{current_bid:,.0f}" if current_bid else "no bid"
                print(f"  [{i}/{total}] {fields['bike_type']} {fields['brand']} {fields['model']} — {bid_str}")

            except Exception as e:
                print(f"  [{i}/{total}] Lot {lot_num} error: {e}", file=sys.stderr)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise
    finally:
        client.close()

    repo.close()
    print(f"\nDone. Scraped {len(results)} bikes.")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape OVM bike auctions")
    parser.add_argument("--url", required=True, help="Auction URL")
    args = parser.parse_args()
    results = run(url=args.url)
    print(json.dumps({"scraped": len(results), "bikes": results}, indent=2, default=str))
