"""Tests for the price predictor."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure project root in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from execution.price_predictor import predict, PricePrediction


def _make_vehicle(make="BMW", model="3-serie", year=2020, mileage_km=80000,
                  fuel_type="Benzine", condition_notes=None):
    v = MagicMock()
    v.id = 1
    v.make = make
    v.model = model
    v.year = year
    v.mileage_km = mileage_km
    v.fuel_type = fuel_type
    v.condition_notes = condition_notes
    return v


def _make_market_price(asking_price, year=2020, mileage_km=75000, fuel_type="Benzine"):
    mp = MagicMock()
    mp.asking_price = asking_price
    mp.year = year
    mp.mileage_km = mileage_km
    mp.fuel_type = fuel_type
    return mp


@patch("execution.price_predictor.Repository")
def test_predict_with_market_data(MockRepo):
    """Prediction uses mileage-weighted average when mileage data exists."""
    repo = MockRepo.return_value
    repo.get_vehicle.return_value = _make_vehicle(mileage_km=80000)
    repo.get_market_prices.return_value = [
        _make_market_price(25000, mileage_km=70000),
        _make_market_price(22000, mileage_km=90000),
        _make_market_price(23000, mileage_km=80000),
    ]

    result = predict(1)
    assert result is not None
    assert result.predicted_price > 0
    assert result.confidence in ("high", "medium", "low")
    assert result.market_count == 3
    assert len(result.reasoning) > 0


@patch("execution.price_predictor.Repository")
def test_predict_no_market_data(MockRepo):
    """Returns low confidence when no market data exists."""
    repo = MockRepo.return_value
    repo.get_vehicle.return_value = _make_vehicle()
    repo.get_market_prices.return_value = []

    result = predict(1)
    assert result is not None
    assert result.confidence == "low"
    assert result.predicted_price == 0
    assert result.market_count == 0


@patch("execution.price_predictor.Repository")
def test_predict_vehicle_not_found(MockRepo):
    """Returns None when vehicle doesn't exist."""
    repo = MockRepo.return_value
    repo.get_vehicle.return_value = None

    result = predict(999)
    assert result is None


@patch("execution.price_predictor.Repository")
def test_predict_confidence_levels(MockRepo):
    """Confidence is high with 10+ listings, medium with 3-9, low with <3."""
    repo = MockRepo.return_value
    repo.get_vehicle.return_value = _make_vehicle()

    # High confidence: 10+ listings
    repo.get_market_prices.return_value = [_make_market_price(20000 + i * 500) for i in range(12)]
    result = predict(1)
    assert result.confidence == "high"

    # Medium confidence: 3-9 listings
    repo.get_market_prices.return_value = [_make_market_price(20000 + i * 500) for i in range(5)]
    result = predict(1)
    assert result.confidence == "medium"

    # Low confidence: <3 listings
    repo.get_market_prices.return_value = [_make_market_price(20000)]
    result = predict(1)
    assert result.confidence == "low"


@patch("execution.price_predictor.Repository")
def test_predict_mileage_weighting(MockRepo):
    """Closer mileage matches get higher weight."""
    repo = MockRepo.return_value
    repo.get_vehicle.return_value = _make_vehicle(mileage_km=80000)

    # One close match (80k), one far match (200k)
    repo.get_market_prices.return_value = [
        _make_market_price(25000, mileage_km=80000),  # close → high weight
        _make_market_price(15000, mileage_km=200000),  # far → low weight
    ]

    result = predict(1)
    assert result is not None
    # Result should be closer to 25000 than to 15000 (before discount)
    # After 20% discount, the weighted avg should still favor the closer match
    assert result.market_avg > 18000  # weighted avg skews toward 25000


@patch("execution.price_predictor.Repository")
def test_predict_damage_adjustment(MockRepo):
    """Damage in condition notes reduces predicted price."""
    repo = MockRepo.return_value

    # Without damage
    repo.get_vehicle.return_value = _make_vehicle(condition_notes="Good condition")
    repo.get_market_prices.return_value = [_make_market_price(20000) for _ in range(5)]
    result_clean = predict(1)

    # With damage
    repo.get_vehicle.return_value = _make_vehicle(condition_notes="Schade aan bumper")
    repo.get_market_prices.return_value = [_make_market_price(20000) for _ in range(5)]
    result_damaged = predict(1)

    assert result_damaged.predicted_price < result_clean.predicted_price


@patch("execution.price_predictor.Repository")
def test_predict_price_never_negative(MockRepo):
    """Predicted price is always >= 0."""
    repo = MockRepo.return_value
    repo.get_vehicle.return_value = _make_vehicle(
        mileage_km=500000,
        condition_notes="Schade, roest, broken"
    )
    repo.get_market_prices.return_value = [_make_market_price(1000, mileage_km=50000)]

    result = predict(1)
    assert result.predicted_price >= 0
