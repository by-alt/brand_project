"""
Export scored data to dashboard/web_data.json (scripts/export_dashboard.py).

This bridges the pipeline to the *static* interactive dashboard (dashboard/index.html).
That dashboard is a single HTML file with no server — perfect to host on GitHub Pages or
any static host. On its own it shows built-in sample data; once this script writes
web_data.json next to it, the dashboard loads your REAL mentions instead and looks
identical, just with your numbers.

Run it after the pipeline (run_all.py calls it automatically):
    python scripts/export_dashboard.py
"""
import os
import sys
import json
import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.database import get_connection, init_db
from config.definitions import BRAND_CATEGORY, CATEGORIES


def build_payload(conn, max_weeks=10, max_text=200) -> dict:
    """Assemble the JSON payload the static dashboard expects.

    Shape: {weeks: [...], sets: {set: [brands]}, mentions: [{brand,set,week,source,
    sentiment,theme,isComplaint,text}, ...]}. Limited to the most recent `max_weeks`
    so the trend stays readable, and text is truncated for size.
    """
    rows = conn.execute(
        "SELECT brand, week, source, sentiment_label, is_complaint, themes, clean_text "
        "FROM scored_mentions WHERE week IS NOT NULL"
    ).fetchall()

    weeks = sorted({r[1] for r in rows if r[1]})[-max_weeks:]
    wkset = set(weeks)

    mentions = []
    for brand, week, source, label, is_complaint, themes, text in rows:
        if week not in wkset:
            continue
        # The static view uses one theme per mention (the first tagged) for its filter
        # and breakdown — keeps the client-side model simple.
        theme = themes.split("|")[0] if themes else None
        mentions.append({
            "brand": brand,
            "set": BRAND_CATEGORY.get(brand, "Other"),
            "week": week,
            "source": source,
            "sentiment": label,
            "theme": theme,
            "isComplaint": int(is_complaint or 0),
            "text": (text or "")[:max_text],
        })

    present = {m["brand"] for m in mentions}
    sets = {cat: [b for b in brands if b in present]
            for cat, brands in CATEGORIES.items()}
    sets = {cat: bs for cat, bs in sets.items() if bs}  # drop empty sets

    return {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "weeks": weeks,
        "sets": sets,
        "mentions": mentions,
    }


def main():
    conn = get_connection()
    init_db(conn)
    payload = build_payload(conn)
    conn.close()

    out = os.path.join(PROJECT_ROOT, "dashboard", "web_data.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"Exported {len(payload['mentions'])} mentions across "
          f"{len(payload['weeks'])} weeks → {out}")
    if not payload["mentions"]:
        print("  (no scored data yet — the dashboard will show its built-in sample until you collect data.)")


if __name__ == "__main__":
    main()
