# Run Dashboard

## Goal
Launch the Streamlit web dashboard for viewing and analyzing auctions.

## Script
`execution/dashboard_app.py`

## How to Run
```bash
streamlit run execution/dashboard_app.py
# or via CLI:
python main.py dashboard
```

## Pages

### 1. Auto Auctions
- Select/delete auctions
- Summary stats (vehicles, bids, market value)
- "Lookup Market Prices" button → fetches AutoScout24 data for all vehicles
- Filter by make/fuel type
- Table: make, model, year, mileage, bid, market price, max bid, deal score
- Per-row: view details button, update bid popover

### 2. Goods Auctions
- Add auction by URL (Dutch/English formats)
- "Lookup Real Prices" button (placeholder)
- Filter by category/brand
- Table with items and deal analysis

### 3. Vehicle Detail
- Full specifications
- Auction status with time remaining
- Price prediction with reasoning
- Deal score with factor breakdown
- Bid strategy with timing advice
- Price history chart (Plotly line)
- Image analysis results
- Vehicle image gallery

## Prerequisites
- Database must exist (auto-created on first use)
- For meaningful data: run scrapers first
- For market comparison: run AutoScout24 scraper
- For image analysis: run image analyzer CLI command

## Dependencies
- streamlit, plotly, pandas (in requirements.txt)
- All execution/ scripts (imported for analysis)
