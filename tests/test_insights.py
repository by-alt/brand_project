"""
Offline tests for the Phase 5-8 insights.

Seeds scored data (and ratings) directly, then checks anomaly detection, root-cause,
benchmarking, and the ratings correlation. No network needed.

Run it with:   python tests/test_insights.py
"""
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from scripts.database import (
    get_connection, init_db, insert_clean, insert_rating,
)
from scripts.analyze import run_score
from scripts.insights import (
    _pearson, detect_anomalies, root_cause, benchmark, ratings_vs_complaints,
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


def _ts(weeks_ago):
    return int((datetime.now(timezone.utc) - timedelta(weeks=weeks_ago)).timestamp())


def _clean(id, brand, text, created_utc):
    return {
        "id": id, "brand": brand, "type": "comment", "author": "u",
        "raw_text": text, "clean_text": text, "created_utc": created_utc,
        "score": 1, "subreddit": "india", "permalink": f"/{id}", "fetched_at": "t",
    }


def test_pearson():
    print("\n[pearson]")
    check("perfect positive correlation ~ 1", round(_pearson([1, 2, 3], [2, 4, 6]), 3) == 1.0)
    check("perfect negative correlation ~ -1", round(_pearson([1, 2, 3], [6, 4, 2]), 3) == -1.0)
    check("no variance returns None", _pearson([1, 1, 1], [2, 3, 4]) is None)


def test_anomaly_detection():
    print("\n[anomaly detection]")
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "anom.db"))
        init_db(conn)
        rid = 0
        # 5 quiet weeks: 2 delivery complaints each.
        for w in range(5, 0, -1):
            for _ in range(2):
                rid += 1
                insert_clean(conn, _clean(f"b{rid}", "Zomato", "delivery was late", _ts(w)))
        # spike week: 10 delivery complaints.
        for _ in range(10):
            rid += 1
            insert_clean(conn, _clean(f"s{rid}", "Zomato", "delivery never arrived, delayed", _ts(0)))
        run_score(conn)

        alerts = detect_anomalies(conn)
        hit = [a for a in alerts if a["brand"] == "Zomato" and a["theme"] == "Delivery"]
        check("the planted delivery spike is flagged", len(hit) == 1)
        check("the flagged count matches the spike", hit and hit[0]["count"] == 10)
        conn.close()


def test_root_cause():
    print("\n[root cause]")
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "rc.db"))
        init_db(conn)
        # last week: mostly negative delivery; previous week: positive.
        insert_clean(conn, _clean("p1", "Swiggy", "love Swiggy, fantastic and fast", _ts(1)))
        insert_clean(conn, _clean("p2", "Swiggy", "Swiggy is great", _ts(1)))
        for i in range(4):
            insert_clean(conn, _clean(f"n{i}", "Swiggy", "Swiggy delivery was late and terrible", _ts(0)))
        run_score(conn)

        rc = root_cause(conn, "Swiggy")
        check("a sentiment drop is detected", rc["status"] == "drop")
        check("Delivery is identified as a top driver",
              any(t["theme"] == "Delivery" for t in rc["top_theme_increases"]))
        check("a human-readable summary is produced", "Swiggy" in rc["summary"])
        conn.close()


def test_benchmark():
    print("\n[benchmark]")
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "bench.db"))
        init_db(conn)
        insert_clean(conn, _clean("g1", "Zomato", "absolutely love Zomato, fantastic", _ts(0)))
        insert_clean(conn, _clean("b1", "Swiggy", "Swiggy is terrible, worst, delivery late", _ts(0)))
        run_score(conn)

        ranked = benchmark(conn)
        check("benchmark returns all brands present", {r["brand"] for r in ranked} == {"Zomato", "Swiggy"})
        check("happier brand ranks first", ranked[0]["brand"] == "Zomato")
        check("each row carries a worst theme field", all("top_theme" in r for r in ranked))
        conn.close()


def test_ratings_correlation():
    print("\n[ratings vs complaints]")
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "rat.db"))
        init_db(conn)
        # Build 3 weeks where higher complaints coincide with lower ratings.
        plans = [(3, "great service love it", 4.5),      # low complaints, high rating
                 (2, "delivery late terrible refund", 4.0),
                 (1, "worst delivery never arrived, refund", 3.5)]  # high complaints, low rating
        rid = 0
        for weeks_ago, text, rating in plans:
            for _ in range(3):
                rid += 1
                insert_clean(conn, _clean(f"c{rid}", "Zomato", text, _ts(weeks_ago)))
            date = (datetime.now(timezone.utc) - timedelta(weeks=weeks_ago)).strftime("%Y-%m-%d")
            insert_rating(conn, {"brand": "Zomato", "captured_date": date,
                                 "rating": rating, "num_reviews": 1000, "source": "test"})
        run_score(conn)

        res = {r["brand"]: r for r in ratings_vs_complaints(conn)}
        check("Zomato has a correlation result", "Zomato" in res)
        check("correlation is computed over the overlapping weeks", res["Zomato"]["weeks"] >= 2)
        conn.close()


if __name__ == "__main__":
    test_pearson()
    test_anomaly_detection()
    test_root_cause()
    test_benchmark()
    test_ratings_correlation()
    print(f"\nAll {passed} checks passed.")
