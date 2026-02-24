# AI-Evaluate Goods Auction Items

## Goal
Use GPT-4o-mini to evaluate goods auction items — estimate realistic market value, recommend max bid, assess risk, and provide plain-language explanation based on item specs, description, and auction data.

## Script
`execution/goods_evaluator.py`

## Inputs
- `--auction "name"` — Filter by auction name (optional)
- `--item-id N` — Evaluate a specific item (optional)
- `--force` — Force re-evaluation, ignoring cache (optional)

If no flags provided, evaluates favorites + top 15 deals by savings percentage.

## How to Run
```bash
# Evaluate all top deals + favorites
python -m execution.goods_evaluator

# Evaluate items in a specific auction
python -m execution.goods_evaluator --auction "Bezorgveiling diverse retourgoederen"

# Evaluate a single item
python -m execution.goods_evaluator --item-id 42

# Force re-evaluation (ignore hash cache)
python -m execution.goods_evaluator --auction "..." --force
```

Or via main CLI:
```bash
python main.py evaluate-goods --auction "..."
```

Or via dashboard: click **"AI Evaluate Deals (GPT-4o-mini)"** button on the Goods Auctions page.

## COST WARNING
This script uses the OpenAI API (GPT-4o-mini). Cost is approximately **$0.0003 per item**.
For 50 items: ~$0.015. For 100 items: ~$0.03. Very low cost.
Still, the script uses hash-based caching to avoid re-evaluating unchanged items.

## Prerequisites
- Items must be scraped into database (run scrape-goods first)
- Ideally run `lookup-prices` first so estimated_value is populated (used to select top deals)
- OPENAI_API_KEY must be set in .env

## What It Does
1. Selects items to evaluate: all favorites + top 15 deals by savings percentage
2. For each item, computes a hash of title + description + specs + bid + condition
3. Skips items whose hash hasn't changed since last evaluation
4. Builds a prompt with: title, brand, category, condition, quantity, description, all specs, current bid, bid count, time remaining
5. Calls GPT-4o-mini (temperature=0.3 for consistent pricing)
6. Parses JSON response: estimated_market_value, recommended_max_bid, risk_level, confidence, explanation
7. Saves results to ai_* columns on the goods_items table

## What AI Considers
- **Dutch auction terms**: Merk=Brand, Zonder=Without, Hoeveelheid=Quantity, etc.
- **Condition keywords**: "defect"=broken (parts only), "retour"=customer return, "niet werkend"=not working
- **Missing accessories**: "zonder voedingskabel"=no power cable, "zonder adapter"=no adapter (reduces value)
- **Realistic used market value** (not retail), accounting for actual condition
- **Quantity**: per-lot pricing for multi-item lots

## Output
JSON summary to stdout:
```json
{
  "evaluated": 12,
  "skipped": 3,
  "errors": 0
}
```

Results are saved to database and displayed in dashboard cards (AI Value, AI Max Bid, Risk, explanation).

## Edge Cases
- No OPENAI_API_KEY → error message, returns empty summary
- Item with no specs or description → prompt handles gracefully ("No specifications available")
- GPT response not valid JSON → fallback with risk_level="high" and explanation of failure
- API error → logged, item counted as error, continues to next item
- Hash unchanged → skipped (no API call), use --force to override
- Items with no estimated_value → still evaluated if favorited, skipped from "top deals" selection

## Learnings
- temperature=0.3 gives more consistent price estimates than default
- Dutch specs must be explained in the prompt or GPT misinterprets them
- Description capped at 500 chars to control token usage
