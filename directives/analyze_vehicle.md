# Analyze Vehicle

## Goal
Run full analysis on a vehicle: predict price, score deal, generate bid strategy.

## Scripts
1. `execution/price_predictor.py` — Predict final auction price
2. `execution/deal_scorer.py` — Score deal quality 1-10
3. `execution/bid_strategist.py` — Recommend max bid + timing

## Inputs
- `--vehicle-id N` — Vehicle ID in database (required for all 3 scripts)

## How to Run
```bash
# Run all three in sequence:
python -m execution.price_predictor --vehicle-id 1
python -m execution.deal_scorer --vehicle-id 1
python -m execution.bid_strategist --vehicle-id 1
```

## Prerequisites
- Vehicle must exist in database (scrape first)
- Market prices should be fetched for best predictions (run autoscout24 scraper first)

## Analysis Pipeline
1. **Price Predictor**: Queries market_prices for same make/model/year (±1 year). Uses mileage-weighted average if mileage data available, otherwise median. Applies auction discount (default 20%). Adjusts for mileage, fuel type, condition.
2. **Deal Scorer**: Base score from bid/predicted ratio. Adjusts for competition (±1), timing (±0.5), condition (-1 for damage), confidence (-0.5 for low). Clamped 1-10.
3. **Bid Strategist**: Max bid = predicted × 0.85. Assesses risk from bid velocity and competition. Timing advice from hours remaining.

## Output
Each script outputs JSON to stdout:
- `{predicted_price, confidence, market_avg, market_count, reasoning[]}`
- `{score, rating, recommendation, factors[]}`
- `{max_bid, timing_advice, risk_level, strategy_notes[]}`

## Edge Cases
- No market data → low confidence, conservative predictions
- No current bid → assumes good starting position
- Damaged condition notes → automatic score/price reduction
- Missing end time → generic timing advice
