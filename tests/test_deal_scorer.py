"""Tests for the deal scorer."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from execution.deal_scorer import score, DealScore
from execution.price_predictor import PricePrediction


def _make_vehicle(condition_notes=None):
    v = MagicMock()
    v.id = 1
    v.make = "BMW"
    v.model = "3-serie"
    v.condition_notes = condition_notes
    return v


def _make_auction(current_bid=5000, bid_count=10, end_time=None, status="active"):
    a = MagicMock()
    a.id = 1
    a.current_bid = current_bid
    a.bid_count = bid_count
    a.end_time = end_time
    a.status = status
    return a


def _make_prediction(predicted_price=20000, confidence="medium"):
    return PricePrediction(
        predicted_price=predicted_price,
        confidence=confidence,
        market_avg=22000,
        market_count=5,
        auction_discount=0.20,
        reasoning=["Test prediction"],
    )


@patch("execution.deal_scorer.Repository")
def test_score_bounds(MockRepo):
    """Score is always between 1 and 10."""
    repo = MockRepo.return_value
    repo.get_vehicle.return_value = _make_vehicle()

    # Very cheap bid → high score
    repo.get_auctions_for_vehicle.return_value = [_make_auction(current_bid=100)]
    result = score(1, _make_prediction(predicted_price=50000))
    assert 1.0 <= result.score <= 10.0

    # Very expensive bid → low score
    repo.get_auctions_for_vehicle.return_value = [_make_auction(current_bid=100000)]
    result = score(1, _make_prediction(predicted_price=5000))
    assert 1.0 <= result.score <= 10.0


@patch("execution.deal_scorer.Repository")
def test_score_low_competition_bonus(MockRepo):
    """Low bid count gives +1.0 bonus."""
    repo = MockRepo.return_value
    repo.get_vehicle.return_value = _make_vehicle()

    # Low competition
    repo.get_auctions_for_vehicle.return_value = [_make_auction(current_bid=10000, bid_count=3)]
    result_low = score(1, _make_prediction())

    # Normal competition
    repo.get_auctions_for_vehicle.return_value = [_make_auction(current_bid=10000, bid_count=10)]
    result_normal = score(1, _make_prediction())

    assert result_low.score > result_normal.score


@patch("execution.deal_scorer.Repository")
def test_score_high_competition_penalty(MockRepo):
    """High bid count gives -1.0 penalty."""
    repo = MockRepo.return_value
    repo.get_vehicle.return_value = _make_vehicle()

    # Normal competition
    repo.get_auctions_for_vehicle.return_value = [_make_auction(current_bid=10000, bid_count=10)]
    result_normal = score(1, _make_prediction())

    # High competition
    repo.get_auctions_for_vehicle.return_value = [_make_auction(current_bid=10000, bid_count=25)]
    result_high = score(1, _make_prediction())

    assert result_normal.score > result_high.score


@patch("execution.deal_scorer.Repository")
def test_score_damage_penalty(MockRepo):
    """Damage in condition notes gives -1.0 penalty."""
    repo = MockRepo.return_value
    repo.get_auctions_for_vehicle.return_value = [_make_auction(current_bid=10000)]

    # Clean
    repo.get_vehicle.return_value = _make_vehicle(condition_notes="Good condition")
    result_clean = score(1, _make_prediction())

    # Damaged
    repo.get_vehicle.return_value = _make_vehicle(condition_notes="Schade aan bumper")
    result_damaged = score(1, _make_prediction())

    assert result_clean.score > result_damaged.score


@patch("execution.deal_scorer.Repository")
def test_score_low_confidence_penalty(MockRepo):
    """Low confidence gives -0.5 penalty."""
    repo = MockRepo.return_value
    repo.get_vehicle.return_value = _make_vehicle()
    repo.get_auctions_for_vehicle.return_value = [_make_auction(current_bid=10000)]

    result_medium = score(1, _make_prediction(confidence="medium"))
    result_low = score(1, _make_prediction(confidence="low"))

    assert result_medium.score > result_low.score


@patch("execution.deal_scorer.Repository")
def test_score_ratings(MockRepo):
    """Verify rating thresholds."""
    repo = MockRepo.return_value
    repo.get_vehicle.return_value = _make_vehicle()

    # Excellent: score >= 8 (very cheap bid)
    repo.get_auctions_for_vehicle.return_value = [_make_auction(current_bid=1000, bid_count=2)]
    result = score(1, _make_prediction(predicted_price=20000))
    assert result.score >= 8
    assert result.rating == "Excellent"

    # Poor: high bid relative to price
    repo.get_auctions_for_vehicle.return_value = [_make_auction(current_bid=25000, bid_count=25)]
    result = score(1, _make_prediction(predicted_price=10000))
    assert result.rating == "Poor"


@patch("execution.deal_scorer.Repository")
def test_score_no_prediction(MockRepo):
    """Returns neutral score when no prediction data available."""
    repo = MockRepo.return_value
    repo.get_vehicle.return_value = _make_vehicle()
    repo.get_auctions_for_vehicle.return_value = [_make_auction()]

    no_pred = PricePrediction(
        predicted_price=0, confidence="low",
        market_avg=0, market_count=0,
        auction_discount=0.20, reasoning=["No data"],
    )
    result = score(1, no_pred)
    assert result.score == 5.0
    assert result.rating == "Fair"


@patch("execution.deal_scorer.Repository")
def test_score_vehicle_not_found(MockRepo):
    """Returns None when vehicle doesn't exist."""
    repo = MockRepo.return_value
    repo.get_vehicle.return_value = None
    result = score(999)
    assert result is None
