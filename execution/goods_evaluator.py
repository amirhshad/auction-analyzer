"""
Goods Deal Evaluator
Uses GPT-4o-mini to evaluate auction item deals using specs, description, and auction data.

Usage:
    python -m execution.goods_evaluator --auction "Bezorgveiling diverse retourgoederen"
    python -m execution.goods_evaluator --item-id 42
    python -m execution.goods_evaluator --auction "..." --force
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from typing import Optional, Callable

from execution.config import OPENAI_API_KEY
from execution.db_models import GoodsItem
from execution.db_repository import Repository

MODEL = "gpt-4o-mini"

# Whether we're in dashboard mode (suppress stdout/stderr to avoid broken pipes)
_quiet = False


def _log(msg: str, error: bool = False):
    """Print only in CLI mode. Streamlit redirects stdout/stderr which causes broken pipes."""
    if _quiet:
        return
    try:
        print(msg, file=sys.stderr if error else sys.stdout)
    except BrokenPipeError:
        pass

GOODS_EVAL_PROMPT = """You are an auction deal analyst specializing in Dutch consumer auctions. Evaluate this item and estimate its realistic market value.

## Item Details
Title: {title}
Brand: {brand}
Category: {category}
Condition: {condition}
Quantity: {quantity}
Description: {description}

## Specifications
{specs_formatted}

## Auction Status
Current Bid: €{current_bid}
Number of Bids: {bid_count}
Time Remaining: {time_remaining}

## Instructions
1. Estimate the realistic USED market value — what someone would actually pay on Marktplaats, eBay, or bol.com for this exact item in this exact condition
2. Account for condition: missing parts, damage, wear, completeness
3. If quantity > 1, give value for the entire lot (not per unit)
4. Assess risk: how easy is this to resell? Is the value estimate reliable?
5. Recommend a maximum bid that leaves room for profit or personal value

## Category-Specific Guidance
- **Electronics**: Age matters a lot. A 2013 laptop is worth far less than specs suggest. Check if model is outdated. Broken electronics = parts value only (screen, RAM, chassis).
- **Household / Kitchen**: Premium brands (Brabantia, KitchenAid, Dyson) hold 40-60% of retail. No-name brands drop to 10-20%. Heavy/bulky items have lower resale due to shipping.
- **Furniture**: Resale value heavily depends on size, weight, and brand. IKEA furniture has very low resale. Designer pieces hold value. Shipping cost often exceeds item value for cheap furniture.
- **Tools / Machinery**: Professional-grade tools (Bosch Pro, Makita, Hilti) hold value well. Consumer-grade tools lose 60-70%. Check voltage/battery compatibility.
- **Clothing / Fashion**: Brand tier is everything. Luxury (€100+) holds value, fast fashion is nearly worthless secondhand. Season and size affect sellability.
- **Toys / Games**: Collectibles and sealed items hold value. Used toys lose 70-90%. Check if complete (all pieces, manuals).
- **Garden / Outdoor**: Seasonal demand — worth more in spring. Heavy items hard to ship.
- **Multi-item lots** (quantity > 1): Calculate per-unit value, then discount 20-30% because lots are harder to sell. Bulk phone cases, cables etc. have very low per-unit value.

Respond ONLY in JSON:
{{
  "estimated_market_value": <float, realistic resale value in EUR>,
  "recommended_max_bid": <float, max you should bid in EUR>,
  "risk_level": "low" | "medium" | "high",
  "confidence": <float, 0.0-1.0>,
  "explanation": "• <8 words max: what it's worth and why> • <8 words max: key risk or condition issue> • <8 words max: bid advice>"
}}

