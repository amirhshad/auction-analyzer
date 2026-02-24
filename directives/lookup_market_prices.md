# Lookup Market Prices

## Goal
Fetch comparable market prices for a specific vehicle make/model/year from the Dutch market.

## Primary Source: Gaspedaal.nl
**Script:** `execution/scrape_gaspedaal.py`

Gaspedaal is a Dutch aggregator that combines listings from AutoScout24, Marktplaats, AutoTrack, ANWB, viaBOVAG, and 10+ other sites into one search. One request gives data from many sources.

### Why Gaspedaal over AutoScout24
- **More listings** — aggregates 5+ sites, typically 50-200 results vs AutoScout24's 20-30
- **No browser needed** — plain HTTP + JSON-LD extraction (httpx, not Playwright)
- **Faster** — ~1s per request vs 5-10s for Playwright
- **Structured data** — clean JSON-LD with price, year, mileage, fuel, transmission, body type
- **Dutch market focus** — default results are NL only

### URL Pattern
```
https://www.gaspedaal.nl/{make}/{model}?bmin={year-1}&bmax={year+1}&kmax={mileage+30000}
```

### How to Run
```bash
python -m execution.scrape_gaspedaal --make BMW --model "X3" --year 2014 --mileage 133000

# Via CLI (auto-falls back to AutoScout24 if Gaspedaal returns 0)
python main.py market --make BMW --model "X3" --year 2014 --mileage 133000
```

### What It Does
1. Converts model name to Gaspedaal URL slug (Dutch naming: "3-serie" not "3-er-reihe")
2. Fetches search page via HTTP GET (no JS rendering needed)
3. Extracts listings from JSON-LD `<script type="application/ld+json">` tags
4. Filters by core model name and body type keywords
5. Fetches page 2 if >90 results on page 1
6. Stores all prices in `market_prices` table (source="gaspedaal")
7. Clears old prices for same make/model before inserting new ones

### Data Available per Listing
- Price (EUR), Year, Mileage (km)
- Fuel type (Benzine/Diesel/Hybride/Elektriciteit)
- Transmission (Automaat/Handgeschakeld)
- Body type, Color, Number of doors
- Seller location (city + province)

### Edge Cases
- Model slug uses Dutch names (3-serie, not 3-er-reihe like AutoScout24)
- Body type filtering handles hyphens ("Coupé-Cabriolet" → matches "coupé", "cc")
- If model 404s, falls back to make-only search
- Rate limited: 2.5s between requests
- No anti-bot measures observed, but be respectful

## Fallback: AutoScout24
**Script:** `execution/scrape_autoscout24.py`

If Gaspedaal returns 0 results, the CLI falls back to AutoScout24.

- Requires Playwright (JS-rendered)
- Rate limited: 3.0s
- Uses `data-*` attributes on article elements
- Handles cookie consent + survey popups
- AutoScout24 slugs differ from Gaspedaal (e.g., "3-er-reihe" vs "3-serie")

## Price Calculation (price_predictor.py)
1. IQR outlier removal (when ≥4 data points)
2. Mileage-weighted average (if vehicle mileage known)
3. Otherwise median
4. Apply auction discount (default 20%)
5. Mileage adjustment vs market average
6. Fuel type and condition adjustments

## Dashboard Integration
- Button "Lookup Market Prices (Gaspedaal)" on Auto Auctions tab
- Dashboard displays median of stored market prices per vehicle
- Market prices are cleared and re-fetched each time button is clicked
