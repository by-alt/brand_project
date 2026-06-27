"""
Phases 5-8 — Insights built on top of the scored data.

  - ratings_vs_complaints : does a brand's complaint share track its app rating? (P5)
  - detect_anomalies      : flag complaint themes spiking above their normal range (P6)
  - root_cause            : when a brand's sentiment drops, explain why (P7)
  - benchmark             : compare brands head-to-head (P8)

Pure SQL + standard library (no pandas), so it stays light and testable.

Usage:
    python scripts/insights.py
"""
import os
import sys
import statistics
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.definitions import ANOMALY_ROLLING_WEEKS, ANOMALY_STD_MULTIPLIER
from scripts.database import get_connection, init_db, count_by_brand
from scripts.analyze import brand_summary, theme_breakdown

# A spike must clear this absolute floor too, so tiny baselines don't over-trigger.
_ANOMALY_MIN_COUNT = 3


def _pearson(xs, ys):
    """Pearson correlation, or None if undefined (too few points / no variance)."""
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _date_to_week(date_str: str) -> str:
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%G-W%V")


# --- Phase 5: ratings vs complaints -------------------------------------------

def weekly_complaint_share(conn, source=None) -> dict:
    """{brand: {week: complaint_share}} using the broad is_complaint flag.

    Pass source='reddit' to use only Reddit complaints — useful for the rating
    correlation, so we don't correlate Play-review complaints against the Play
    rating (which would be somewhat circular).
    """
    query = ("SELECT brand, week, COUNT(*) AS total, SUM(is_complaint) AS complaints "
             "FROM scored_mentions")
    params = ()
    if source:
        query += " WHERE source = ?"
        params = (source,)
    query += " GROUP BY brand, week"

    rows = conn.execute(query, params).fetchall()
    out = {}
    for brand, week, total, complaints in rows:
        if total:
            out.setdefault(brand, {})[week] = (complaints or 0) / total
    return out


def weekly_rating(conn) -> dict:
    """{brand: {week: avg_rating}} from the daily rating snapshots."""
    out = {}
    for brand, date, rating, *_ in conn.execute(
        "SELECT brand, captured_date, rating FROM app_ratings WHERE rating IS NOT NULL"
    ):
        out.setdefault(brand, {}).setdefault(_date_to_week(date), []).append(rating)
    return {b: {w: sum(v) / len(v) for w, v in weeks.items()} for b, weeks in out.items()}


def ratings_vs_complaints(conn, source="reddit") -> list:
    """Per brand, correlate weekly complaint share with weekly app rating.

    Defaults to Reddit complaints vs Play ratings — an independent cross-source check
    ("do Reddit complaints track the app's star rating?"). Pass source=None to pool
    all complaint sources instead.
    """
    shares = weekly_complaint_share(conn, source=source)
    ratings = weekly_rating(conn)
    results = []
    for brand in sorted(set(shares) & set(ratings)):
        weeks = sorted(set(shares[brand]) & set(ratings[brand]))
        xs = [shares[brand][w] for w in weeks]
        ys = [ratings[brand][w] for w in weeks]
        r = _pearson(xs, ys)
        results.append({
            "brand": brand,
            "weeks": len(weeks),
            "correlation": round(r, 3) if r is not None else None,
            "note": ("more complaints track lower ratings" if r is not None and r < -0.3
                     else "no clear relationship yet" if r is not None
                     else "need at least ~3 overlapping weeks"),
        })
    return results


# --- Phase 6: anomaly detection -----------------------------------------------

def weekly_theme_counts(conn) -> dict:
    """{(brand, theme): [(week, count), ...]} ordered by week."""
    rows = conn.execute(
        """SELECT s.brand, mt.theme, s.week, COUNT(*) AS n
           FROM mention_themes mt
           JOIN scored_mentions s ON mt.id = s.id AND mt.brand = s.brand
           GROUP BY s.brand, mt.theme, s.week
           ORDER BY s.week"""
    ).fetchall()
    series = {}
    for brand, theme, week, n in rows:
        series.setdefault((brand, theme), []).append((week, n))
    return series


