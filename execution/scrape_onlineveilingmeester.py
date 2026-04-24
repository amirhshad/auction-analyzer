"""
OnlineVeilingmeester Auction Scraper
Uses the OVM REST API for reliable data extraction.

Usage:
    python -m execution.scrape_onlineveilingmeester --url "https://www.onlineveilingmeester.nl/en/auctions/8673/lots"
"""

import argparse
import json
import re
import sys
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
    """Extract auction ID from URL like /auctions/8673/lots or /veilingen/8673/kavels."""
    m = re.search(r'/(?:auctions|veilingen)/(\d+)', url)
    return m.group(1) if m else None


def _parse_mileage_from_specs(specs_html: str) -> Optional[int]:
    """Extract mileage from HTML specs text like 'Afgelezen tellerstand: 155.686'."""
    if not specs_html:
        return None
    # Strip HTML tags
    text = re.sub(r'<[^>]+>', '\n', specs_html)
    for line in text.split('\n'):
        line = line.strip()
        if any(w in line.lower() for w in ('tellerstand', 'kilometerstand')):
            nums = re.sub(r'[^\d]', '', line.split(':')[-1] if ':' in line else line)
            if nums:
                return int(nums)
    return None


def _parse_fuel_from_specs(specs_html: str) -> Optional[str]:
    """Extract fuel type from HTML specs text."""
    if not specs_html:
        return None
    text = re.sub(r'<[^>]+>', '\n', specs_html)
    for line in text.split('\n'):
        line = line.strip()
        if 'brandstof' in line.lower() or 'fuel' in line.lower():
            val = line.split(':')[-1].strip() if ':' in line else None
            if val:
                return val.capitalize()
    return None


_TRANSMISSION_MAP = {
    "AUTOMAAT": "Automatic",
    "HANDGESCHAKELD": "Manual",
    "SEMI_AUTOMAAT": "Semi-automatic",
}

_FUEL_MAP = {
    "DIESEL": "Diesel",
    "BENZINE": "Petrol",
    "ELEKTRISCH": "Electric",
    "HYBRIDE": "Hybrid",
    "LPG": "LPG",
    "CNG": "CNG",
}


