"""
Pluggable review data sources (scripts/sources.py).

The whole point of this module: the pipeline should NOT be welded to one way of getting
reviews. A "source" is just a function with the signature

    fetch(app_id: str, count: int) -> list[dict]

returning review dicts shaped like {reviewId, content, score, thumbsUpCount, userName, at}.

`run_fetch(conn, count, fetcher=...)` in fetch_reviews.py accepts any such function, so
swapping the underlying source is a one-argument change — no pipeline edits.

Why this matters for real / company use:
  - The default scraper (google-play-scraper) is fine for a portfolio or low-volume
    research, but it's unofficial: it can be rate-limited or blocked from cloud IPs, and
    it breaks when Google changes internals. It is NOT a durable foundation for a product.
  - For production, a company plugs in a LEGITIMATE source here instead — its own Play
    Console review data, or a licensed review-monitoring API (AppFollow, Sensor Tower,
    Data.ai, Apify, SerpApi, etc.). Those handle access, scale, and compliance.
  - The honest non-option: proxy pools / user-agent spoofing to dodge blocks. That fights
    the platform's terms, is legally fragile, and breaks constantly — exactly what a
    company does NOT want under a product. The fix for blocking is a legitimate source,
    not better evasion.

See docs/DATA_SOURCES.md for the full rationale and provider notes.
"""


def google_play_scraper_source(app_id: str, count: int) -> list:
    """DEFAULT source: the unofficial google-play-scraper. Good for research/portfolio.

    Lazily imported and isolated so the rest of the project doesn't depend on it.
    """
    from scripts.fetch_reviews import fetch_reviews_for_app  # already retry-wrapped
    return fetch_reviews_for_app(app_id, count)


def licensed_api_source(app_id: str, count: int) -> list:
    """TEMPLATE for a licensed / official review API (the company-grade path).

    Replace the body with a real call to your provider (AppFollow, Sensor Tower, Data.ai,
    Apify, or the Google Play Console Reviews API for your own app). The ONLY contract is
    to return the same review-dict shape the pipeline expects, so nothing downstream changes.

    Set the provider key via env (e.g. REVIEWS_API_KEY) — never hard-code it.
    """
    import os

    api_key = os.getenv("REVIEWS_API_KEY")
    if not api_key:
        raise RuntimeError(
            "licensed_api_source needs REVIEWS_API_KEY. This is a template — wire it to "
            "your provider (AppFollow / Sensor Tower / Play Console API). See docs/DATA_SOURCES.md."
        )

    # --- Example shape of a real implementation (pseudo-code, intentionally inert) ---
    # import httpx
    # resp = httpx.get(
    #     "https://api.your-provider.com/v1/reviews",
    #     params={"app_id": app_id, "limit": count, "country": "in", "lang": "en"},
    #     headers={"Authorization": f"Bearer {api_key}"},
    #     timeout=30.0,
    # )
    # resp.raise_for_status()
    # return [
    #     {
    #         "reviewId": r["id"],
    #         "content": r["text"],
    #         "score": r["rating"],            # 1..5 stars
    #         "thumbsUpCount": r.get("likes", 0),
    #         "userName": r.get("author", ""),
    #         "at": r["created_at"],           # datetime
    #     }
    #     for r in resp.json()["reviews"]
    # ]
    raise NotImplementedError("Fill in licensed_api_source for your provider.")


# The source the pipeline uses by default. To switch a deployment to a licensed source,
# either pass fetcher=licensed_api_source to run_fetch, or point this name at it.
DEFAULT_SOURCE = google_play_scraper_source
