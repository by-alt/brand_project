"""
Phase 3 — Sentiment scoring, theme tagging, and aggregation.

Reads `clean_mentions`, scores each one, and writes the analysis layer:
    - scored_mentions : one row per mention, with sentiment + themes + week
    - mention_themes  : one row per (mention, theme), for easy theme aggregation

Then it aggregates and prints the headline numbers from docs/METRICS.md:
    - Net Sentiment   = (positive - negative) / total
    - Complaint Rate  = negative / total
    - Theme breakdown = how complaints split across themes

Usage:
    python scripts/analyze.py

Notes / honest limitations:
    - Sentiment uses VADER, a transparent rule-based model. It misses sarcasm and
      Hinglish nuance, and can misread negation ("refund not received" may score
      positive). That's why a mention counts as a complaint if it is negative OR
      matches a complaint theme keyword — the two signals cover each other.
    - Theme keywords are matched WHOLE-WORD (like brands), so "late" tags a delivery
      complaint but does NOT fire on "plate" or "translate". The trade-off is some
      lost recall on inflections (e.g. "refund" won't catch "refunds"); precision is
      the safer choice for a credibility-focused project.
"""
import os
import sys
import re
from datetime import datetime, timezone
from collections import Counter

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.definitions import (
    COMPLAINT_THEMES,
    SENTIMENT_NEGATIVE_MAX,
    SENTIMENT_POSITIVE_MIN,
)
from scripts.database import (
    get_connection,
    init_db,
    iter_clean_mentions,
    insert_scored,
    insert_mention_theme,
    clear_scored,
    count_by_brand,
    insert_aspect,
    clear_aspects,
    get_absa_cache,
    set_absa_cache,
    absa_cache_has_rows,
)
from scripts import absa

_ANALYZER = SentimentIntensityAnalyzer()

# Pre-compile whole-word patterns for each theme keyword.
_THEME_PATTERNS = {
    theme: [re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE) for kw in keywords]
    for theme, keywords in COMPLAINT_THEMES.items()
}


def score_sentiment(text: str):
    """Return (compound_score, label) for a piece of text using VADER."""
    compound = _ANALYZER.polarity_scores(text or "")["compound"]
    return compound, _label_for(compound)


def _label_for(compound: float) -> str:
    if compound <= SENTIMENT_NEGATIVE_MAX:
        return "negative"
    if compound >= SENTIMENT_POSITIVE_MIN:
        return "positive"
    return "neutral"


def sentiment_from_stars(stars):
    """Map a 1-5 star rating to (compound, label), or None if not a valid rating.

    Stars are ground-truth sentiment, so for Google Play reviews this is both more
    accurate than VADER (which misreads Hinglish / Indian English) and free to compute.
    Linear map keeps the same -1..+1 scale as VADER: 1->-1, 3->0, 5->+1.

    Defensive: any non-numeric or out-of-range value returns None so the caller falls
    back to VADER rather than crashing the whole scoring run on one bad review.
    """
    try:
        value = float(stars)
    except (TypeError, ValueError):
        return None
    if not (1 <= value <= 5):
        return None
    compound = (value - 3) / 2.0
    return compound, _label_for(compound)


def score_mention(text: str, source: str, stars) -> tuple:
    """Pick the right sentiment method: star rating for reviews, VADER otherwise."""
    if source == "google_play":
        from_stars = sentiment_from_stars(stars)
        if from_stars is not None:
            return from_stars
    return score_sentiment(text)


def tag_themes(text: str) -> list:
    """Return the list of complaint themes whose keywords appear in the text."""
    if not text:
        return []
    return [
        theme
        for theme, patterns in _THEME_PATTERNS.items()
        if any(p.search(text) for p in patterns)
    ]


def iso_week(created_utc: int) -> str:
    """Turn a unix timestamp into an ISO year-week label like '2026-W03'."""
    dt = datetime.fromtimestamp(created_utc or 0, tz=timezone.utc)
    return dt.strftime("%G-W%V")


def run_score(conn, absa_classifier=None) -> Counter:
    """Rebuild the analysis layer from clean_mentions. Returns a Counter of outcomes.

    If `absa_classifier` is provided (a function text -> {'aspects': [...]}), OR an
    LLM key is configured, each mention is additionally enriched with LLM aspect-based
    sentiment, stored in mention_aspects. Results are cached per unique text so re-runs
    and repeated complaints never re-hit the API. ABSA is purely additive — with no
    classifier and no key, scoring behaves exactly as before.
    """
    import json

    init_db(conn)
    clear_scored(conn)
    clear_aspects(conn)

    # Decide the ABSA function: an explicit one (tests/custom) wins; otherwise use the
    # real LLM only when a key is present. Even with no classifier, we still enrich from
    # the cache if it holds results — so aspects don't vanish on a keyless run.
    classifier = absa_classifier or (absa.classify if absa.is_enabled() else None)
    use_absa = bool(classifier) or absa_cache_has_rows(conn)

    stats = Counter()
    for row in iter_clean_mentions(conn):
        stats["scored"] += 1
        text = row["clean_text"] or ""
        compound, label = score_mention(text, row.get("source"), row.get("stars"))
        themes = tag_themes(text)
        is_complaint = 1 if (label == "negative" or themes) else 0
        if is_complaint:
            stats["complaints"] += 1

        insert_scored(
            conn,
            {
                "id": row["id"],
                "brand": row["brand"],
                "type": row["type"],
                "source": row.get("source"),
                "clean_text": text,
                "created_utc": row["created_utc"],
                "week": iso_week(row["created_utc"]),
                "score": row["score"],
                "subreddit": row["subreddit"],
                "sentiment_compound": compound,
                "sentiment_label": label,
                "is_complaint": is_complaint,
                "themes": "|".join(themes),
            },
        )
        for theme in themes:
            insert_mention_theme(conn, row["id"], row["brand"], theme)

        # Optional LLM aspect enrichment (cached, fail-safe). Restores from cache even
        # without a live classifier.
        if use_absa and text:
            result = _absa_for(conn, text, classifier, json)
            if result:
                stats["absa_enriched"] += 1
                for asp in result.get("aspects", []):
                    insert_aspect(conn, row["id"], row["brand"], asp["category"], asp["sentiment"])

    conn.commit()
    return stats


