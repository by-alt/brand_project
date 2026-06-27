"""
Offline tests for Google Play review ingestion (the high-volume source).

The most important check: a review never names the brand ("delivery was late"), so it
MUST be kept by the cleaner via source-awareness — whereas the same text from Reddit
would be dropped as a false match. No network needed (the fetch is mocked).

Run it with:   python tests/test_reviews.py
"""
import os
import sys
import tempfile
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from scripts.database import get_connection, init_db, insert_mention, count_by_brand
from scripts.fetch_reviews import store_reviews, run_fetch
from scripts.clean_data import run_clean
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


def _review(review_id, content, stars=2, helpful=0):
    return {
        "reviewId": review_id, "content": content, "score": stars,
        "thumbsUpCount": helpful, "userName": "someone",
        "at": datetime.now(timezone.utc),
    }


def test_store_reviews():
    print("\n[store reviews]")
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "rev.db"))
        init_db(conn)
        reviews = [_review("r1", "delivery was late and cold"),
                   _review("r2", "great app, fast and reliable")]
        added = store_reviews(conn, "Zomato", reviews)
        check("both reviews stored", added == 2)

        row = conn.execute(
            "SELECT id, type, source FROM mentions WHERE id='gp_r1'"
        ).fetchone()
        check("review id is prefixed with gp_", row[0] == "gp_r1")
        check("review type is 'review'", row[1] == "review")
        check("review source is 'google_play'", row[2] == "google_play")

        # Re-storing is de-duplicated.
        again = store_reviews(conn, "Zomato", reviews)
        check("re-storing the same reviews adds nothing", again == 0)
        conn.close()


def test_run_fetch_with_mock():
    print("\n[run_fetch with mock fetcher]")
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "rev2.db"))
        init_db(conn)
        # Fake fetcher returns 2 reviews for any app.
        fake = lambda app_id, count: [_review(f"{app_id}_a", "refund never came"),
                                      _review(f"{app_id}_b", "love it")]
        per_brand = run_fetch(conn, count=10, fetcher=fake)
        check("every brand got reviews", all(v == 2 for v in per_brand.values()))
        conn.close()


def test_reviews_survive_cleaning():
    print("\n[source-aware cleaning]")
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "rev3.db"))
        init_db(conn)

        # A review that does NOT name the brand.
        store_reviews(conn, "Zomato", [_review("keep1", "delivery was late and the food was cold")])
        # The SAME text, but as a Reddit mention (brand not in text).
        insert_mention(conn, {
            "id": "t1_drop", "brand": "Zomato", "type": "comment", "source": "reddit",
            "author": "u", "text": "delivery was late and the food was cold",
            "created_utc": int(datetime.now(timezone.utc).timestamp()), "score": 1,
            "subreddit": "india", "permalink": "/x", "fetched_at": "t",
        })

        stats = run_clean(conn)
        clean_counts = count_by_brand(conn, "clean_mentions")
        check("the review is KEPT despite not naming the brand", clean_counts.get("Zomato") == 1)
        check("the equivalent Reddit mention is dropped as a false match", stats["false_match"] == 1)

        # And the kept review scores + themes normally.
        run_score(conn)
        themed = conn.execute(
            "SELECT themes FROM scored_mentions WHERE id='gp_keep1'"
        ).fetchone()
        check("the review gets a Delivery theme", themed and "Delivery" in themed[0])
        conn.close()


def test_review_sentiment_uses_stars():
    print("\n[review sentiment from stars]")
    from scripts.analyze import sentiment_from_stars, score_mention

    # The star mapping itself.
    check("1 star -> negative", sentiment_from_stars(1)[1] == "negative")
    check("3 stars -> neutral", sentiment_from_stars(3)[1] == "neutral")
    check("5 stars -> positive", sentiment_from_stars(5)[1] == "positive")
    check("invalid star value falls back (None)", sentiment_from_stars(0) is None)
    # Regression: a non-numeric star value must NOT crash — it falls back to VADER.
    check("string star value does not crash, returns None", sentiment_from_stars("oops") is None)
    check("a string '5' is coerced, not crashed", sentiment_from_stars("5")[1] == "positive")
    check("score_mention survives a bad star value",
          score_mention("great service", "google_play", stars="bad")[1] == "positive")

    # The whole point: stars override misleading text for reviews.
    # Cheerful-sounding text but a 1-star rating should still be negative.
    _, label = score_mention("amazing love it so good", "google_play", stars=1)
    check("a 1-star review with cheerful text is still negative", label == "negative")

    # Reddit (no stars) still uses VADER on the text.
    _, label_reddit = score_mention("amazing love it so good", "reddit", stars=None)
    check("Reddit text still scored positive by VADER", label_reddit == "positive")


def test_stars_end_to_end():
    print("\n[stars through the pipeline]")
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "stars.db"))
        init_db(conn)
        # A 1-star review whose text alone might fool VADER.
        store_reviews(conn, "Zomato", [_review("s1", "the app is fine I guess it works", stars=1)])
        run_clean(conn)
        run_score(conn)
        row = conn.execute(
            "SELECT sentiment_label, is_complaint FROM scored_mentions WHERE id='gp_s1'"
        ).fetchone()
        check("1-star review scored negative via its rating", row[0] == "negative")
        check("and is therefore flagged as a complaint", row[1] == 1)
        conn.close()


if __name__ == "__main__":
    test_store_reviews()
    test_run_fetch_with_mock()
    test_reviews_survive_cleaning()
    test_review_sentiment_uses_stars()
    test_stars_end_to_end()
    print(f"\nAll {passed} checks passed.")
