# Manage Database

## Goal
Initialize, maintain, and query the auction analyzer database.

## Database Location
`data/auction_analyzer.db` (SQLite)

## Setup
The database initializes automatically on first use. All scripts import `db_models.init_db()` which creates tables if they don't exist.

## Scripts
- **execution/db_models.py** — 7 SQLAlchemy models + `init_db()`, `get_session()`, `get_engine()`
- **execution/db_repository.py** — `Repository` class with all CRUD operations

## Tables
1. **vehicles** — Vehicle listings from auctions (dedup: external_id + source)
2. **auctions** — Auction details per vehicle (bid, status, timing)
3. **price_history** — Historical bid snapshots over time
4. **market_prices** — AutoScout24 comparable prices
5. **goods_items** — Non-vehicle auction items (dedup: external_id + source)
6. **goods_price_cache** — Retail price lookups for goods
7. **image_analyses** — GPT-4 Vision analysis results

## Common Operations
- **Reset DB**: Delete `data/auction_analyzer.db` and re-run any script — tables auto-create
- **Check integrity**: `python -c "from execution.db_repository import Repository; r = Repository(); print(len(r.list_vehicles()), 'vehicles')"`
- **Deduplication**: Handled automatically by `upsert_vehicle()` and `upsert_goods_item()` using external_id + source

## Edge Cases
- SQLite doesn't handle concurrent writes well — only one process should write at a time
- Large image_urls_json fields are stored as TEXT (JSON strings)
- All timestamps are UTC
