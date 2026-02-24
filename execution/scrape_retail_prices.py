"""
Retail Price Lookup (bol.com + Amazon.nl)
Uses Playwright to search for product retail prices.

Usage:
    python -m execution.scrape_retail_prices --query "Samsung Monitor 32 inch"
    python -m execution.scrape_retail_prices --goods-id 42
"""

import argparse
import json
import re
import sys
import time
from typing import Optional

from execution.config import SCRAPING_DELAY_SECONDS
from execution.rate_limiter import RateLimiter
from execution.db_repository import Repository


def _clean_search_query(title: str) -> str:
    """Build a search query from an auction item title.
    Strips lot numbers, auction codes, and noisy suffixes."""
    # Remove lot IDs like A1-38027-12785
    q = re.sub(r'A\d+-\d+(-\d+)?', '', title)
    # Remove common noise words
    q = re.sub(r'\b(kavel|lot|partij|retour|veiling|auction)\b', '', q, flags=re.IGNORECASE)
    # Remove extra whitespace and punctuation noise
    q = re.sub(r'[()[\]{}]', '', q)
    q = re.sub(r'\s+', ' ', q).strip()
    # Cap length for search engines
    if len(q) > 100:
        q = q[:100].rsplit(' ', 1)[0]
    return q


def _parse_price_text(text: str) -> Optional[float]:
    """Parse a Dutch price string to float."""
    if not text:
        return None
    cleaned = re.sub(r'[€\s]', '', text)
    # Handle "1.299,00" or "29,99" or "1299.00"
    if ',' in cleaned and '.' in cleaned:
        cleaned = cleaned.replace('.', '').replace(',', '.')
    elif ',' in cleaned:
        cleaned = cleaned.replace(',', '.')
    try:
        val = float(cleaned)
        return val if val > 0 else None
    except ValueError:
        return None


def search_bol(page, query: str, rate_limiter: RateLimiter) -> list[dict]:
    """Search bol.com and extract product prices."""
    results = []
    search_url = f"https://www.bol.com/nl/nl/s/?searchtext={query.replace(' ', '+')}"

    rate_limiter.wait()
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        # Dismiss cookie consent
        try:
            consent = page.locator("button#js-first-screen-accept, button:has-text('Accepteren'), button:has-text('cookies accepteren')")
            if consent.count() > 0:
                consent.first.click(timeout=5000)
                page.wait_for_timeout(1000)
        except Exception:
            pass

        page.wait_for_timeout(1500)

        # Extract prices from search results
        data = page.evaluate("""() => {
            const items = [];
            // bol.com product cards
            const cards = document.querySelectorAll(
                '[data-test="product-card"], .product-item--row, [class*="product-item"], li[class*="product"]'
            );
            for (const card of [...cards].slice(0, 10)) {
                const titleEl = card.querySelector(
                    '[data-test="product-title"], .product-title, a[href*="/p/"]'
                );
                const priceWhole = card.querySelector(
                    '[data-test="price-current"] .promo-price, .promo-price, [class*="price"] [class*="whole"]'
                );
                const priceFraction = card.querySelector(
                    '[class*="price"] [class*="fraction"], sup'
                );

                let priceText = '';
                if (priceWhole) {
                    priceText = priceWhole.textContent.trim();
                    if (priceFraction) {
                        priceText += ',' + priceFraction.textContent.trim();
                    }
                }

                // Fallback: look for any price-like text in the card
                if (!priceText) {
                    const allText = card.textContent;
                    const priceMatch = allText.match(/€\\s*([\\d.,]+)/);
                    if (priceMatch) priceText = priceMatch[1];
                }

                const title = titleEl ? titleEl.textContent.trim() : '';
                const link = titleEl ? (titleEl.href || titleEl.closest('a')?.href || '') : '';

                if (title && priceText) {
                    items.push({ title: title.substring(0, 120), price: priceText, url: link });
                }
            }

            // Fallback: try extracting from any visible price on the page
            if (items.length === 0) {
                const priceEls = document.querySelectorAll('[class*="price"], [data-test*="price"]');
                for (const el of [...priceEls].slice(0, 5)) {
                    const text = el.textContent.trim();
                    const match = text.match(/€?\\s*([\\d]+[.,]\\d{2})/);
                    if (match) {
                        items.push({ title: '', price: match[1], url: '' });
                    }
                }
            }

            return items;
        }""")

        for item in data:
            price = _parse_price_text(item.get("price", ""))
            if price and price > 0:
                results.append({
                    "title": item.get("title", ""),
                    "price": price,
                    "source": "bol.com",
                    "source_url": item.get("url", ""),
                })

    except Exception as e:
        print(f"    bol.com search error: {e}", file=sys.stderr)

    return results


