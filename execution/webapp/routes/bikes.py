"""Bikes Auctions page routes."""
from statistics import median as _median
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse

from execution.webapp.deps import templates, get_repo, get_task_manager

router = APIRouter(tags=["bikes"])


def _render(request: Request, name: str, ctx: dict):
    return templates.TemplateResponse(request, name, context=ctx)


def _build_bike_rows(repo, bikes):
    rows = []
    for b in bikes:
        bid = b.current_bid or 0
        ai_value = b.ai_estimated_value or 0
        ai_savings = ai_value - bid if ai_value and bid else 0
        ai_savings_pct = (ai_savings / ai_value * 100) if ai_value and ai_savings > 0 else 0

        # Market price from Marktplaats
        market_prices = repo.get_bike_market_prices(b.brand or "", b.model)
        market_med = 0
        if market_prices:
            prices = [mp.asking_price for mp in market_prices if mp.asking_price]
            if prices:
                market_med = _median(prices)

        market_savings = market_med - bid if market_med and bid else 0
        market_savings_pct = (market_savings / market_med * 100) if market_med and market_savings > 0 else 0

        if not ai_value or not bid:
            rating = 0
        else:
            rating = min(10, max(1, round(ai_savings_pct / 5)))
            if ai_savings_pct <= 0:
                rating = 1

        # Image analysis summary
        analyses = repo.get_bike_image_analyses(b.id)
        img_score = None
        if analyses:
            scores = [a.condition_score for a in analyses if a.condition_score]
            if scores:
                img_score = round(sum(scores) / len(scores), 1)

        rows.append({
            "id": b.id,
            "fav": bool(b.is_favorite),
            "bike_type": b.bike_type or "N/A",
            "brand": b.brand or "N/A",
            "model": b.model or "N/A",
            "frame_size": b.frame_size or "N/A",
            "color": b.color or "N/A",
            "condition": b.condition or "N/A",
            "bid": bid,
            "market": market_med,
            "market_savings": market_savings,
            "market_savings_pct": market_savings_pct,
            "ai_value": ai_value,
            "ai_max_bid": b.ai_recommended_max_bid or 0,
            "ai_risk_level": b.ai_risk_level or "",
            "ai_explanation": b.ai_explanation or "",
            "ai_savings": ai_savings,
            "ai_savings_pct": ai_savings_pct,
            "rating": rating,
            "img_score": img_score,
            "url": b.url or "",
            "auction_name": b.auction_name or "",
            "image_url": (b.image_urls[0] if b.image_urls else ""),
        })
    return rows


def _filter_rows(rows, bike_type: str = "All", brand: str = "All"):
    if bike_type != "All":
        rows = [r for r in rows if r["bike_type"] == bike_type]
    if brand != "All":
        rows = [r for r in rows if r["brand"] == brand]
    return rows


def _compute_stats(rows):
    return {
        "total_bikes": len(rows),
        "total_bids": sum(1 for r in rows if r["bid"]),
        "total_bid_value": sum(r["bid"] for r in rows),
        "total_ai_value": sum(r["ai_value"] for r in rows),
    }


def _get_filter_options(rows):
    types = sorted(set(r["bike_type"] for r in rows if r["bike_type"] != "N/A"))
    brands = sorted(set(r["brand"] for r in rows if r["brand"] != "N/A"))
    return types, brands


# ---------------------------------------------------------------------------
# Full page
# ---------------------------------------------------------------------------
@router.get("/bikes", response_class=HTMLResponse)
def bikes_page(request: Request, repo=Depends(get_repo)):
    auction_names = repo.get_bike_auction_names()
    bikes = repo.list_bikes(limit=200)
    rows = _build_bike_rows(repo, bikes)
    types, brands = _get_filter_options(rows)
    stats = _compute_stats(rows)
    fav_rows = [r for r in rows if r["fav"]]

    return _render(request, "bikes/page.html", {
        "active_page": "bikes",
        "auction_names": auction_names,
        "selected_auction": "All",
        "bike_types": types,
        "brands": brands,
        "selected_type": "All",
        "selected_brand": "All",
        "stats": stats,
        "rows": rows,
        "fav_rows": fav_rows,
        "active_tab": "favorites",
    })


