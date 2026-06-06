# Bikes Tab — Design Spec
**Date:** 2026-06-06  
**Status:** Approved

## Overview

Add a dedicated "Bikes" tab to the Auction Analyzer dashboard for analyzing bicycle auctions from OnlineVeilingmeester. The feature follows the same 3-layer architecture (directives → orchestration → execution scripts) as the existing Auto and Goods tabs.

**Reference auction:** https://onlineveilingmeester.nl/en/auctions/9086/lots  
**Auction name:** "Tweewielers" (99 lots — racefietsen, fatbikes, mountainbikes, wielrenfietsen)  
**Data source:** Same OVM REST API used by the vehicle scraper — no Playwright needed.

---

## Data Model

Three new tables added to `execution/db_models.py`.

### `bikes` table

Core bike record with inline auction data (same pattern as `goods_items`).

| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| external_id | String | OVM lot number |
| source | String | "onlineveilingmeester" |
| url | String | Lot page URL |
| bike_type | String | Racefiets, Fatbike, Mountainbike, Wielrenfiets, etc. (from `kavelData.product`) |
| brand | String | e.g. Cannondale, Cube (from `kavelData.merk`) |
| model | String | e.g. SystemSix, C80 (from `kavelData.productType`) |
| frame_size | String | e.g. "54 cm" (from `kavelData.maatvoering`) |
| color | String | from `kavelData.kleur` |
| condition | String | Gebruikt, Nieuw, etc. (from `kavelData.conditie`) |
| components | Text | Parsed plain text from `kavelData.specificaties` HTML |
| notes | Text | from `kavelData.bijzonderheden` HTML |
| image_urls_json | Text | JSON array of image URLs |
| auction_name | String | from `veiling.naam` |
| current_bid | Float | from `hoogsteBod` |
| bid_count | Integer | from `aantalBiedingen` |
| end_time | DateTime | from `sluitingsDatumISO` |
| is_favorite | Integer | 0/1 |
| ai_estimated_value | Float | |
| ai_recommended_max_bid | Float | |
| ai_risk_level | String | low / medium / high |
| ai_explanation | Text | |
| ai_evaluated_at | DateTime | |
| ai_eval_hash | String | hash of inputs to detect stale evals |
| created_at | DateTime | |
| updated_at | DateTime | |

Unique constraint: `(external_id, source)`.

### `bike_market_prices` table

Comparable used bike listings from Marktplaats.nl.

| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| brand | String | |
| model | String | |
| bike_type | String | |
| asking_price | Float | EUR |
| source | String | "marktplaats" |
| source_url | String | |
| scraped_at | DateTime | |

### `bike_image_analyses` table

Same structure as `image_analyses` but with `bike_id` FK.

| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| bike_id | Integer FK → bikes.id | |
| image_url | String | |
| image_type | String | exterior / detail / damage |
| condition_score | Float | 1–10 |
| overall_condition | String | excellent / good / fair / poor |
| confidence | Float | |
| damage_detected | Text | JSON |
| assessment_details | Text | JSON |
| raw_response | Text | |
| model_used | String | |
| analyzed_at | DateTime | |

---

## Execution Scripts

### New scripts

**`execution/scrape_bikes.py`**  
Scrapes a bike auction from OVM via REST API. Same API endpoints as `scrape_onlineveilingmeester.py` but maps to bike fields instead of vehicle fields. Accepts `--url` argument. Stores results in `bikes` table. Progress callback supported for dashboard integration.

Key API endpoints used:
- `GET /rest/en/veilingen/{id}/kavelVolgNummers` — get lot numbers
- `GET /rest/nl/v2/veilingen/{id}/kavels/{lot}` — get lot detail (kavelData + bid info + images)

**`execution/scrape_marktplaats.py`**  
Fetches comparable used bike listings from Marktplaats.nl by brand + model. Uses plain HTTP (no Playwright). Stores prices in `bike_market_prices`, clearing old prices for the same brand/model before inserting new ones. Accepts `--brand`, `--model`, `--bike-type`.

**`execution/bike_evaluator.py`**  
AI evaluation using Claude API. Reads bike data + market prices from DB, calls Claude to estimate market value, recommend max bid, and assess risk level. Caches evaluation using `ai_eval_hash` to avoid redundant API calls. Same pattern as `goods_evaluator.py`. Accepts `--bike-id`.

