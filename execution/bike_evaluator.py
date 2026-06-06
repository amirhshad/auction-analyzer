"""
Bike Deal Evaluator
Uses GPT-4o-mini to evaluate auction bike deals.

Usage:
    python -m execution.bike_evaluator --auction "Tweewielers"
    python -m execution.bike_evaluator --bike-id 42
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from statistics import median
from typing import Optional, Callable

from execution.config import OPENAI_API_KEY
from execution.db_models import Bike
from execution.db_repository import Repository

MODEL = "gpt-4o-mini"
_quiet = False


def _log(msg: str, error: bool = False):
    if _quiet:
        return
    try:
        print(msg, file=sys.stderr if error else sys.stdout)
    except BrokenPipeError:
        pass


BIKE_EVAL_PROMPT = """You are an auction deal analyst specializing in Dutch used bike auctions. Evaluate this bike and estimate its realistic used market value.

## Bike Details
Type: {bike_type}
Brand: {brand}
Model: {model}
Frame Size: {frame_size}
Color: {color}
Condition: {condition}
Components: {components}
Notes: {notes}

## Auction Status
Current Bid: €{current_bid}
Number of Bids: {bid_count}
Time Remaining: {time_remaining}

## Market Prices (from Marktplaats)
{market_summary}

## Instructions
1. Estimate the realistic USED market value on Marktplaats for this bike in this condition
2. Account for: brand prestige, component quality (Shimano hierarchy: Dura-Ace > Ultegra > 105 > Tiagra > Sora), frame material (carbon > aluminum > steel), frame size fit, condition
3. High-end components (Di2 electronic shifting, hydraulic disc, carbon wheels) significantly increase value
4. Damaged or repainted frames reduce value by 30-50%
5. Recommend a max bid that leaves value margin
6. Assess resale risk: niche frame sizes (very small/large) are harder to sell

