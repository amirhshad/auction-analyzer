"""
Price Predictor
Predicts final auction price based on market comparison data.

Usage:
    python -m execution.price_predictor --vehicle-id 1
"""

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from typing import Optional
from statistics import median

from execution.config import AUCTION_DISCOUNT, MILEAGE_ADJUSTMENT_PER_KM
from execution.db_repository import Repository


@dataclass
class PricePrediction:
    predicted_price: float
    confidence: str  # high / medium / low
    market_avg: float
    market_count: int
    auction_discount: float
    reasoning: list[str]


def predict(vehicle_id: int) -> Optional[PricePrediction]:
    """Predict final auction price for a vehicle."""
    repo = Repository()

    vehicle = repo.get_vehicle(vehicle_id)
    if not vehicle:
        print(f"Vehicle {vehicle_id} not found", file=sys.stderr)
        repo.close()
        return None

    if not vehicle.make or not vehicle.model:
        print(f"Vehicle {vehicle_id} missing make/model", file=sys.stderr)
        repo.close()
        return None

    # Get market prices (±1 year range)
    market_prices = repo.get_market_prices(
        make=vehicle.make,
        model=vehicle.model,
        year=vehicle.year,
        year_range=1,
    )

    repo.close()

    if not market_prices:
        return PricePrediction(
            predicted_price=0,
            confidence="low",
            market_avg=0,
            market_count=0,
            auction_discount=AUCTION_DISCOUNT,
            reasoning=["No market data available — cannot predict price"],
        )

    reasoning = []
    prices = [mp.asking_price for mp in market_prices if mp.asking_price]

    if not prices:
        return PricePrediction(
            predicted_price=0,
            confidence="low",
            market_avg=0,
            market_count=0,
            auction_discount=AUCTION_DISCOUNT,
            reasoning=["Market data found but no valid prices"],
        )

    # Remove outliers using IQR method (only when we have enough data)
    if len(prices) >= 4:
        sorted_prices = sorted(prices)
        q1 = sorted_prices[len(sorted_prices) // 4]
        q3 = sorted_prices[3 * len(sorted_prices) // 4]
        iqr = q3 - q1
        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr
        filtered = [p for p in prices if lower_bound <= p <= upper_bound]
        removed = len(prices) - len(filtered)
        if filtered and removed > 0:
            reasoning.append(f"Removed {removed} outlier(s) outside €{lower_bound:,.0f}–€{upper_bound:,.0f} range")
            prices = filtered
            # Also filter market_prices list so mileage-weighted calc uses clean data
            market_prices = [mp for mp in market_prices if mp.asking_price and lower_bound <= mp.asking_price <= upper_bound]

    # Calculate weighted or simple average
    if vehicle.mileage_km and any(mp.mileage_km for mp in market_prices):
        # Mileage-weighted average
        weighted_sum = 0.0
        weight_total = 0.0

        for mp in market_prices:
            if mp.asking_price and mp.mileage_km:
                mileage_diff = abs(vehicle.mileage_km - mp.mileage_km)
                weight = max(0.1, 1.0 / (1.0 + mileage_diff / 10000))
                weighted_sum += mp.asking_price * weight
                weight_total += weight

        if weight_total > 0:
            market_avg = weighted_sum / weight_total
            reasoning.append(f"Mileage-weighted average of {len(prices)} market listings: €{market_avg:,.0f}")
        else:
            market_avg = median(prices)
            reasoning.append(f"Median of {len(prices)} market listings: €{market_avg:,.0f}")
    else:
        market_avg = median(prices)
        reasoning.append(f"Median of {len(prices)} market listings: €{market_avg:,.0f}")

    # Apply auction discount
    predicted = market_avg * (1 - AUCTION_DISCOUNT)
    reasoning.append(f"Auction discount applied: {AUCTION_DISCOUNT:.0%} → €{predicted:,.0f}")

    # Mileage adjustment
    if vehicle.mileage_km:
        avg_mileage = [mp.mileage_km for mp in market_prices if mp.mileage_km]
        if avg_mileage:
            expected_mileage = median(avg_mileage)
            mileage_diff = vehicle.mileage_km - expected_mileage
            if abs(mileage_diff) > 5000:
                adjustment = -mileage_diff * MILEAGE_ADJUSTMENT_PER_KM
                predicted += adjustment
                direction = "above" if mileage_diff > 0 else "below"
                reasoning.append(
                    f"Mileage {vehicle.mileage_km:,} km is {abs(mileage_diff):,.0f} km {direction} average "
                    f"({expected_mileage:,.0f} km) → adjustment: €{adjustment:+,.0f}"
                )

    # Fuel type adjustment
    if vehicle.fuel_type:
        fuel_lower = vehicle.fuel_type.lower()
        if fuel_lower in ("elektrisch", "electric"):
            predicted *= 1.05
            reasoning.append("Electric vehicle premium: +5%")
        elif fuel_lower in ("diesel",):
            predicted *= 0.97
            reasoning.append("Diesel discount: -3%")

    # Condition adjustment
    if vehicle.condition_notes:
        notes_lower = vehicle.condition_notes.lower()
        damage_words = ["schade", "damage", "broken", "defect", "roest", "rust", "dent", "deuk"]
        if any(w in notes_lower for w in damage_words):
            predicted *= 0.85
            reasoning.append("Damage noted in condition: -15%")

    predicted = max(0, predicted)

    # Confidence based on data quality
    if len(prices) >= 10:
        confidence = "high"
    elif len(prices) >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    reasoning.append(f"Confidence: {confidence} (based on {len(prices)} comparable listings)")

    return PricePrediction(
        predicted_price=round(predicted, 2),
        confidence=confidence,
        market_avg=round(market_avg, 2),
        market_count=len(prices),
        auction_discount=AUCTION_DISCOUNT,
        reasoning=reasoning,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Predict auction price for a vehicle")
    parser.add_argument("--vehicle-id", type=int, required=True, help="Vehicle ID")
    args = parser.parse_args()

    result = predict(args.vehicle_id)
    if result:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(json.dumps({"error": "Could not predict price"}))
