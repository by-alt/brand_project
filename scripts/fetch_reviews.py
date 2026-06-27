"""
Fetch Google Play REVIEW TEXT — the high-volume, on-topic complaint source.

Unlike Reddit (sparse, forward-only), app-store reviews are plentiful and span months
back, so the weekly trends and anomaly baselines have real depth immediately. Each
review is stored in the same `mentions` table (source='google_play'), so it flows
through the exact same cleaning, sentiment, and theme-tagging pipeline as Reddit.

Usage:
    python scripts/fetch_reviews.py                 # ~300 newest reviews per brand
    python scripts/fetch_reviews.py --count 1000

Requires `google-play-scraper` (in requirements.txt). The live fetch is isolated in
fetch_reviews_for_app() so the storage logic is testable with fake data.
"""
import os
import sys
import time
import argparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.definitions import APP_IDS
from scripts.database import get_connection, init_db, insert_mention


def fetch_reviews_for_app(app_id: str, count: int) -> list:
    """Fetch up to `count` newest reviews for one app from Google Play.

    Returns a list of dicts with: reviewId, content, score (stars), thumbsUpCount,
    userName, at (datetime). Isolated + lazily imported so tests don't need the package.

    Wrapped in retries: Google Play occasionally rate-limits or drops a request, and one
    transient failure shouldn't abort the brand's fetch.
    """
    from google_play_scraper import reviews, Sort
    from scripts.retry import with_retries

    @with_retries(max_attempts=3, base_delay=2.0)
    def _do():
        result, _ = reviews(
            app_id,
            lang="en",
            country="in",
            sort=Sort.NEWEST,
            count=count,
        )
        return result

    return _do()


def store_reviews(conn, brand: str, reviews_list: list) -> int:
    """Store a list of review dicts as raw mentions. Returns how many were new."""
    new_rows = 0
    for r in reviews_list:
        review_id = r.get("reviewId")
        content = r.get("content")
        at = r.get("at")
        if not review_id or not content or at is None:
            continue
        created_utc = int(at.timestamp()) if hasattr(at, "timestamp") else int(at)
        row = {
            "id": f"gp_{review_id}",
            "brand": brand,
            "type": "review",
            "source": "google_play",
            "author": r.get("userName"),
            "text": content,
            "created_utc": created_utc,
            "score": r.get("thumbsUpCount", 0),  # "helpful" votes, akin to upvotes
            "stars": r.get("score"),             # the 1-5 star rating = ground-truth sentiment
            "subreddit": None,                   # not applicable to reviews
            "permalink": None,
            "fetched_at": _now_iso(),
        }
        if insert_mention(conn, row):
            new_rows += 1
    return new_rows


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def run_fetch(conn, count, fetcher=fetch_reviews_for_app, sleep=time.sleep) -> dict:
    """Fetch and store reviews for every brand, politely.

    Between brands we pause for a configurable delay plus random jitter. Spacing
    requests out (rather than firing them back-to-back) is the most effective and
    fully legitimate way to avoid rate-limits/blocks — it's being a courteous client,
    not evading anything. `fetcher` and `sleep` are injectable for offline tests.
    """
    import random
    from config.definitions import FETCH_MIN_DELAY_SECONDS, FETCH_JITTER_SECONDS

    per_brand = {}
    brands = list(APP_IDS.items())
    for i, (brand, app_id) in enumerate(brands):
        try:
            reviews_list = fetcher(app_id, count)
        except Exception as e:
            print(f"  ! could not fetch reviews for {brand} ({app_id}): {e}")
            per_brand[brand] = 0
            continue
        added = store_reviews(conn, brand, reviews_list)
        per_brand[brand] = added
        flag = "  (got 0 — possible block or no new reviews)" if not reviews_list else ""
        print(f"  {brand:8} +{added} new reviews{flag}")

        # Pause before the next brand (skip after the last one).
        if i < len(brands) - 1:
            sleep(FETCH_MIN_DELAY_SECONDS + random.uniform(0, FETCH_JITTER_SECONDS))

    conn.commit()
    return per_brand


def main():
    from config.definitions import REVIEWS_PER_BRAND

    parser = argparse.ArgumentParser(description="Fetch Google Play reviews into the raw table.")
    parser.add_argument("--count", type=int, default=REVIEWS_PER_BRAND, help="newest reviews per brand")
    args = parser.parse_args()

    conn = get_connection()
    init_db(conn)
    print(f"Fetching up to {args.count} reviews per brand (polite mode: spacing requests)...")
    per_brand = run_fetch(conn, args.count)
    total = sum(per_brand.values())

    # Collection-health check: loudly flag brands that returned nothing, so a silent
    # block doesn't masquerade as a successful (but empty) run.
    empty = [b for b, n in per_brand.items() if n == 0]
    got = len(per_brand) - len(empty)
    print(f"\nDone. {total} new reviews across {got}/{len(per_brand)} brands.")
    if empty:
        print(f"  ! WARNING: no data for: {', '.join(empty)}")
        print("    If this is most/all brands, you're likely being rate-limited or blocked")
        print("    (common from cloud IPs). Try running from a home connection, or plug in")
        print("    a licensed data source — see docs/DATA_SOURCES.md.")
    conn.close()


if __name__ == "__main__":
    main()
