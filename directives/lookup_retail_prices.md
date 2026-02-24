# Lookup Retail Prices (bol.com + Amazon.nl)

## Goal
Fetch retail/market prices for goods auction items from bol.com and Amazon.nl to estimate real value.

## Script
`execution/scrape_retail_prices.py`

## Inputs
- `--query "Samsung Monitor 32"` — Direct search query
- `--goods-id 42` — Look up price for a specific goods item ID
- `--auction "Bezorgveiling diverse retourgoederen"` — Look up all items in an auction

## How to Run
```bash
# Single query
python -m execution.scrape_retail_prices --query "Acer Chromebook CB515"

# Specific item
python -m execution.scrape_retail_prices --goods-id 42

# All items in an auction
python -m execution.scrape_retail_prices --auction "Bezorgveiling diverse retourgoederen"

# Via CLI
python main.py lookup-prices --auction "Bezorgveiling diverse retourgoederen"
```

## What It Does
1. Takes goods item titles and builds clean search queries (strips lot IDs, noise words)
2. Checks `goods_price_cache` for existing lookups first
3. Searches bol.com: `https://www.bol.com/nl/nl/s/?searchtext={query}`
4. Searches Amazon.nl: `https://www.amazon.nl/s?k={query}`
5. Extracts product prices from search result cards
6. Calculates median price from all results
7. Stores in `goods_price_cache` table with confidence score
8. Updates `goods_items` with estimated_value and recommended_max_bid
9. Applies condition-based discount:
   - New: 10% discount
   - Return: 30% discount
   - Used: 50% discount
   - Damaged: 70% discount
   - Unknown: 35% discount
10. Recommended max bid = estimated_value × 0.80 (20% safety margin)

## Output
- Prices cached in `goods_price_cache` table
- `goods_items.estimated_value` and `goods_items.recommended_max_bid` updated
- JSON summary printed to stdout

## Tech
- Playwright (JavaScript-rendered sites)
- Rate limited: 2.5s between requests
- Progress callback support for dashboard integration
- Reuses browser session across items for efficiency

## bol.com Structure (as of Feb 2026)
- Search URL: `/nl/nl/s/?searchtext={query}`
- Product cards: `[data-test="product-card"]` or `.product-item--row`
- Price: `.promo-price` or `[data-test="price-current"]`
- Cookie consent: `button#js-first-screen-accept`

## Amazon.nl Structure (as of Feb 2026)
- Search URL: `/s?k={query}`
- Product cards: `[data-component-type="s-search-result"]`
- Price: `.a-price .a-price-whole` + `.a-price-fraction`
- Cookie consent: `#sp-cc-accept`

## Edge Cases
- Query cleanup: strips A1-XXXXX lot IDs, noise words (kavel, lot, retour, veiling)
- Long titles are truncated to 100 chars for search
- Confidence score: `min(num_results / 5.0, 1.0)` — more results = higher confidence
- If no results found on either site, item keeps null estimated_value
- Cached prices are reused to avoid redundant lookups
- Both sites may block aggressive scraping — rate limit is enforced
