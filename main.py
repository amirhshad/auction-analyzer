"""
Auction Analyzer CLI
Entry point for all commands.

Usage:
    python main.py dashboard
    python main.py scrape --pages 2 --lots 20
    python main.py market --make BMW --model "3-serie" --year 2020
    python main.py list --limit 50
    python main.py analyze --id 1
    python main.py analyze-images --id 1 --max-images 5
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def cmd_dashboard(args):
    """Launch the Streamlit dashboard."""
    dashboard_path = PROJECT_ROOT / "execution" / "dashboard_app.py"
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(dashboard_path)])


def cmd_scrape(args):
    """Scrape Troostwijk vehicle auctions."""
    from execution.scrape_troostwijk import run
    results = run(url=args.url, pages=args.pages, max_lots=args.lots)
    print(json.dumps({"scraped": len(results)}, indent=2))


def cmd_scrape_ovm(args):
    """Scrape OnlineVeilingmeester auctions."""
    from execution.scrape_onlineveilingmeester import run
    results = run(url=args.url)
    print(json.dumps({"scraped": len(results)}, indent=2))


def cmd_scrape_goods(args):
    """Scrape goods auctions."""
    from execution.scrape_goods import run
    results = run(url=args.url, max_lots=args.lots)
    print(json.dumps({"scraped": len(results)}, indent=2))


def cmd_lookup_prices(args):
    """Look up retail prices for goods items."""
    from execution.scrape_retail_prices import lookup_goods_prices
    summary = lookup_goods_prices(auction_name=args.auction)
    print(json.dumps(summary, indent=2))


def cmd_market(args):
    """Fetch market prices from Gaspedaal (primary) or AutoScout24 (fallback)."""
    from execution.scrape_gaspedaal import run
    results = run(make=args.make, model=args.model, year=args.year, mileage_km=args.mileage)
    if not results:
        print("  No Gaspedaal results, trying AutoScout24...")
        from execution.scrape_autoscout24 import run as run_as24
        results = run_as24(make=args.make, model=args.model, year=args.year, mileage_km=args.mileage)
    print(json.dumps({"found": len(results)}, indent=2))


def cmd_list(args):
    """List vehicles in the database."""
    from execution.db_repository import Repository
    repo = Repository()
    vehicles = repo.list_vehicles(limit=args.limit)

    if not vehicles:
        print("No vehicles found.")
        repo.close()
        return

    print(f"{'ID':>4} {'Make':<12} {'Model':<20} {'Year':>5} {'Mileage':>10} {'Source':<20}")
    print("-" * 75)
    for v in vehicles:
        mileage = f"{v.mileage_km:,} km" if v.mileage_km else "N/A"
        print(f"{v.id:>4} {(v.make or 'N/A'):<12} {(v.model or 'N/A'):<20} {(v.year or 0):>5} {mileage:>10} {(v.source or 'N/A'):<20}")

    print(f"\nTotal: {len(vehicles)} vehicles")
    repo.close()


def cmd_analyze(args):
    """Run full analysis on a vehicle."""
    from execution.price_predictor import predict
    from execution.deal_scorer import score
    from execution.bid_strategist import get_strategy
    from dataclasses import asdict

    print(f"Analyzing vehicle {args.id}...\n")

    prediction = predict(args.id)
    if prediction:
        print("=== Price Prediction ===")
        print(f"  Predicted price: €{prediction.predicted_price:,.0f}")
        print(f"  Confidence: {prediction.confidence}")
        print(f"  Market average: €{prediction.market_avg:,.0f} ({prediction.market_count} listings)")
        for r in prediction.reasoning:
            print(f"  - {r}")
        print()

    deal = score(args.id, prediction)
    if deal:
        print("=== Deal Score ===")
        print(f"  Score: {deal.score}/10 ({deal.rating})")
        print(f"  Recommendation: {deal.recommendation}")
        for f in deal.factors:
            print(f"  - {f}")
        print()

    strategy = get_strategy(args.id, prediction)
    if strategy:
        print("=== Bid Strategy ===")
        print(f"  Max bid: €{strategy.max_bid:,.0f}")
        print(f"  Risk level: {strategy.risk_level}")
        print(f"  Timing: {strategy.timing_advice}")
        for n in strategy.strategy_notes:
            print(f"  - {n}")

    # JSON output
    output = {}
    if prediction:
        output["prediction"] = asdict(prediction)
    if deal:
        output["deal_score"] = asdict(deal)
    if strategy:
        output["strategy"] = asdict(strategy)

    print(f"\n--- JSON ---")
    print(json.dumps(output, indent=2))


def cmd_analyze_images(args):
    """Analyze vehicle images with GPT-4 Vision."""
    from execution.image_analyzer import run
    from dataclasses import asdict

    result = run(args.id, max_images=args.max_images)
    if result:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(json.dumps({"error": "Image analysis failed"}))


def cmd_evaluate_goods(args):
    """AI-evaluate goods auction items."""
    from execution.goods_evaluator import evaluate_goods_items
    item_ids = [args.item_id] if args.item_id else None
    summary = evaluate_goods_items(
        item_ids=item_ids,
        auction_name=args.auction,
        force=args.force,
    )
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Auction Analyzer — Dutch vehicle & goods auction tool")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # dashboard
    subparsers.add_parser("dashboard", help="Launch the Streamlit dashboard")

    # scrape (Troostwijk)
    p_scrape = subparsers.add_parser("scrape", help="Scrape Troostwijk vehicle auctions")
    p_scrape.add_argument("--url", help="Specific auction URL to scrape")
    p_scrape.add_argument("--pages", type=int, default=2, help="Category pages to scrape")
    p_scrape.add_argument("--lots", type=int, default=20, help="Max lots to scrape")

    # scrape-ovm (OnlineVeilingmeester)
    p_ovm = subparsers.add_parser("scrape-ovm", help="Scrape OnlineVeilingmeester auctions")
    p_ovm.add_argument("--url", required=True, help="Auction URL")

    # scrape-goods
    p_goods = subparsers.add_parser("scrape-goods", help="Scrape goods auctions")
    p_goods.add_argument("--url", required=True, help="Auction URL")
    p_goods.add_argument("--lots", type=int, default=50, help="Max lots to scrape")

    # lookup-prices
    p_prices = subparsers.add_parser("lookup-prices", help="Look up retail prices for goods items")
    p_prices.add_argument("--auction", help="Auction name to look up")

    # market
    p_market = subparsers.add_parser("market", help="Fetch AutoScout24 market prices")
    p_market.add_argument("--make", required=True, help="Vehicle make")
    p_market.add_argument("--model", help="Vehicle model")
    p_market.add_argument("--year", type=int, help="Year")
    p_market.add_argument("--mileage", type=int, help="Mileage in km")

    # list
    p_list = subparsers.add_parser("list", help="List vehicles in database")
    p_list.add_argument("--limit", type=int, default=50, help="Max vehicles to show")

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="Run full analysis on a vehicle")
    p_analyze.add_argument("--id", type=int, required=True, help="Vehicle ID")

    # analyze-images
    p_images = subparsers.add_parser("analyze-images", help="Analyze vehicle images with AI")
    p_images.add_argument("--id", type=int, required=True, help="Vehicle ID")
    p_images.add_argument("--max-images", type=int, default=5, help="Max images to analyze")

    # evaluate-goods
    p_eval = subparsers.add_parser("evaluate-goods", help="AI-evaluate goods auction items")
    p_eval.add_argument("--auction", help="Filter by auction name")
    p_eval.add_argument("--item-id", type=int, help="Evaluate a specific item")
    p_eval.add_argument("--force", action="store_true", help="Force re-evaluation (ignore cache)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "dashboard": cmd_dashboard,
        "scrape": cmd_scrape,
        "scrape-ovm": cmd_scrape_ovm,
        "scrape-goods": cmd_scrape_goods,
        "lookup-prices": cmd_lookup_prices,
        "market": cmd_market,
        "list": cmd_list,
        "analyze": cmd_analyze,
        "analyze-images": cmd_analyze_images,
        "evaluate-goods": cmd_evaluate_goods,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