# ---------------------------------------------------------------------------
# Tab partials
# ---------------------------------------------------------------------------
@router.get("/bikes/tab/{tab_name}", response_class=HTMLResponse)
def bikes_tab(
    request: Request,
    tab_name: str,
    auction: str = "All",
    bike_type: str = "All",
    brand: str = "All",
    repo=Depends(get_repo),
):
    bikes = repo.list_bikes(limit=200, auction_name=auction if auction != "All" else None)
    rows = _build_bike_rows(repo, bikes)
    filtered = _filter_rows(rows, bike_type, brand)
    stats = _compute_stats(filtered)

    template_map = {
        "favorites": "bikes/_favorites.html",
        "best_deals": "bikes/_best_deals.html",
        "all_bikes": "bikes/_all_bikes.html",
    }
    template_name = template_map.get(tab_name, "bikes/_favorites.html")

    ctx = {
        "rows": filtered,
        "stats": stats,
        "active_tab": tab_name,
        "selected_auction": auction,
        "selected_type": bike_type,
        "selected_brand": brand,
    }

    if tab_name == "favorites":
        ctx["fav_rows"] = [r for r in filtered if r["fav"]]
    elif tab_name == "best_deals":
        sorted_rows = sorted(filtered, key=lambda r: r["ai_savings_pct"], reverse=True)
        ctx["deal_rows"] = [r for r in sorted_rows if r["ai_savings"] > 0][:10] or sorted_rows[:5]

    return _render(request, template_name, ctx)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
@router.post("/bikes/scrape", response_class=HTMLResponse)
def bikes_scrape(request: Request, url: str = Form(...)):
    tm = get_task_manager()
    task_id = tm.create_task_id()

    def _do():
        from execution.scrape_bikes import run as scrape_fn
        return scrape_fn(url=url, progress_callback=tm.make_progress_callback(task_id))

    tm.submit(task_id, _do)
    return _render(request, "partials/_progress.html", {"task_id": task_id})


@router.post("/bikes/refresh-bids", response_class=HTMLResponse)
def bikes_refresh_bids(request: Request, auction: str = Form("All")):
    tm = get_task_manager()
    task_id = tm.create_task_id()

    def _do():
        from execution.refresh_bike_bids import refresh_bike_bids
        return refresh_bike_bids(
            auction_name=auction if auction != "All" else None,
            progress_callback=tm.make_progress_callback(task_id),
        )

    tm.submit(task_id, _do)
    return _render(request, "partials/_progress.html", {"task_id": task_id})


@router.post("/bikes/lookup-prices", response_class=HTMLResponse)
def bikes_lookup_prices(request: Request, auction: str = Form("All")):
    tm = get_task_manager()
    task_id = tm.create_task_id()

    def _do():
        from execution.scrape_marktplaats import run_for_all_bikes
        return run_for_all_bikes(
            auction_name=auction if auction != "All" else None,
            progress_callback=tm.make_progress_callback(task_id),
        )

    tm.submit(task_id, _do)
    return _render(request, "partials/_progress.html", {"task_id": task_id})


@router.post("/bikes/evaluate", response_class=HTMLResponse)
def bikes_evaluate(request: Request, auction: str = Form("All")):
    tm = get_task_manager()
    task_id = tm.create_task_id()

    def _do():
        from execution.bike_evaluator import evaluate_bikes
        return evaluate_bikes(
            auction_name=auction if auction != "All" else None,
            progress_callback=tm.make_progress_callback(task_id),
        )

    tm.submit(task_id, _do)
    return _render(request, "partials/_progress.html", {"task_id": task_id})


@router.post("/bikes/analyze-images", response_class=HTMLResponse)
def bikes_analyze_images(request: Request, auction: str = Form("All")):
    tm = get_task_manager()
    task_id = tm.create_task_id()

    def _do():
        from execution.image_analyzer import run as analyze_fn
        from execution.db_repository import Repository
        repo = Repository()
        bikes = repo.list_bikes(limit=9999, auction_name=auction if auction != "All" else None)
        # Only bikes that have no analysis yet
        pending = [b for b in bikes if not repo.get_bike_image_analyses(b.id)]
        repo.close()

        total = len(pending)
        cb = tm.make_progress_callback(task_id)
        for i, bike in enumerate(pending, 1):
            cb(i, total, f"Analyzing images for {bike.brand} {bike.model}...")
            analyze_fn(bike_id=bike.id, max_images=5)
        return {"analyzed": total}

    tm.submit(task_id, _do)
    return _render(request, "partials/_progress.html", {"task_id": task_id})


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------
@router.post("/api/bikes/favorite/{bike_id}", response_class=HTMLResponse)
def toggle_bike_favorite(bike_id: int, repo=Depends(get_repo)):
    repo.toggle_bike_favorite(bike_id)
    return HTMLResponse("")


@router.delete("/api/bikes/auction/{auction_name}")
def delete_bike_auction(auction_name: str, repo=Depends(get_repo)):
    count = repo.delete_bike_auction(auction_name)
    return {"deleted": count}
