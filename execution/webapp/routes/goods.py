"""Goods Auctions page routes."""
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse

from execution.webapp.deps import templates, get_repo, get_task_manager

router = APIRouter(tags=["goods"])


def _render(request: Request, name: str, ctx: dict):
    """Render a template with the new Starlette API."""
    return templates.TemplateResponse(request, name, context=ctx)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build_goods_rows(items):
    """Build row dicts for a list of goods items."""
    rows = []
    for item in items:
        bid = item.current_bid or 0
        est = item.estimated_value or 0
        savings = est - bid if est and bid else 0
        savings_pct = (savings / est * 100) if est and savings > 0 else 0

        # Rating /10 based on savings percentage
        if not est or not bid:
            rating = 0
        else:
            rating = min(10, max(1, round(savings_pct / 5)))
            if savings_pct <= 0:
                rating = 1

        ai_value = item.ai_estimated_value or 0
        ai_savings = ai_value - bid if ai_value and bid else 0
        ai_savings_pct = (ai_savings / ai_value * 100) if ai_value and ai_savings > 0 else 0

        rows.append({
            "id": item.id,
            "fav": bool(item.is_favorite),
            "title": (item.title or "N/A")[:60],
            "category": item.category or "N/A",
            "brand": item.brand or "N/A",
            "condition": item.condition or "N/A",
            "qty": item.quantity or 1,
            "bid": bid,
            "est_value": est,
            "max_bid": item.recommended_max_bid or 0,
            "ai_value": ai_value,
            "ai_max_bid": item.ai_recommended_max_bid or 0,
            "ai_risk_level": item.ai_risk_level or "",
            "ai_explanation": item.ai_explanation or "",
            "rating": rating,
            "savings": savings,
            "savings_pct": savings_pct,
            "ai_savings": ai_savings,
            "ai_savings_pct": ai_savings_pct,
            "url": item.url or "",
        })
    return rows


def _filter_rows(rows, category: str = "All", brand: str = "All"):
    filtered = rows
    if category != "All":
        filtered = [r for r in filtered if r["category"] == category]
    if brand != "All":
        filtered = [r for r in filtered if r["brand"] == brand]
    return filtered


def _compute_stats(rows, items):
    total_items = len(rows)
    total_bids = sum(1 for r in rows if r["bid"])
    total_bid_val = sum(r["bid"] for r in rows)
    total_est_val = sum(r["est_value"] for r in rows)
    return {
        "total_items": total_items,
        "total_bids": total_bids,
        "total_bid_value": total_bid_val,
        "total_est_value": total_est_val,
    }


def _get_filter_options(rows):
    categories = sorted(set(r["category"] for r in rows if r["category"] != "N/A"))
    brands = sorted(set(r["brand"] for r in rows if r["brand"] != "N/A"))
    return categories, brands


# ---------------------------------------------------------------------------
# Full page
# ---------------------------------------------------------------------------
@router.get("/goods", response_class=HTMLResponse)
def goods_page(request: Request, repo=Depends(get_repo)):
    auction_names = repo.get_goods_auction_names()
    selected = "All"

    items = repo.list_goods_items(limit=200)
    rows = _build_goods_rows(items)
    categories, brands = _get_filter_options(rows)
    stats = _compute_stats(rows, items)

    fav_rows = [r for r in rows if r["fav"]]

    return _render(request, "goods/page.html", {
        "active_page": "goods",
        "auction_names": auction_names,
        "selected_auction": selected,
        "categories": categories,
        "brands": brands,
        "selected_category": "All",
        "selected_brand": "All",
        "stats": stats,
        "rows": rows,
        "fav_rows": fav_rows,
        "active_tab": "favorites",
    })


# ---------------------------------------------------------------------------
# Tab partials
# ---------------------------------------------------------------------------
@router.get("/goods/tab/{tab_name}", response_class=HTMLResponse)
def goods_tab(
    request: Request,
    tab_name: str,
    auction: str = "All",
    category: str = "All",
    brand: str = "All",
    repo=Depends(get_repo),
):
    items = repo.list_goods_items(
        limit=200,
        auction_name=auction if auction != "All" else None,
    )
    rows = _build_goods_rows(items)
    filtered = _filter_rows(rows, category, brand)
    stats = _compute_stats(filtered, items)

    template_map = {
        "favorites": "goods/_favorites.html",
        "best_deals": "goods/_best_deals.html",
        "all_items": "goods/_all_items.html",
        "analysis": "goods/_analysis.html",
    }
    template_name = template_map.get(tab_name, "goods/_favorites.html")

    ctx = {
        "rows": filtered,
        "stats": stats,
        "active_tab": tab_name,
        "selected_auction": auction,
        "selected_category": category,
        "selected_brand": brand,
    }

    if tab_name == "favorites":
        ctx["fav_rows"] = [r for r in filtered if r["fav"]]
    elif tab_name == "best_deals":
        sorted_rows = sorted(filtered, key=lambda r: r["savings_pct"], reverse=True)
        ctx["deal_rows"] = [r for r in sorted_rows if r["savings"] > 0][:10] or sorted_rows[:5]
    elif tab_name == "analysis":
        ctx["all_rows"] = filtered

    return _render(request, template_name, ctx)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
@router.post("/goods/scrape", response_class=HTMLResponse)
def goods_scrape(
    request: Request,
    url: str = Form(...),
    max_items: int = Form(50),
):
    tm = get_task_manager()
    task_id = tm.create_task_id()

    def _do_scrape():
        from execution.scrape_goods import run as scrape_goods_fn
        return scrape_goods_fn(url=url, max_lots=max_items, progress_callback=tm.make_progress_callback(task_id))

    tm.submit(task_id, _do_scrape)
    return _render(request, "partials/_progress.html", {"task_id": task_id})


@router.post("/goods/refresh-bids", response_class=HTMLResponse)
def goods_refresh_bids(
    request: Request,
    auction: str = Form("All"),
):
    tm = get_task_manager()
    task_id = tm.create_task_id()

    def _do_refresh():
        from execution.refresh_bids import refresh_bids
        return refresh_bids(
            auction_name=auction if auction != "All" else None,
            progress_callback=tm.make_progress_callback(task_id),
        )

    tm.submit(task_id, _do_refresh)
    return _render(request, "partials/_progress.html", {"task_id": task_id})


@router.post("/goods/lookup-prices", response_class=HTMLResponse)
def goods_lookup_prices(
    request: Request,
    auction: str = Form("All"),
):
    tm = get_task_manager()
    task_id = tm.create_task_id()

    def _do_lookup():
        from execution.scrape_retail_prices import lookup_goods_prices
        return lookup_goods_prices(
            auction_name=auction if auction != "All" else None,
            progress_callback=tm.make_progress_callback(task_id),
        )

    tm.submit(task_id, _do_lookup)
    return _render(request, "partials/_progress.html", {"task_id": task_id})


@router.post("/goods/evaluate", response_class=HTMLResponse)
def goods_evaluate(
    request: Request,
    auction: str = Form("All"),
):
    tm = get_task_manager()
    task_id = tm.create_task_id()

    def _do_evaluate():
        from execution.goods_evaluator import evaluate_goods_items
        return evaluate_goods_items(
            auction_name=auction if auction != "All" else None,
            progress_callback=tm.make_progress_callback(task_id),
        )

    tm.submit(task_id, _do_evaluate)
    return _render(request, "partials/_progress.html", {"task_id": task_id})