**`execution/refresh_bike_bids.py`**  
Re-fetches `current_bid` and `bid_count` for all active (non-ended) bikes via OVM API. Same pattern as `refresh_bids.py`.

### Modified scripts

**`execution/image_analyzer.py`**  
Add `--bike-id` param. When `--bike-id` is provided, stores results in `bike_image_analyses` table instead of `image_analyses`. Existing `--vehicle-id` behavior unchanged.

---

## Webapp Layer

### New files

**`execution/webapp/routes/bikes.py`**  
FastAPI router handling all `/bikes` endpoints. Manages task lifecycle (scrape, market lookup, AI eval, image analysis, refresh bids) using the existing `TaskManager`. Follows the same HTMX partial-reload pattern as `routes/auto.py` and `routes/goods.py`.

**`execution/webapp/templates/bikes/page.html`**  
Main tab page. Three sub-tabs: All Bikes, Best Deals, Favorites.

**`execution/webapp/templates/bikes/_controls.html`**  
URL input + action buttons:
- Scrape Auction (triggers `scrape_bikes.py`)
- Lookup Market Prices (triggers `scrape_marktplaats.py` for each unique brand/model)
- Run AI Evaluation (triggers `bike_evaluator.py` for all unevaluated bikes)
- Analyze Images (triggers `image_analyzer.py --bike-id` for all bikes without image analysis)
- Refresh Bids (triggers `refresh_bike_bids.py`)

**`execution/webapp/templates/bikes/_all_bikes.html`**  
Sortable table of all scraped bikes. Columns: image thumbnail, bike type, brand/model, frame size, condition, current bid, market price (median from Marktplaats), AI value estimate, deal score, end time, favorites toggle.

**`execution/webapp/templates/bikes/_best_deals.html`**  
Top bikes ranked by deal score (bid vs. AI estimated value ratio).

**`execution/webapp/templates/bikes/_bike_card.html`**  
Expanded bike detail card: image gallery, full specs, component list, AI evaluation summary, image analysis condition score and damage notes.

**`execution/webapp/templates/bikes/_favorites.html`**  
Favorited bikes.

### Modified files

**`execution/webapp/app.py`**  
Register the new bikes router.

**`execution/webapp/templates/base.html`**  
Add "Bikes" tab to navigation.

---

## Directives

**`directives/scrape_bikes.md`**  
How to scrape a bike auction from OVM. Inputs: `--url`. Covers bike-specific field mapping, the OVM REST API endpoints, and edge cases (missing brand, missing frame size).

**`directives/lookup_bike_prices.md`**  
How to fetch market prices from Marktplaats for a given bike. Covers URL pattern, data extraction, dedup strategy, and fallback when no results found.

**`directives/evaluate_bikes.md`**  
How to run the full evaluation pipeline: (1) lookup market prices on Marktplaats, (2) run AI evaluation, (3) run image analysis. Covers deal scoring logic and when to skip steps.

---

## OVM API — Bike Field Mapping

From `kavelData` object:

| OVM field | Bike column | Example |
|---|---|---|
| `naam` | title / fallback | "Racefiets, Cannondale, SystemSix, Acid Red" |
| `product` | bike_type | "Racefiets" |
| `merk` | brand | "Cannondale" |
| `productType` | model | "SystemSix" |
| `kleur` | color | "Acid Red" |
| `maatvoering` | frame_size | "Framemaat 54 cm." |
| `conditie` | condition | "Gebruikt" |
| `specificaties` | components (stripped HTML) | Shimano Ultegra Di2... |
| `bijzonderheden` | notes (stripped HTML) | "Het frame is gewrapt..." |

From parent object:

| OVM field | Bike column |
|---|---|
| `hoogsteBod` | current_bid |
| `aantalBiedingen` | bid_count |
| `sluitingsDatumISO` | end_time |
| `imageList` | image_urls_json |
| `veiling.naam` | auction_name |

---

## Key Constraints & Edge Cases

- OVM lot numbers are sequential strings ("1"–"99"); use as `external_id`
- Some lots have no brand (`merk` is null) — fallback to first capitalized word in `naam`
- Some lots have no frame size (`maatvoering` is null) — store as null, don't block scrape
- `specificaties` and `bijzonderheden` are HTML — strip tags before storing in `components`/`notes`
- Rate limit: 2.5s between OVM API requests (reuse `RateLimiter`)
- Marktplaats search may return 0 results for obscure models — fall back to brand-only search
- Image analysis is optional and can be run independently after scraping