def detect_anomalies(conn) -> list:
    """Flag (brand, theme, week) where the count spikes above its recent normal range.

    Normal range = trailing-window mean + (multiplier x std). Requires the absolute
    count to also clear a small floor so tiny baselines don't over-trigger.
    """
    alerts = []
    for (brand, theme), series in weekly_theme_counts(conn).items():
        counts = [n for _, n in series]
        for i in range(ANOMALY_ROLLING_WEEKS, len(series)):
            window = counts[i - ANOMALY_ROLLING_WEEKS:i]
            if len(window) < 2:
                continue
            mean = statistics.mean(window)
            std = statistics.pstdev(window)
            threshold = mean + ANOMALY_STD_MULTIPLIER * std
            week, count = series[i]
            if count > threshold and count >= _ANOMALY_MIN_COUNT:
                alerts.append({
                    "brand": brand, "theme": theme, "week": week, "count": count,
                    "baseline": round(mean, 1), "threshold": round(threshold, 1),
                })
    return alerts


# --- Phase 7: root-cause drill-down -------------------------------------------

def _brand_weekly_sentiment(conn, brand):
    rows = conn.execute(
        """SELECT week, COUNT(*) AS total,
                  SUM(sentiment_label='positive') AS pos,
                  SUM(sentiment_label='negative') AS neg
           FROM scored_mentions WHERE brand = ? GROUP BY week ORDER BY week""",
        (brand,),
    ).fetchall()
    return [
        {"week": w, "total": t, "net": ((p or 0) - (n or 0)) / t if t else 0.0}
        for w, t, p, n in rows
    ]


def root_cause(conn, brand) -> dict:
    """Explain a brand's most recent week-over-week sentiment move."""
    weekly = _brand_weekly_sentiment(conn, brand)
    if len(weekly) < 2:
        return {"brand": brand, "status": "insufficient_data"}

    last, prev = weekly[-1], weekly[-2]
    delta = round(last["net"] - prev["net"], 3)

    # Which themes grew most from the previous week to the latest week?
    theme_series = weekly_theme_counts(conn)
    growth = []
    for (b, theme), series in theme_series.items():
        if b != brand:
            continue
        by_week = dict(series)
        change = by_week.get(last["week"], 0) - by_week.get(prev["week"], 0)
        if change > 0:
            growth.append({"theme": theme, "increase": change})
    growth.sort(key=lambda x: x["increase"], reverse=True)

    status = "drop" if delta < 0 else "stable_or_up"
    if status == "drop" and growth:
        top = growth[0]
        summary = (f"{brand} net sentiment fell {abs(delta):.2f} in {last['week']}, "
                   f"led by a rise in '{top['theme']}' complaints (+{top['increase']}).")
    elif status == "drop":
        summary = f"{brand} net sentiment fell {abs(delta):.2f} in {last['week']}."
    else:
        summary = f"{brand} net sentiment held steady or rose in {last['week']}."

    return {
        "brand": brand, "status": status, "week": last["week"],
        "net_change": delta, "top_theme_increases": growth[:3], "summary": summary,
    }


# --- Phase 8: competitive benchmarking ----------------------------------------

def benchmark(conn) -> list:
    """Rank brands best-to-worst by net sentiment, with their worst complaint theme
    and competitive set (category)."""
    from config.definitions import BRAND_CATEGORY

    top_theme = {}
    for row in theme_breakdown(conn):  # already ordered by count desc within brand
        top_theme.setdefault(row["brand"], row["theme"])

    rows = brand_summary(conn)
    for r in rows:
        r["top_theme"] = top_theme.get(r["brand"], "-")
        r["category"] = BRAND_CATEGORY.get(r["brand"], "Other")
    return sorted(rows, key=lambda r: r["net_sentiment"], reverse=True)


