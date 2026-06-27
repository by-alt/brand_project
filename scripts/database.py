"""
SQLite layer for the Brand Health Tracker.

Two tables, by design:
  - mentions        : the RAW data exactly as pulled from Reddit (never edited).
  - clean_mentions  : the CLEANED, analysis-ready data, rebuilt from raw by Phase 2.

Keeping raw and clean separate means cleaning is always reproducible: if we change
a cleaning rule, we just rebuild clean_mentions from the untouched raw data.

Both tables use a composite primary key (id, brand) so re-runs never duplicate, and
a single post mentioning two brands is stored once per brand.
"""
import os
import sqlite3

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "brand_health.db",
)

RAW_SCHEMA = """
CREATE TABLE IF NOT EXISTS mentions (
    id           TEXT    NOT NULL,
    brand        TEXT    NOT NULL,
    type         TEXT    NOT NULL,
    source       TEXT,                -- 'reddit' or 'google_play'
    author       TEXT,
    text         TEXT,
    created_utc  INTEGER,
    score        INTEGER,
    stars        INTEGER,              -- 1-5 star rating (Google Play reviews only)
    subreddit    TEXT,
    permalink    TEXT,
    fetched_at   TEXT,
    PRIMARY KEY (id, brand)
);
CREATE INDEX IF NOT EXISTS idx_mentions_brand   ON mentions(brand);
CREATE INDEX IF NOT EXISTS idx_mentions_created ON mentions(created_utc);
"""

CLEAN_SCHEMA = """
CREATE TABLE IF NOT EXISTS clean_mentions (
    id           TEXT    NOT NULL,
    brand        TEXT    NOT NULL,
    type         TEXT,
    source       TEXT,
    author       TEXT,
    raw_text     TEXT,            -- the original text, kept for transparency
    clean_text   TEXT,            -- normalized text used for analysis
    created_utc  INTEGER,
    score        INTEGER,
    stars        INTEGER,         -- 1-5 star rating (Google Play reviews only)
    subreddit    TEXT,
    permalink    TEXT,
    fetched_at   TEXT,
    PRIMARY KEY (id, brand)
);
CREATE INDEX IF NOT EXISTS idx_clean_brand   ON clean_mentions(brand);
CREATE INDEX IF NOT EXISTS idx_clean_created ON clean_mentions(created_utc);
"""

# Phase 3 analysis layer, rebuilt from clean_mentions.
SCORED_SCHEMA = """
CREATE TABLE IF NOT EXISTS scored_mentions (
    id                 TEXT    NOT NULL,
    brand              TEXT    NOT NULL,
    type               TEXT,
    source             TEXT,
    clean_text         TEXT,
    created_utc        INTEGER,
    week               TEXT,        -- ISO year-week, e.g. 2026-W03
    score              INTEGER,
    subreddit          TEXT,
    sentiment_compound REAL,        -- VADER compound, -1..+1
    sentiment_label    TEXT,        -- positive / neutral / negative
    is_complaint       INTEGER,     -- 1 if negative OR matches a complaint theme
    themes             TEXT,        -- pipe-delimited, human-readable
    PRIMARY KEY (id, brand)
);
CREATE INDEX IF NOT EXISTS idx_scored_brand ON scored_mentions(brand);
CREATE INDEX IF NOT EXISTS idx_scored_week  ON scored_mentions(week);

-- One row per (mention, theme): the tidy/long table for theme aggregation.
CREATE TABLE IF NOT EXISTS mention_themes (
    id     TEXT NOT NULL,
    brand  TEXT NOT NULL,
    theme  TEXT NOT NULL,
    PRIMARY KEY (id, brand, theme)
);
CREATE INDEX IF NOT EXISTS idx_mention_themes_theme ON mention_themes(theme);
CREATE INDEX IF NOT EXISTS idx_mention_themes_brand ON mention_themes(brand);
"""

