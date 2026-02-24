import json
from datetime import datetime, timezone
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime, Text, ForeignKey, UniqueConstraint, text
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

from execution.config import DATABASE_URL

Base = declarative_base()

# ---------------------------------------------------------------------------
# Singleton engine / session
# ---------------------------------------------------------------------------
_engine = None
_SessionFactory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(DATABASE_URL, echo=False)
    return _engine


def get_session() -> Session:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine())
    return _SessionFactory()


def init_db():
    """Create all tables if they don't exist, and run lightweight migrations."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    # Add is_favorite column to existing tables if missing
    with engine.connect() as conn:
        for table in ("vehicles", "goods_items"):
            try:
                conn.execute(text(f"SELECT is_favorite FROM {table} LIMIT 1"))
            except Exception:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN is_favorite INTEGER DEFAULT 0"))
                conn.commit()
        # Add AI evaluation columns to goods_items if missing
        for col in ("ai_estimated_value", "ai_recommended_max_bid", "ai_risk_level",
                     "ai_explanation", "ai_evaluated_at", "ai_eval_hash"):
            try:
                conn.execute(text(f"SELECT {col} FROM goods_items LIMIT 1"))
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                col_type = "TEXT" if col in ("ai_risk_level", "ai_explanation", "ai_eval_hash") else "REAL" if col != "ai_evaluated_at" else "TIMESTAMP"
                conn.execute(text(f"ALTER TABLE goods_items ADD COLUMN {col} {col_type}"))
                conn.commit()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Vehicle(Base):
    __tablename__ = "vehicles"
    __table_args__ = (UniqueConstraint("external_id", "source", name="uq_vehicle_ext"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    external_id = Column(String, nullable=False)
    source = Column(String, nullable=False)  # troostwijk / onlineveilingmeester
    url = Column(String)

    make = Column(String)
    model = Column(String)
    year = Column(Integer)
    mileage_km = Column(Integer)
    fuel_type = Column(String)
    power_hp = Column(Integer)
    transmission = Column(String)
    body_type = Column(String)
    color = Column(String)
    location = Column(String)
    condition_notes = Column(Text)
    mot_expiry = Column(String)

    image_urls_json = Column(Text)  # JSON array of URLs
    is_favorite = Column(Integer, default=0)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    auctions = relationship("Auction", back_populates="vehicle", cascade="all, delete-orphan")
    price_history = relationship("PriceHistory", back_populates="vehicle", cascade="all, delete-orphan")
    image_analyses = relationship("ImageAnalysis", back_populates="vehicle", cascade="all, delete-orphan")

    @property
    def image_urls(self) -> list[str]:
        if self.image_urls_json:
            return json.loads(self.image_urls_json)
        return []

    @image_urls.setter
    def image_urls(self, urls: list[str]):
        self.image_urls_json = json.dumps(urls)


class Auction(Base):
    __tablename__ = "auctions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    auction_name = Column(String)

    current_bid = Column(Float)
    bid_count = Column(Integer, default=0)
    end_time = Column(DateTime)
    status = Column(String, default="active")  # active / ended
    final_price = Column(Float)
    scraped_at = Column(DateTime, default=_utcnow)

    vehicle = relationship("Vehicle", back_populates="auctions")


class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    bid_amount = Column(Float, nullable=False)
    bid_count = Column(Integer)
    recorded_at = Column(DateTime, default=_utcnow)

    vehicle = relationship("Vehicle", back_populates="price_history")


class MarketPrice(Base):
    __tablename__ = "market_prices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer)
    mileage_km = Column(Integer)
    fuel_type = Column(String)

    asking_price = Column(Float, nullable=False)
    source = Column(String, default="autoscout24")
    source_url = Column(String)
    scraped_at = Column(DateTime, default=_utcnow)


class GoodsItem(Base):
    __tablename__ = "goods_items"
    __table_args__ = (UniqueConstraint("external_id", "source", name="uq_goods_ext"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    external_id = Column(String, nullable=False)
    source = Column(String, nullable=False)
    auction_id = Column(String)
    auction_name = Column(String)
    url = Column(String)

    title = Column(String)
    description = Column(Text)
    category = Column(String)
    brand = Column(String)
    condition = Column(String)
    quantity = Column(Integer, default=1)

    current_bid = Column(Float)
    bid_count = Column(Integer, default=0)
    estimated_value = Column(Float)
    recommended_max_bid = Column(Float)

    end_time = Column(DateTime)
    location = Column(String)
    image_url = Column(String)
    image_urls_json = Column(Text)
    specs_json = Column(Text)
    is_favorite = Column(Integer, default=0)

    # AI evaluation fields
    ai_estimated_value = Column(Float)
    ai_recommended_max_bid = Column(Float)
    ai_risk_level = Column(String)
    ai_explanation = Column(Text)
    ai_evaluated_at = Column(DateTime)
    ai_eval_hash = Column(String)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    @property
    def image_urls(self) -> list[str]:
        if self.image_urls_json:
            return json.loads(self.image_urls_json)
        return []

    @property
    def specs(self) -> dict:
        if self.specs_json:
            return json.loads(self.specs_json)
        return {}


class GoodsPriceCache(Base):
    __tablename__ = "goods_price_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_name = Column(String, nullable=False)
    search_query = Column(String)
    retail_price = Column(Float)
    source = Column(String)
    source_url = Column(String)
    confidence = Column(Float)
    looked_up_at = Column(DateTime, default=_utcnow)


class ImageAnalysis(Base):
    __tablename__ = "image_analyses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    image_url = Column(String, nullable=False)
    image_type = Column(String)  # exterior / interior / engine / damage

    condition_score = Column(Float)  # 1-10
    overall_condition = Column(String)  # excellent / good / fair / poor
    confidence = Column(Float)

    damage_detected = Column(Text)  # JSON
    assessment_details = Column(Text)  # JSON

    raw_response = Column(Text)
    model_used = Column(String)
    analyzed_at = Column(DateTime, default=_utcnow)

    vehicle = relationship("Vehicle", back_populates="image_analyses")

    @property
    def damages(self) -> list:
        if self.damage_detected:
            return json.loads(self.damage_detected)
        return []

    @property
    def assessment(self) -> dict:
        if self.assessment_details:
            return json.loads(self.assessment_details)
        return {}
