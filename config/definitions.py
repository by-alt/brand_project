"""
Central definitions for the brands we track and the complaint-theme keywords.
Kept here (not hard-coded across scripts) so they're easy to extend.
These mirror the definitions in docs/METRICS.md — keep the two in sync.
"""

# Brands tracked, with name variants to match in text (all lowercase).
# Organized into two competitive SETS so benchmarking stays apples-to-apples:
# food/quick-commerce delivery apps, and e-commerce marketplaces.
BRANDS = {
    # --- Food & quick commerce ---
    "Zomato":  ["zomato"],
    "Swiggy":  ["swiggy"],
    # Note: "blink it" (with a space) was deliberately left out — it false-matches
    # common phrases like "in a blink it was gone". Keep variants brand-specific.
    "Blinkit": ["blinkit", "grofers"],
    "Zepto":   ["zepto"],
    # --- E-commerce marketplaces ---
    "Meesho":   ["meesho"],
    "Flipkart": ["flipkart"],
    "Amazon":   ["amazon"],
}

# Which competitive set each brand belongs to. Benchmarking compares WITHIN a set,
# because a food app and an e-commerce app have different complaint patterns —
# ranking them head-to-head would be meaningless.
CATEGORIES = {
    "Food & quick commerce": ["Zomato", "Swiggy", "Blinkit", "Zepto"],
    "E-commerce":            ["Meesho", "Flipkart", "Amazon"],
}

# Reverse lookup: brand -> category.
BRAND_CATEGORY = {brand: cat for cat, brands in CATEGORIES.items() for brand in brands}

# Subreddits worth searching for these brands. Extend as you discover more.
SUBREDDITS = [
    "india",
    "bangalore",
    "mumbai",
    "delhi",
    "indianfood",
    "bestofindia",
]

# Complaint themes -> trigger keywords (all lowercase).
# A mention can match more than one theme.
# Complaint themes -> trigger keywords (all lowercase). Matched WHOLE-WORD, so the
# lists include the inflected forms that matter (e.g. crash/crashes/crashing) rather
# than relying on substring matching, which would false-fire (e.g. "late" in "plate").
COMPLAINT_THEMES = {
    "Delivery": [
        "late", "delayed", "delays", "never arrived", "didn't arrive",
        "delivery time", "rider", "riders", "not delivered", "undelivered",
    ],
    "Pricing": [
        "expensive", "price hike", "surge", "surge pricing", "charged extra",
        "costly", "overpriced", "pricey",
    ],
    "Refunds & payments": [
        "refund", "refunds", "refunded", "money not returned", "payment failed",
        "charged twice", "double charged", "deducted",
    ],
    "App & tech": [
        "crash", "crashes", "crashing", "crashed", "bug", "bugs", "glitch",
        "glitches", "app not working", "can't log in", "cannot login", "login issue",
    ],
    "Customer service": [
        "no response", "unresponsive", "support useless", "rude", "no help",
        "unhelpful", "worst support",
    ],
    "Product/food quality": [
        "spoiled", "stale", "wrong item", "missing item", "bad quality",
        "poor quality", "rotten", "undercooked",
    ],
    # E-commerce-specific themes (harmless to the delivery brands — they just rarely match).
    # Bare "return"/"exchange" were deliberately removed: they false-fire on "I'll return
    # later", "in return for", "exchange offer". The specific phrasings below keep the
    # genuine return complaints without the noise.
    "Returns & replacement": [
        "returns", "returned", "replacement", "want to return", "want to exchange",
        "return this", "return the", "return my", "return pickup", "return request",
        "wrong size",
    ],
    "Counterfeit / damaged": [
        "fake", "counterfeit", "duplicate product", "not original", "defective",
        "damaged", "tampered",
    ],
}

# VADER sentiment thresholds (standard).
SENTIMENT_NEGATIVE_MAX = -0.05  # compound <= this  => negative
SENTIMENT_POSITIVE_MIN = 0.05   # compound >= this  => positive

# Anomaly detection parameters (see docs/METRICS.md section 7).
ANOMALY_ROLLING_WEEKS = 4
ANOMALY_STD_MULTIPLIER = 2


# --- Phase 2: data-cleaning configuration -------------------------------------

# Content that Reddit replaces when a post/comment is gone. Dropped during cleaning.
DELETED_MARKERS = {"[deleted]", "[removed]", "[ removed by reddit ]"}

# Known bot accounts to drop (lowercased). Extend as you spot more in your data.
BOT_AUTHORS = {
    "automoderator",
    "automoderatorbot",
    "repostsleuthbot",
    "savevideo",
    "redditsave",
    "stabbot",
    "remindmebot",
    "wikitextbot",
}

# Phrases that strongly indicate bot/automated content (matched case-insensitively).
# Kept deliberately specific: generic phrases like "performed automatically" were
# removed because real complaints contain them (e.g. "my refund was performed
# automatically but is wrong"), and dropping those would bias the data.
BOT_TEXT_SIGNATURES = (
    "i am a bot",
    "i'm a bot",
    "beep boop",
    "this action was performed automatically",  # the literal AutoModerator footer
)

# After cleaning, text shorter than this (or with no letters) is treated as noise.
MIN_CLEAN_TEXT_LEN = 3


# --- Phase 5: Google Play app IDs (for the app-rating outcome metric) ----------
# Verified from the Play Store URLs (play.google.com/store/apps/details?id=...).
APP_IDS = {
    "Zomato":  "com.application.zomato",
    "Swiggy":  "in.swiggy.android",
    "Blinkit": "com.grofers.customerapp",
    "Zepto":   "com.zeptoconsumerapp",
    "Meesho":   "com.meesho.supply",
    "Flipkart": "com.flipkart.android",
    # Amazon's India shopping app (note the "in." prefix vs the global "com." build).
    "Amazon":   "in.amazon.mShop.android.shopping",
}

# --- Polite fetching --------------------------------------------------------
# Being a courteous client is the single most effective, legitimate way to avoid
# rate-limits/blocks: space requests out and add randomness so traffic doesn't look
# robotic. These are the gentle defaults; tune via env without touching code.
import os as _os


def _env_float(name, default):
    """Read a float env var, falling back to default on blank/invalid (never crash import)."""
    try:
        return float(_os.getenv(name) or default)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name, default):
    """Read an int env var, falling back to default on blank/invalid (never crash import)."""
    try:
        return int(_os.getenv(name) or default)
    except (TypeError, ValueError):
        return int(default)


# Seconds to wait between consecutive brand fetches.
FETCH_MIN_DELAY_SECONDS = _env_float("FETCH_MIN_DELAY_SECONDS", 4)
# Extra random 0..JITTER seconds added to each delay (avoids a robotic fixed cadence).
FETCH_JITTER_SECONDS = _env_float("FETCH_JITTER_SECONDS", 3)
# Newest reviews to request per brand per run. Smaller + more frequent beats one
# giant scrape (less load, fewer blocks; the composite PK dedups across runs).
REVIEWS_PER_BRAND = _env_int("REVIEWS_PER_BRAND", 200)