# Phase 5: daily snapshots of each brand's Google Play rating (the outcome metric).
RATINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_ratings (
    brand         TEXT NOT NULL,
    captured_date TEXT NOT NULL,   -- YYYY-MM-DD (one snapshot per day)
    rating        REAL,            -- store rating, e.g. 4.3
    num_reviews   INTEGER,
    source        TEXT,
    PRIMARY KEY (brand, captured_date)
);
CREATE INDEX IF NOT EXISTS idx_ratings_brand ON app_ratings(brand);
"""

# Aspect-Based Sentiment Analysis (LLM) layer.
#   mention_aspects : one row per (mention, aspect) — the categorized, per-aspect
#                     sentiment the LLM extracts (richer than keyword themes).
#   absa_cache      : caches the LLM result per unique text so re-runs and repeated
#                     complaints never re-hit (or re-bill) the API.
ABSA_SCHEMA = """
CREATE TABLE IF NOT EXISTS mention_aspects (
    id        TEXT NOT NULL,
    brand     TEXT NOT NULL,
    category  TEXT NOT NULL,
    sentiment TEXT,
    PRIMARY KEY (id, brand, category)
);
CREATE INDEX IF NOT EXISTS idx_aspects_brand    ON mention_aspects(brand);
CREATE INDEX IF NOT EXISTS idx_aspects_category ON mention_aspects(category);

CREATE TABLE IF NOT EXISTS absa_cache (
    text_hash   TEXT PRIMARY KEY,
    result_json TEXT
);
"""


def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (creating the data/ folder if needed) and return a connection."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return sqlite3.connect(db_path)


def _column_exists(conn, table, column) -> bool:
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


def _ensure_column(conn, table, column, coltype="TEXT") -> None:
    """Add a column to an existing table if it's missing (safe, in-place migration)."""
    if not _column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables/indexes if missing, and migrate older DBs in place."""
    conn.executescript(RAW_SCHEMA)
    conn.executescript(CLEAN_SCHEMA)
    conn.executescript(SCORED_SCHEMA)
    conn.executescript(RATINGS_SCHEMA)
    conn.executescript(ABSA_SCHEMA)
    # Migrations for DBs created before a column existed (no data lost).
    _ensure_column(conn, "mentions", "author")
    _ensure_column(conn, "mentions", "source")
    _ensure_column(conn, "mentions", "stars", "INTEGER")
    _ensure_column(conn, "clean_mentions", "source")
    _ensure_column(conn, "clean_mentions", "stars", "INTEGER")
    _ensure_column(conn, "scored_mentions", "source")
    conn.commit()


def insert_mention(conn: sqlite3.Connection, row: dict) -> bool:
    """
    Insert a raw mention; ignore it if (id, brand) already exists.
    Returns True if a new row was added, False if it was a duplicate.
    """
    row = {**row, "source": row.get("source") or "reddit", "stars": row.get("stars")}
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO mentions
            (id, brand, type, source, author, text, created_utc, score, stars, subreddit, permalink, fetched_at)
        VALUES
            (:id, :brand, :type, :source, :author, :text, :created_utc, :score, :stars, :subreddit, :permalink, :fetched_at)
        """,
        row,
    )
    return cur.rowcount == 1


def insert_clean(conn: sqlite3.Connection, row: dict) -> None:
    """Insert/replace a cleaned mention. Phase 2 rebuilds this table each run."""
    row = {**row, "source": row.get("source") or "reddit", "stars": row.get("stars")}
    conn.execute(
        """
        INSERT OR REPLACE INTO clean_mentions
            (id, brand, type, source, author, raw_text, clean_text,
             created_utc, score, stars, subreddit, permalink, fetched_at)
        VALUES
            (:id, :brand, :type, :source, :author, :raw_text, :clean_text,
             :created_utc, :score, :stars, :subreddit, :permalink, :fetched_at)
        """,
        row,
    )


def clear_clean(conn: sqlite3.Connection) -> None:
    """Empty the clean table so it can be rebuilt deterministically from raw."""
    conn.execute("DELETE FROM clean_mentions")


def iter_raw_mentions(conn: sqlite3.Connection):
    """Yield each raw mention as a dict, oldest first.

    Builds dicts from the cursor description so it does NOT change the
    connection's row_factory (which would surprise later queries). Ordering
    by time makes cleaning deterministic and makes de-dup keep the earliest.
    """
    cur = conn.execute(
        """SELECT id, brand, type, source, author, text,
                  created_utc, score, stars, subreddit, permalink, fetched_at
           FROM mentions
           ORDER BY created_utc, id"""
    )
    columns = [d[0] for d in cur.description]
    for row in cur:
        yield dict(zip(columns, row))


