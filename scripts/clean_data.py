"""
Phase 2 — Clean and structure the raw data.

Reads the untouched `mentions` table, applies cleaning rules, and writes the
analysis-ready result into `clean_mentions`. The raw table is never modified, so
this step is fully reproducible: change a rule, re-run, and the clean table is
rebuilt from scratch.

Usage:
    python scripts/clean_data.py

What gets removed (and why):
    - deleted / removed content   ([deleted], [removed]) — no text to analyze
    - bot content                 (known bot authors + bot phrasing) — not customers
    - noise                       (empty or letter-less text after cleaning)
    - false brand matches         brand only appeared inside a URL we stripped out
    - duplicates                  identical cleaned text for the same brand

What gets normalized:
    - HTML entities decoded (&amp; -> &), Unicode normalized (NFKC)
    - markdown links [text](url) reduced to their visible text
    - bare URLs and zero-width characters removed
    - whitespace collapsed

Note on case: we deliberately DO NOT lowercase the stored clean_text. Phase 3 uses
VADER for sentiment, which reads capitalization as emphasis ("TERRIBLE" is stronger
than "terrible"). Brand and theme matching are already case-insensitive, so keeping
the original case loses nothing and helps later.
"""
import os
import sys
import re
import html
import unicodedata
from collections import Counter

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.definitions import (
    DELETED_MARKERS,
    BOT_AUTHORS,
    BOT_TEXT_SIGNATURES,
    MIN_CLEAN_TEXT_LEN,
)
from scripts.database import (
    get_connection,
    init_db,
    iter_raw_mentions,
    insert_clean,
    clear_clean,
    count_by_brand,
)
from scripts.pull_data import matched_brands  # reuse the exact same matcher


# Pre-compiled cleaning patterns.
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")          # [text](url) -> text
_URL = re.compile(r"https?://\S+|www\.\S+")              # bare urls
_MD_EMPHASIS = re.compile(r"[*~`]+")                     # **bold** *italic* ~~strike~~ `code`
_MD_QUOTE_HEADING = re.compile(r"(?m)^\s*[>#]+\s*")      # leading > quote / # heading markers
_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
_WHITESPACE = re.compile(r"\s+")
# Matches bracketed removal markers like [deleted], [removed], [removed by reddit].
_DELETED_RE = re.compile(r"^\[\s*(deleted|removed)\b[^\]]*\]$")


def clean_text(raw: str) -> str:
    """Normalize a piece of Reddit text into analysis-ready form."""
    if not raw:
        return ""
    text = html.unescape(raw)                       # &amp; -> &, &#x200B; -> char
    text = unicodedata.normalize("NFKC", text)      # canonical Unicode form
    text = _MD_LINK.sub(r"\1", text)                # keep link text, drop the url
    text = _URL.sub(" ", text)                      # remove leftover bare urls
    text = _MD_QUOTE_HEADING.sub(" ", text)         # drop > and # line markers
    text = _MD_EMPHASIS.sub("", text)               # drop * ~ ` formatting chars
    text = _ZERO_WIDTH.sub("", text)                # strip zero-width marks
    text = _WHITESPACE.sub(" ", text).strip()       # collapse whitespace
    return text


def _is_bot(author: str, low_text: str) -> bool:
    """True if this looks like a bot, by author name or telltale phrasing."""
    if author and author.lower() in BOT_AUTHORS:
        return True
    return any(sig in low_text for sig in BOT_TEXT_SIGNATURES)


def classify(row: dict, seen: set):
    """
    Decide what to do with one raw row.

    Returns (clean_text_or_None, reason). If clean_text is None the row is dropped,
    and `reason` says why. On keep, `reason` is "kept" and `seen` is updated so the
    next identical (brand, text) is treated as a duplicate.
    """
    text = row.get("text") or ""

    # 1. Deleted / removed content.
    stripped = text.strip().lower()
    if stripped in DELETED_MARKERS or _DELETED_RE.match(stripped):
        return None, "deleted"

    # 2. Bot by author (works even before we look at the text).
    if row.get("author") and row["author"].lower() in BOT_AUTHORS:
        return None, "bot"

    # 3. Normalize, then drop empty / letter-less noise.
    ct = clean_text(text)
    if len(ct) < MIN_CLEAN_TEXT_LEN or not any(c.isalpha() for c in ct):
        return None, "noise"

    low = ct.lower()

    # 4. Bot by phrasing.
    if _is_bot(row.get("author"), low):
        return None, "bot"

    # 5. Re-validate the brand on the CLEANED text — but ONLY for sources where the
    #    brand name is expected in the text (Reddit, found by searching the name).
    #    Google Play reviews are inherently about the brand without naming it, so the
    #    brand from the source is authoritative and this check is skipped.
    if row.get("source") != "google_play" and row["brand"] not in matched_brands(ct):
        return None, "false_match"

    # 6. De-duplicate identical cleaned text within the same brand.
    key = (row["brand"], low)
    if key in seen:
        return None, "duplicate"
    seen.add(key)

    return ct, "kept"


def run_clean(conn) -> Counter:
    """Rebuild clean_mentions from raw. Returns a Counter of outcomes."""
    init_db(conn)
    clear_clean(conn)

    stats = Counter()
    seen = set()

    for row in iter_raw_mentions(conn):
        stats["raw_total"] += 1
        ct, reason = classify(row, seen)
        stats[reason] += 1
        if ct is None:
            continue
        insert_clean(
            conn,
            {
                "id": row["id"],
                "brand": row["brand"],
                "type": row["type"],
                "source": row.get("source"),
                "author": row["author"],
                "raw_text": row["text"],
                "clean_text": ct,
                "created_utc": row["created_utc"],
                "score": row["score"],
                "stars": row.get("stars"),
                "subreddit": row["subreddit"],
                "permalink": row["permalink"],
                "fetched_at": row["fetched_at"],
            },
        )

    conn.commit()
    return stats


def main():
    conn = get_connection()
    init_db(conn)

    raw_counts = count_by_brand(conn, "mentions")
    if not raw_counts:
        print("No raw data found. Run scripts/pull_data.py first.")
        conn.close()
        return

    print("Cleaning raw mentions...")
    stats = run_clean(conn)

    kept = stats.get("kept", 0)
    total = stats.get("raw_total", 0)
    print(f"\nProcessed {total} raw rows -> {kept} clean rows.")
    print("Dropped:")
    for reason in ("deleted", "bot", "noise", "false_match", "duplicate"):
        if stats.get(reason):
            print(f"  {reason:12} {stats[reason]}")

    print("\nClean mentions by brand:")
    clean_counts = count_by_brand(conn, "clean_mentions")
    if clean_counts:
        for brand, count in sorted(clean_counts.items()):
            print(f"  {brand:8} {count}")
    else:
        print("  (nothing survived cleaning — check your raw data and rules)")
    conn.close()


if __name__ == "__main__":
    main()
