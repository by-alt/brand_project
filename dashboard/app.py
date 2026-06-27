"""
Streamlit dashboard for the Brand Health Tracker.

Run it with:
    streamlit run dashboard/app.py

This is the interactive, decision-oriented view: filter and drill into the data, see
WHY sentiment moved (root cause), read the actual complaints behind each number, and
spot anomalies annotated right on the trend. Reads data/brand_health.db.
"""
import os
import sys

import pandas as pd
import streamlit as st

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.database import get_connection, init_db, count_by_brand
from scripts.insights import (
    benchmark, benchmark_by_category, detect_anomalies, ratings_vs_complaints,
    root_cause, aspect_breakdown, example_complaints,
)

st.set_page_config(page_title="Brand Health Tracker", layout="wide")

# Header row: title on the left, a real Reload button on the right. Streamlit re-runs the
# whole script on each interaction, so this button re-reads the database — useful when the
# pipeline has collected new data in another terminal while this dashboard is open. (It does
# NOT scan the Play Store / Reddit; collection is a separate step — run scripts/run_all.py.)
_title_col, _btn_col = st.columns([5, 1])
with _title_col:
    st.title("Brand Health Tracker")
    st.caption("What customers complain about across consumer apps — why it's moving, and the evidence behind it.")
with _btn_col:
    st.write("")
    if st.button("🔄 Reload data", use_container_width=True,
                 help="Re-read the latest data from the database. To collect NEW reviews, run scripts/run_all.py first."):
        st.rerun()

conn = get_connection()
init_db(conn)

# Show when the most recent mention was collected, so staleness is visible at a glance.
try:
    _latest = conn.execute("SELECT MAX(fetched_at) FROM mentions").fetchone()[0]
    if _latest:
        st.caption(f"Most recent data collected: {_latest}")
except Exception:
    pass

if not count_by_brand(conn, "scored_mentions"):
    st.warning("No scored data yet. Run the pipeline first: "
               "`python scripts/run_all.py`.")
    st.stop()

# Full scored set (with text + source) used for filtering/exploration.
scored = pd.read_sql_query(
    "SELECT brand, week, source, sentiment_label, is_complaint, themes, clean_text "
    "FROM scored_mentions", conn
)

# ── Sidebar: filters / drill-down ────────────────────────────────────────────
st.sidebar.header("Filters")
all_brands = sorted(scored["brand"].unique())
sel_brands = st.sidebar.multiselect("Brands", all_brands, default=all_brands)

sources = ["All"] + sorted(s for s in scored["source"].dropna().unique())
sel_source = st.sidebar.radio("Source", sources, horizontal=True)

theme_options = ["All"] + sorted(
    {t for row in scored["themes"].dropna() for t in row.split("|") if t}
)
sel_theme = st.sidebar.selectbox("Complaint theme", theme_options)

weeks = sorted(scored["week"].dropna().unique())
if len(weeks) >= 2:
    wk_lo, wk_hi = st.sidebar.select_slider("Week range", options=weeks, value=(weeks[0], weeks[-1]))
else:
    wk_lo, wk_hi = (weeks[0], weeks[-1]) if weeks else (None, None)

# Apply filters to the exploratory view (diagnostic panels below use full history so
# their baselines stay intact).
view = scored[scored["brand"].isin(sel_brands)]
if sel_source != "All":
    view = view[view["source"] == sel_source]
if sel_theme != "All":
    view = view[view["themes"].fillna("").str.contains(sel_theme, regex=False)]
if wk_lo and wk_hi:
    view = view[(view["week"] >= wk_lo) & (view["week"] <= wk_hi)]

st.sidebar.caption(f"{len(view):,} mentions match these filters.")

# ── KPI cards ────────────────────────────────────────────────────────────────
anomalies = detect_anomalies(conn)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Mentions (filtered)", f"{len(view):,}")
rate = (view["is_complaint"].mean() * 100) if len(view) else 0
c2.metric("Complaint rate", f"{rate:.0f}%")
c3.metric("Brands tracked", f"{scored['brand'].nunique()}")
c4.metric("Active alerts", f"{len(anomalies)}")

# ── Why it's moving: root cause (the diagnostic headline) ────────────────────
st.header("Why it's moving")
st.caption("Week-over-week sentiment change and the complaint theme driving it — full history.")
causes = [root_cause(conn, b) for b in sel_brands]
drops = sorted([c for c in causes if c.get("status") == "drop"],
               key=lambda c: c.get("net_change", 0))
if drops:
    for c in drops[:3]:
        st.error(f"📉 {c['summary']}")
steady = [c for c in causes if c.get("status") == "stable_or_up"]
for c in steady[:2]:
    st.success(f"📈 {c['summary']}")
if not drops and not steady:
    st.info("Not enough weekly history yet to attribute sentiment moves.")

