import json
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import and_

from execution.db_models import (
    get_session, init_db,
    Vehicle, Auction, PriceHistory, MarketPrice,
    GoodsItem, GoodsPriceCache, ImageAnalysis,
)


class Repository:
    """Data access layer for all database operations."""

    def __init__(self):
        init_db()
        self.session = get_session()

    def close(self):
        self.session.close()

    # ------------------------------------------------------------------
    # Vehicle
    # ------------------------------------------------------------------
    def upsert_vehicle(self, external_id: str, source: str, **kwargs) -> Vehicle:
        """Insert or update a vehicle, deduplicating on external_id + source."""
        vehicle = self.session.query(Vehicle).filter(
            and_(Vehicle.external_id == external_id, Vehicle.source == source)
        ).first()

        if vehicle:
            for key, value in kwargs.items():
                if value is not None:
                    setattr(vehicle, key, value)
            vehicle.updated_at = datetime.now(timezone.utc)
        else:
            vehicle = Vehicle(external_id=external_id, source=source, **kwargs)
            self.session.add(vehicle)

        self.session.commit()
        self.session.refresh(vehicle)
        return vehicle

    def get_vehicle(self, vehicle_id: int) -> Optional[Vehicle]:
        return self.session.query(Vehicle).get(vehicle_id)

    def list_vehicles(self, limit: int = 100, offset: int = 0,
                      make: Optional[str] = None, fuel_type: Optional[str] = None) -> list[Vehicle]:
        q = self.session.query(Vehicle)
        if make:
            q = q.filter(Vehicle.make.ilike(f"%{make}%"))
        if fuel_type:
            q = q.filter(Vehicle.fuel_type.ilike(f"%{fuel_type}%"))
        return q.order_by(Vehicle.created_at.desc()).offset(offset).limit(limit).all()

    def toggle_vehicle_favorite(self, vehicle_id: int) -> bool:
        vehicle = self.session.query(Vehicle).get(vehicle_id)
        if vehicle:
            vehicle.is_favorite = 0 if vehicle.is_favorite else 1
            self.session.commit()
            return bool(vehicle.is_favorite)
        return False

    def delete_vehicle(self, vehicle_id: int) -> bool:
        vehicle = self.session.query(Vehicle).get(vehicle_id)
        if vehicle:
            self.session.delete(vehicle)
            self.session.commit()
            return True
        return False

    # ------------------------------------------------------------------
    # Auction
    # ------------------------------------------------------------------
    def upsert_auction(self, vehicle_id: int, auction_name: str, **kwargs) -> Auction:
        auction = self.session.query(Auction).filter(
            and_(Auction.vehicle_id == vehicle_id, Auction.auction_name == auction_name)
        ).first()

        if auction:
            for key, value in kwargs.items():
                if value is not None:
                    setattr(auction, key, value)
        else:
            auction = Auction(vehicle_id=vehicle_id, auction_name=auction_name, **kwargs)
            self.session.add(auction)

        self.session.commit()
        self.session.refresh(auction)
        return auction

    def get_auctions_for_vehicle(self, vehicle_id: int) -> list[Auction]:
        return self.session.query(Auction).filter(Auction.vehicle_id == vehicle_id).all()

    def get_active_auctions(self) -> list[Auction]:
        return self.session.query(Auction).filter(Auction.status == "active").all()

    def get_auction_names(self) -> list[str]:
        """Return distinct auction names."""
        rows = self.session.query(Auction.auction_name).distinct().all()
        return [r[0] for r in rows if r[0]]

    def get_vehicles_by_auction(self, auction_name: str) -> list[Vehicle]:
        """Get all vehicles in a specific auction."""
        vehicle_ids = self.session.query(Auction.vehicle_id).filter(
            Auction.auction_name == auction_name
        ).all()
        ids = [r[0] for r in vehicle_ids]
        if not ids:
            return []
        return self.session.query(Vehicle).filter(Vehicle.id.in_(ids)).all()

    def delete_auction(self, auction_name: str) -> int:
        """Delete all auctions (and their vehicles) for a given auction name."""
        auctions = self.session.query(Auction).filter(Auction.auction_name == auction_name).all()
        vehicle_ids = {a.vehicle_id for a in auctions}
        count = 0
        for vid in vehicle_ids:
            if self.delete_vehicle(vid):
                count += 1
        return count

    def update_bid(self, auction_id: int, new_bid: float, new_bid_count: Optional[int] = None) -> Optional[Auction]:
        auction = self.session.query(Auction).get(auction_id)
        if auction:
            auction.current_bid = new_bid
            if new_bid_count is not None:
                auction.bid_count = new_bid_count
            auction.scraped_at = datetime.now(timezone.utc)
            self.session.commit()
            self.session.refresh(auction)
        return auction

    # ------------------------------------------------------------------
    # Price History
    # ------------------------------------------------------------------
    def add_price_history(self, vehicle_id: int, bid_amount: float, bid_count: Optional[int] = None) -> PriceHistory:
        ph = PriceHistory(vehicle_id=vehicle_id, bid_amount=bid_amount, bid_count=bid_count)
        self.session.add(ph)
        self.session.commit()
        self.session.refresh(ph)
        return ph

    def get_price_history(self, vehicle_id: int) -> list[PriceHistory]:
        return (
            self.session.query(PriceHistory)
            .filter(PriceHistory.vehicle_id == vehicle_id)
            .order_by(PriceHistory.recorded_at)
            .all()
        )

    # ------------------------------------------------------------------
    # Market Price
    # ------------------------------------------------------------------
    def upsert_market_price(self, make: str, model: str, asking_price: float, **kwargs) -> MarketPrice:
        mp = MarketPrice(make=make, model=model, asking_price=asking_price, **kwargs)
        self.session.add(mp)
        self.session.commit()
        self.session.refresh(mp)
        return mp

    def clear_market_prices(self, make: str, model: str) -> int:
        """Delete all market prices for a make/model before re-scraping."""
        deleted = self.session.query(MarketPrice).filter(
            and_(
                MarketPrice.make.ilike(f"%{make}%"),
                MarketPrice.model.ilike(f"%{model}%"),
            )
        ).delete(synchronize_session="fetch")
        self.session.commit()
        return deleted

    def get_market_prices(self, make: str, model: str,
                          year: Optional[int] = None, year_range: int = 1) -> list[MarketPrice]:
        q = self.session.query(MarketPrice).filter(
            and_(
                MarketPrice.make.ilike(f"%{make}%"),
                MarketPrice.model.ilike(f"%{model}%"),
            )
        )
        if year:
            q = q.filter(
                and_(
                    MarketPrice.year >= year - year_range,
                    MarketPrice.year <= year + year_range,
                )
            )
        return q.all()

    # ------------------------------------------------------------------
    # Goods Item
    # ------------------------------------------------------------------
    def upsert_goods_item(self, external_id: str, source: str, **kwargs) -> GoodsItem:
        item = self.session.query(GoodsItem).filter(
            and_(GoodsItem.external_id == external_id, GoodsItem.source == source)
        ).first()

        if item:
            for key, value in kwargs.items():
                if value is not None:
                    setattr(item, key, value)
            item.updated_at = datetime.now(timezone.utc)
        else:
            item = GoodsItem(external_id=external_id, source=source, **kwargs)
            self.session.add(item)

        self.session.commit()
        self.session.refresh(item)
        return item

    def list_goods_items(self, limit: int = 100, offset: int = 0,
                         category: Optional[str] = None, brand: Optional[str] = None,
                         auction_name: Optional[str] = None) -> list[GoodsItem]:
        q = self.session.query(GoodsItem)
        if category:
            q = q.filter(GoodsItem.category.ilike(f"%{category}%"))
        if brand:
            q = q.filter(GoodsItem.brand.ilike(f"%{brand}%"))
        if auction_name:
            q = q.filter(GoodsItem.auction_name == auction_name)
        return q.order_by(GoodsItem.created_at.desc()).offset(offset).limit(limit).all()

    def toggle_goods_favorite(self, item_id: int) -> bool:
        item = self.session.query(GoodsItem).get(item_id)
        if item:
            item.is_favorite = 0 if item.is_favorite else 1
            self.session.commit()
            return bool(item.is_favorite)
        return False

    def delete_goods_item(self, item_id: int) -> bool:
        item = self.session.query(GoodsItem).get(item_id)
        if item:
            self.session.delete(item)
            self.session.commit()
            return True
        return False

    def get_goods_auction_names(self) -> list[str]:
        rows = self.session.query(GoodsItem.auction_name).distinct().all()
        return [r[0] for r in rows if r[0]]

    def delete_goods_auction(self, auction_name: str) -> int:
        items = self.session.query(GoodsItem).filter(GoodsItem.auction_name == auction_name).all()
        count = len(items)
        for item in items:
            self.session.delete(item)
        self.session.commit()
        return count

    def reformat_goods_auction_names(self, formatter) -> int:
        """Update all goods items' auction_name using a formatter function.

        Only reformats names that don't already contain ' | ' (the formatted separator).

        Args:
            formatter: callable(raw_name, location, url) -> formatted_name
        Returns:
            Number of items updated.
        """
        items = self.session.query(GoodsItem).all()
        updated = 0
        for item in items:
            current = item.auction_name or ""
            # Skip already-formatted names (contain " | ")
            if " | " in current:
                continue
            new_name = formatter(current, item.location, item.url or "")
            if new_name != current:
                item.auction_name = new_name
                updated += 1
        if updated:
            self.session.commit()
        return updated

    # ------------------------------------------------------------------
    # Goods AI Evaluation
    # ------------------------------------------------------------------
    def update_goods_ai_eval(self, item_id: int, **kwargs):
        """Update AI evaluation fields on a goods item."""
        item = self.session.query(GoodsItem).get(item_id)
        if item:
            for k, v in kwargs.items():
                setattr(item, k, v)
            self.session.commit()

    # ------------------------------------------------------------------
    # Goods Price Cache
    # ------------------------------------------------------------------
    def cache_goods_price(self, product_name: str, retail_price: float, **kwargs) -> GoodsPriceCache:
        entry = GoodsPriceCache(product_name=product_name, retail_price=retail_price, **kwargs)
        self.session.add(entry)
        self.session.commit()
        self.session.refresh(entry)
        return entry

    def get_cached_price(self, product_name: str) -> Optional[GoodsPriceCache]:
        return (
            self.session.query(GoodsPriceCache)
            .filter(GoodsPriceCache.product_name.ilike(f"%{product_name}%"))
            .order_by(GoodsPriceCache.looked_up_at.desc())
            .first()
        )

    # ------------------------------------------------------------------
    # Image Analysis
    # ------------------------------------------------------------------
    def save_image_analysis(self, vehicle_id: int, image_url: str, **kwargs) -> ImageAnalysis:
        analysis = ImageAnalysis(vehicle_id=vehicle_id, image_url=image_url, **kwargs)
        self.session.add(analysis)
        self.session.commit()
        self.session.refresh(analysis)
        return analysis

    def get_image_analyses(self, vehicle_id: int) -> list[ImageAnalysis]:
        return (
            self.session.query(ImageAnalysis)
            .filter(ImageAnalysis.vehicle_id == vehicle_id)
            .order_by(ImageAnalysis.analyzed_at)
            .all()
        )
