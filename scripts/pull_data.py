"""
Phase 1 — Pull recent Reddit mentions of the tracked brands into SQLite.

Usage:
    python scripts/pull_data.py                 # default: 50 posts/term, last month
    python scripts/pull_data.py --limit 100 --comments 30 --time week

Requires a filled-in .env (see docs/REDDIT_SETUP.md).

Design notes:
  - Brand matching is WHOLE-WORD (regex \\b...\\b), so "Zomato" does NOT match
    inside "zomatouniverse". This honors the definition in docs/METRICS.md.
  - The network-free parts (matching, row extraction, storage) are separated from
    the Reddit calls so they can be unit-tested with mock data (see tests/).
  - praw is imported lazily inside build_reddit() so this module can be imported
    and tested without praw installed or credentials present.
"""
import os
import sys
import re
import argparse
from datetime import datetime, timezone

# Make project root importable so `config` and `scripts` packages resolve.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.credentials import get_reddit_credentials
from config.definitions import BRANDS, SUBREDDITS
from scripts.database import (
    get_connection,
    init_db,
    insert_mention,
    count_by_brand,
)

# Pre-compile one case-insensitive, word-boundary pattern per brand variant.
_BRAND_PATTERNS = {
    brand: [re.compile(r"\b" + re.escape(v) + r"\b", re.IGNORECASE) for v in variants]
    for brand, variants in BRANDS.items()
}


class RedditAccessError(RuntimeError):
    """Raised when Reddit rejects our requests outright (usually bad credentials)."""


# Substrings that signal a fatal auth/connection problem (vs. a one-off hiccup).
_FATAL_SIGNS = ("401", "403", "unauthorized", "oauth", "invalid_grant", "forbidden")


def _looks_fatal(err: Exception) -> bool:
    """True if the error looks like an auth/credentials problem we shouldn't retry."""
    return any(sign in str(err).lower() for sign in _FATAL_SIGNS)


def matched_brands(text: str) -> list:
    """Return the list of brands whose name/variant appears as a whole word in text."""
    if not text:
        return []
    return [
        brand
        for brand, patterns in _BRAND_PATTERNS.items()
        if any(p.search(text) for p in patterns)
    ]


def _author_name(obj) -> str:
    """Return the author's username, or None if deleted/unavailable.

    praw exposes .author as a Redditor (with .name) or None for deleted authors.
    Mocks without an author attribute also resolve safely to None.
    """
    author = getattr(obj, "author", None)
    return getattr(author, "name", None) if author is not None else None


def extract_post_row(submission) -> dict:
    """Turn a praw submission (or mock with the same attributes) into a base row."""
    title = submission.title or ""
    body = getattr(submission, "selftext", "") or ""
    return {
        "id": f"t3_{submission.id}",
        "type": "post",
        "source": "reddit",
        "author": _author_name(submission),
        "text": (title + " " + body).strip(),
        "created_utc": int(submission.created_utc),
        "score": int(submission.score),
        "subreddit": str(submission.subreddit),
        "permalink": getattr(submission, "permalink", ""),
    }


def extract_comment_row(comment) -> dict:
    """Turn a praw comment (or mock) into a base row."""
    return {
        "id": f"t1_{comment.id}",
        "type": "comment",
        "source": "reddit",
        "author": _author_name(comment),
        "text": comment.body or "",
        "created_utc": int(comment.created_utc),
        "score": int(comment.score),
        "subreddit": str(comment.subreddit),
        "permalink": getattr(comment, "permalink", ""),
    }


def store_item(conn, base_row: dict, fetched_at: str):
    """
    Tag a base row with every brand it mentions and insert one row per brand.
    Returns (brands_matched, new_rows_added).
    """
    brands = matched_brands(base_row["text"])
    new_rows = 0
    for brand in brands:
        row = dict(base_row, brand=brand, fetched_at=fetched_at)
        if insert_mention(conn, row):
            new_rows += 1
    return brands, new_rows


def build_reddit():
    """Construct a read-only praw client from credentials in .env."""
    import praw  # imported lazily so tests don't need praw

    creds = get_reddit_credentials()
    return praw.Reddit(
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
        user_agent=creds["user_agent"],
        check_for_async=False,
    )


def run_pull(reddit, conn, limit_per_term=50, comment_cap=20, time_filter="month"):
    """
    Search the configured subreddits for each brand variant, store matching posts
    and their top comments. Returns the number of new rows added.
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    multi = reddit.subreddit("+".join(SUBREDDITS))
    search_terms = sorted({v for variants in BRANDS.values() for v in variants})

    seen_submissions = set()
    total_new = 0

    for term in search_terms:
        try:
            # praw's search is lazy — the network call happens *while we iterate*,
            # so the loop has to live inside this try, not just the search() call.
            for submission in multi.search(
                term, sort="new", time_filter=time_filter, limit=limit_per_term
            ):
                if submission.id in seen_submissions:
                    continue
                seen_submissions.add(submission.id)

                # Isolate each submission so one bad post can't abort the term.
                try:
                    _, n = store_item(conn, extract_post_row(submission), fetched_at)
                    total_new += n

                    # Top-level comments only (no deep expansion), up to the cap.
                    submission.comments.replace_more(limit=0)
                    for comment in submission.comments.list()[:comment_cap]:
                        _, n = store_item(conn, extract_comment_row(comment), fetched_at)
                        total_new += n
                except Exception as e:
                    print(f"  ! skipped a post ({getattr(submission, 'id', '?')}): {e}")
        except RedditAccessError:
            raise  # already a clear message; let it bubble up
        except Exception as e:
            if _looks_fatal(e):
                raise RedditAccessError(
                    "Reddit rejected the request — this is almost always a credentials "
                    "problem. Double-check the three values in your .env file "
                    "(see docs/REDDIT_SETUP.md)."
                ) from e
            # Otherwise treat it as a transient, per-term hiccup and keep going.
            print(f"  ! search failed for '{term}': {e}")

        conn.commit()

    return total_new


def main():
    parser = argparse.ArgumentParser(description="Pull Reddit brand mentions into SQLite.")
    parser.add_argument("--limit", type=int, default=50, help="max posts per search term")
    parser.add_argument("--comments", type=int, default=20, help="max comments per post")
    parser.add_argument(
        "--time",
        default="month",
        choices=["day", "week", "month", "year", "all"],
        help="how far back to search",
    )
    args = parser.parse_args()

    conn = get_connection()
    init_db(conn)

    print("Connecting to Reddit...")
    try:
        reddit = build_reddit()
    except ImportError:
        print("praw isn't installed yet. Run:  pip install -r requirements.txt")
        conn.close()
        return
    except RuntimeError as e:
        # The credentials helper already explains exactly what's missing.
        print(e)
        conn.close()
        return

    print(f"Pulling mentions for {len(BRANDS)} brands across {len(SUBREDDITS)} subreddits...")
    try:
        new_rows = run_pull(
            reddit,
            conn,
            limit_per_term=args.limit,
            comment_cap=args.comments,
            time_filter=args.time,
        )
    except RedditAccessError as e:
        print(f"\n{e}")
        conn.close()
        return

    print(f"\nDone. {new_rows} new mention(s) added this run.")
    print("Total mentions by brand:")
    counts = count_by_brand(conn)
    if counts:
        for brand, count in sorted(counts.items()):
            print(f"  {brand:8} {count}")
    else:
        print("  (none yet — try a wider --time window or check your subreddits)")
    conn.close()


if __name__ == "__main__":
    main()
