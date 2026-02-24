# Scrape Goods Auctions

## Goal
Scrape non-vehicle auction items (electronics, kitchen, furniture, etc.) from Troostwijk or OnlineVeilingmeester and store in database.

## Script
`execution/scrape_goods.py`

## Inputs
- `--url AUCTION_URL` — Full URL of the goods auction (required)
- `--max-lots N` — Maximum number of lots to scrape (default: 50)

## How to Run
```bash
# Troostwijk goods auction
python -m execution.scrape_goods --url "https://www.troostwijkauctions.com/a/bezorgveiling-diverse-retourgoederen-A1-38027" --max-lots 30

# OnlineVeilingmeester
python -m execution.scrape_goods --url "https://www.onlineveilingmeester.nl/nl/veilingen/keuken-veiling-456"

# Via CLI
python main.py scrape-goods --url "https://www.troostwijkauctions.com/a/..." --lots 30
```

## What It Does
1. Detects source (Troostwijk vs OnlineVeilingmeester) from URL
2. Navigates to auction page, dismisses cookie consent
3. Collects lot URLs:
   - Troostwijk: `a[href*="/l/"]` links with pagination support
   - OVM: `a[href*="/kavels/"]`, `a[href*="/lots/"]` links
4. Scrapes each lot detail page:
   - Troostwijk: JS evaluate extracting from `dt/dd` pairs, `h5` sections, bid text
   - OVM: CSS selectors for lot elements
5. Extracts: title, description, brand, category, condition, quantity, specs, images, bid info, end time, location
6. Auto-detects category from keywords in title/description
7. Stores items in `goods_items` table with dedup by `external_id + source`

## Output
- Items stored in `goods_items` table
- JSON summary printed to stdout

## Tech
- Playwright (JavaScript-rendered sites)
- Rate limited: 2.5s between requests
- Progress callback support for dashboard integration
- Separate extraction logic per source (Troostwijk vs OVM)

## Troostwijk Site Structure (as of Feb 2026)
- Auction URLs: `/a/{slug}-A1-{id}`
- Lot URLs: `/l/{slug}-A1-{auction_id}-{lot_id}`
- Lot specs: `dt/dd` pairs (Merk, Hoeveelheid, Locatie, Kavel, etc.)
- Description: Under `h5` "Beschrijving" heading
- Notes: Under `h5` "Aanvullende details" heading
- Bid info: `dt` containing "Huidig bod" text + bid count in parentheses → `dd` sibling has "€ XX,XX" price
- Bid count: also available as "X Biedingen" text in bid history section
- End time: "Sluit in:" text node followed by `<p>` sibling with "DD month YYYY HH:MM"
- Images: `img[src*="media.tbauctions"]`
- Cookie consent: `#onetrust-accept-btn-handler`
- Pagination: nav links with page numbers

## Category Detection
Expanded keyword mapping (Dutch + English):
- Electronics: laptop, chromebook, monitor, tablet, iphone, samsung, tv, camera, printer, gaming
- Kitchen: keuken, oven, koelkast, vaatwas, blender
- Furniture: meubel, stoel, tafel, kast, bed
- Tools: gereedschap, boor
- Machinery: machine
- Sports: fiets, bike, sport
- Garden: tuin, garden
- Clothing: kleding, schoenen, fashion
- Toys: speelgoed, lego

## Condition Detection
From description/notes keywords:
- "nieuw" / "new" → New
- "retour" / "return" → Return
- "gebruikt" / "used" → Used
- "beschadigd" / "damaged" → Damaged

## Edge Cases
- Brand: extracted from `dt/dd` "Merk" spec on Troostwijk, or first capitalized word in title
- Quantity: from "Hoeveelheid" spec or patterns like "3 stuks" / "2x"
- Lot ID: Troostwijk uses `A1-38027-12785` format, OVM uses numeric IDs
- Cookie consent must be dismissed before interaction
- Troostwijk shows ~48 lots per page; pagination is handled automatically
- Retail value estimation is not yet implemented (needs price comparison API)
