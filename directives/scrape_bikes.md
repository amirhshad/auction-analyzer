# Scrape Bike Auctions

## Goal
Scrape bicycle auction listings from OnlineVeilingmeester and store in database.

## Script
`execution/scrape_bikes.py`

## Inputs
- `--url AUCTION_URL` — Full URL of the bike auction (required)

## How to Run
```bash
python -m execution.scrape_bikes --url "https://onlineveilingmeester.nl/en/auctions/9086/lots"
```

## What It Does
1. Extracts auction ID from URL
2. Calls OVM REST API to get lot numbers
3. Fetches each lot detail page via API
4. Extracts: bike_type, brand, model, frame_size, color, condition, components, notes, images, bid info
5. Stores in `bikes` table (upsert by external_id + source)

## Output
- Bikes stored in `bikes` table (source: "onlineveilingmeester")
- JSON summary printed to stdout

## Tech
- Plain HTTP via httpx (OVM REST API — no Playwright needed)
- Rate limited: 2.5s between requests

## OVM API Endpoints
- `GET /rest/en/veilingen/{auction_id}/kavelVolgNummers` — lot number list
- `GET /rest/nl/v2/veilingen/{auction_id}/kavels/{lot_num}` — lot detail

## Edge Cases
- Some lots have no `merk` (brand) — fallback: second comma-segment of `naam`
- Some lots have no `maatvoering` (frame_size) — stored as null
- HTML in `specificaties` and `bijzonderheden` is stripped to plain text
- Auction ID extracted from both Dutch (/veilingen/) and English (/auctions/) URLs
