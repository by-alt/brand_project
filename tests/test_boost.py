"""
Offline tests for the boost layer: topic clustering, retries, and alerting.
No network, no API keys, no Docker needed.

Run:  python tests/test_boost.py
"""
import os
import sys
import time
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from scripts.retry import with_retries
from scripts.topics import discover_topics
from scripts.alerts import format_alert, check_and_alert
from scripts.database import get_connection, init_db, insert_clean
from scripts.analyze import run_score

passed = 0

def check(label, condition):
    global passed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        raise AssertionError(label)


def test_retry():
    print("\n[retry]")
    # Fails twice then succeeds — should retry and return the value. Injected sleep = instant.
    calls = {"n": 0}
    @with_retries(max_attempts=3, base_delay=0.01, sleep=lambda s: None)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        return "ok"
    check("retries until success", flaky() == "ok")
    check("called exactly 3 times", calls["n"] == 3)

    # Always fails — should exhaust attempts and re-raise the real error.
    attempts = {"n": 0}
    @with_retries(max_attempts=2, base_delay=0.01, sleep=lambda s: None)
    def broken():
        attempts["n"] += 1
        raise ValueError("nope")
    raised = False
    try:
        broken()
    except ValueError:
        raised = True
    check("re-raises after exhausting attempts", raised)
    check("stopped after max_attempts (2)", attempts["n"] == 2)

    # Only listed exceptions trigger a retry.
    only = {"n": 0}
    @with_retries(max_attempts=3, exceptions=(KeyError,), sleep=lambda s: None)
    def wrong_error():
        only["n"] += 1
        raise ValueError("not retried")
    try:
        wrong_error()
    except ValueError:
        pass
    check("does not retry unlisted exceptions", only["n"] == 1)


def test_topics():
    print("\n[topics]")
    # Too few documents -> no clusters (guards against meaningless output).
    check("returns [] when too little data", discover_topics(["a", "b"], n_clusters=3) == [])

    # Two clearly separable themes; clustering should find structure and top terms.
    delivery = ["delivery was late and cold"] * 8 + ["driver never arrived late delivery"] * 7
    refunds = ["refund not received money stuck"] * 8 + ["payment failed no refund"] * 7
    topics = discover_topics(delivery + refunds, n_clusters=2)
    check("produces clusters from enough data", len(topics) >= 1)
    all_terms = " ".join(t["term"] for top in topics for t in [{"term": x} for x in top["terms"]])
    check("surfaces meaningful terms (delivery/refund)",
          ("delivery" in all_terms or "refund" in all_terms or "late" in all_terms))
    check("each topic reports a size", all(t["size"] > 0 for t in topics))
    # Regression: homogeneous text used to crash the TF-IDF vectorizer ("no terms remain").
    check("homogeneous text returns [] instead of crashing",
          discover_topics(["identical complaint"] * 20, n_clusters=4) == [])


def test_alerts():
    print("\n[alerts]")
    anomaly = {"brand": "Zepto", "theme": "Refunds & payments", "week": "2026-W25",
               "count": 41, "baseline": 12.0, "threshold": 20.0}
    msg = format_alert(anomaly)
    check("alert names the brand and theme", "Zepto" in msg and "Refunds" in msg)
    check("alert shows the percent jump", "%" in msg)

    # check_and_alert with an injected sender: no network, capture what would be sent.
    sent = []
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "a.db"))
        init_db(conn)
        # No anomalies seeded -> nothing sent, no crash.
        msgs = check_and_alert(conn, sender=lambda url, m: sent.append(m), webhook_url="http://x")
        check("no anomalies -> no alerts sent", msgs == [] and sent == [])
        conn.close()

    # With no webhook configured and no sender -> dry run, still no crash.
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "b.db"))
        init_db(conn)
        msgs = check_and_alert(conn)  # no URL, no sender
        check("dry run is safe with no webhook", isinstance(msgs, list))
        conn.close()


