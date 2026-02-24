"""
Bid Strategist
Recommends max bid, timing advice, and risk assessment.

Usage:
    python -m execution.bid_strategist --vehicle-id 1
"""

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

from execution.db_repository import Repository
from execution.price_predictor import predict, PricePrediction


@dataclass
class BidStrategy:
    max_bid: float
    timing_advice: str
    risk_level: str  # low / medium / high
    strategy_notes: list[str]


def get_strategy(vehicle_id: int, prediction: Optional[PricePrediction] = None) -> Optional[BidStrategy]:
    """Generate bidding strategy for a vehicle."""
    repo = Repository()

    vehicle = repo.get_vehicle(vehicle_id)
    if not vehicle:
        print(f"Vehicle {vehicle_id} not found", file=sys.stderr)
        repo.close()
        return None

    auctions = repo.get_auctions_for_vehicle(vehicle_id)
    auction = auctions[0] if auctions else None
    price_history = repo.get_price_history(vehicle_id)

    repo.close()

    # Get prediction if not provided
    if prediction is None:
        prediction = predict(vehicle_id)

    notes = []

    if not prediction or prediction.predicted_price <= 0:
        return BidStrategy(
            max_bid=0,
            timing_advice="Wait for market data before bidding.",
            risk_level="high",
            strategy_notes=["No market data available — cannot recommend a bid."],
        )

    # Max bid: 85% of predicted price (15% safety margin)
    max_bid = prediction.predicted_price * 0.85
    notes.append(f"Max bid = predicted price (€{prediction.predicted_price:,.0f}) × 0.85 = €{max_bid:,.0f}")

    # Risk assessment
    risk_level = "medium"
    current_bid = auction.current_bid if auction and auction.current_bid else 0

    if current_bid > 0:
        bid_ratio = current_bid / prediction.predicted_price
        if bid_ratio > 0.8:
            risk_level = "high"
            notes.append(f"Current bid already at {bid_ratio:.0%} of predicted price — high risk")
        elif bid_ratio < 0.4:
            risk_level = "low"
            notes.append(f"Current bid only {bid_ratio:.0%} of predicted price — low risk, good opportunity")
        else:
            notes.append(f"Current bid at {bid_ratio:.0%} of predicted price — moderate risk")

    # Bid velocity (from price history)
    if len(price_history) >= 2:
        recent = price_history[-1]
        previous = price_history[-2]
        if recent.bid_amount and previous.bid_amount:
            velocity = recent.bid_amount - previous.bid_amount
            time_diff = (recent.recorded_at - previous.recorded_at).total_seconds() / 3600
            if time_diff > 0:
                hourly_rate = velocity / time_diff
                notes.append(f"Bid velocity: €{hourly_rate:,.0f}/hour")
                if hourly_rate > 500:
                    risk_level = "high"
                    notes.append("Fast-moving auction — expect aggressive bidding")

    # Competition check
    if auction and auction.bid_count is not None:
        if auction.bid_count > 15:
            notes.append(f"High competition: {auction.bid_count} bids — consider reducing max bid")
            max_bid *= 0.95  # Reduce by 5% for competitive auctions
        elif auction.bid_count < 3:
            notes.append(f"Low competition: {auction.bid_count} bids — good opportunity")

    # Timing advice
    timing_advice = "Place your bid strategically."
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

            if hours_remaining > 48:
                timing_advice = "No rush — monitor the auction and bid in the last 24 hours."
                notes.append(f"{hours_remaining:.0f} hours remaining — plenty of time")
            elif hours_remaining > 24:
                timing_advice = "Consider placing your bid soon, but last-hour sniping is also viable."
                notes.append(f"{hours_remaining:.0f} hours remaining — start planning your bid")
            elif hours_remaining > 2:
                timing_advice = "Auction ending soon — place your max bid within the next few hours."
                notes.append(f"{hours_remaining:.1f} hours remaining — bid soon")
            elif hours_remaining > 0:
                timing_advice = "Auction ending very soon — bid now if you want this vehicle."
                notes.append(f"{hours_remaining:.1f} hours remaining — last chance!")
            else:
                timing_advice = "Auction has ended."
                notes.append("Auction has already ended")
        except (ValueError, TypeError):
            timing_advice = "Could not determine auction end time — bid at your discretion."

    # Prediction confidence impact
    if prediction.confidence == "low":
        notes.append("Low confidence prediction — consider bidding lower than recommended max")
        max_bid *= 0.90  # Additional 10% discount for uncertainty

    max_bid = round(max(0, max_bid), 2)

    return BidStrategy(
        max_bid=max_bid,
        timing_advice=timing_advice,
        risk_level=risk_level,
        strategy_notes=notes,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Get bidding strategy for a vehicle")
    parser.add_argument("--vehicle-id", type=int, required=True, help="Vehicle ID")
    args = parser.parse_args()

    result = get_strategy(args.vehicle_id)
    if result:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(json.dumps({"error": "Could not generate strategy"}))
