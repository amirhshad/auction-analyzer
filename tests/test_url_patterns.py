"""Tests for URL pattern handling (Dutch/English formats)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from execution.scrape_onlineveilingmeester import normalize_url, get_lot_urls_pattern, URL_PATTERNS
from execution.scrape_troostwijk import extract_lot_id, parse_price, parse_int


# ---------------------------------------------------------------------------
# OnlineVeilingmeester URL handling
# ---------------------------------------------------------------------------

def test_normalize_url_adds_https():
    """URLs without protocol get https:// added."""
    assert normalize_url("www.example.com/auction").startswith("https://")


def test_normalize_url_preserves_https():
    """URLs with https:// are preserved."""
    url = "https://www.onlineveilingmeester.nl/nl/veilingen/test"
    assert normalize_url(url) == url


def test_normalize_url_strips_trailing_slash():
    """Trailing slashes are removed."""
    assert normalize_url("https://example.com/test/") == "https://example.com/test"


def test_url_patterns_dutch_to_lots():
    """Dutch /veilingen/ URL maps to /kavels/ for lot listing."""
    url = "https://www.onlineveilingmeester.nl/nl/veilingen/auto-veiling-123"
    result = get_lot_urls_pattern(url)
    assert "/kavels/" in result


def test_url_patterns_english_to_lots():
    """English /auctions/ URL maps to /lots/ for lot listing."""
    url = "https://www.onlineveilingmeester.nl/en/auctions/car-auction-123"
    result = get_lot_urls_pattern(url)
    # Should convert to lots path
    assert "/lots/" in result or "/kavels/" in result


def test_url_patterns_bidirectional():
    """URL pattern mapping contains both directions."""
    assert "/veilingen/" in URL_PATTERNS
    assert "/kavels/" in URL_PATTERNS
    assert "/auctions/" in URL_PATTERNS
    assert "/lots/" in URL_PATTERNS


# ---------------------------------------------------------------------------
# Troostwijk URL handling
# ---------------------------------------------------------------------------

def test_extract_lot_id_from_lot_url():
    """Extract numeric lot ID from /lot/12345 URL."""
    assert extract_lot_id("https://www.troostwijk.com/nl/lot/12345") == "12345"


def test_extract_lot_id_from_kavel_url():
    """Extract numeric lot ID from /kavel/67890 URL."""
    assert extract_lot_id("https://www.troostwijk.com/nl/kavel/67890") == "67890"


def test_extract_lot_id_fallback():
    """Falls back to last URL segment when no pattern matches."""
    assert extract_lot_id("https://example.com/something/abc123") == "abc123"


# ---------------------------------------------------------------------------
# Price/number parsing
# ---------------------------------------------------------------------------

def test_parse_price_dutch_format():
    """Parse Dutch price format: € 1.250,00."""
    assert parse_price("€ 1.250,00") == 1250.00


def test_parse_price_simple():
    """Parse simple price: €500."""
    assert parse_price("€500") == 500.0


def test_parse_price_with_spaces():
    """Parse price with spaces: € 12.500."""
    assert parse_price("€ 12.500") == 12500.0


def test_parse_price_none():
    """Returns None for empty/None input."""
    assert parse_price("") is None
    assert parse_price(None) is None


def test_parse_int_with_units():
    """Parse integer from text with units like '123.456 km'."""
    assert parse_int("123.456 km") == 123456


def test_parse_int_simple():
    """Parse simple integer."""
    assert parse_int("150 pk") == 150


def test_parse_int_none():
    """Returns None for empty/None input."""
    assert parse_int("") is None
    assert parse_int(None) is None