def search_amazon(page, query: str, rate_limiter: RateLimiter) -> list[dict]:
    """Search Amazon.nl and extract product prices."""
    results = []
    search_url = f"https://www.amazon.nl/s?k={query.replace(' ', '+')}"

    rate_limiter.wait()
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        # Dismiss cookie consent
        try:
            consent = page.locator("#sp-cc-accept, button:has-text('Cookies accepteren'), button:has-text('Accept')")
            if consent.count() > 0:
                consent.first.click(timeout=5000)
                page.wait_for_timeout(1000)
        except Exception:
            pass

        page.wait_for_timeout(1500)

        # Extract prices from search results
        data = page.evaluate("""() => {
            const items = [];
            const cards = document.querySelectorAll(
                '[data-component-type="s-search-result"], .s-result-item[data-asin]'
            );
            for (const card of [...cards].slice(0, 10)) {
                const titleEl = card.querySelector('h2 a, .a-link-normal .a-text-normal');
                const wholeEl = card.querySelector('.a-price .a-price-whole');
                const fractionEl = card.querySelector('.a-price .a-price-fraction');

                let priceText = '';
                if (wholeEl) {
                    priceText = wholeEl.textContent.trim().replace('.', '').replace(',', '');
                    if (fractionEl) {
                        priceText += ',' + fractionEl.textContent.trim();
                    }
                }

                // Fallback
                if (!priceText) {
                    const priceEl = card.querySelector('.a-price .a-offscreen');
                    if (priceEl) priceText = priceEl.textContent.trim();
                }

                const title = titleEl ? titleEl.textContent.trim() : '';
                const link = titleEl ? titleEl.href || '' : '';

                if (title && priceText) {
                    items.push({
                        title: title.substring(0, 120),
                        price: priceText,
                        url: link ? 'https://www.amazon.nl' + (link.startsWith('/') ? link : '') : '',
                    });
                }
            }
            return items;
        }""")

        for item in data:
            price = _parse_price_text(item.get("price", ""))
            if price and price > 0:
                results.append({
                    "title": item.get("title", ""),
                    "price": price,
                    "source": "amazon.nl",
                    "source_url": item.get("url", ""),
                })

    except Exception as e:
        print(f"    Amazon.nl search error: {e}", file=sys.stderr)

    return results


