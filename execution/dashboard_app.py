"""
Auction Analyzer Dashboard
Streamlit web interface with 3 pages: Auto Auctions, Goods Auctions, Vehicle Detail.

Usage:
    streamlit run execution/dashboard_app.py
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import asdict

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from execution.db_repository import Repository
from execution.price_predictor import predict
from execution.deal_scorer import score
from execution.bid_strategist import get_strategy

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Auction Analyzer",
    page_icon="🔨",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "selected_vehicle_id" not in st.session_state:
    st.session_state.selected_vehicle_id = None


def get_repo() -> Repository:
    """Get a repository instance."""
    return Repository()


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------
st.sidebar.title("Auction Analyzer")
page = st.sidebar.radio("Navigation", ["Auto Auctions", "Goods Auctions", "Vehicle Detail"])


# ===================================================================
# PAGE 1: Auto Auctions
# ===================================================================
def page_auto_auctions():
    st.title("Auto Auctions")
    repo = get_repo()

    # Add auction section
    with st.expander("Add Auction", expanded=not bool(repo.get_auction_names())):
        add_tab1, add_tab2 = st.tabs(["Scrape by URL", "Troostwijk Quick Scrape"])

        with add_tab1:
            url_col1, url_col2, url_col3 = st.columns([3, 1, 1])
            with url_col1:
                auction_url = st.text_input(
                    "Auction URL",
                    placeholder="https://www.troostwijkauctions.com/a/... or https://www.onlineveilingmeester.nl/...",
                    key="auto_auction_url",
                )
            with url_col2:
                max_vehicles = st.number_input("Max Vehicles", min_value=1, max_value=200, value=50, key="url_max_vehicles")
            with url_col3:
                st.write("")  # spacing
                scrape_url_btn = st.button("Scrape URL", type="primary", key="scrape_url_btn")

            if scrape_url_btn and auction_url:
                progress_bar = st.progress(0, text="Starting scraper...")
                status_text = st.empty()

                def _url_progress(current, total, message):
                    if total > 0:
                        pct = min(int((current / total) * 90) + 10, 99)
                    else:
                        pct = 10
                    progress_bar.progress(pct, text=message)

                try:
                    progress_bar.progress(5, text="Launching browser...")
                    if "onlineveilingmeester" in auction_url.lower():
                        from execution.scrape_onlineveilingmeester import run as scrape_ovm
                        progress_bar.progress(10, text="Scraping OnlineVeilingmeester...")
                        results = scrape_ovm(url=auction_url)
                    elif "troostwijk" in auction_url.lower():
                        from execution.scrape_troostwijk import run as scrape_tw
                        progress_bar.progress(10, text="Collecting lot URLs...")
                        results = scrape_tw(url=auction_url, max_lots=int(max_vehicles), progress_callback=_url_progress)
                    else:
                        st.error("Unsupported URL. Use troostwijkauctions.com or onlineveilingmeester.nl")
                        results = []
                    progress_bar.progress(100, text="Done!")
                    st.success(f"Scraped {len(results)} vehicles!")
                    st.rerun()
                except Exception as e:
                    progress_bar.empty()
                    st.error(f"Scraping failed: {e}")

        with add_tab2:
            tw_col1, tw_col2 = st.columns(2)
            with tw_col1:
                tw_pages = st.number_input("Pages", min_value=1, max_value=10, value=2, key="tw_pages")
            with tw_col2:
                tw_lots = st.number_input("Max Lots", min_value=1, max_value=100, value=20, key="tw_lots")

            if st.button("Scrape Troostwijk", type="primary", key="scrape_tw_btn"):
                progress_bar = st.progress(0, text="Starting Troostwijk scraper...")

                def _tw_progress(current, total, message):
                    if total > 0:
                        pct = min(int((current / total) * 90) + 10, 99)
                    else:
                        pct = 10
                    progress_bar.progress(pct, text=message)

                try:
                    from execution.scrape_troostwijk import run as scrape_tw
                    progress_bar.progress(5, text="Launching browser and collecting lots...")
                    results = scrape_tw(pages=int(tw_pages), max_lots=int(tw_lots), progress_callback=_tw_progress)
                    progress_bar.progress(100, text="Done!")
                    st.success(f"Scraped {len(results)} vehicles!")
                    st.rerun()
                except Exception as e:
                    progress_bar.empty()
                    st.error(f"Scraping failed: {e}")

    # Auction selector
    auction_names = repo.get_auction_names()

    if not auction_names:
        repo.close()
        return

    col1, col2 = st.columns([3, 1])
    with col1:
        selected_auction = st.selectbox("Select Auction", ["All"] + auction_names)
    with col2:
        if selected_auction != "All":
            if st.button("Delete Auction", type="secondary"):
                count = repo.delete_auction(selected_auction)
                st.success(f"Deleted {count} vehicles from '{selected_auction}'")
                st.rerun()

    # Get vehicles
    if selected_auction == "All":
        vehicles = repo.list_vehicles(limit=200)
    else:
        vehicles = repo.get_vehicles_by_auction(selected_auction)

    if not vehicles:
        st.warning("No vehicles in this auction.")
        repo.close()
        return

    # Summary stats
    auctions_data = []
    for v in vehicles:
        v_auctions = repo.get_auctions_for_vehicle(v.id)
        auction = v_auctions[0] if v_auctions else None
        auctions_data.append((v, auction))

    total_vehicles = len(vehicles)
    total_bids = sum((a.bid_count or 0) for _, a in auctions_data if a)
    total_bid_value = sum((a.current_bid or 0) for _, a in auctions_data if a)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Vehicles", total_vehicles)
    c2.metric("Total Bids", total_bids)
    c3.metric("Total Bid Value", f"€{total_bid_value:,.0f}")
    c4.metric("Avg Bid", f"€{total_bid_value / max(total_vehicles, 1):,.0f}")

    # Lookup market prices button
    if st.button("Lookup Market Prices (Gaspedaal)"):
        from execution.scrape_gaspedaal import run as scrape_market
        # Build unique make/model/year/mileage combos from vehicles
        seen = set()
        lookups = []
        for v in vehicles:
            if v.make and v.model:
                key = (v.make, v.model, v.year)
                if key not in seen:
                    seen.add(key)
                    lookups.append((v.make, v.model, v.year, v.mileage_km))

        if not lookups:
            st.warning("No vehicles with make/model data to look up.")
        else:
            progress_bar = st.progress(0, text="Starting market price lookup...")
            status_text = st.empty()
            total_found = 0
            for idx, (make, model, year, mileage_km) in enumerate(lookups):
                pct = int((idx / len(lookups)) * 100)
                year_str = str(year) if year else "?"
                mileage_str = f"{mileage_km:,} km" if mileage_km else "?"
                progress_bar.progress(pct, text=f"Looking up {make} {model} ({year_str}, {mileage_str})... ({idx + 1}/{len(lookups)})")
                status_text.text(f"Searching Gaspedaal for {make} {model} (year: {year_str}, mileage: {mileage_str})...")
                try:
                    found = scrape_market(make=make, model=model, year=year, mileage_km=mileage_km)
                    total_found += len(found)
                except Exception as e:
                    st.warning(f"Error fetching prices for {make} {model}: {e}")
            progress_bar.progress(100, text="Done!")
            status_text.empty()
            st.success(f"Market prices updated! Found {total_found} prices for {len(lookups)} models.")
            st.rerun()

    # Filters
    col_f1, col_f2 = st.columns(2)
    makes = sorted(set(v.make for v in vehicles if v.make))
    fuels = sorted(set(v.fuel_type for v in vehicles if v.fuel_type))

    with col_f1:
        filter_make = st.selectbox("Filter by Make", ["All"] + makes)
    with col_f2:
        filter_fuel = st.selectbox("Filter by Fuel Type", ["All"] + fuels)

    # Build table data
    rows = []
    vehicle_id_map = {}  # map row index → vehicle_id for favorite toggling
    for v, auction in auctions_data:
        if filter_make != "All" and v.make != filter_make:
            continue
        if filter_fuel != "All" and v.fuel_type != filter_fuel:
            continue

        # Get market price
        market_prices = repo.get_market_prices(v.make or "", v.model or "", v.year)
        avg_market = None
        if market_prices:
            prices = [mp.asking_price for mp in market_prices if mp.asking_price]
            if prices:
                from statistics import median as _median
                avg_market = _median(prices)

        # Get prediction + score
        prediction = predict(v.id)
        deal = score(v.id, prediction)
        strategy = get_strategy(v.id, prediction)

        row = {
            "Fav": bool(v.is_favorite),
            "ID": v.id,
            "Make": v.make or "N/A",
            "Model": v.model or "N/A",
            "Year": v.year or 0,
            "Mileage (km)": v.mileage_km if v.mileage_km else None,
            "Current Bid": auction.current_bid if auction and auction.current_bid else None,
            "Market Price": avg_market if avg_market else None,
            "Max Bid": strategy.max_bid if strategy and strategy.max_bid else None,
            "Deal Score": deal.score if deal else None,
            "Auction Link": v.url or "",
            # Raw values for sorting/analysis
            "_score": deal.score if deal else 0,
            "_rating": deal.rating if deal else "N/A",
            "_bid": auction.current_bid if auction and auction.current_bid else 0,
            "_market": avg_market or 0,
            "_max_bid": strategy.max_bid if strategy and strategy.max_bid else 0,
            "_mileage": v.mileage_km or 0,
            "_year": v.year or 0,
            "_factors": deal.factors if deal else [],
            "_recommendation": deal.recommendation if deal else "",
        }
        vehicle_id_map[len(rows)] = v.id
        rows.append(row)

    if not rows:
        st.info("No vehicles match the current filters.")
        repo.close()
        return

    # --- Four tabs ---
    tab_fav, tab_deals, tab_all, tab_analysis = st.tabs([
        "Favorites",
        "Best Deals",
        "All Vehicles",
        "Analysis",
    ])

    link_config = {
        "Auction Link": st.column_config.LinkColumn("Auction Link", display_text="View"),
    }
    display_cols = ["Fav", "ID", "Make", "Model", "Year", "Mileage (km)",
                    "Current Bid", "Market Price", "Max Bid", "Deal Score", "Auction Link"]

    # --- Tab 0: Favorites ---
    with tab_fav:
        fav_rows = [r for r in rows if r["Fav"]]
        if fav_rows:
            for r in fav_rows:
                score_val = r["_score"]
                rating = r["_rating"]
                bid = r["_bid"]
                market = r["_market"]
                savings = market - bid if market and bid else 0

                with st.container(border=True):
                    cols = st.columns([2, 1, 1, 1, 1])
                    link = r.get("Auction Link")
                    if link:
                        cols[0].markdown(f"**[{r['Make']} {r['Model']}]({link})** ({r['Year']})")
                    else:
                        cols[0].markdown(f"**{r['Make']} {r['Model']}** ({r['Year']})")
                    cols[1].metric("Bid", f"€{r['Current Bid']:,.0f}" if r["Current Bid"] else "N/A")
                    cols[2].metric("Market", f"€{r['Market Price']:,.0f}" if r["Market Price"] else "N/A")
                    cols[3].metric("Savings", f"€{savings:,.0f}" if savings > 0 else "N/A",
                                   delta=f"{savings / market * 100:.0f}%" if market and savings > 0 else None)
                    cols[4].metric("Score", f"{score_val}/10", delta=rating,
                                   delta_color="normal" if score_val >= 6 else "inverse")

                    if r["_recommendation"]:
                        st.caption(r["_recommendation"])
        else:
            st.info("No favorites yet. Mark vehicles as favorite in the All Vehicles tab.")

    # --- Tab 1: Best Deals ---
    with tab_deals:
        sorted_rows = sorted(rows, key=lambda r: r["_score"], reverse=True)
        top_deals = [r for r in sorted_rows if r["_score"] >= 6.0]
        if not top_deals:
            top_deals = sorted_rows[:5]

        for r in top_deals:
            score_val = r["_score"]
            rating = r["_rating"]
            bid = r["_bid"]
            market = r["_market"]
            savings = market - bid if market and bid else 0

            with st.container(border=True):
                cols = st.columns([2, 1, 1, 1, 1])
                link = r.get("Auction Link")
                if link:
                    cols[0].markdown(f"**[{r['Make']} {r['Model']}]({link})** ({r['Year']})")
                else:
                    cols[0].markdown(f"**{r['Make']} {r['Model']}** ({r['Year']})")
                cols[1].metric("Bid", f"€{r['Current Bid']:,.0f}" if r["Current Bid"] else "N/A")
                cols[2].metric("Market", f"€{r['Market Price']:,.0f}" if r["Market Price"] else "N/A")
                cols[3].metric("Savings", f"€{savings:,.0f}" if savings > 0 else "N/A",
                               delta=f"{savings / market * 100:.0f}%" if market and savings > 0 else None)
                cols[4].metric("Score", f"{score_val}/10", delta=rating,
                               delta_color="normal" if score_val >= 6 else "inverse")

                if r["_recommendation"]:
                    st.caption(r["_recommendation"])

        if not top_deals:
            st.info("No deals scored yet. Run market price lookup first.")

    # --- Tab 2: All Vehicles ---
    with tab_all:
        df = pd.DataFrame(rows)[display_cols]
        edited_df = st.data_editor(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                **link_config,
                "Fav": st.column_config.CheckboxColumn("Fav", default=False),
                "Mileage (km)": st.column_config.NumberColumn("Mileage (km)", format="%d km"),
                "Current Bid": st.column_config.NumberColumn("Current Bid", format="€%d"),
                "Market Price": st.column_config.NumberColumn("Market Price", format="€%d"),
                "Max Bid": st.column_config.NumberColumn("Max Bid", format="€%d"),
                "Deal Score": st.column_config.NumberColumn("Deal Score", format="%d/10"),
            },
            disabled=[c for c in display_cols if c != "Fav"],
            key="auto_fav_editor",
        )
        # Persist favorite changes
        if edited_df is not None:
            for idx in range(len(edited_df)):
                new_fav = bool(edited_df.iloc[idx]["Fav"])
                old_fav = bool(rows[idx]["Fav"])
                if new_fav != old_fav:
                    vid = vehicle_id_map[idx]
                    repo.toggle_vehicle_favorite(vid)

    # --- Tab 3: Analysis ---
    with tab_analysis:
        import plotly.express as px

        a_col1, a_col2 = st.columns(2)

        with a_col1:
            # Score distribution
            scores = [r["_score"] for r in rows if r["_score"] > 0]
            if scores:
                fig = px.histogram(x=scores, nbins=10, labels={"x": "Deal Score", "y": "Count"},
                                   title="Deal Score Distribution")
                fig.update_layout(showlegend=False, margin=dict(t=40, b=20))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No deal scores available yet.")

        with a_col2:
            # Bid vs Market scatter
            scatter_data = [{"Make": r["Make"], "Model": r["Model"],
                            "Bid": r["_bid"], "Market": r["_market"], "Score": r["_score"]}
                           for r in rows if r["_bid"] > 0 and r["_market"] > 0]
            if scatter_data:
                sdf = pd.DataFrame(scatter_data)
                fig = px.scatter(sdf, x="Market", y="Bid", color="Score",
                                 hover_data=["Make", "Model"],
                                 title="Bid vs Market Price",
                                 color_continuous_scale="RdYlGn")
                fig.add_shape(type="line", x0=0, y0=0,
                              x1=sdf["Market"].max() * 1.1, y1=sdf["Market"].max() * 1.1,
                              line=dict(dash="dash", color="gray"))
                fig.update_layout(margin=dict(t=40, b=20))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No market data for comparison. Run market price lookup first.")

        # Vehicle distribution pie chart
        make_counts = {}
        for r in rows:
            m = r["Make"]
            make_counts[m] = make_counts.get(m, 0) + 1
        if make_counts:
            pie_df = pd.DataFrame({"Make": list(make_counts.keys()), "Count": list(make_counts.values())})
            fig_pie = px.pie(pie_df, values="Count", names="Make",
                             title="Vehicle Distribution by Make",
                             hole=0.3)
            fig_pie.update_traces(textposition="inside", textinfo="percent+label")
            fig_pie.update_layout(margin=dict(t=40, b=20))
            st.plotly_chart(fig_pie, use_container_width=True)

        # Rating breakdown
        ratings = {}
        for r in rows:
            rat = r["_rating"]
            if rat != "N/A":
                ratings[rat] = ratings.get(rat, 0) + 1
        if ratings:
            r_col1, r_col2, r_col3, r_col4 = st.columns(4)
            r_col1.metric("Excellent", ratings.get("Excellent", 0))
            r_col2.metric("Good", ratings.get("Good", 0))
            r_col3.metric("Fair", ratings.get("Fair", 0))
            r_col4.metric("Poor", ratings.get("Poor", 0))

        # Make breakdown
        make_data = {}
        for r in rows:
            m = r["Make"]
            if m not in make_data:
                make_data[m] = {"count": 0, "total_bid": 0, "avg_score": []}
            make_data[m]["count"] += 1
            make_data[m]["total_bid"] += r["_bid"]
            if r["_score"] > 0:
                make_data[m]["avg_score"].append(r["_score"])

        if make_data:
            make_rows = []
            for m, d in make_data.items():
                avg_s = sum(d["avg_score"]) / len(d["avg_score"]) if d["avg_score"] else 0
                make_rows.append({"Make": m, "Vehicles": d["count"],
                                  "Total Bids": f"€{d['total_bid']:,.0f}",
                                  "Avg Score": f"{avg_s:.1f}/10" if avg_s else "N/A"})
            st.subheader("By Make")
            st.dataframe(pd.DataFrame(make_rows), use_container_width=True, hide_index=True)

    repo.close()


# ===================================================================
# PAGE 2: Goods Auctions
# ===================================================================
def page_goods_auctions():
    st.title("Goods Auctions")
    repo = get_repo()

    # Reformat old-style auction names (one-time migration)
    if "goods_names_migrated" not in st.session_state:
        from execution.scrape_goods import _format_auction_name
        updated = repo.reformat_goods_auction_names(_format_auction_name)
        if updated:
            print(f"  Reformatted {updated} goods auction name(s)")
        st.session_state["goods_names_migrated"] = True

    # Add auction section
    with st.expander("Add Auction", expanded=not bool(repo.get_goods_auction_names())):
        url_col1, url_col2, url_col3 = st.columns([3, 1, 1])
        with url_col1:
            goods_url = st.text_input(
                "Auction URL",
                placeholder="https://www.onlineveilingmeester.nl/... or https://www.troostwijkauctions.com/...",
                key="goods_auction_url",
            )
        with url_col2:
            goods_max = st.number_input("Max Items", min_value=1, max_value=200, value=50, key="goods_max_items")
        with url_col3:
            st.write("")  # spacing
            scrape_goods_btn = st.button("Scrape URL", type="primary", key="scrape_goods_btn")

        if scrape_goods_btn and goods_url:
            progress_bar = st.progress(0, text="Starting scraper...")

            def _goods_progress(current, total, message):
                if total > 0:
                    pct = min(int((current / total) * 90) + 10, 99)
                else:
                    pct = 10
                progress_bar.progress(pct, text=message)

            try:
                from execution.scrape_goods import run as scrape_goods_fn
                progress_bar.progress(5, text="Launching browser...")
                results = scrape_goods_fn(url=goods_url, max_lots=int(goods_max), progress_callback=_goods_progress)
                progress_bar.progress(100, text="Done!")
                st.success(f"Scraped {len(results)} items!")
                st.rerun()
            except Exception as e:
                progress_bar.empty()
                st.error(f"Scraping failed: {e}")

    # Auction selector
    auction_names = repo.get_goods_auction_names()

    if not auction_names:
        st.info("No goods auctions found. Add one using the URL input above.")
        repo.close()
        return

    col1, col2 = st.columns([3, 1])
    with col1:
        selected_auction = st.selectbox("Select Auction", ["All"] + auction_names, key="goods_auction")
    with col2:
        if selected_auction != "All":
            if st.button("Delete Auction", key="del_goods"):
                count = repo.delete_goods_auction(selected_auction)
                st.success(f"Deleted {count} items from '{selected_auction}'")
                st.rerun()

    # Get items
    items = repo.list_goods_items(
        limit=200,
        auction_name=selected_auction if selected_auction != "All" else None,
    )

    if not items:
        st.warning("No items in this auction.")
        repo.close()
        return

    # Summary stats
    total_items = len(items)
    total_bids = sum(1 for i in items if i.current_bid)
    total_bid_val = sum((i.current_bid or 0) for i in items)
    total_est_val = sum((i.estimated_value or 0) for i in items)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Items", total_items)
    c2.metric("Total Bids", total_bids)
    c3.metric("Total Bid Value", f"€{total_bid_val:,.0f}")
    c4.metric("Est. Retail Value", f"€{total_est_val:,.0f}" if total_est_val else "N/A")

    # Refresh Bid Prices button
    if st.button("Refresh Bid Prices", key="refresh_bid_prices"):
        from execution.refresh_bids import refresh_bids
        progress_bar = st.progress(0, text="Starting bid refresh...")

        def _bid_progress(current, total, message):
            if total > 0:
                pct = min(int((current / total) * 95) + 5, 99)
            else:
                pct = 5
            progress_bar.progress(pct, text=message)

        try:
            auction_filter = selected_auction if selected_auction != "All" else None
            summary = refresh_bids(
                auction_name=auction_filter,
                progress_callback=_bid_progress,
            )
            progress_bar.progress(100, text="Done!")
            st.success(
                f"Updated {summary['updated']}/{summary['total']} bids"
                + (f" ({summary['failed']} failed)" if summary['failed'] else "")
            )
            st.rerun()
        except Exception as e:
            progress_bar.empty()
            st.error(f"Bid refresh failed: {e}")

    # Lookup Real Prices button
    if st.button("Lookup Real Prices (bol.com + Amazon)", key="lookup_goods_prices"):
        from execution.scrape_retail_prices import lookup_goods_prices
        progress_bar = st.progress(0, text="Starting price lookup...")
        status_text = st.empty()

        def _price_progress(current, total, message):
            if total > 0:
                pct = min(int((current / total) * 95) + 5, 99)
            else:
                pct = 5
            progress_bar.progress(pct, text=message)

        try:
            auction_filter = selected_auction if selected_auction != "All" else None
            summary = lookup_goods_prices(
                auction_name=auction_filter,
                progress_callback=_price_progress,
            )
            progress_bar.progress(100, text="Done!")
            st.success(
                f"Looked up {summary['looked_up']} items — "
                f"found retail prices for {summary['prices_found']}!"
            )
            st.rerun()
        except Exception as e:
            progress_bar.empty()
            st.error(f"Price lookup failed: {e}")

    # AI Evaluate Deals button
    if st.button("AI Evaluate Deals (GPT-4o-mini)", key="ai_evaluate_goods"):
        from execution.goods_evaluator import evaluate_goods_items
        progress_bar = st.progress(0, text="Starting AI evaluation...")

        def _eval_progress(current, total, message):
            if total > 0:
                pct = min(int((current / total) * 95) + 5, 99)
            else:
                pct = 5
            progress_bar.progress(pct, text=message)

        try:
            auction_filter = selected_auction if selected_auction != "All" else None
            summary = evaluate_goods_items(
                auction_name=auction_filter,
                progress_callback=_eval_progress,
            )
            progress_bar.progress(100, text="Done!")
            if 'error' in summary:
                st.session_state["ai_eval_error"] = summary['error']
            else:
                msg = (
                    f"Evaluated {summary['evaluated']} items — "
                    f"{summary['skipped']} skipped (unchanged), "
                    f"{summary['errors']} errors"
                )
                st.session_state["ai_eval_msg"] = msg
            st.rerun()
        except Exception as e:
            progress_bar.empty()
            st.error(f"AI evaluation failed: {e}")

    # Show persistent messages after rerun
    if "ai_eval_error" in st.session_state:
        st.error(st.session_state.pop("ai_eval_error"))
    if "ai_eval_msg" in st.session_state:
        st.success(st.session_state.pop("ai_eval_msg"))

    # Filters
    categories = sorted(set(i.category for i in items if i.category))
    brands = sorted(set(i.brand for i in items if i.brand))

    col_f1, col_f2 = st.columns(2)
    with col_f1:
        filter_cat = st.selectbox("Filter by Category", ["All"] + categories)
    with col_f2:
        filter_brand = st.selectbox("Filter by Brand", ["All"] + brands)

    # Build row data
    rows = []
    goods_id_map = {}  # map row index → item_id for favorite toggling
    goods_item_map = {}  # map row index → item object for AI fields
    for item in items:
        if filter_cat != "All" and item.category != filter_cat:
            continue
        if filter_brand != "All" and item.brand != filter_brand:
            continue

        bid = item.current_bid or 0
        est = item.estimated_value or 0
        savings = est - bid if est and bid else 0
        savings_pct = (savings / est * 100) if est and savings > 0 else 0

        # Rating /10 based on savings percentage (bid vs estimated value)
        if not est or not bid:
            rating = None
            rating_num = 0
        else:
            rating_num = min(10, max(1, round(savings_pct / 5)))
            if savings_pct <= 0:
                rating_num = 1
            rating = rating_num

        goods_id_map[len(rows)] = item.id
        goods_item_map[len(rows)] = item
        rows.append({
            "Fav": bool(item.is_favorite),
            "ID": item.id,
            "Title": (item.title or "N/A")[:60],
            "Category": item.category or "N/A",
            "Brand": item.brand or "N/A",
            "Condition": item.condition or "N/A",
            "Qty": item.quantity or 1,
            "Current Bid": item.current_bid if item.current_bid else None,
            "Est. Value": item.estimated_value if item.estimated_value else None,
            "Max Bid": item.recommended_max_bid if item.recommended_max_bid else None,
            "AI Value": item.ai_estimated_value if item.ai_estimated_value else None,
            "AI Max Bid": item.ai_recommended_max_bid if item.ai_recommended_max_bid else None,
            "Risk": (item.ai_risk_level or "").title(),
            "Rating": rating,
            "Auction Link": item.url or "",
            # Raw values for sorting/analysis
            "_bid": bid,
            "_est": est,
            "_max_bid": item.recommended_max_bid or 0,
            "_savings": savings,
            "_savings_pct": savings_pct,
            "_category": item.category or "N/A",
            "_brand": item.brand or "N/A",
            "_ai_explanation": item.ai_explanation or "",
            "_ai_risk_level": item.ai_risk_level or "",
            "_ai_value": item.ai_estimated_value or 0,
        })

    if not rows:
        st.info("No items match the current filters.")
        repo.close()
        return

    # --- Four tabs ---
    tab_fav, tab_deals, tab_all, tab_analysis = st.tabs([
        "Favorites",
        "Best Deals",
        "All Items",
        "Analysis",
    ])

    link_config = {
        "Auction Link": st.column_config.LinkColumn("Auction Link", display_text="View"),
    }
    display_cols = ["Fav", "ID", "Title", "Category", "Brand", "Condition", "Qty",
                    "Current Bid", "Est. Value", "Max Bid", "AI Value", "AI Max Bid", "Risk",
                    "Rating", "Auction Link"]

    # --- Card renderer ---
    def _render_deal_card(r):
        """Render a single deal card with native Streamlit components."""
        bid = r["_bid"]
        max_bid = r["_max_bid"]
        savings = r["_savings"]
        savings_pct = r["_savings_pct"]
        ai_value = r["_ai_value"]
        has_ai = bool(r["_ai_explanation"])
        has_retail = bool(r["Est. Value"])
        risk_level = r["_ai_risk_level"]
        risk_color = {"low": "#16a34a", "medium": "#d97706", "high": "#dc2626"}.get(risk_level, "#6b7280")
        risk_bg = {"low": "#f0fdf4", "medium": "#fffbeb", "high": "#fef2f2"}.get(risk_level, "#f9fafb")

        with st.container(border=True):
            # Header row: title + risk badge
            link = r.get("Auction Link")
            title_text = r["Title"]
            subtitle = " · ".join(x for x in [r["Brand"], r["Category"]] if x != "N/A")
            h1, h2 = st.columns([5, 1])
            with h1:
                title_md = f"**[{title_text}]({link})**" if link else f"**{title_text}**"
                if subtitle:
                    title_md += f"&nbsp;&nbsp;`{subtitle}`"
                st.markdown(title_md)
            with h2:
                if has_ai and risk_level:
                    st.markdown(
                        f"<div style='text-align:right'><span style='background:{risk_color};color:white;"
                        f"padding:3px 10px;border-radius:10px;font-size:0.78em;font-weight:600'>"
                        f"{risk_level.upper()} RISK</span></div>",
                        unsafe_allow_html=True,
                    )

            # Current bid
            st.metric("Current Bid", f"€{r['Current Bid']:,.0f}" if r["Current Bid"] else "N/A")

            # Retail Lookup row
            if has_retail:
                st.markdown(
                    "<span style='font-size:0.78em;font-weight:600;color:#64748b;"
                    "text-transform:uppercase;letter-spacing:0.5px'>Retail Lookup</span>",
                    unsafe_allow_html=True,
                )
                c1, c2, c3 = st.columns(3)
                c1.metric("Retail Value", f"€{r['Est. Value']:,.0f}" if r["Est. Value"] else "N/A")
                c2.metric("Max Bid", f"€{max_bid:,.0f}" if max_bid else "N/A")
                c3.metric(
                    "Savings",
                    f"€{savings:,.0f}" if savings > 0 else "N/A",
                    delta=f"{savings_pct:.0f}%" if savings_pct > 0 else None,
                )

            # AI Evaluation row
            if has_ai:
                ai_savings = ai_value - bid if ai_value and bid else 0
                ai_savings_pct = (ai_savings / ai_value * 100) if ai_value and ai_savings > 0 else 0
                st.markdown(
                    "<span style='font-size:0.78em;font-weight:600;color:#64748b;"
                    "text-transform:uppercase;letter-spacing:0.5px'>AI Evaluation</span>",
                    unsafe_allow_html=True,
                )
                c1, c2, c3 = st.columns(3)
                c1.metric("AI Market Value", f"€{r['AI Value']:,.0f}" if r["AI Value"] else "N/A")
                c2.metric("AI Max Bid", f"€{r['AI Max Bid']:,.0f}" if r["AI Max Bid"] else "N/A")
                c3.metric(
                    "AI Savings",
                    f"€{ai_savings:,.0f}" if ai_savings > 0 else "N/A",
                    delta=f"{ai_savings_pct:.0f}%" if ai_savings_pct > 0 else None,
                )

            # AI analysis bullets
            if r["_ai_explanation"]:
                bullets = [
                    b.strip().lstrip("•-– ").strip()
                    for b in r["_ai_explanation"].replace("\n", "•").split("•")
                    if b.strip().lstrip("•-– ").strip()
                ]
                if bullets:
                    md_lines = "\n".join(f"- {b}" for b in bullets)
                    st.markdown(
                        f"<div style='background:{risk_bg};border-left:3px solid {risk_color};"
                        f"border-radius:0 6px 6px 0;padding:8px 14px;font-size:0.88em'>"
                        f"\n\n{md_lines}\n\n</div>",
                        unsafe_allow_html=True,
                    )

    # --- Tab 0: Favorites ---
    with tab_fav:
        fav_rows = [r for r in rows if r["Fav"]]
        if fav_rows:
            for r in fav_rows:
                _render_deal_card(r)
        else:
            st.info("No favorites yet. Mark items as favorite in the All Items tab.")

    # --- Tab 1: Best Deals ---
    with tab_deals:
        sorted_rows = sorted(rows, key=lambda r: r["_savings_pct"], reverse=True)
        top_deals = [r for r in sorted_rows if r["_savings"] > 0][:10]
        if not top_deals:
            top_deals = sorted_rows[:5]

        for r in top_deals:
            _render_deal_card(r)

        if not top_deals:
            st.info("No deals available yet.")

    # --- Tab 2: All Items ---
    with tab_all:
        df = pd.DataFrame(rows)[display_cols]
        edited_df = st.data_editor(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                **link_config,
                "Fav": st.column_config.CheckboxColumn("Fav", default=False),
                "Current Bid": st.column_config.NumberColumn("Current Bid", format="€%d"),
                "Est. Value": st.column_config.NumberColumn("Est. Value", format="€%d"),
                "Max Bid": st.column_config.NumberColumn("Max Bid", format="€%d"),
                "AI Value": st.column_config.NumberColumn("AI Value", format="€%d"),
                "AI Max Bid": st.column_config.NumberColumn("AI Max Bid", format="€%d"),
                "Rating": st.column_config.NumberColumn("Rating", format="%d/10"),
            },
            disabled=[c for c in display_cols if c != "Fav"],
            key="goods_fav_editor",
        )
        # Persist favorite changes
        if edited_df is not None:
            for idx in range(len(edited_df)):
                new_fav = bool(edited_df.iloc[idx]["Fav"])
                old_fav = bool(rows[idx]["Fav"])
                if new_fav != old_fav:
                    gid = goods_id_map[idx]
                    repo.toggle_goods_favorite(gid)

    # --- Tab 3: Analysis ---
    with tab_analysis:
        import plotly.express as px

        a_col1, a_col2 = st.columns(2)

        with a_col1:
            # Category distribution pie
            cat_counts = {}
            for r in rows:
                c = r["_category"]
                cat_counts[c] = cat_counts.get(c, 0) + 1
            if cat_counts:
                pie_df = pd.DataFrame({"Category": list(cat_counts.keys()), "Count": list(cat_counts.values())})
                fig = px.pie(pie_df, values="Count", names="Category",
                             title="Item Distribution by Category", hole=0.3)
                fig.update_traces(textposition="inside", textinfo="percent+label")
                fig.update_layout(margin=dict(t=40, b=20))
                st.plotly_chart(fig, use_container_width=True)

        with a_col2:
            # Bid vs Estimated Value scatter
            scatter_data = [{"Title": r["Title"][:30], "Bid": r["_bid"], "Est. Value": r["_est"],
                            "Category": r["_category"]}
                           for r in rows if r["_bid"] > 0 and r["_est"] > 0]
            if scatter_data:
                sdf = pd.DataFrame(scatter_data)
                fig = px.scatter(sdf, x="Est. Value", y="Bid", color="Category",
                                 hover_data=["Title"],
                                 title="Bid vs Estimated Value")
                fig.add_shape(type="line", x0=0, y0=0,
                              x1=sdf["Est. Value"].max() * 1.1, y1=sdf["Est. Value"].max() * 1.1,
                              line=dict(dash="dash", color="gray"))
                fig.update_layout(margin=dict(t=40, b=20))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No estimated values for comparison.")

        # Brand distribution pie
        brand_counts = {}
        for r in rows:
            b = r["_brand"]
            if b != "N/A":
                brand_counts[b] = brand_counts.get(b, 0) + 1
        if brand_counts:
            pie_df = pd.DataFrame({"Brand": list(brand_counts.keys()), "Count": list(brand_counts.values())})
            fig_pie = px.pie(pie_df, values="Count", names="Brand",
                             title="Item Distribution by Brand", hole=0.3)
            fig_pie.update_traces(textposition="inside", textinfo="percent+label")
            fig_pie.update_layout(margin=dict(t=40, b=20))
            st.plotly_chart(fig_pie, use_container_width=True)

        # Category breakdown table
        cat_data = {}
        for r in rows:
            c = r["_category"]
            if c not in cat_data:
                cat_data[c] = {"count": 0, "total_bid": 0, "total_est": 0}
            cat_data[c]["count"] += 1
            cat_data[c]["total_bid"] += r["_bid"]
            cat_data[c]["total_est"] += r["_est"]

        if cat_data:
            cat_rows = []
            for c, d in cat_data.items():
                cat_rows.append({
                    "Category": c,
                    "Items": d["count"],
                    "Total Bids": f"€{d['total_bid']:,.0f}",
                    "Total Est. Value": f"€{d['total_est']:,.0f}",
                })
            st.subheader("By Category")
            st.dataframe(pd.DataFrame(cat_rows), use_container_width=True, hide_index=True)

    repo.close()


# ===================================================================
# PAGE 3: Vehicle Detail
# ===================================================================
def page_vehicle_detail():
    st.title("Vehicle Detail")
    repo = get_repo()

    # Vehicle selector
    vehicle_id = st.session_state.selected_vehicle_id
    vehicles = repo.list_vehicles(limit=200)
    vehicle_options = {f"{v.id}: {v.make} {v.model} ({v.year})": v.id for v in vehicles}

    if not vehicle_options:
        st.info("No vehicles in database.")
        repo.close()
        return

    # Find current selection in options
    default_key = None
    if vehicle_id:
        for key, vid in vehicle_options.items():
            if vid == vehicle_id:
                default_key = key
                break

    selected = st.selectbox(
        "Select Vehicle",
        list(vehicle_options.keys()),
        index=list(vehicle_options.keys()).index(default_key) if default_key else 0,
    )
    vehicle_id = vehicle_options[selected]
    st.session_state.selected_vehicle_id = vehicle_id

    vehicle = repo.get_vehicle(vehicle_id)
    if not vehicle:
        st.error("Vehicle not found.")
        repo.close()
        return

    # --- Specifications ---
    st.subheader("Specifications")
    spec_col1, spec_col2 = st.columns(2)

    with spec_col1:
        st.markdown(f"**Make:** {vehicle.make or 'N/A'}")
        st.markdown(f"**Model:** {vehicle.model or 'N/A'}")
        st.markdown(f"**Year:** {vehicle.year or 'N/A'}")
        st.markdown(f"**Mileage:** {vehicle.mileage_km:,} km" if vehicle.mileage_km else "**Mileage:** N/A")
        st.markdown(f"**Fuel Type:** {vehicle.fuel_type or 'N/A'}")
        st.markdown(f"**Power:** {vehicle.power_hp} HP" if vehicle.power_hp else "**Power:** N/A")

    with spec_col2:
        st.markdown(f"**Transmission:** {vehicle.transmission or 'N/A'}")
        st.markdown(f"**Body Type:** {vehicle.body_type or 'N/A'}")
        st.markdown(f"**Color:** {vehicle.color or 'N/A'}")
        st.markdown(f"**Location:** {vehicle.location or 'N/A'}")
        st.markdown(f"**MOT Expiry:** {vehicle.mot_expiry or 'N/A'}")
        st.markdown(f"**Source:** {vehicle.source or 'N/A'}")

    if vehicle.condition_notes:
        st.markdown(f"**Condition Notes:** {vehicle.condition_notes}")

    if vehicle.url:
        st.markdown(f"[View Original Listing]({vehicle.url})")

    # --- Auction Status ---
    st.subheader("Auction Status")
    auctions = repo.get_auctions_for_vehicle(vehicle_id)
    if auctions:
        auction = auctions[0]
        ac1, ac2, ac3, ac4 = st.columns(4)
        ac1.metric("Current Bid", f"€{auction.current_bid:,.0f}" if auction.current_bid else "N/A")
        ac2.metric("Bid Count", auction.bid_count or 0)
        ac3.metric("Status", auction.status or "N/A")

        if auction.end_time:
            try:
                if isinstance(auction.end_time, str):
                    end_time = datetime.fromisoformat(auction.end_time.replace("Z", "+00:00"))
                else:
                    end_time = auction.end_time
                    if end_time.tzinfo is None:
                        end_time = end_time.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                remaining = end_time - now
                hours = remaining.total_seconds() / 3600
                if hours > 0:
                    ac4.metric("Time Remaining", f"{hours:.1f}h")
                else:
                    ac4.metric("Time Remaining", "Ended")
            except (ValueError, TypeError):
                ac4.metric("End Time", str(auction.end_time)[:19])
    else:
        st.info("No auction data available.")

    # --- Price Prediction ---
    st.subheader("Price Prediction")
    prediction = predict(vehicle_id)
    if prediction and prediction.predicted_price > 0:
        pc1, pc2, pc3 = st.columns(3)
        pc1.metric("Predicted Price", f"€{prediction.predicted_price:,.0f}")
        pc2.metric("Market Average", f"€{prediction.market_avg:,.0f}")
        pc3.metric("Confidence", prediction.confidence.upper())

        with st.expander("Prediction Reasoning"):
            for r in prediction.reasoning:
                st.markdown(f"- {r}")
    else:
        st.warning("No market data available for prediction. Run market price lookup first.")

    # --- Deal Score ---
    st.subheader("Deal Score")
    deal = score(vehicle_id, prediction)
    if deal:
        ds1, ds2 = st.columns(2)
        ds1.metric("Score", f"{deal.score}/10")
        ds2.metric("Rating", deal.rating)
        st.info(deal.recommendation)

        with st.expander("Score Factors"):
            for f in deal.factors:
                st.markdown(f"- {f}")

    # --- Bid Strategy ---
    st.subheader("Bid Strategy")
    strategy = get_strategy(vehicle_id, prediction)
    if strategy:
        bs1, bs2 = st.columns(2)
        bs1.metric("Max Bid", f"€{strategy.max_bid:,.0f}" if strategy.max_bid else "N/A")
        bs2.metric("Risk Level", strategy.risk_level.upper())
        st.info(strategy.timing_advice)

        with st.expander("Strategy Notes"):
            for n in strategy.strategy_notes:
                st.markdown(f"- {n}")

    # --- Price History Chart ---
    st.subheader("Price History")
    history = repo.get_price_history(vehicle_id)
    if history and len(history) >= 2:
        df_hist = pd.DataFrame([
            {"Time": h.recorded_at, "Bid Amount (€)": h.bid_amount, "Bid Count": h.bid_count}
            for h in history
        ])
        fig = px.line(df_hist, x="Time", y="Bid Amount (€)", title="Bid History Over Time",
                      markers=True)
        fig.update_layout(height=400)
        st.plotly_chart(fig, use_container_width=True)
    elif history:
        st.info(f"Only 1 price point recorded (€{history[0].bid_amount:,.0f}). Need more data for a chart.")
    else:
        st.info("No price history recorded yet.")

    # --- Image Analysis ---
    st.subheader("Image Analysis")
    analyses = repo.get_image_analyses(vehicle_id)
    if analyses:
        scores_list = [a.condition_score for a in analyses if a.condition_score]
        if scores_list:
            avg_score = sum(scores_list) / len(scores_list)
            ia1, ia2 = st.columns(2)
            ia1.metric("Avg Condition Score", f"{avg_score:.1f}/10")
            ia2.metric("Images Analyzed", len(analyses))

        for a in analyses:
            with st.expander(f"Image: {a.image_type or 'unknown'} — Score: {a.condition_score}/10"):
                st.markdown(f"**Condition:** {a.overall_condition}")
                st.markdown(f"**Confidence:** {a.confidence}")
                damages = a.damages
                if damages:
                    st.markdown("**Damages:**")
                    for d in damages:
                        st.markdown(f"- {d}")
                assessment = a.assessment
                if assessment:
                    st.markdown(f"**Assessment:** {assessment.get('assessment', 'N/A')}")
                if a.image_url:
                    st.image(a.image_url, width=400)
    else:
        st.info("No image analysis performed yet. Use CLI:\n\n`python main.py analyze-images --id " + str(vehicle_id) + " --max-images 5`")

    # --- Vehicle Images ---
    if vehicle.image_urls:
        st.subheader("Vehicle Images")
        img_cols = st.columns(min(len(vehicle.image_urls), 3))
        for i, url in enumerate(vehicle.image_urls[:9]):
            with img_cols[i % 3]:
                st.image(url, width=300)

    repo.close()


# ===================================================================
# Router
# ===================================================================
if page == "Auto Auctions":
    page_auto_auctions()
elif page == "Goods Auctions":
    page_goods_auctions()
elif page == "Vehicle Detail":
    page_vehicle_detail()