def benchmark_by_category(conn) -> dict:
    """Benchmark grouped into competitive sets: {category: [ranked brand rows]}."""
    grouped = {}
    for row in benchmark(conn):
        grouped.setdefault(row["category"], []).append(row)
    return grouped


def example_complaints(conn, brand=None, theme=None, source=None, limit=5) -> list:
    """Return actual recent complaint texts — the evidence behind the numbers.

    Optionally filtered by brand, theme (matched against the stored theme list), and
    source ('reddit' / 'google_play'). Newest first. This is what turns "refunds are up"
    into "refunds are up, and here are the actual reviews saying so".
    """
    q = ["SELECT brand, clean_text, source, week, themes FROM scored_mentions",
         "WHERE is_complaint = 1 AND clean_text IS NOT NULL AND length(clean_text) > 0"]
    params = []
    if brand:
        q.append("AND brand = ?"); params.append(brand)
    if source:
        q.append("AND source = ?"); params.append(source)
    if theme:
        q.append("AND themes LIKE ?"); params.append(f"%{theme}%")
    q.append("ORDER BY created_utc DESC LIMIT ?"); params.append(limit)
    rows = conn.execute(" ".join(q), params).fetchall()
    cols = ["brand", "text", "source", "week", "themes"]
    return [dict(zip(cols, r)) for r in rows]


def aspect_breakdown(conn) -> list:
    """LLM aspect categories per brand, negative-first (richer than keyword themes).

    Empty unless ABSA ran (i.e. an LLM key was configured). Surfaces categories that
    keywords can't catch, like 'UI bug' and 'Feature request'.
    """
    rows = conn.execute(
        """
        SELECT brand, category,
               COUNT(*)                              AS n,
               SUM(sentiment = 'negative')           AS negative
        FROM mention_aspects
        GROUP BY brand, category
        ORDER BY brand, negative DESC, n DESC
        """
    ).fetchall()
    return [{"brand": b, "category": c, "n": n, "negative": neg or 0} for b, c, n, neg in rows]


def main():
    conn = get_connection()
    init_db(conn)
    if not count_by_brand(conn, "scored_mentions"):
        print("No scored data found. Run scripts/analyze.py first.")
        conn.close()
        return

    print("=== Competitive benchmark (by set, best to worst net sentiment) ===")
    for category, rows in benchmark_by_category(conn).items():
        print(f"  [{category}]")
        for r in rows:
            print(f"    {r['brand']:9} net={r['net_sentiment']:+.2f}  "
                  f"complaint%={r['complaint_rate']*100:>4.0f}  worst: {r['top_theme']}")

    print("\n=== Anomaly alerts (theme spikes) ===")
    alerts = detect_anomalies(conn)
    if alerts:
        for a in alerts:
            print(f"  ⚠ {a['brand']} — '{a['theme']}' spiked to {a['count']} in "
                  f"{a['week']} (normal ~{a['baseline']})")
    else:
        print("  none (need several weeks of data before spikes can show)")

    print("\n=== Root-cause for latest week ===")
    for brand in sorted(count_by_brand(conn, "scored_mentions")):
        rc = root_cause(conn, brand)
        if rc["status"] in ("drop", "stable_or_up"):
            print(f"  {rc['summary']}")

    print("\n=== Ratings vs complaints ===")
    rc_list = ratings_vs_complaints(conn)
    if rc_list:
        for r in rc_list:
            corr = r["correlation"]
            corr_s = f"{corr:+.2f}" if corr is not None else "n/a"
            print(f"  {r['brand']:8} r={corr_s} over {r['weeks']} wk — {r['note']}")
    else:
        print("  no overlapping rating + complaint weeks yet (run fetch_ratings daily)")
    conn.close()


if __name__ == "__main__":
    main()
