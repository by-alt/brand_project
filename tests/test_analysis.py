"""
Offline tests for the Phase 3 analysis layer.

Verifies sentiment labelling, whole-word theme tagging, the complaint flag, and the
SQL aggregations — by seeding clean_mentions and running the scorer. VADER is
rule-based and deterministic, so these assertions are stable. No network needed.

Run it with:   python tests/test_analysis.py
"""
import os
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from scripts.database import get_connection, init_db, insert_clean, count_by_brand
from scripts.analyze import (
    score_sentiment,
    tag_themes,
    iso_week,
    run_score,
    brand_summary,
    theme_breakdown,
)

passed = 0

def check(label, condition):
    global passed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        raise AssertionError(label)


def _clean(id, brand, text):
    return {
        "id": id, "brand": brand, "type": "comment", "author": "u",
        "raw_text": text, "clean_text": text,
        "created_utc": 1_700_000_000, "score": 1,
        "subreddit": "india", "permalink": f"/r/india/{id}",
        "fetched_at": "2026-01-01T00:00:00Z",
    }


def test_sentiment():
    print("\n[sentiment]")
    check("clearly positive text -> positive", score_sentiment("absolutely love this, fantastic")[1] == "positive")
    check("clearly negative text -> negative", score_sentiment("terrible, worst experience, awful")[1] == "negative")
    check("neutral/factual text -> neutral", score_sentiment("the order id is 12345")[1] == "neutral")
    check("compound is within -1..+1", -1 <= score_sentiment("meh")[0] <= 1)


def test_themes():
    print("\n[theme tagging]")
    check("'delivery was late' -> Delivery", "Delivery" in tag_themes("delivery was late"))
    check("'asked for a refund' -> Refunds & payments", "Refunds & payments" in tag_themes("i asked for a refund"))
    check("'app keeps crashing' -> App & tech", "App & tech" in tag_themes("the app keeps crashing"))
    check("multiple themes can co-occur",
          set(tag_themes("late delivery and the app had a bug")) >= {"Delivery", "App & tech"})
    # The whole-word regression: 'late' must NOT match inside 'plate'.
    check("'plate' does NOT trigger the 'late' keyword", "Delivery" not in tag_themes("the plate looked nice"))
    check("clean text with no keywords -> no themes", tag_themes("just a normal neutral sentence") == [])


def test_iso_week():
    print("\n[week]")
    wk = iso_week(1_700_000_000)  # 2023-11-14 UTC
    check("week is formatted like YYYY-Www", len(wk) == 8 and "-W" in wk)


def test_scoring_and_aggregation():
    print("\n[run_score + aggregation]")
    rows = [
        _clean("t1_a", "Zomato", "absolutely love Zomato, fantastic service"),   # positive, no theme
        _clean("t1_b", "Zomato", "Zomato delivery was late and food was cold"),  # negative + Delivery
        _clean("t1_c", "Zomato", "asked Zomato for a refund, still waiting"),     # theme: refunds
        _clean("t1_d", "Swiggy", "Swiggy is the worst, terrible app crashes"),    # negative + App & tech
    ]
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "score.db"))
        init_db(conn)
        for r in rows:
            insert_clean(conn, r)

        stats = run_score(conn)
        check("all 4 mentions are scored", stats["scored"] == 4)
        check("scored table is populated", count_by_brand(conn, "scored_mentions").get("Zomato") == 3)

        # is_complaint: b (negative), c (refund theme), d (negative) = 3 complaints.
        check("three mentions flagged as complaints", stats["complaints"] == 3)

        summary = {r["brand"]: r for r in brand_summary(conn)}
        check("Zomato total is 3", summary["Zomato"]["total"] == 3)
        check("net sentiment is between -1 and 1",
              all(-1 <= s["net_sentiment"] <= 1 for s in summary.values()))
        check("complaint rate is a valid proportion",
              all(0 <= s["complaint_rate"] <= 1 for s in summary.values()))

        themes = {(t["brand"], t["theme"]) for t in theme_breakdown(conn)}
        check("Delivery theme recorded for Zomato", ("Zomato", "Delivery") in themes)
        check("App & tech theme recorded for Swiggy", ("Swiggy", "App & tech") in themes)

        # Idempotent rebuild.
        stats2 = run_score(conn)
        check("re-scoring is idempotent", stats2["scored"] == stats["scored"])
        conn.close()


if __name__ == "__main__":
    test_sentiment()
    test_themes()
    test_iso_week()
    test_scoring_and_aggregation()
    print(f"\nAll {passed} checks passed.")