def lookup_price(query: str, page=None, rate_limiter=None,
                 close_browser: bool = True) -> list[dict]:
    """Search bol.com and Amazon.nl for a product price.

    Args:
        query: Search query (product name).
        page: Optional Playwright page to reuse.
        rate_limiter: Optional rate limiter to reuse.
        close_browser: Whether to close the browser when done (only if we created it).

    Returns:
        List of price results with title, price, source, source_url.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Error: playwright not installed.", file=sys.stderr)
        return []

    if rate_limiter is None:
        rate_limiter = RateLimiter(SCRAPING_DELAY_SECONDS)

    own_browser = page is None
    browser = None
    pw = None

    try:
        if own_browser:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="nl-NL",
            )
            page = context.new_page()

        all_results = []

        # Search bol.com
        bol_results = search_bol(page, query, rate_limiter)
        all_results.extend(bol_results)
        print(f"    bol.com: {len(bol_results)} prices found")

        # Search Amazon.nl
        amazon_results = search_amazon(page, query, rate_limiter)
        all_results.extend(amazon_results)
        print(f"    Amazon.nl: {len(amazon_results)} prices found")

        return all_results

    finally:
        if own_browser and close_browser:
            if browser:
                browser.close()
            if pw:
                pw.stop()


def lookup_goods_prices(item_ids: Optional[list[int]] = None,
                        auction_name: Optional[str] = None,
                        progress_callback=None) -> dict:
    """Look up retail prices for goods items and update the database.

    Args:
        item_ids: Specific item IDs to look up. If None, looks up all items.
        auction_name: Filter by auction name.
        progress_callback: Optional callable(current, total, message).

    Returns:
        Summary dict with counts.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Error: playwright not installed.", file=sys.stderr)
        return {"looked_up": 0, "prices_found": 0}

    repo = Repository()
    rate_limiter = RateLimiter(SCRAPING_DELAY_SECONDS)

    # Get items to look up
    if item_ids:
        items = []
        for iid in item_ids:
            item = repo.session.query(
                __import__('execution.db_models', fromlist=['GoodsItem']).GoodsItem
            ).get(iid)
            if item:
                items.append(item)
    else:
        items = repo.list_goods_items(limit=200, auction_name=auction_name)

    if not items:
        repo.close()
        return {"looked_up": 0, "prices_found": 0}

    total = len(items)
    looked_up = 0
    prices_found = 0

    print(f"Looking up retail prices for {total} items...")
    if progress_callback:
        progress_callback(0, total, f"Looking up prices for {total} items...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="nl-NL",
        )
        page = context.new_page()

        try:
            for i, item in enumerate(items):
                title = item.title or ""
                if not title:
                    continue

                query = _clean_search_query(title)
                if len(query) < 3:
                    continue

                print(f"  [{i+1}/{total}] Searching: {query[:60]}...")
                if progress_callback:
                    progress_callback(i + 1, total, f"Looking up price {i+1}/{total}: {title[:40]}...")

                # Check cache first
                cached = repo.get_cached_price(query)
                if cached and cached.retail_price:
                    # Use cached price
                    _update_item_price(repo, item, cached.retail_price)
                    prices_found += 1
                    looked_up += 1
                    print(f"    Cached: €{cached.retail_price:,.2f} ({cached.source})")
                    continue

                # Search online
                results = lookup_price(query, page=page, rate_limiter=rate_limiter,
                                       close_browser=False)

                if results:
                    # Use median price for reliability
                    price_values = sorted(r["price"] for r in results)
                    median_price = price_values[len(price_values) // 2]

                    # Use the best match (first bol.com result, or first overall)
                    best = next((r for r in results if r["source"] == "bol.com"), results[0])

                    # Cache the result
                    repo.cache_goods_price(
                        product_name=title,
                        retail_price=median_price,
                        search_query=query,
                        source=best["source"],
                        source_url=best.get("source_url", ""),
                        confidence=min(len(results) / 5.0, 1.0),
                    )

                    # Update the goods item
                    _update_item_price(repo, item, median_price)
                    prices_found += 1
                    print(f"    Found: €{median_price:,.2f} (from {len(results)} results)")
                else:
                    print(f"    No prices found")

                looked_up += 1

        except Exception as e:
            print(f"Error during price lookup: {e}", file=sys.stderr)
        finally:
            browser.close()

    repo.close()
    summary = {"looked_up": looked_up, "prices_found": prices_found}
    print(f"\nDone. Looked up {looked_up} items, found {prices_found} prices.")
    return summary


def _update_item_price(repo: Repository, item, retail_price: float):
    """Update a goods item with estimated value and recommended max bid."""
    # Estimated value = retail price (condition-adjusted)
    condition = (item.condition or "").lower()
    if condition in ("new", "nieuw"):
        discount = 0.10  # 10% discount for new items at auction
    elif condition in ("return", "retour"):
        discount = 0.30  # 30% discount for returns
    elif condition in ("used", "gebruikt"):
        discount = 0.50  # 50% discount for used
    elif condition in ("damaged", "beschadigd"):
        discount = 0.70  # 70% discount for damaged
    else:
        discount = 0.35  # Default 35% for unknown condition

    estimated_value = retail_price * (1 - discount)
    recommended_max = estimated_value * 0.80  # 20% safety margin

    # Update via repository
    item.estimated_value = round(estimated_value, 2)
    item.recommended_max_bid = round(recommended_max, 2)
    item.updated_at = __import__('datetime').datetime.now(
        __import__('datetime').timezone.utc
    )
    repo.session.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Look up retail prices on bol.com and Amazon.nl")
    parser.add_argument("--query", help="Search query")
    parser.add_argument("--goods-id", type=int, help="Look up price for a specific goods item ID")
    parser.add_argument("--auction", help="Look up prices for all items in an auction")
    args = parser.parse_args()

    if args.query:
        results = lookup_price(args.query)
        print(json.dumps(results, indent=2, default=str))
    elif args.goods_id:
        summary = lookup_goods_prices(item_ids=[args.goods_id])
        print(json.dumps(summary, indent=2))
    elif args.auction:
        summary = lookup_goods_prices(auction_name=args.auction)
        print(json.dumps(summary, indent=2))
    else:
        print("Provide --query, --goods-id, or --auction", file=sys.stderr)
        sys.exit(1)
