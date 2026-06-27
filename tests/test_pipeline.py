"""
Offline tests for the Phase 1 pipeline.

These run without praw, credentials, or any network access. They use small mock
"submission" and "comment" objects that look like the real praw ones, so we can
verify the logic end to end:

  - whole-word brand matching (and that it rejects false positives)
  - one post mentioning two brands becomes two rows
  - re-running never creates duplicates
  - the full run_pull() flow stores the right things

Run it with:   python tests/test_pipeline.py
"""
import os
import sys
import tempfile

# Make the project importable when run directly.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from scripts.database import get_connection, init_db, count_by_brand
from scripts.pull_data import (
    matched_brands,
    extract_post_row,
    store_item,
    run_pull,
)


# --- tiny stand-ins for praw objects -------------------------------------------

class FakeComment:
    def __init__(self, cid, body, score=1):
        self.id = cid
        self.body = body
        self.score = score
        self.created_utc = 1_700_000_000
        self.subreddit = "india"
        self.permalink = f"/r/india/comments/{cid}"


class FakeComments:
    """Mimics submission.comments: .replace_more() and .list()."""
    def __init__(self, comments):
        self._comments = comments

    def replace_more(self, limit=0):
        return []

    def list(self):
        return self._comments


class FakeSubmission:
    def __init__(self, sid, title, selftext="", comments=None, score=10):
        self.id = sid
        self.title = title
        self.selftext = selftext
        self.score = score
        self.created_utc = 1_700_000_000
        self.subreddit = "india"
        self.permalink = f"/r/india/comments/{sid}"
        self.comments = FakeComments(comments or [])


class FakeMulti:
    """Mimics reddit.subreddit('a+b+c'); search returns the same fixed set."""
    def __init__(self, submissions):
        self._submissions = submissions

    def search(self, term, sort="new", time_filter="month", limit=50):
        return self._submissions


class FakeReddit:
    def __init__(self, submissions):
        self._multi = FakeMulti(submissions)

    def subreddit(self, name):
        return self._multi


# --- small assertion helper ----------------------------------------------------

passed = 0

def check(label, condition):
    global passed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        raise AssertionError(label)


# --- the tests -----------------------------------------------------------------

def test_matching():
    print("\n[matching]")
    check("matches a clean brand mention", matched_brands("Zomato was late") == ["Zomato"])
    check("rejects false positive 'zomatouniverse'", matched_brands("the zomatouniverse page") == [])
    check("is case-insensitive", matched_brands("SWIGGY rocks") == ["Swiggy"])
    check("matches the 'grofers' variant -> Blinkit", matched_brands("ex-grofers user") == ["Blinkit"])
    check("ignores the dropped 'blink it' phrase", matched_brands("in a blink it vanished") == [])
    two = matched_brands("Zomato vs Swiggy debate")
    check("finds both brands in one text", set(two) == {"Zomato", "Swiggy"})


def test_storage_dedup():
    print("\n[storage + dedup]")
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "test.db"))
        init_db(conn)

        post = FakeSubmission("aaa", "Zomato vs Swiggy thoughts?")
        base = extract_post_row(post)

        brands, new = store_item(conn, base, "2026-01-01T00:00:00Z")
        check("a two-brand post inserts two rows", new == 2 and set(brands) == {"Zomato", "Swiggy"})

        _, new_again = store_item(conn, base, "2026-01-01T00:00:00Z")
        check("re-storing the same post adds nothing", new_again == 0)

        conn.close()


def test_full_run():
    print("\n[full run_pull]")
    submissions = [
        FakeSubmission("p1", "Zomato delivery was late again",
                       comments=[FakeComment("c1", "Swiggy refund never came")]),
        FakeSubmission("p2", "Zomato vs Swiggy who wins"),
        FakeSubmission("p3", "check out the zomatouniverse fanpage"),  # matches nothing
    ]
    reddit = FakeReddit(submissions)

    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "run.db"))
        init_db(conn)

        new = run_pull(reddit, conn, limit_per_term=50, comment_cap=20)
        # p1 post -> Zomato (1), p1 comment -> Swiggy (1),
        # p2 post -> Zomato + Swiggy (2), p3 -> 0  => 4 rows
        check("first run adds the expected 4 rows", new == 4)

        counts = count_by_brand(conn)
        check("Zomato count is 2", counts.get("Zomato") == 2)
        check("Swiggy count is 2", counts.get("Swiggy") == 2)

        new_again = run_pull(reddit, conn, limit_per_term=50, comment_cap=20)
        check("second identical run adds 0 (dedup holds)", new_again == 0)

        conn.close()


class RaisingMulti:
    """Simulates a search outage — raises while iterating, like a real praw failure."""
    def search(self, *args, **kwargs):
        raise RuntimeError("simulated search outage")


class RaisingReddit:
    def subreddit(self, name):
        return RaisingMulti()


class BadComments:
    """Comments object whose .list() blows up, to test per-post isolation."""
    def replace_more(self, limit=0):
        return []

    def list(self):
        raise RuntimeError("simulated comment failure")


def test_robustness():
    print("\n[robustness]")

    # A search outage should be caught, not crash the run.
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "r1.db"))
        init_db(conn)
        new = run_pull(RaisingReddit(), conn)
        check("search outage is handled and run returns 0", new == 0)
        conn.close()

    # A post whose comments fail should still get saved (failure is isolated).
    sub = FakeSubmission("p9", "Zomato was great today")
    sub.comments = BadComments()
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "r2.db"))
        init_db(conn)
        new = run_pull(FakeReddit([sub]), conn)
        check("comment failure is isolated; the post is still saved", new == 1)
        check("the saved post is counted under Zomato", count_by_brand(conn).get("Zomato") == 1)
        conn.close()


class AuthFailMulti:
    """Search raises a 401, like Reddit does when credentials are wrong."""
    def search(self, *args, **kwargs):
        raise Exception("received 401 HTTP response")


class AuthFailReddit:
    def subreddit(self, name):
        return AuthFailMulti()


def test_auth_failure_is_loud():
    print("\n[auth handling]")
    from scripts.pull_data import RedditAccessError

    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "auth.db"))
        init_db(conn)
        raised = False
        try:
            run_pull(AuthFailReddit(), conn, limit_per_term=5)
        except RedditAccessError:
            raised = True
        check("a 401 aborts loudly (not a silent '0 mentions')", raised)
        conn.close()


if __name__ == "__main__":
    test_matching()
    test_storage_dedup()
    test_full_run()
    test_robustness()
    test_auth_failure_is_loud()
    print(f"\nAll {passed} checks passed.")
