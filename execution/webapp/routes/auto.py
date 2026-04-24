"""Auto Auctions page routes."""
from statistics import median as _median
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse

from execution.webapp.deps import templates, get_repo, get_task_manager
from execution.price_predictor import predict
from execution.deal_scorer import score
from execution.bid_strategist import get_strategy

router = APIRouter(tags=["auto"])


def _render(request: Request, name: str, ctx: dict):
    """Render a template with the new Starlette API."""
    return templates.TemplateResponse(request, name, context=ctx)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build_vehicle_rows(repo, vehicles):
    """Build row dicts for a list of vehicles (mirrors dashboard logic)."""
    rows = []
    for v in vehicles:
        auctions = repo.get_auctions_for_vehicle(v.id)
        auction = auctions[0] if auctions else None

        # Market price
        market_prices = repo.get_market_prices(v.make or "", v.model or "", v.year)
        avg_market = None
        if market_prices:
            prices = [mp.asking_price for mp in market_prices if mp.asking_price]
            if prices:
                avg_market = _median(prices)

        prediction = predict(v.id)
        deal = score(v.id, prediction)
        strategy = get_strategy(v.id, prediction)

        bid = auction.current_bid if auction and auction.current_bid else 0
        market = avg_market or 0
        savings = market - bid if market and bid else 0

        rows.append({
            "id": v.id,
            "fav": bool(v.is_favorite),
            "make": v.make or "N/A",
            "model": v.model or "N/A",
            "year": v.year or 0,
            "mileage_km": v.mileage_km,
            "bid": bid,
            "market": market,
            "max_bid": strategy.max_bid if strategy and strategy.max_bid else 0,
            "score": deal.score if deal else 0,
            "rating": deal.rating if deal else "N/A",
            "recommendation": deal.recommendation if deal else "",
            "factors": deal.factors if deal else [],
            "savings": savings,
            "savings_pct": (savings / market * 100) if market and savings > 0 else 0,
            "url": v.url or "",
            "fuel_type": v.fuel_type or "",
            "bid_count": auction.bid_count if auction else 0,
        })
    return rows


def _filter_rows(rows, make: str = "All", fuel: str = "All"):
    filtered = rows
    if make != "All":
        filtered = [r for r in filtered if r["make"] == make]
    if fuel != "All":
        filtered = [r for r in filtered if r["fuel_type"] == fuel]
    return filtered


def _compute_stats(rows):
    total = len(rows)
    total_bids = sum(r["bid_count"] for r in rows)
    total_bid_value = sum(r["bid"] for r in rows)
    avg_bid = total_bid_value / max(total, 1)
    return {
        "total_vehicles": total,
        "total_bids": total_bids,
        "total_bid_value": total_bid_value,
        "avg_bid": avg_bid,
    }


def _get_filter_options(rows):
    makes = sorted(set(r["make"] for r in rows if r["make"] != "N/A"))
    fuels = sorted(set(r["fuel_type"] for r in rows if r["fuel_type"]))
    return makes, fuels


# ---------------------------------------------------------------------------
# Full page
# ---------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
@router.get("/auto", response_class=HTMLResponse)
def auto_page(request: Request, repo=Depends(get_repo)):
    auction_names = repo.get_auction_names()
    selected = "All"

    vehicles = repo.list_vehicles(limit=200)
    rows = _build_vehicle_rows(repo, vehicles)
    makes, fuels = _get_filter_options(rows)
    stats = _compute_stats(rows)

    fav_rows = [r for r in rows if r["fav"]]

    return _render(request, "auto/page.html", {
        "active_page": "auto",
        "auction_names": auction_names,
        "selected_auction": selected,
        "makes": makes,
        "fuels": fuels,
        "selected_make": "All",
        "selected_fuel": "All",
        "stats": stats,
        "rows": rows,
        "fav_rows": fav_rows,
        "active_tab": "favorites",
    })


