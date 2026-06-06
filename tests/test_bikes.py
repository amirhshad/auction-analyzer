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


from execution.db_models import get_engine, Base
from execution.db_repository import Repository
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import tempfile, os


def _make_test_repo():
    """Create an in-memory SQLite repo for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    repo = Repository.__new__(Repository)
    repo.session = sessionmaker(bind=engine)()
    return repo


def test_upsert_bike_creates_new():
    repo = _make_test_repo()
    bike = repo.upsert_bike(external_id="1", source="ovm", brand="Cannondale", model="SystemSix", current_bid=500.0)
    assert bike.id is not None
    assert bike.brand == "Cannondale"


def test_upsert_bike_updates_existing():
    repo = _make_test_repo()
    repo.upsert_bike(external_id="1", source="ovm", current_bid=500.0)
    updated = repo.upsert_bike(external_id="1", source="ovm", current_bid=750.0)
    assert updated.current_bid == 750.0
    from execution.db_models import Bike as BikeModel
    assert repo.session.query(BikeModel).count() == 1


def test_list_bikes_returns_all():
    repo = _make_test_repo()
    repo.upsert_bike(external_id="1", source="ovm", brand="Trek")
    repo.upsert_bike(external_id="2", source="ovm", brand="Giant")
    assert len(repo.list_bikes()) == 2


def test_toggle_bike_favorite():
    repo = _make_test_repo()
    bike = repo.upsert_bike(external_id="1", source="ovm")
    assert bike.is_favorite == 0
    repo.toggle_bike_favorite(bike.id)
    from execution.db_models import Bike
    refreshed = repo.session.query(Bike).get(bike.id)
    assert refreshed.is_favorite == 1


def test_upsert_bike_market_price():
    repo = _make_test_repo()
    mp = repo.upsert_bike_market_price(brand="Trek", model="Domane", asking_price=1200.0)
    assert mp.id is not None
    assert mp.asking_price == 1200.0


def test_get_bike_market_prices_filters_by_brand():
    repo = _make_test_repo()
    repo.upsert_bike_market_price(brand="Trek", model="Domane", asking_price=1200.0)
    repo.upsert_bike_market_price(brand="Giant", model="TCR", asking_price=900.0)
    prices = repo.get_bike_market_prices(brand="Trek")
    assert len(prices) == 1
    assert prices[0].brand == "Trek"


def test_clear_bike_market_prices():
    repo = _make_test_repo()
    repo.upsert_bike_market_price(brand="Trek", model="Domane", asking_price=1200.0)
    repo.upsert_bike_market_price(brand="Trek", model="Domane", asking_price=1300.0)
    deleted = repo.clear_bike_market_prices(brand="Trek", model="Domane")
    assert deleted == 2
    assert repo.get_bike_market_prices("Trek") == []


from execution.scrape_bikes import _extract_auction_id, _strip_html, _extract_bike_fields


def test_extract_auction_id_english():
    url = "https://onlineveilingmeester.nl/en/auctions/9086/lots"
    assert _extract_auction_id(url) == "9086"


def test_extract_auction_id_dutch():
    url = "https://www.onlineveilingmeester.nl/nl/veilingen/8000/kavels"
    assert _extract_auction_id(url) == "8000"


def test_extract_auction_id_missing():
    assert _extract_auction_id("https://example.com/") is None


def test_strip_html_removes_tags():
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_handles_br():
    result = _strip_html("Line1<br>Line2")
    assert "Line1" in result and "Line2" in result


def test_extract_bike_fields_full():
    kavel_data = {
        "naam": "Racefiets, Cannondale, SystemSix, Acid Red",
        "product": "Racefiets",
        "merk": "Cannondale",
        "productType": "SystemSix",
        "kleur": "Acid Red",
        "maatvoering": "Framemaat 54 cm.",
        "conditie": "Gebruikt",
        "specificaties": "<p>Shimano Ultegra Di2</p>",
        "bijzonderheden": "<p>Frame is wrapped</p>",
    }
    fields = _extract_bike_fields(kavel_data)
    assert fields["bike_type"] == "Racefiets"
    assert fields["brand"] == "Cannondale"
    assert fields["model"] == "SystemSix"
    assert fields["color"] == "Acid Red"
    assert fields["frame_size"] == "Framemaat 54 cm."
    assert fields["condition"] == "Gebruikt"
    assert "Shimano" in fields["components"]
    assert "wrapped" in fields["notes"]


def test_extract_bike_fields_fallback_brand():
    """When merk is missing, falls back to second comma-part of naam."""
    kavel_data = {
        "naam": "Fatbike, C80",
        "product": "Fatbike",
        "merk": None,
        "productType": "C80",
        "conditie": "gebruikt",
    }
    fields = _extract_bike_fields(kavel_data)
    assert fields["bike_type"] == "Fatbike"
    assert fields["brand"] == "C80"
    assert fields["model"] == "C80"


from execution.scrape_marktplaats import _parse_dutch_price, _build_search_url, _extract_prices_from_html


def test_parse_dutch_price_dot_thousands():
    assert _parse_dutch_price("€1.999,-") == 1999.0


def test_parse_dutch_price_comma_decimal():
    assert _parse_dutch_price("€2.600,00") == 2600.0


def test_parse_dutch_price_simple():
    assert _parse_dutch_price("€850") == 850.0


def test_parse_dutch_price_nbsp():
    assert _parse_dutch_price("€\xa02.500") == 2500.0


def test_parse_dutch_price_out_of_range():
    assert _parse_dutch_price("€5") is None   # too cheap for a bike
    assert _parse_dutch_price("€500000") is None  # too expensive


def test_build_search_url_with_model():
    url = _build_search_url(brand="Cannondale", model="SystemSix")
    assert "cannondale" in url.lower()
    assert "systemsix" in url.lower() or "system" in url.lower()


def test_build_search_url_brand_only():
    url = _build_search_url(brand="Trek", model=None)
    assert "trek" in url.lower()


def test_extract_prices_from_html_finds_prices():
    html = '<span class="price">€1.999,-</span><span class="price">€850</span>'
    prices = _extract_prices_from_html(html)
    assert 1999.0 in prices
    assert 850.0 in prices
