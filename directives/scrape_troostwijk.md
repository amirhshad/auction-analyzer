# Scrape Troostwijk Vehicle Auctions

## Goal
Scrape vehicle auction listings from troostwijkauctions.com and store them in the database.

## Script
`execution/scrape_troostwijk.py`

## Inputs
- `--url URL` — Specific auction URL to scrape (e.g. `https://www.troostwijkauctions.com/a/...`)
- `--pages N` — Number of category pages to scrape when no URL given (default: 2)
- `--lots N` — Maximum number of individual lots to scrape (default: 20)

## How to Run
```bash
# Scrape a specific auction by URL
python -m execution.scrape_troostwijk --url "https://www.troostwijkauctions.com/a/globe-car-auctions-..." --lots 50

# Scrape from general vehicle category
python -m execution.scrape_troostwijk --pages 2 --lots 20

# Via CLI
python main.py scrape --url "https://www.troostwijkauctions.com/a/..." --lots 50
```

## What It Does
1. If `--url` given: navigates to that auction page and collects lot links
2. Otherwise: navigates to Troostwijk vehicle category pages
3. Collects `/l/` lot URLs from listing pages (handles pagination)
4. Visits each lot detail page
5. Extracts: make, model, year, mileage, fuel, power, transmission, body type, color, MOT expiry, condition notes, images, bid info, end time
6. Deduplicates by external_id + source
7. Stores vehicles, auctions, and price history in DB
8. Outputs JSON summary

## Output
- Vehicles stored in `vehicles` table
- Auction data stored in `auctions` table
- Price snapshots stored in `price_history` table
- JSON summary printed to stdout

## Tech
- Playwright (Chromium headless) — site is JS-rendered, requires browser
- Cookie consent dismissed via `#onetrust-accept-btn-handler`
- Rate limited: 2.5s between requests
- Lot detail extraction via `page.evaluate()` JS

## Site Structure (as of Feb 2026)
- Domain: `troostwijkauctions.com` (not `troostwijk.com`)
- Auction URLs: `/a/{slug}-A1-{id}`
- Lot URLs: `/l/{slug}-A1-{auction_id}-{lot_id}`
- Lot detail specs: `dt/dd` pairs with Dutch labels (Merk, Model, Bouwjaar, etc.)
- Images hosted on `media.tbauctions.com`
- End times in Dutch format: "09 feb 2026 14:47"

## Edge Cases
- Cookie consent popup must be dismissed before interaction
- Pagination: if more than ~48 lots, need to click page 2, 3, etc.
- End time is a Dutch date string — parsed via `_parse_end_time()`
- Some lots may have incomplete specs (missing model, mileage, etc.)
- Lot links use `/l/` path pattern, extracted via `a[href*="/l/"]`
- If a lot page fails to load, it's skipped (logged to stderr)
- Deduplication prevents duplicate entries on re-runs
