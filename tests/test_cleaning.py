"""
Offline tests for the Phase 2 cleaning layer.

Seeds a raw `mentions` table with deliberately messy, realistic Reddit-style rows
and checks that cleaning normalizes text and drops the right things for the right
reasons. No network or praw needed.

Run it with:   python tests/test_cleaning.py
"""
import os
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from scripts.database import get_connection, init_db, insert_mention, count_by_brand
from scripts.clean_data import clean_text, run_clean

passed = 0

def check(label, condition):
    global passed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        raise AssertionError(label)


def _raw(id, brand, text, author="real_user", type="comment"):
    """Build a raw row with sensible defaults."""
    return {
        "id": id, "brand": brand, "type": type, "author": author, "text": text,
        "created_utc": 1_700_000_000, "score": 5,
        "subreddit": "india", "permalink": f"/r/india/{id}",
        "fetched_at": "2026-01-01T00:00:00Z",
    }


def test_clean_text():
    print("\n[clean_text normalization]")
    check("collapses whitespace and newlines",
          clean_text("Zomato   was\n\nlate") == "Zomato was late")
    check("decodes HTML entities",
          clean_text("good food &amp; service") == "good food & service")
    check("reduces markdown links to their text",
          clean_text("see [this thread](https://reddit.com/x) about Swiggy")
          == "see this thread about Swiggy")
    check("strips bare URLs",
          clean_text("Zomato refund http://t.co/abc pending").replace("  ", " ")
          == "Zomato refund pending")
    check("preserves case (for sentiment)",
          clean_text("Zomato is TERRIBLE") == "Zomato is TERRIBLE")


def test_full_clean():
    print("\n[run_clean end to end]")
    rows = [
        _raw("t1_keep1", "Zomato", "Zomato delivery was super late and cold"),
        _raw("t1_keep2", "Swiggy", "Love the new **Swiggy** UI honestly"),
        _raw("t1_del",   "Zomato", "[deleted]"),
        _raw("t1_rem",   "Swiggy", "[removed]"),
        _raw("t1_bot1",  "Zomato", "Great point about Zomato.", author="AutoModerator"),
        _raw("t1_bot2",  "Swiggy", "Swiggy mentioned. Beep boop, I am a bot."),
        _raw("t1_noise", "Zomato", "Zomato ...!!!"),  # no real letters beyond brand? -> has letters, kept
        _raw("t1_url",   "Zomato", "check https://zomato.com/menu"),  # brand only in URL -> false_match
        _raw("t1_dup1",  "Swiggy", "Swiggy is great"),
        _raw("t1_dup2",  "Swiggy", "Swiggy is great"),  # duplicate of dup1
    ]

    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "clean.db"))
        init_db(conn)
        for r in rows:
            insert_mention(conn, r)

        stats = run_clean(conn)

        check("deleted + removed are dropped", stats["deleted"] == 2)
        check("both bots are dropped", stats["bot"] == 2)
        check("URL-only brand mention is dropped as false match", stats["false_match"] == 1)
        check("the duplicate is dropped", stats["duplicate"] == 1)

        clean_counts = count_by_brand(conn, "clean_mentions")
        # Kept: keep1 (Zomato), keep2 (Swiggy), noise-row "Zomato ...!!!" (has the
        # word Zomato -> valid), dup1 (Swiggy). url/deleted/bot/dup2 all dropped.
        check("Zomato clean count is 2", clean_counts.get("Zomato") == 2)
        check("Swiggy clean count is 2", clean_counts.get("Swiggy") == 2)

        # The markdown stars should be gone from stored clean_text.
        stored = conn.execute(
            "SELECT clean_text FROM clean_mentions WHERE id='t1_keep2'"
        ).fetchone()[0]
        check("markdown formatting removed from stored text", "**" not in stored)

        # Re-running must be idempotent (rebuild from raw, same result).
        stats2 = run_clean(conn)
        check("re-running yields the same kept count", stats2["kept"] == stats["kept"])

        conn.close()


def test_raw_is_untouched():
    print("\n[raw data preserved]")
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "raw.db"))
        init_db(conn)
        insert_mention(conn, _raw("t1_x", "Zomato", "[deleted]"))
        run_clean(conn)
        raw_left = conn.execute("SELECT COUNT(*) FROM mentions").fetchone()[0]
        check("cleaning does not delete rows from the raw table", raw_left == 1)
        conn.close()


def test_audit_fixes():
    print("\n[audit fixes]")
    from scripts.database import iter_raw_mentions

    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "fix.db"))
        init_db(conn)

        # Fix 1: a real complaint containing "performed automatically" must be KEPT.
        insert_mention(conn, _raw(
            "t1_real", "Zomato",
            "My refund was performed automatically but the amount is wrong, Zomato is useless",
            author="angry_customer",
        ))
        # Fix 3: deleted-marker variants must be dropped.
        insert_mention(conn, _raw("t1_rbr", "Swiggy", "[removed by reddit]"))
        # A genuine bot must still be dropped.
        insert_mention(conn, _raw("t1_bot", "Zomato", "Zomato. I am a bot, beep boop.", author="x"))

        # Fix 2: iterating raw must NOT change how the connection returns rows.
        before = type(conn.execute("SELECT brand FROM mentions").fetchone()).__name__
        list(iter_raw_mentions(conn))
        after = type(conn.execute("SELECT brand FROM mentions").fetchone()).__name__
        check("iter_raw_mentions doesn't mutate the connection", before == after == "tuple")

        stats = run_clean(conn)
        check("real complaint with 'performed automatically' is KEPT", stats["kept"] == 1)
        check("'[removed by reddit]' variant is dropped as deleted", stats["deleted"] == 1)
        check("a genuine bot is still dropped", stats["bot"] == 1)
        conn.close()


if __name__ == "__main__":
    test_clean_text()
    test_full_clean()
    test_raw_is_untouched()
    test_audit_fixes()
    print(f"\nAll {passed} checks passed.")