Respond ONLY in JSON:
{{
  "estimated_market_value": <float EUR>,
  "recommended_max_bid": <float EUR>,
  "risk_level": "low" | "medium" | "high",
  "confidence": <float 0.0-1.0>,
  "explanation": "• <8 words: value and why> • <8 words: key condition/component factor> • <8 words: bid advice>"
}}"""


def _compute_eval_hash(bike: Bike) -> str:
    parts = [
        bike.brand or "",
        bike.model or "",
        bike.bike_type or "",
        bike.frame_size or "",
        bike.condition or "",
        bike.components or "",
        str(bike.current_bid or 0),
        str(bike.bid_count or 0),
        bike.end_time.strftime("%Y-%m-%d %H") if bike.end_time else "",
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _format_prompt(bike: Bike, market_prices: list) -> str:
    time_remaining = "Unknown"
    if bike.end_time:
        try:
            end_time = bike.end_time
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
            remaining = end_time - datetime.now(timezone.utc)
            hours = remaining.total_seconds() / 3600
            if hours > 48:
                time_remaining = f"{hours / 24:.0f} days"
            elif hours > 0:
                time_remaining = f"{hours:.1f} hours"
            else:
                time_remaining = "Ended"
        except (ValueError, TypeError):
            pass

    if market_prices:
        prices = [mp.asking_price for mp in market_prices if mp.asking_price]
        if prices:
            med = median(prices)
            market_summary = (
                f"{len(prices)} listings found. "
                f"Median: €{med:,.0f}, "
                f"Range: €{min(prices):,.0f}–€{max(prices):,.0f}"
            )
        else:
            market_summary = "No market price data available."
    else:
        market_summary = "No market price data available."

    return BIKE_EVAL_PROMPT.format(
        bike_type=bike.bike_type or "Unknown",
        brand=bike.brand or "Unknown",
        model=bike.model or "Unknown",
        frame_size=bike.frame_size or "Unknown",
        color=bike.color or "Unknown",
        condition=bike.condition or "Unknown",
        components=(bike.components or "Not specified")[:400],
        notes=(bike.notes or "None")[:200],
        current_bid=f"{bike.current_bid:.2f}" if bike.current_bid else "No bids",
        bid_count=bike.bid_count or 0,
        time_remaining=time_remaining,
        market_summary=market_summary,
    )


def _call_gpt(client, prompt: str) -> dict:
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.3,
        )
        text = response.choices[0].message.content.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        result = json.loads(text)
        for field in ("estimated_market_value", "recommended_max_bid", "risk_level", "explanation"):
            if field not in result:
                raise ValueError(f"Missing field: {field}")
        if isinstance(result["explanation"], list):
            result["explanation"] = " • ".join(str(b).lstrip("•-– ").strip() for b in result["explanation"])
        return result
    except json.JSONDecodeError:
        return {"estimated_market_value": None, "recommended_max_bid": None,
                "risk_level": "high", "confidence": 0.1,
                "explanation": "AI evaluation could not parse response."}
    except Exception as e:
        return {"estimated_market_value": None, "recommended_max_bid": None,
                "risk_level": "high", "confidence": 0.0,
                "explanation": f"AI evaluation error: {str(e)}"}


def evaluate_bikes(
    bike_ids: Optional[list[int]] = None,
    auction_name: Optional[str] = None,
    force: bool = False,
    progress_callback: Optional[Callable] = None,
) -> dict:
    global _quiet
    _quiet = progress_callback is not None

    if not OPENAI_API_KEY:
        return {"evaluated": 0, "skipped": 0, "errors": 0,
                "error": "OPENAI_API_KEY not set in .env"}

    try:
        from openai import OpenAI
    except ImportError:
        return {"evaluated": 0, "skipped": 0, "errors": 0,
                "error": "openai package not installed — run: pip install openai"}

    repo = Repository()

    if bike_ids:
        bikes = [repo.get_bike(bid) for bid in bike_ids]
        bikes = [b for b in bikes if b is not None]
    else:
        bikes = repo.list_bikes(limit=9999, auction_name=auction_name)

    if not bikes:
        repo.close()
        return {"evaluated": 0, "skipped": 0, "errors": 0}

    client = OpenAI(api_key=OPENAI_API_KEY)
    evaluated = skipped = errors = 0

    try:
        for idx, bike in enumerate(bikes):
            if progress_callback:
                label = f"{bike.brand or ''} {bike.model or ''}".strip() or "bike"
                progress_callback(idx, len(bikes), f"Evaluating {label}...")

            new_hash = _compute_eval_hash(bike)
            if not force and bike.ai_eval_hash == new_hash and bike.ai_evaluated_at:
                skipped += 1
                continue

            market_prices = repo.get_bike_market_prices(bike.brand or "", bike.model)
            prompt = _format_prompt(bike, market_prices)
            result = _call_gpt(client, prompt)

            repo.update_bike_ai_eval(
                bike.id,
                ai_estimated_value=result.get("estimated_market_value"),
                ai_recommended_max_bid=result.get("recommended_max_bid"),
                ai_risk_level=result["risk_level"],
                ai_explanation=result["explanation"],
                ai_evaluated_at=datetime.now(timezone.utc),
                ai_eval_hash=new_hash,
            )

            if result.get("estimated_market_value") is None:
                errors += 1
            else:
                evaluated += 1
                _log(f"  [{idx+1}/{len(bikes)}] {bike.brand} {bike.model} → "
                     f"€{result['estimated_market_value']:,.0f} | Risk: {result['risk_level']}")

        if progress_callback:
            progress_callback(len(bikes), len(bikes), "Done!")
    finally:
        repo.close()

    return {"evaluated": evaluated, "skipped": skipped, "errors": errors}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI-evaluate bike auction items")
    parser.add_argument("--auction", help="Filter by auction name")
    parser.add_argument("--bike-id", type=int)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = evaluate_bikes(
        bike_ids=[args.bike_id] if args.bike_id else None,
        auction_name=args.auction,
        force=args.force,
    )
    print(json.dumps(result, indent=2))
