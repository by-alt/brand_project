"""
Offline tests for the LLM ABSA layer and the e-commerce competitive set.

No network and no API key needed: a fake classifier stands in for the LLM, so we can
verify parsing, category filtering, caching, graceful fallback, and the end-to-end
enrichment path deterministically.

Run it with:   python tests/test_absa.py
"""
import os
import sys
import json
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from scripts import absa
from scripts.database import get_connection, init_db, insert_clean, get_absa_cache
from scripts.analyze import run_score
from scripts.insights import aspect_breakdown, benchmark_by_category

passed = 0

def check(label, condition):
    global passed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        raise AssertionError(label)


def _clean(id, brand, text):
    import time
    return {
        "id": id, "brand": brand, "type": "review", "source": "google_play",
        "author": "u", "raw_text": text, "clean_text": text,
        "created_utc": int(time.time()), "score": 0, "stars": 2,
        "subreddit": None, "permalink": None, "fetched_at": "t",
    }


def test_parsing():
    print("\n[absa parsing]")
    fenced = '```json\n{"aspects":[{"category":"Delivery","sentiment":"negative"}]}\n```'
    check("parses JSON inside markdown fences",
          absa._parse(fenced)["aspects"] == [{"category": "Delivery", "sentiment": "negative"}])
    check("drops unknown categories",
          absa._parse('{"aspects":[{"category":"Nonsense","sentiment":"negative"}]}')["aspects"] == [])
    check("drops invalid sentiments",
          absa._parse('{"aspects":[{"category":"Pricing","sentiment":"furious"}]}')["aspects"] == [])
    check("handles junk gracefully", absa._parse("not json at all")["aspects"] == [])


def test_classify_with_fake_llm():
    print("\n[absa classify]")
    fake = lambda prompt: '{"aspects":[{"category":"Refunds & payments","sentiment":"negative"},{"category":"UI bug","sentiment":"negative"}]}'
    out = absa.classify("charged twice and the button does nothing", caller=fake)
    check("extracts multiple aspects", len(out["aspects"]) == 2)
    check("surfaces a category keywords can't catch (UI bug)",
          any(a["category"] == "UI bug" for a in out["aspects"]))

    # A caller that raises must not crash — returns None.
    boom = lambda prompt: (_ for _ in ()).throw(RuntimeError("rate limited"))
    check("an API failure returns None (never crashes)", absa.classify("x", caller=boom) is None)


def test_disabled_by_default():
    print("\n[graceful fallback]")
    saved = os.environ.pop("GEMINI_API_KEY", None)
    try:
        check("ABSA is disabled with no API key", absa.is_enabled() is False)
        with tempfile.TemporaryDirectory() as tmp:
            conn = get_connection(os.path.join(tmp, "noabsa.db"))
            init_db(conn)
            insert_clean(conn, _clean("c1", "Zomato", "delivery was late"))
            stats = run_score(conn)  # no classifier, no key -> no ABSA, no crash
            check("scoring still works without ABSA", stats["scored"] == 1)
            check("no aspects recorded when disabled", aspect_breakdown(conn) == [])
            conn.close()
    finally:
        if saved is not None:
            os.environ["GEMINI_API_KEY"] = saved


def test_end_to_end_with_cache():
    print("\n[absa end-to-end + caching]")
    calls = {"n": 0}
    def fake_classifier(text):
        calls["n"] += 1
        return {"aspects": [{"category": "Refunds & payments", "sentiment": "negative"}]}

    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "absa.db"))
        init_db(conn)
        # Two mentions with IDENTICAL text -> the LLM should be called once (cache hit).
        insert_clean(conn, _clean("m1", "Meesho", "refund never came after return"))
        insert_clean(conn, _clean("m2", "Meesho", "refund never came after return"))

        stats = run_score(conn, absa_classifier=fake_classifier)
        check("both mentions enriched", stats["absa_enriched"] == 2)
        check("identical text only hit the LLM once (cache works)", calls["n"] == 1)

        aspects = aspect_breakdown(conn)
        check("aspect breakdown records the category",
              any(a["category"] == "Refunds & payments" for a in aspects))

        # Re-run: everything served from cache, zero new LLM calls.
        run_score(conn, absa_classifier=fake_classifier)
        check("re-run uses the cache (still one call total)", calls["n"] == 1)

        # Regression: a keyless re-run (no classifier) must NOT wipe aspects — they're
        # restored from the cache.
        run_score(conn)  # no classifier, simulating a run without an API key
        check("keyless re-run keeps aspects (restored from cache)",
              any(a["category"] == "Refunds & payments" for a in aspect_breakdown(conn)))
        conn.close()


def test_ecommerce_category_grouping():
    print("\n[e-commerce competitive set]")
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(os.path.join(tmp, "cat.db"))
        init_db(conn)
        insert_clean(conn, _clean("d1", "Swiggy", "love it fast great"))
        insert_clean(conn, _clean("e1", "Flipkart", "terrible, fake product, want a return"))
        run_score(conn)
        grouped = benchmark_by_category(conn)
        check("two competitive sets are produced", set(grouped.keys()) == {"Food & quick commerce", "E-commerce"})
        check("Flipkart lands in E-commerce",
              any(r["brand"] == "Flipkart" for r in grouped.get("E-commerce", [])))
        check("Swiggy lands in Food & quick commerce",
              any(r["brand"] == "Swiggy" for r in grouped.get("Food & quick commerce", [])))
        conn.close()


def test_audit_fixes():
    print("\n[audit fixes]")
    from scripts.analyze import tag_themes
    # Bug 1: _parse must not raise on brace-shaped invalid JSON.
    check("_parse survives invalid JSON (unquoted keys)", absa._parse("{aspects: [bad]}")["aspects"] == [])
    check("_parse survives partial JSON garbage", absa._parse("junk {x} junk")["aspects"] == [])
    # Bug 2: common words must not false-fire the Returns theme...
    check("'return to the app later' is NOT a return complaint",
          "Returns & replacement" not in tag_themes("I'll return to this app later"))
    check("'exchange offer' is NOT a return complaint",
          "Returns & replacement" not in tag_themes("great exchange offer during sale"))
    # ...but genuine return complaints still are.
    check("'want to return this' IS a return complaint",
          "Returns & replacement" in tag_themes("I want to return this product"))
    check("'wrong size' IS a return complaint",
          "Returns & replacement" in tag_themes("they sent the wrong size"))


if __name__ == "__main__":
    test_parsing()
    test_classify_with_fake_llm()
    test_disabled_by_default()
    test_end_to_end_with_cache()
    test_ecommerce_category_grouping()
    test_audit_fixes()
    print(f"\nAll {passed} checks passed.")