# ── Competitive benchmark, by set ────────────────────────────────────────────
st.header("Competitive standing")
st.caption("Ranked WITHIN each set — food/quick-commerce and e-commerce aren't comparable head-to-head.")
bench = pd.DataFrame(benchmark(conn))
if not bench.empty:
    for category in bench["category"].unique():
        sub = bench[bench["category"] == category]
        st.subheader(category)
        bv = sub[["brand", "total", "net_sentiment", "complaint_rate", "top_theme"]].copy()
        bv["complaint_rate"] = (bv["complaint_rate"] * 100).round(0).astype(int).astype(str) + "%"
        bv.columns = ["Brand", "Mentions", "Net sentiment", "Complaint rate", "Worst theme"]
        st.dataframe(bv, hide_index=True, use_container_width=True)

# ── Weekly sentiment with anomalies annotated ────────────────────────────────
st.header("Weekly sentiment")
st.caption("Net sentiment per brand (−1 to +1). Red dots mark weeks where a complaint theme spiked.")
weekly = (
    view.assign(
        pos=lambda d: (d.sentiment_label == "positive").astype(int),
        neg=lambda d: (d.sentiment_label == "negative").astype(int),
    )
    .groupby(["week", "brand"])
    .apply(lambda g: (g.pos.sum() - g.neg.sum()) / len(g), include_groups=False)
    .reset_index(name="net_sentiment")
)
if not weekly.empty:
    try:
        import altair as alt
        line = alt.Chart(weekly).mark_line(point=False).encode(
            x=alt.X("week:O", title="Week"),
            y=alt.Y("net_sentiment:Q", title="Net sentiment", scale=alt.Scale(domain=[-1, 1])),
            color=alt.Color("brand:N", title="Brand"),
        )
        # Anomaly markers: place a red dot on the brand's line at the spike week.
        adf = pd.DataFrame(anomalies)
        layers = [line]
        if not adf.empty:
            marks = adf.merge(weekly, on=["brand", "week"], how="inner")
            if not marks.empty:
                pts = alt.Chart(marks).mark_point(size=90, color="#CC4B3E", filled=True).encode(
                    x="week:O", y="net_sentiment:Q",
                    tooltip=["brand", "theme", "week", "count"],
                )
                layers.append(pts)
        st.altair_chart(alt.layer(*layers).properties(height=320), use_container_width=True)
    except Exception:
        # Fallback to the simple chart if Altair isn't available for any reason.
        st.line_chart(weekly.pivot(index="week", columns="brand", values="net_sentiment").sort_index())

# ── Complaint themes ─────────────────────────────────────────────────────────
st.header("Complaint themes")
tview = view[view["themes"].fillna("") != ""]
if not tview.empty:
    exploded = (
        tview.assign(theme=tview["themes"].str.split("|"))
        .explode("theme")
        .query("theme != ''")
    )
    pivot = exploded.pivot_table(index="theme", columns="brand", values="is_complaint",
                                 aggfunc="count", fill_value=0)
    st.bar_chart(pivot)
else:
    st.info("No themed complaints in the current filter.")

# ── What the LLM caught (ABSA) ───────────────────────────────────────────────
aspects = aspect_breakdown(conn)
if aspects:
    st.header("What the LLM caught")
    st.caption("Aspect categories keyword matching can't detect — e.g. UI bugs and feature requests.")
    adf = pd.DataFrame(aspects)
    special = adf[adf["category"].isin(["UI bug", "Feature request"])]
    if not special.empty:
        cols = st.columns(len(special) if len(special) <= 4 else 4)
        for i, (_, r) in enumerate(special.head(4).iterrows()):
            cols[i].metric(f"{r['category']} · {r['brand']}", int(r["n"]))
    st.dataframe(adf.rename(columns={"brand": "Brand", "category": "Aspect",
                                     "n": "Mentions", "negative": "Negative"}),
                 hide_index=True, use_container_width=True)

# ── Evidence: actual complaints behind the numbers ───────────────────────────
st.header("Read the actual complaints")
st.caption("The real text behind the charts — filtered by your sidebar selections.")
ex_brand = None if len(sel_brands) != 1 else sel_brands[0]
examples = example_complaints(
    conn,
    brand=ex_brand,
    theme=None if sel_theme == "All" else sel_theme,
    source=None if sel_source == "All" else sel_source,
    limit=8,
)
if examples:
    for e in examples:
        tag = " · ".join(t for t in [e["brand"], e["source"], e["week"]] if t)
        with st.expander(f"{tag}  —  {e['themes'] or 'general'}"):
            st.write(e["text"])
else:
    st.info("No matching complaints. Widen the filters (or pick a single brand for sharper results).")

# ── Ratings vs complaints ────────────────────────────────────────────────────
st.header("Complaints vs app rating")
rvc = ratings_vs_complaints(conn)
if rvc:
    st.dataframe(pd.DataFrame(rvc), hide_index=True, use_container_width=True)
    st.caption("A negative correlation means more complaints track lower ratings.")
else:
    st.info("No overlapping rating + complaint weeks yet. Run scripts/fetch_ratings.py daily.")

conn.close()
