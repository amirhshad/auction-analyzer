"""
Deal Scorer
Scores auction deals from 1 to 10 based on price, competition, timing, and condition.

Usage:
    python -m execution.deal_scorer --vehicle-id 1
"""

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

from execution.config import LOW_COMPETITION_THRESHOLD
from execution.db_repository import Repository
from execution.price_predictor import predict, PricePrediction


@dataclass
class DealScore:
    score: float  # 1-10
    rating: str  # Excellent / Good / Fair / Poor
    recommendation: str
    factors: list[str]


def score(vehicle_id: int, prediction: Optional[PricePrediction] = None) -> Optional[DealScore]:
    """Score a deal from 1 (terrible) to 10 (amazing)."""
    repo = Repository()

    vehicle = repo.get_vehicle(vehicle_id)
    if not vehicle:
        print(f"Vehicle {vehicle_id} not found", file=sys.stderr)
        repo.close()
        return None

    auctions = repo.get_auctions_for_vehicle(vehicle_id)
    auction = auctions[0] if auctions else None

    repo.close()

    # Get prediction if not provided
    if prediction is None:
        prediction = predict(vehicle_id)

    if not prediction or prediction.predicted_price <= 0:
        return DealScore(
            score=5.0,
            rating="Fair",
            recommendation="Insufficient market data to score this deal accurately.",
            factors=["No market data available — defaulting to neutral score"],
        )

    current_bid = auction.current_bid if auction and auction.current_bid else 0
    factors = []

    # Base score: ratio of current bid to predicted price
    if current_bid > 0:
        price_ratio = current_bid / prediction.predicted_price
        base_score = 10 - (price_ratio * 5)
        factors.append(f"Price ratio: €{current_bid:,.0f} / €{prediction.predicted_price:,.0f} = {price_ratio:.2f} → base score {base_score:.1f}")
    else:
        base_score = 8.0
        factors.append("No current bid — starting score 8.0")

    deal_score = base_score

    # Competition adjustment
    if auction and auction.bid_count is not None:
        if auction.bid_count < LOW_COMPETITION_THRESHOLD:
            deal_score += 1.0
            factors.append(f"Low competition ({auction.bid_count} bids < {LOW_COMPETITION_THRESHOLD}): +1.0")
        elif auction.bid_count > 20:
            deal_score -= 1.0
            factors.append(f"High competition ({auction.bid_count} bids > 20): -1.0")

    # Time remaining adjustment
    if auction and auction.end_time:
        try:
            if isinstance(auction.end_time, str):
                end_time = datetime.fromisoformat(auction.end_time.replace("Z", "+00:00"))
            else:
                end_time = auction.end_time
                if end_time.tzinfo is None:
                    end_time = end_time.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            hours_remaining = (end_time - now).total_seconds() / 3600

            if hours_remaining > 24:
                deal_score += 0.5
                factors.append(f"More than 24h remaining ({hours_remaining:.0f}h): +0.5")
            elif hours_remaining < 1:
                deal_score -= 0.5
                factors.append(f"Less than 1h remaining ({hours_remaining:.1f}h): -0.5")
        except (ValueError, TypeError):
            pass

    # Condition adjustment
    if vehicle.condition_notes:
        notes_lower = vehicle.condition_notes.lower()
        damage_words = ["schade", "damage", "broken", "defect", "roest", "rust", "dent", "deuk"]
        if any(w in notes_lower for w in damage_words):
            deal_score -= 1.0
            factors.append("Damage mentioned in condition notes: -1.0")

    # Confidence adjustment
    if prediction.confidence == "low":
        deal_score -= 0.5
        factors.append("Low prediction confidence: -0.5")

    # Clamp to 1-10
    deal_score = max(1.0, min(10.0, deal_score))

    # Rating
    if deal_score >= 8:
        rating = "Excellent"
        recommendation = "Strong buy — this is significantly below market value."
    elif deal_score >= 6:
        rating = "Good"
        recommendation = "Worth bidding — decent deal compared to market."
    elif deal_score >= 4:
        rating = "Fair"
        recommendation = "Proceed with caution — close to market value."
    else:
        rating = "Poor"
        recommendation = "Not recommended — likely overpriced for auction."

    return DealScore(
        score=round(deal_score, 1),
        rating=rating,
        recommendation=recommendation,
        factors=factors,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Score a deal for a vehicle")
    parser.add_argument("--vehicle-id", type=int, required=True, help="Vehicle ID")
    args = parser.parse_args()

    result = score(args.vehicle_id)
    if result:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(json.dumps({"error": "Could not score deal"}))
