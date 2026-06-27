"""
Phase 9 (Upgrade 5) — Validate that the system catches a known event.

We build a small synthetic dataset with a deliberate, known spike: several quiet weeks
of delivery complaints followed by one week where they jump. Then we run the real
cleaning + scoring + anomaly detection and check that the spike is flagged.

If the planted event is caught, the detector works. This runs on a throwaway database
and never touches your real data.

Usage:
    python scripts/validate.py
"""
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.database import get_connection, init_db, insert_mention
from scripts.clean_data import run_clean
from scripts.analyze import run_score
from scripts.insights import detect_anomalies

BRAND = "Zomato"
BASELINE_WEEKS = 5          # quiet weeks before the spike
BASELINE_PER_WEEK = 2       # delivery complaints per quiet week
SPIKE_COUNT = 10            # delivery complaints in the spike week


def _ts(weeks_ago: int) -> int:
    """A unix timestamp `weeks_ago` weeks before now (UTC)."""
    return int((datetime.now(timezone.utc) - timedelta(weeks=weeks_ago)).timestamp())


def build_synthetic(conn):
    """Plant quiet baseline weeks plus one obvious delivery-complaint spike.

    Each mention's text is made unique (a ref number) so the Phase 2 de-duplicator
    treats them as distinct complaints — just like real posts from different users.
    """
    rid = 0
    # Quiet baseline weeks (oldest first).
    for w in range(BASELINE_WEEKS, 0, -1):
        for _ in range(BASELINE_PER_WEEK):
            rid += 1
            insert_mention(conn, {
                "id": f"t1_base{rid}", "brand": BRAND, "type": "comment", "author": "user",
                "text": f"Zomato delivery was late again on order {rid}, not delivered on time",
                "created_utc": _ts(w), "score": 1, "subreddit": "india",
                "permalink": f"/x{rid}", "fetched_at": "t",
            })
    # The spike, in the most recent week.
    for _ in range(SPIKE_COUNT):
        rid += 1
        insert_mention(conn, {
            "id": f"t1_spike{rid}", "brand": BRAND, "type": "comment", "author": "user",
            "text": f"Zomato delivery never arrived for order {rid}, delayed for hours, rider missing",
            "created_utc": _ts(0), "score": 1, "subreddit": "india",
            "permalink": f"/x{rid}", "fetched_at": "t",
        })
    conn.commit()


def main():
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "validate.db"))
        init_db(conn)

        print(f"Planting {BASELINE_WEEKS} quiet weeks (~{BASELINE_PER_WEEK}/wk) "
              f"then a spike of {SPIKE_COUNT} delivery complaints...")
        build_synthetic(conn)
        run_clean(conn)
        run_score(conn)

        alerts = detect_anomalies(conn)
        caught = [a for a in alerts if a["brand"] == BRAND and a["theme"] == "Delivery"]

        if caught:
            a = caught[0]
            print(f"\nPASS — the detector caught the planted event:")
            print(f"  {a['brand']} 'Delivery' spiked to {a['count']} in {a['week']} "
                  f"(normal baseline ~{a['baseline']}).")
            print("\nThis demonstrates the alerting works end to end on a known event.")
        else:
            print("\nFAIL — the planted spike was NOT flagged. Check the anomaly settings.")
            sys.exit(1)

        conn.close()


if __name__ == "__main__":
    main()