## Dutch Auction Context
- Specs are often in Dutch: Merk=Brand, Opslagcapaciteit=Storage, Zonder=Without, Hoeveelheid=Quantity, Beelddiagonaal=Screen size, Vermogen=Power
- "Retour" or "retourgoederen" = customer returns — may have cosmetic damage, missing parts, or be incomplete
- "Zonder voedingskabel/adapter/afstandsbediening" = missing power cable/adapter/remote — reduce value €10-30 each
- "Niet werkend" or "defect" = broken/non-functional — only worth parts/repair value
- "Marge: Nee" = no VAT margin, relevant for business buyers
- Auction items typically sell 20-40% below retail
- If you cannot determine the value, set confidence below 0.3 and explain why"""


def _compute_eval_hash(item) -> str:
    """Hash of inputs that affect evaluation — used to skip unchanged items."""
    components = [
        item.title or "",
        item.description or "",
        item.specs_json or "",
        item.condition or "",
        str(item.quantity or 1),
        str(item.current_bid or 0),
        str(item.bid_count or 0),
        item.end_time.strftime("%Y-%m-%d %H") if item.end_time else "",
    ]
    return hashlib.sha256("|".join(components).encode()).hexdigest()[:16]


def _format_prompt(item) -> str:
    """Build the evaluation prompt from item data."""
    specs = item.specs
    if specs:
        specs_text = "\n".join(f"- {k}: {v}" for k, v in specs.items())
    else:
        specs_text = "No specifications available"

    description = (item.description or "No description available")[:500]

    time_remaining = "Unknown"
    if item.end_time:
        try:
            if isinstance(item.end_time, str):
                end_time = datetime.fromisoformat(item.end_time.replace("Z", "+00:00"))
            else:
                end_time = item.end_time
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
            time_remaining = "Unknown"

    return GOODS_EVAL_PROMPT.format(
        title=item.title or "Unknown item",
        brand=item.brand or "Unknown",
        category=item.category or "Unknown",
        condition=item.condition or "Unknown",
        quantity=item.quantity or 1,
        description=description,
        specs_formatted=specs_text,
        current_bid=f"{item.current_bid:.2f}" if item.current_bid else "No bids",
        bid_count=item.bid_count or 0,
        time_remaining=time_remaining,
    )


def _call_gpt_evaluate(client, prompt: str) -> dict:
    """Call GPT-4o-mini and parse the JSON response."""
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.3,
        )
        text = response.choices[0].message.content.strip()

        # Handle markdown code blocks (same pattern as image_analyzer.py)
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        result = json.loads(text)

        # Validate required fields
        for field in ("estimated_market_value", "recommended_max_bid", "risk_level", "explanation"):
            if field not in result:
                raise ValueError(f"Missing required field: {field}")

        # Ensure explanation is a string (model may return a list)
        if isinstance(result["explanation"], list):
            result["explanation"] = " • ".join(str(b).lstrip("•-– ").strip() for b in result["explanation"])

        return result

    except json.JSONDecodeError:
        _log("    Warning: Could not parse GPT response as JSON", error=True)
        return {
            "estimated_market_value": None,
            "recommended_max_bid": None,
            "risk_level": "high",
            "confidence": 0.1,
            "explanation": "AI evaluation could not parse response.",
        }
    except Exception as e:
        _log(f"    Error in GPT evaluation: {e}", error=True)
        return {
            "estimated_market_value": None,
            "recommended_max_bid": None,
            "risk_level": "high",
            "confidence": 0.0,
            "explanation": f"AI evaluation error: {str(e)}",
        }


def get_items_to_evaluate(repo: Repository, auction_name: Optional[str] = None) -> list:
    """Get all items for the given auction (or all auctions). Hash-based skip logic prevents redundant API calls."""
    all_items = repo.list_goods_items(limit=200, auction_name=auction_name)
    _log(f"  Found {len(all_items)} total items to evaluate")
    return all_items


def evaluate_goods_items(
    item_ids: Optional[list[int]] = None,
    auction_name: Optional[str] = None,
    force: bool = False,
    progress_callback: Optional[Callable] = None,
) -> dict:
    """Evaluate goods items with GPT-4o-mini. Returns summary dict."""
    global _quiet
    _quiet = progress_callback is not None  # Suppress prints in dashboard mode

    if not OPENAI_API_KEY:
        _log("Error: OPENAI_API_KEY not set in .env", error=True)
        return {"evaluated": 0, "skipped": 0, "errors": 0, "error": "OPENAI_API_KEY not set in .env — add your OpenAI API key to the .env file"}

    try:
        from openai import OpenAI
    except ImportError:
        _log("Error: openai not installed. Run: pip install openai", error=True)
        return {"evaluated": 0, "skipped": 0, "errors": 0, "error": "openai package not installed — run: pip install openai"}

    repo = Repository()

    # Determine items to evaluate
    if item_ids:
        items = [repo.session.query(GoodsItem).get(iid) for iid in item_ids]
        items = [i for i in items if i is not None]
    else:
        items = get_items_to_evaluate(repo, auction_name=auction_name)

    if not items:
        # Debug: check why no items were found
        total_in_db = len(repo.list_goods_items(limit=200, auction_name=auction_name))
        repo.close()
        return {"evaluated": 0, "skipped": 0, "errors": 0, "items_in_db": total_in_db}

    client = OpenAI(api_key=OPENAI_API_KEY)
    evaluated = 0
    skipped = 0
    errors = 0

    for idx, item in enumerate(items):
        if progress_callback:
            progress_callback(idx, len(items), f"Evaluating {(item.title or 'item')[:40]}...")

        # Check hash to skip unchanged items
        new_hash = _compute_eval_hash(item)
        if not force and item.ai_eval_hash == new_hash and item.ai_evaluated_at:
            skipped += 1
            continue

        # Build prompt and call GPT
        prompt = _format_prompt(item)
        result = _call_gpt_evaluate(client, prompt)

        if result.get("estimated_market_value") is None:
            errors += 1
            # Still save the explanation so user sees the error
            repo.update_goods_ai_eval(
                item.id,
                ai_risk_level="high",
                ai_explanation=result.get("explanation", "Evaluation failed"),
                ai_evaluated_at=datetime.now(timezone.utc),
                ai_eval_hash=new_hash,
            )
            continue

        # Save results
        repo.update_goods_ai_eval(
            item.id,
            ai_estimated_value=result["estimated_market_value"],
            ai_recommended_max_bid=result["recommended_max_bid"],
            ai_risk_level=result["risk_level"],
            ai_explanation=result["explanation"],
            ai_evaluated_at=datetime.now(timezone.utc),
            ai_eval_hash=new_hash,
        )

        evaluated += 1
        _log(f"  [{idx + 1}/{len(items)}] {(item.title or '')[:50]} → "
             f"AI Value: €{result['estimated_market_value']:,.0f} | "
             f"Max Bid: €{result['recommended_max_bid']:,.0f} | "
             f"Risk: {result['risk_level']}")

    if progress_callback:
        progress_callback(len(items), len(items), "Done!")

    repo.close()

    summary = {"evaluated": evaluated, "skipped": skipped, "errors": errors}
    _log(f"\nSummary: {evaluated} evaluated, {skipped} skipped (unchanged), {errors} errors")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI-evaluate goods auction items")
    parser.add_argument("--auction", help="Filter by auction name")
    parser.add_argument("--item-id", type=int, help="Evaluate a specific item")
    parser.add_argument("--force", action="store_true", help="Force re-evaluation (ignore hash)")
    args = parser.parse_args()

    item_ids = [args.item_id] if args.item_id else None
    result = evaluate_goods_items(
        item_ids=item_ids,
        auction_name=args.auction,
        force=args.force,
    )
    print(json.dumps(result, indent=2))