def iter_clean_mentions(conn: sqlite3.Connection):
    """Yield each cleaned mention as a dict, oldest first (no row_factory mutation)."""
    cur = conn.execute(
        """SELECT id, brand, type, source, clean_text, created_utc, score, stars, subreddit
           FROM clean_mentions
           ORDER BY created_utc, id"""
    )
    columns = [d[0] for d in cur.description]
    for row in cur:
        yield dict(zip(columns, row))


def insert_scored(conn: sqlite3.Connection, row: dict) -> None:
    """Insert/replace a scored mention. Phase 3 rebuilds this table each run."""
    row = {**row, "source": row.get("source") or "reddit"}
    conn.execute(
        """
        INSERT OR REPLACE INTO scored_mentions
            (id, brand, type, source, clean_text, created_utc, week, score, subreddit,
             sentiment_compound, sentiment_label, is_complaint, themes)
        VALUES
            (:id, :brand, :type, :source, :clean_text, :created_utc, :week, :score, :subreddit,
             :sentiment_compound, :sentiment_label, :is_complaint, :themes)
        """,
        row,
    )


def insert_mention_theme(conn: sqlite3.Connection, id: str, brand: str, theme: str) -> None:
    """Record that a mention belongs to a theme (one row per pair)."""
    conn.execute(
        "INSERT OR REPLACE INTO mention_themes (id, brand, theme) VALUES (?, ?, ?)",
        (id, brand, theme),
    )


def clear_scored(conn: sqlite3.Connection) -> None:
    """Empty the Phase 3 tables so they can be rebuilt deterministically."""
    conn.execute("DELETE FROM scored_mentions")
    conn.execute("DELETE FROM mention_themes")


def count_by_brand(conn: sqlite3.Connection, table: str = "mentions") -> dict:
    """Return {brand: row_count} for a known table."""
    allowed = {"mentions", "clean_mentions", "scored_mentions"}
    if table not in allowed:
        raise ValueError(f"table must be one of {allowed}")
    rows = conn.execute(f"SELECT brand, COUNT(*) FROM {table} GROUP BY brand").fetchall()
    return {brand: count for brand, count in rows}


def insert_rating(conn: sqlite3.Connection, row: dict) -> None:
    """Store/replace one daily app-rating snapshot."""
    conn.execute(
        """
        INSERT OR REPLACE INTO app_ratings
            (brand, captured_date, rating, num_reviews, source)
        VALUES (:brand, :captured_date, :rating, :num_reviews, :source)
        """,
        row,
    )


def iter_ratings(conn: sqlite3.Connection):
    """Yield each rating snapshot as a dict, oldest first."""
    cur = conn.execute(
        """SELECT brand, captured_date, rating, num_reviews, source
           FROM app_ratings ORDER BY captured_date, brand"""
    )
    columns = [d[0] for d in cur.description]
    for row in cur:
        yield dict(zip(columns, row))


# --- ABSA (LLM aspect) helpers -------------------------------------------------

def insert_aspect(conn, id: str, brand: str, category: str, sentiment: str) -> None:
    """Record one extracted (mention, category) aspect with its sentiment."""
    conn.execute(
        "INSERT OR REPLACE INTO mention_aspects (id, brand, category, sentiment) VALUES (?, ?, ?, ?)",
        (id, brand, category, sentiment),
    )


def clear_aspects(conn) -> None:
    """Empty the aspect table so it can be rebuilt (the cache is kept)."""
    conn.execute("DELETE FROM mention_aspects")


def get_absa_cache(conn, text_hash: str):
    """Return the cached LLM result JSON for a text hash, or None if not cached."""
    row = conn.execute(
        "SELECT result_json FROM absa_cache WHERE text_hash = ?", (text_hash,)
    ).fetchone()
    return row[0] if row else None


def set_absa_cache(conn, text_hash: str, result_json: str) -> None:
    """Cache an LLM result so the same text is never classified twice."""
    conn.execute(
        "INSERT OR REPLACE INTO absa_cache (text_hash, result_json) VALUES (?, ?)",
        (text_hash, result_json),
    )


def absa_cache_has_rows(conn) -> bool:
    """True if any ABSA results are cached. Lets scoring restore aspects from cache
    even on a run with no API key, so previously-computed aspects don't vanish."""
    return conn.execute("SELECT 1 FROM absa_cache LIMIT 1").fetchone() is not None