def test_polite_fetch():
    print("\n[polite fetch + pluggable source]")
    from scripts.fetch_reviews import run_fetch
    from config.definitions import APP_IDS
    from datetime import datetime, timezone
    import tempfile
    from scripts.database import get_connection, init_db

    _now = datetime.now(timezone.utc)

    # A fake source returning one review per call — verifies the seam works without network.
    def fake_source(app_id, count):
        return [{"reviewId": f"{app_id}-1", "content": "late delivery", "score": 2,
                 "thumbsUpCount": 0, "userName": "u", "at": _now}]

    sleeps = []
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "f.db"))
        init_db(conn)
        per_brand = run_fetch(conn, 5, fetcher=fake_source, sleep=lambda s: sleeps.append(s))
        check("every brand fetched via the injected source", len(per_brand) == len(APP_IDS))
        check("each brand stored its review", all(v == 1 for v in per_brand.values()))
        # It should pause between brands but not after the last one.
        check("throttles between brands (n-1 sleeps)", len(sleeps) == len(APP_IDS) - 1)
        check("each sleep is a positive delay", all(s > 0 for s in sleeps))
        conn.close()

    # A source that returns nothing -> recorded as 0, no crash (the empty-block case).
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "e.db"))
        init_db(conn)
        per_brand = run_fetch(conn, 5, fetcher=lambda a, c: [], sleep=lambda s: None)
        check("empty source records 0 for every brand (no crash)",
              all(v == 0 for v in per_brand.values()))
        conn.close()

    # A source that raises for one brand must not abort the others.
    def flaky_source(app_id, count):
        if "zomato" in app_id:
            raise RuntimeError("blocked")
        return [{"reviewId": f"{app_id}-1", "content": "ok", "score": 5,
                 "thumbsUpCount": 0, "userName": "u", "at": _now}]
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "k.db"))
        init_db(conn)
        per_brand = run_fetch(conn, 5, fetcher=flaky_source, sleep=lambda s: None)
        check("one brand failing doesn't abort the rest", per_brand["Zomato"] == 0 and per_brand["Swiggy"] == 1)
        conn.close()


def test_safe_config_parsing():
    print("\n[config robustness]")
    from config.definitions import _env_float, _env_int
    import os as _os
    # Blank or invalid env must fall back to default, never crash.
    _os.environ["X_BLANK"] = ""
    _os.environ["X_BAD"] = "lots"
    _os.environ["X_OK"] = "2.5"
    check("blank env falls back to default", _env_float("X_BLANK", 4) == 4.0)
    check("invalid int falls back to default", _env_int("X_BAD", 200) == 200)
    check("valid env value is honored", _env_float("X_OK", 1) == 2.5)
    for k in ("X_BLANK", "X_BAD", "X_OK"):
        _os.environ.pop(k, None)


def test_example_complaints():
    print("\n[example complaints]")
    import tempfile, time
    from scripts.database import get_connection, init_db, insert_clean
    from scripts.analyze import run_score
    from scripts.insights import example_complaints
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "ex.db"))
        init_db(conn)
        def mk(id, brand, text):
            return {"id": id, "brand": brand, "type": "review", "source": "google_play",
                    "author": "u", "raw_text": text, "clean_text": text,
                    "created_utc": int(time.time()), "score": 0, "stars": 1,
                    "subreddit": None, "permalink": None, "fetched_at": "t"}
        insert_clean(conn, mk("a", "Zepto", "refund never came, charged twice"))
        insert_clean(conn, mk("b", "Flipkart", "fake product, want to return this"))
        run_score(conn)
        check("returns real complaint text", len(example_complaints(conn)) >= 1)
        check("filters by brand", all(e["brand"] == "Zepto" for e in example_complaints(conn, brand="Zepto")))
        check("filters by theme",
              any("Flipkart" == e["brand"] for e in example_complaints(conn, theme="Returns & replacement")))
        conn.close()


def test_dashboard_export():
    print("\n[dashboard export]")
    import tempfile, time, json
    from scripts.database import get_connection, init_db, insert_clean
    from scripts.analyze import run_score
    from scripts.export_dashboard import build_payload
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "x.db"))
        init_db(conn)
        def mk(id, brand, text):
            return {"id": id, "brand": brand, "type": "review", "source": "google_play",
                    "author": "u", "raw_text": text, "clean_text": text,
                    "created_utc": int(time.time()), "score": 0, "stars": 1,
                    "subreddit": None, "permalink": None, "fetched_at": "t"}
        insert_clean(conn, mk("a", "Zepto", "refund never came charged twice"))
        insert_clean(conn, mk("b", "Flipkart", "fake product want to return this"))
        run_score(conn)
        p = build_payload(conn)
        check("payload has weeks/sets/mentions", all(k in p for k in ("weeks", "sets", "mentions")))
        check("mentions carry the expected fields",
              all(k in p["mentions"][0] for k in ("brand", "set", "week", "source", "sentiment", "theme", "isComplaint", "text")))
        check("payload is JSON-serializable", bool(json.dumps(p)))
        check("brands grouped into competitive sets", "E-commerce" in p["sets"] and "Food & quick commerce" in p["sets"])
        conn.close()


if __name__ == "__main__":
    test_retry()
    test_topics()
    test_alerts()
    test_polite_fetch()
    test_safe_config_parsing()
    test_example_complaints()
    test_dashboard_export()
    print(f"\nAll {passed} checks passed.")
