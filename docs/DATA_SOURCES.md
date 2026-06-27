# Data sources & avoiding blocks

This pipeline separates *what we analyse* from *where the data comes from*, so the data
source is swappable. This document explains the options honestly — including why the
right answer for company use is **not** "get better at scraping".

## The two layers

- **Collection** — gets raw reviews/posts. This is the fragile, external part.
- **Everything else** (clean → score → themes → insights → dashboard) — stable, and
  source-agnostic. It works the same regardless of where the reviews came from.

The seam is `run_fetch(conn, count, fetcher=...)` in `scripts/fetch_reviews.py`, plus
`scripts/sources.py`. A "source" is just a function `fetch(app_id, count) -> [review dicts]`.

## The blocking problem (be honest about it)

The default review source uses `google-play-scraper`, which is **unofficial**. It:
- can be **rate-limited or blocked**, especially from datacenter IPs (GitHub Actions, most
  cloud VPSs) — so it may work from your laptop but fail when deployed;
- can **break silently** when Google changes internals (a run "succeeds" with zero rows).

This is fine for a **portfolio or low-volume research** project. It is **not** a durable
foundation for a product a company depends on.

### What we DO to be resilient (legitimate)

These are "be a courteous client" measures, not evasion:
- **Polite spacing**: a configurable delay + random jitter between brand fetches
  (`FETCH_MIN_DELAY_SECONDS`, `FETCH_JITTER_SECONDS`).
- **Retries with backoff** on transient errors (`scripts/retry.py`).
- **Incremental, modest pulls**: fetch the newest `REVIEWS_PER_BRAND` each run and dedup
  on store (composite primary key), so we request less over time.
- **Per-brand isolation**: one brand failing never aborts the others.
- **Loud empty-fetch warnings**: if brands return zero, the run says so instead of
  pretending it worked.
- **Run from a residential IP** (your machine, or a self-hosted runner) when possible —
  home IPs are blocked far less than cloud ranges.

### What we deliberately DON'T do (and why)

Proxy rotation, user-agent spoofing, CAPTCHA-solving — the "evasion" toolkit. We avoid it
on purpose: it fights the platform's terms of service, is legally fragile, and breaks
constantly. No company wants a product built on that. The correct fix for blocking is a
**legitimate source**, below.

## The company-grade path: legitimate sources

For real/production use, plug a licensed or official source into
`scripts/sources.py:licensed_api_source` (or pass `fetcher=...` to `run_fetch`). Options:

- **Google Play Console Reviews API** — official, free, for **your own app's** reviews.
  The right choice when a company tracks *its own* brand.
- **Review-monitoring vendors** — AppFollow, Sensor Tower, Data.ai, etc. Built exactly for
  voice-of-customer at scale; they handle access, history, and compliance.
- **Licensed scraping APIs** — Apify, SerpApi, etc. They manage proxying/leg/scale so you
  don't run an evasion arms race yourself.

Because the rest of the pipeline is source-agnostic, switching to any of these changes
**one function**, not the analysis. That separation is what makes this usable beyond a
demo.

## Reddit

Reddit uses its **official API** via PRAW with your credentials, so it isn't subject to the
scraping-block problem. Its limitation is *volume*, not access: search is shallow and
capped, so treat Reddit as a secondary signal and let Play reviews carry the analysis.
