"""API routes: favorite toggle, delete auction, SSE task streaming."""
from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse

from execution.webapp.deps import get_repo, get_task_manager

router = APIRouter(prefix="/api", tags=["api"])


# ---------------------------------------------------------------------------
# Auto: Favorites
# ---------------------------------------------------------------------------
@router.post("/auto/favorite/{vehicle_id}")
def toggle_auto_favorite(vehicle_id: int, repo=Depends(get_repo)):
    is_fav = repo.toggle_vehicle_favorite(vehicle_id)
    checked = "checked" if is_fav else ""
    return Response(
        content=f'''<input type="checkbox" {checked}
                     class="w-4 h-4 accent-amber-500 cursor-pointer"
                     hx-post="/api/auto/favorite/{vehicle_id}"
                     hx-swap="outerHTML" />''',
        media_type="text/html",
    )


# ---------------------------------------------------------------------------
# Auto: Delete auction
# ---------------------------------------------------------------------------
@router.delete("/auto/auction/{auction_name:path}")
def delete_auto_auction(auction_name: str, repo=Depends(get_repo)):
    repo.delete_auction(auction_name)
    return Response(status_code=200, headers={"HX-Redirect": "/auto"})


# ---------------------------------------------------------------------------
# Goods: Favorites
# ---------------------------------------------------------------------------
@router.post("/goods/favorite/{item_id}")
def toggle_goods_favorite(item_id: int, repo=Depends(get_repo)):
    is_fav = repo.toggle_goods_favorite(item_id)
    checked = "checked" if is_fav else ""
    return Response(
        content=f'''<input type="checkbox" {checked}
                     class="w-4 h-4 accent-amber-500 cursor-pointer"
                     hx-post="/api/goods/favorite/{item_id}"
                     hx-swap="outerHTML" />''',
        media_type="text/html",
    )


# ---------------------------------------------------------------------------
# Goods: Delete auction
# ---------------------------------------------------------------------------
@router.delete("/goods/auction/{auction_name:path}")
def delete_goods_auction(auction_name: str, repo=Depends(get_repo)):
    repo.delete_goods_auction(auction_name)
    return Response(status_code=200, headers={"HX-Redirect": "/goods"})


# ---------------------------------------------------------------------------
# SSE Task Stream
# ---------------------------------------------------------------------------
@router.get("/tasks/{task_id}/stream")
async def task_stream(task_id: str):
    tm = get_task_manager()
    return StreamingResponse(
        tm.stream(task_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
