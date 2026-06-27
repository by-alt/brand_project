"""
Phase 4 — Run the whole pipeline in order: pull -> ratings -> clean -> score.

This is what the daily GitHub Action calls, and it's handy locally too:

    python scripts/run_all.py

Each stage is wrapped so one failing stage reports clearly without losing the others'
progress (the DB is committed as each stage completes).
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.database import get_connection, init_db
from scripts import pull_data, clean_data, analyze, fetch_ratings, fetch_reviews


def main():
    conn = get_connection()
    init_db(conn)

    print("[1/5] Pulling Reddit mentions...")
    try:
        reddit = pull_data.build_reddit()
        pull_data.run_pull(reddit, conn)
    except pull_data.RedditAccessError as e:
        print(f"  stopped: {e}")
    except Exception as e:
        print(f"  pull failed: {e}")

    print("[2/5] Fetching Google Play reviews...")
    try:
        from config.definitions import REVIEWS_PER_BRAND
        fetch_reviews.run_fetch(conn, count=REVIEWS_PER_BRAND)
    except Exception as e:
        print(f"  reviews failed: {e}")

    print("[3/5] Fetching app ratings...")
    try:
        fetch_ratings.snapshot_ratings(conn)
    except Exception as e:
        print(f"  ratings failed: {e}")

    print("[4/5] Cleaning...")
    clean_data.run_clean(conn)

    print("[5/5] Scoring + tagging...")
    analyze.run_score(conn)

    # Refresh the static dashboard's data file so a hosted dashboard/ folder shows the
    # latest numbers. Safe to fail — the dashboard falls back to its built-in sample.
    try:
        from scripts import export_dashboard
        payload = export_dashboard.build_payload(conn)
        import json
        out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "dashboard", "web_data.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        print(f"  exported {len(payload['mentions'])} mentions to dashboard/web_data.json")
    except Exception as e:
        print(f"  dashboard export skipped: {e}")

    print("Pipeline complete.")
    conn.close()


if __name__ == "__main__":
    main()