def run(url: str, progress_callback=None) -> list[dict]:
    """Main scraper entry point. Uses OVM REST API for reliable data extraction."""
    auction_id = _extract_auction_id(url)
    if not auction_id:
        print(f"Error: Could not extract auction ID from URL: {url}", file=sys.stderr)
        return []

    rate_limiter = RateLimiter(SCRAPING_DELAY_SECONDS)
    repo = Repository()
    results = []

    print(f"Scraping OnlineVeilingmeester auction {auction_id} via API...")

    client = httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True)

    try:
        # Step 1: Get list of lot numbers
        rate_limiter.wait()
        resp = client.get(f"{OVM_BASE}/rest/en/veilingen/{auction_id}/kavelVolgNummers")
        resp.raise_for_status()
        lot_numbers = resp.json().get("volgNummers", [])

        # Get auction name
        rate_limiter.wait()
        resp_info = client.get(f"{OVM_BASE}/rest/nl/v2/veilingen/{auction_id}/kavels/{lot_numbers[0]}")
        auction_name = "OnlineVeilingmeester"
        if resp_info.status_code == 200:
            veiling = resp_info.json().get("veiling", {})
            auction_name = veiling.get("naam", auction_name)

        total = len(lot_numbers)
        print(f"  Auction: {auction_name}")
        print(f"  Found {total} lots")

        if progress_callback:
            progress_callback(0, total, f"Found {total} lots to scrape...")

        # Step 2: Fetch each lot from the Dutch API (has full specs)
        for i, lot_num in enumerate(lot_numbers, 1):
            if progress_callback:
                progress_callback(i, total, f"Fetching lot {i}/{total}...")

            try:
                rate_limiter.wait()
                resp = client.get(f"{OVM_BASE}/rest/nl/v2/veilingen/{auction_id}/kavels/{lot_num}")
                if resp.status_code != 200:
                    print(f"  [{i}/{total}] Lot {lot_num} — HTTP {resp.status_code}", file=sys.stderr)
                    continue

                data = resp.json()
                kd = data.get("kavelData", {})

                # Extract vehicle info from kavelData
                title = kd.get("naam", "")
                make = kd.get("merk")
                model = kd.get("productType")
                year_str = kd.get("bouwjaar")
                year = int(year_str) if year_str and year_str.isdigit() else None
                mileage_km = kd.get("kilometerstand")
                if isinstance(mileage_km, str):
                    mileage_km = int(re.sub(r'[^\d]', '', mileage_km)) if re.sub(r'[^\d]', '', mileage_km) else None

                # Fuel and transmission from kavelData or specs HTML
                fuel_raw = kd.get("brandstof", "")
                fuel_type = _FUEL_MAP.get(fuel_raw.upper()) if fuel_raw else None
                trans_raw = kd.get("transmissie", "")
                transmission = _TRANSMISSION_MAP.get(trans_raw.upper()) if trans_raw else None

                condition = kd.get("conditie")
                specs_html = kd.get("specificaties", "")

                # Fallbacks from specs HTML
                if not mileage_km:
                    mileage_km = _parse_mileage_from_specs(specs_html)
                if not fuel_type:
                    fuel_type = _parse_fuel_from_specs(specs_html)

                # Fallback: parse title for make/model/year
                if not make and title:
                    parts = [p.strip() for p in title.split(",") if p.strip()]
                    if len(parts) >= 2:
                        make = parts[1]
                    if len(parts) >= 3:
                        last = parts[-1].strip()
                        if re.match(r'^(19|20)\d{2}$', last):
                            if not year:
                                year = int(last)
                            model = ", ".join(parts[2:-1]) if len(parts) > 3 else None
                        else:
                            model = ", ".join(parts[2:])

                # Current bid and bid count
                current_bid = data.get("hoogsteBod")
                bid_count = data.get("aantalBiedingen")
                end_time_str = data.get("sluitingsDatumISO")
                end_time = None
                if end_time_str:
                    from datetime import datetime
                    try:
                        end_time = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pass

                # Images (first 20)
                image_list = data.get("imageList", [])[:20]
                image_urls = [f"{IMAGE_BASE}/{img}" for img in image_list]

                # Lot URL
                lot_url = f"{OVM_BASE}/en/auctions/{auction_id}/lots/{lot_num}"

                # Store in DB
                external_id = str(lot_num)
                db_vehicle = repo.upsert_vehicle(
                    external_id=external_id,
                    source="onlineveilingmeester",
                    url=lot_url,
                    make=make,
                    model=model,
                    year=year,
                    mileage_km=mileage_km,
                    fuel_type=fuel_type,
                    transmission=transmission,
                    condition_notes=condition,
                    image_urls_json=json.dumps(image_urls) if image_urls else None,
                )

                repo.upsert_auction(
                    vehicle_id=db_vehicle.id,
                    auction_name=auction_name,
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
                    "make": make,
                    "model": model,
                    "year": year,
                    "mileage_km": mileage_km,
                    "fuel_type": fuel_type,
                    "current_bid": current_bid,
                    "bid_count": bid_count,
                    "url": lot_url,
                }
                results.append(result)
                bid_str = f"€{current_bid:,.0f}" if current_bid else "no bid"
                km_str = f"{mileage_km:,} km" if mileage_km else "? km"
                print(f"  [{i}/{total}] {make} {model} ({year}) — {km_str} — {bid_str}")

            except Exception as e:
                print(f"  [{i}/{total}] Lot {lot_num} — error: {e}", file=sys.stderr)
                continue

    except Exception as e:
        print(f"Error during scraping: {e}", file=sys.stderr)
        raise
    finally:
        client.close()

    repo.close()
    print(f"\nDone. Scraped {len(results)} vehicles.")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape OnlineVeilingmeester auctions")
    parser.add_argument("--url", required=True, help="Auction URL (Dutch or English format)")
    args = parser.parse_args()

    results = run(url=args.url)
    print(json.dumps({"scraped": len(results), "vehicles": results}, indent=2, default=str))
