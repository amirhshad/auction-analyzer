# Analyze Vehicle Images

## Goal
Use GPT-4 Vision to assess vehicle condition from auction photos.

## Script
`execution/image_analyzer.py`

## Inputs
- `--vehicle-id N` — Vehicle ID (required)
- `--max-images N` — Maximum images to analyze (default: 5)

## How to Run
```bash
python -m execution.image_analyzer --vehicle-id 1 --max-images 5
```

## COST WARNING
This script uses the OpenAI API (GPT-4 Vision). Each image costs tokens.
**Always confirm with the user before running on multiple images.**
Estimated cost: ~$0.01-0.05 per image depending on resolution.

## Prerequisites
- Vehicle must have image URLs in database (image_urls_json field)
- OPENAI_API_KEY must be set in .env

## What It Does
1. Fetches vehicle's image URLs from database
2. Sends each image to GPT-4o with analysis prompt
3. Extracts: image type, condition score (1-10), damages, assessment
4. Saves each result to image_analyses table
5. Aggregates: overall score, all damages, estimated repair cost, value adjustment

## Output
JSON to stdout:
```json
{
  "overall_score": 7.5,
  "images_analyzed": 5,
  "damages": ["minor scratch on bumper", "small rust spot"],
  "repair_cost_estimate": 400,
  "value_adjustment": -400,
  "image_results": [...]
}
```

## Edge Cases
- No OPENAI_API_KEY → error message, exits
- No images → error message, exits
- GPT response not valid JSON → fallback parsing with defaults
- API rate limit → standard OpenAI retry behavior
- Repair cost is a rough estimate: €200 per unique damage item
