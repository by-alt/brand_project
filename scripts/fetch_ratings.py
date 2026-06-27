"""
Phase 5 (Upgrade 1) — Fetch Google Play ratings as the real outcome metric.

Records one rating snapshot per brand per day into `app_ratings`. Run daily (it's
wired into the automated pipeline), and over time you build a rating time series to
test against complaint trends: do complaint spikes lead rating drops?

Usage:
    python scripts/fetch_ratings.py

Requires the `google-play-scraper` package (in requirements.txt). The live fetch is
isolated in fetch_play_rating() so the storage logic can be tested with fake data.
"""
import os
import sys
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.definitions import APP_IDS
from scripts.database import get_connection, init_db, insert_rating


def fetch_play_rating(app_id: str) -> dict:
    """Fetch the current rating + review count for one app from Google Play.

    Isolated here (and importing the scraper lazily) so the rest of the module can be
    imported and tested without the package or network.
    """
    from google_play_scraper import app  # lazy import

    info = app(app_id, lang="en", country="in")
    return {"rating": info.get("score"), "num_reviews": info.get("ratings")}


def snapshot_ratings(conn, fetcher=fetch_play_rating) -> dict:
    """Capture today's rating for every brand. `fetcher` is injectable for tests."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    captured = {}
    for brand, app_id in APP_IDS.items():
        try:
            result = fetcher(app_id)
        except Exception as e:
            print(f"  ! could not fetch rating for {brand} ({app_id}): {e}")
            continue
        insert_rating(
            conn,
            {
                "brand": brand,
                "captured_date": today,
                "rating": result.get("rating"),
                "num_reviews": result.get("num_reviews"),
                "source": "google_play",
            },
        )
        captured[brand] = result.get("rating")
    conn.commit()
    return captured


def main():
    conn = get_connection()
    init_db(conn)
    print("Fetching Google Play ratings...")
    captured = snapshot_ratings(conn)
    if captured:
        print("Today's ratings:")
        for brand, rating in sorted(captured.items()):
            print(f"  {brand:8} {rating}")
    else:
        print("No ratings captured (check your network / app IDs).")
    conn.close()


if __name__ == "__main__":
    main()