# ---------------------------------------------------------------------------
# Tab partials
# ---------------------------------------------------------------------------
@router.get("/auto/tab/{tab_name}", response_class=HTMLResponse)
def auto_tab(
    request: Request,
    tab_name: str,
    auction: str = "All",
    make: str = "All",
    fuel: str = "All",
    repo=Depends(get_repo),
):
    if auction == "All":
        vehicles = repo.list_vehicles(limit=200)
    else:
        vehicles = repo.get_vehicles_by_auction(auction)

    rows = _build_vehicle_rows(repo, vehicles)
    filtered = _filter_rows(rows, make, fuel)
    stats = _compute_stats(filtered)

    template_map = {
        "favorites": "auto/_favorites.html",
        "best_deals": "auto/_best_deals.html",
        "all_vehicles": "auto/_all_vehicles.html",
        "analysis": "auto/_analysis.html",
    }
    template_name = template_map.get(tab_name, "auto/_favorites.html")

    ctx = {
        "rows": filtered,
        "stats": stats,
        "active_tab": tab_name,
        "selected_auction": auction,
        "selected_make": make,
        "selected_fuel": fuel,
    }

    if tab_name == "favorites":
        ctx["fav_rows"] = [r for r in filtered if r["fav"]]
    elif tab_name == "best_deals":
        sorted_rows = sorted(filtered, key=lambda r: r["score"], reverse=True)
        ctx["deal_rows"] = [r for r in sorted_rows if r["score"] >= 6.0] or sorted_rows[:5]
    elif tab_name == "analysis":
        ctx["all_rows"] = filtered

    return _render(request, template_name, ctx)


# ---------------------------------------------------------------------------
# Scrape actions
# ---------------------------------------------------------------------------
@router.post("/auto/scrape", response_class=HTMLResponse)
def auto_scrape(
    request: Request,
    url: str = Form(...),
    max_vehicles: int = Form(50),
):
    tm = get_task_manager()
    task_id = tm.create_task_id()

    def _do_scrape():
        if "onlineveilingmeester" in url.lower():
            from execution.scrape_onlineveilingmeester import run as scrape_ovm
            return scrape_ovm(url=url, progress_callback=tm.make_progress_callback(task_id))
        elif "troostwijk" in url.lower():
            from execution.scrape_troostwijk import run as scrape_tw
            return scrape_tw(url=url, max_lots=max_vehicles, progress_callback=tm.make_progress_callback(task_id))
        else:
            raise ValueError("Unsupported URL. Use troostwijkauctions.com or onlineveilingmeester.nl")

    tm.submit(task_id, _do_scrape)
    return _render(request, "partials/_progress.html", {"task_id": task_id})


@router.post("/auto/scrape-troostwijk", response_class=HTMLResponse)
def auto_scrape_troostwijk(
    request: Request,
    pages: int = Form(2),
    lots: int = Form(20),
):
    tm = get_task_manager()
    task_id = tm.create_task_id()

    def _do_scrape():
        from execution.scrape_troostwijk import run as scrape_tw
        return scrape_tw(pages=pages, max_lots=lots, progress_callback=tm.make_progress_callback(task_id))

    tm.submit(task_id, _do_scrape)
    return _render(request, "partials/_progress.html", {"task_id": task_id})


@router.post("/auto/lookup-market", response_class=HTMLResponse)
def auto_lookup_market(
    request: Request,
    auction: str = Form("All"),
    repo=Depends(get_repo),
):
    if auction == "All":
        vehicles = repo.list_vehicles(limit=200)
    else:
        vehicles = repo.get_vehicles_by_auction(auction)

    # Build unique make/model/year combos
    seen = set()
    lookups = []
    for v in vehicles:
        if v.make and v.model:
            key = (v.make, v.model, v.year)
            if key not in seen:
                seen.add(key)
                lookups.append((v.make, v.model, v.year, v.mileage_km))

    tm = get_task_manager()
    task_id = tm.create_task_id()

    def _do_lookup():
        from execution.scrape_gaspedaal import run as scrape_market
        state = tm.get_state(task_id)
        total_found = 0
        for idx, (make, model, year, mileage_km) in enumerate(lookups):
            if state:
                state.progress = min(int((idx / max(len(lookups), 1)) * 90) + 10, 99)
                state.message = f"Looking up {make} {model} ({idx + 1}/{len(lookups)})"
            try:
                found = scrape_market(make=make, model=model, year=year, mileage_km=mileage_km)
                total_found += len(found)
            except Exception:
                pass
        return {"total_found": total_found, "lookups": len(lookups)}

    tm.submit(task_id, _do_lookup)
    return _render(request, "partials/_progress.html", {"task_id": task_id})
