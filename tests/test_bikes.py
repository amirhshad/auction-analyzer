# tests/test_bikes.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from execution.db_models import Bike, BikeMarketPrice, BikeImageAnalysis


def test_bike_model_has_required_columns():
    """Bike model has all expected columns."""
    cols = {c.key for c in Bike.__table__.columns}
    for col in ("id", "external_id", "source", "url", "bike_type", "brand",
                "model", "frame_size", "color", "condition", "components",
                "notes", "image_urls_json", "auction_name", "current_bid",
                "bid_count", "end_time", "is_favorite", "ai_estimated_value",
                "ai_recommended_max_bid", "ai_risk_level", "ai_explanation",
                "ai_evaluated_at", "ai_eval_hash", "created_at", "updated_at"):
        assert col in cols, f"Missing column: {col}"


def test_bike_image_urls_property():
    """Bike.image_urls parses JSON list."""
    b = Bike(image_urls_json='["http://a.com/1.jpg", "http://a.com/2.jpg"]')
    assert b.image_urls == ["http://a.com/1.jpg", "http://a.com/2.jpg"]


def test_bike_image_urls_empty():
    """Bike.image_urls returns [] when null."""
    b = Bike()
    assert b.image_urls == []


def test_bike_market_price_columns():
    """BikeMarketPrice has all expected columns."""
    cols = {c.key for c in BikeMarketPrice.__table__.columns}
    for col in ("id", "brand", "model", "bike_type", "asking_price", "source", "source_url", "scraped_at"):
        assert col in cols, f"Missing column: {col}"


def test_bike_image_analysis_columns():
    """BikeImageAnalysis has all expected columns."""
    cols = {c.key for c in BikeImageAnalysis.__table__.columns}
    for col in ("id", "bike_id", "image_url", "image_type", "condition_score",
                "overall_condition", "confidence", "damage_detected",
                "assessment_details", "raw_response", "model_used", "analyzed_at"):
        assert col in cols, f"Missing column: {col}"
