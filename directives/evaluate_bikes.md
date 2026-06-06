# Evaluate Bikes

## Goal
Run full evaluation pipeline on bikes: Marktplaats market prices + AI evaluation + image analysis.

## Scripts
1. `execution/scrape_marktplaats.py` — market price lookup
2. `execution/bike_evaluator.py` — AI deal evaluation
3. `execution/image_analyzer.py` — image condition analysis

## How to Run
```bash
# Step 1: Lookup market prices for all bikes in auction
from execution.scrape_marktplaats import run_for_all_bikes
run_for_all_bikes(auction_name="Tweewielers")

# Step 2: AI evaluation (reads market prices from DB)
python -m execution.bike_evaluator --auction "Tweewielers"

# Step 3: Image analysis
python -m execution.image_analyzer --bike-id 1 --max-images 5
```

## Pipeline Order
Always run market prices BEFORE AI evaluation — the evaluator reads prices from the DB to include in its prompt.

## AI Evaluation
- Uses GPT-4o-mini
- Evaluates: brand prestige, component tier (Di2 > Ultegra > 105), frame material, frame size, condition
- Returns: estimated_market_value, recommended_max_bid, risk_level (low/medium/high), explanation
- Caches via hash — skips unchanged bikes on re-run; use `--force` to override

## Image Analysis
- Uses GPT-4o Vision
- Returns: condition_score (1–10), overall_condition, damages detected
- Stored in `bike_image_analyses` table
- Run `--max-images 5` for speed; first images are usually the most informative

## Edge Cases
- No market prices → AI uses general knowledge (lower confidence)
- Bike with no images → image analysis skipped gracefully
- Niche frame sizes (very small/large) → higher risk_level (harder to resell)
