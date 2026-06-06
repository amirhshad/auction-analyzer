# Lookup Bike Market Prices

## Goal
Fetch comparable used bike prices from Marktplaats.nl for market comparison.

## Script
`execution/scrape_marktplaats.py`

## Inputs
- `--brand BRAND` — Bike brand (required)
- `--model MODEL` — Bike model (optional)

## How to Run
```bash
# Single bike
python -m execution.scrape_marktplaats --brand Cannondale --model SystemSix

# All bikes in DB (from dashboard or orchestrator)
from execution.scrape_marktplaats import run_for_all_bikes
run_for_all_bikes(auction_name="Tweewielers")
```

## What It Does
1. Builds Marktplaats search URL for brand + model
2. Fetches HTML via plain HTTP (no Playwright)
3. Extracts prices from JSON-LD script tags (primary)
4. Falls back to regex price extraction if JSON-LD yields < 3 results
5. If brand+model yields < 3 results, retries with brand-only
6. Clears old prices for same brand/model, stores new ones in `bike_market_prices`

## Output
- Prices stored in `bike_market_prices` table (source: "marktplaats")
- Returns `{"brand", "model", "fetched", "prices"}` dict

## Edge Cases
- Dutch price format: €1.999,- = 1999, €2.600,00 = 2600
- Prices filtered to range €50–€50,000 (excludes accessories, exotic bikes)
- Brand-only fallback when model is too specific to find listings
- Rate limited: 2.5s between requests
