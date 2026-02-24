# Scrape OnlineVeilingmeester

## Goal
Scrape vehicle auction listings from OnlineVeilingmeester and store in database.

## Script
`execution/scrape_onlineveilingmeester.py`

## Inputs
- `--url AUCTION_URL` — Full URL of the auction (required)

## How to Run
```bash
python -m execution.scrape_onlineveilingmeester --url "https://www.onlineveilingmeester.nl/nl/veilingen/auto-veiling-123"
```

## What It Does
1. Navigates to auction page
2. Extracts auction name
3. Finds all lot/kavel links on the page
4. Visits each lot detail page
5. Extracts: make, model, year, mileage, fuel, power, transmission, color, condition, images, bid info
6. Stores vehicles, auctions, and price history in DB

## Output
- Vehicles stored in `vehicles` table (source: "onlineveilingmeester")
- Auction data stored in `auctions` table
- JSON summary printed to stdout

## Tech
- Playwright (JavaScript-rendered site)
- Rate limited: 2.5s between requests

## URL Format Support
Both Dutch and English URL formats are supported:
- Dutch: `/veilingen/...` and `/kavels/...`
- English: `/auctions/...` and `/lots/...`

## Edge Cases
- Site uses both Dutch and English paths interchangeably
- Cookie consent popup must be dismissed
- Lot IDs extracted from URL path
- Specs table format varies between auctions