def _absa_for(conn, text, classifier, json_mod):
    """Return cached aspects for `text`, classifying + caching on a cache miss.

    Cache hits are returned regardless of whether a live classifier is available, so
    aspects persist across runs that lack an API key. Only successful classifications
    are cached, so a transient API failure is retried next run rather than poisoning
    the cache with an empty result.
    """
    key = absa.text_hash(text)
    cached = get_absa_cache(conn, key)
    if cached is not None:
        try:
            return json_mod.loads(cached)
        except Exception:
            return None
    if classifier is None:
        return None  # cache miss and nothing to classify with
    result = classifier(text)
    if result is not None:
        set_absa_cache(conn, key, json_mod.dumps(result))
    return result


# --- aggregation (the "use SQL to aggregate" part) ----------------------------

def brand_summary(conn) -> list:
    """Per-brand headline metrics: totals, net sentiment, complaint rate."""
    rows = conn.execute(
        """
        SELECT brand,
               COUNT(*)                                  AS total,
               SUM(sentiment_label = 'positive')         AS positive,
               SUM(sentiment_label = 'neutral')          AS neutral,
               SUM(sentiment_label = 'negative')         AS negative
        FROM scored_mentions
        GROUP BY brand
        ORDER BY brand
        """
    ).fetchall()

    summary = []
    for brand, total, pos, neu, neg in rows:
        total = total or 0
        pos, neu, neg = pos or 0, neu or 0, neg or 0
        net = (pos - neg) / total if total else 0.0
        complaint_rate = neg / total if total else 0.0
        summary.append({
            "brand": brand,
            "total": total,
            "positive": pos,
            "neutral": neu,
            "negative": neg,
            "net_sentiment": round(net, 3),
            "complaint_rate": round(complaint_rate, 3),
        })
    return summary


def weekly_net_sentiment(conn) -> list:
    """Per-brand, per-week net sentiment (the trend line for Phase 6+)."""
    rows = conn.execute(
        """
        SELECT brand, week,
               COUNT(*)                          AS total,
               SUM(sentiment_label = 'positive') AS pos,
               SUM(sentiment_label = 'negative') AS neg
        FROM scored_mentions
        GROUP BY brand, week
        ORDER BY week, brand
        """
    ).fetchall()
    out = []
    for brand, week, total, pos, neg in rows:
        total = total or 0
        net = ((pos or 0) - (neg or 0)) / total if total else 0.0
        out.append({"brand": brand, "week": week, "total": total, "net_sentiment": round(net, 3)})
    return out


def theme_breakdown(conn) -> list:
    """How many complaints fall in each theme, per brand."""
    rows = conn.execute(
        """
        SELECT brand, theme, COUNT(*) AS n
        FROM mention_themes
        GROUP BY brand, theme
        ORDER BY brand, n DESC
        """
    ).fetchall()
    return [{"brand": b, "theme": t, "n": n} for b, t, n in rows]


def main():
    conn = get_connection()
    init_db(conn)

    if not count_by_brand(conn, "clean_mentions"):
        print("No clean data found. Run scripts/clean_data.py first.")
        conn.close()
        return

    print("Scoring sentiment and tagging themes...")
    stats = run_score(conn)
    print(f"Scored {stats.get('scored', 0)} mentions "
          f"({stats.get('complaints', 0)} flagged as complaints).\n")

    print("Brand summary (net sentiment ranges -1..+1):")
    print(f"  {'brand':8} {'total':>6} {'net':>7} {'complaint%':>11}")
    for r in brand_summary(conn):
        print(f"  {r['brand']:8} {r['total']:>6} {r['net_sentiment']:>7} "
              f"{r['complaint_rate'] * 100:>10.1f}%")

    print("\nTop complaint themes by brand:")
    current = None
    for r in theme_breakdown(conn):
        if r["brand"] != current:
            current = r["brand"]
            print(f"  {current}:")
        print(f"    {r['theme']:22} {r['n']}")

    print("\nTip: spot-check a few rows to trust the labels, e.g.:")
    print('  sqlite3 data/brand_health.db "SELECT sentiment_label, themes, clean_text '
          'FROM scored_mentions ORDER BY RANDOM() LIMIT 10;"')
    conn.close()


if __name__ == "__main__":
    main()
