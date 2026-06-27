"""
Aspect-Based Sentiment Analysis (ABSA) via an LLM.

This SUPPLEMENTS the fast, free star/VADER sentiment with a richer, aspect-level
layer. A single review can be negative on several things at once ("late delivery AND
charged twice"), or raise a "Feature request" / "UI bug" that keyword matching never
catches. The LLM extracts a list of (category, sentiment) pairs per mention.

Deliberate design choices:
  - SUPPLEMENT, not replace. The headline sentiment number stays the cheap, reliable
    star/VADER value. ABSA adds categorized aspects on top.
  - DEGRADE GRACEFULLY. With no API key set, this whole layer is a no-op — the
    pipeline runs exactly as before. Nothing here can crash a run.
  - CACHED. Each unique text is classified once; results are cached in the DB, so
    re-runs and repeated complaints cost nothing.
  - PROVIDER-SWAPPABLE. Gemini Flash is the default (free tier, fast, cheap, smart
    enough for short complaints). Swapping to OpenAI/Claude means writing one caller.

Enable by setting GEMINI_API_KEY in your .env. Optionally GEMINI_MODEL to override.
"""
import os
import json
import hashlib

# The controlled vocabulary the model must choose from. Keeping categories fixed makes
# the output consistent and aggregatable, and extends the keyword themes with things
# keywords structurally cannot detect (UI bug, feature request).
ABSA_CATEGORIES = [
    "Delivery", "Pricing", "Refunds & payments", "App & tech", "Customer service",
    "Product quality", "Returns & replacement", "Counterfeit / damaged",
    "UI bug", "Feature request", "Other",
]

_VALID_SENTIMENTS = {"negative", "neutral", "positive"}

_PROMPT = """You classify short customer complaints about consumer apps.
For the review below, identify each distinct aspect being discussed and its sentiment.
Choose every category ONLY from this list: {categories}

Return ONLY valid JSON, no prose, no markdown fences, in exactly this shape:
{{"aspects": [{{"category": "<one category from the list>", "sentiment": "negative|neutral|positive"}}]}}

If nothing meaningful is expressed, return {{"aspects": []}}.

Review: {text}
JSON:"""


def is_enabled() -> bool:
    """ABSA runs only when an API key is configured. Otherwise it's a clean no-op."""
    return bool(os.getenv("GEMINI_API_KEY"))


def text_hash(text: str) -> str:
    """Stable hash of the cleaned text, used as the cache key."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _model_name() -> str:
    # Model names change over time — override via env, and verify the current name on
    # the provider's docs. This default is a reasonable Flash-tier choice.
    return os.getenv("GEMINI_MODEL", "gemini-1.5-flash")


def _build_prompt(text: str) -> str:
    # Cap length to keep tokens (and cost) bounded; complaints are short anyway.
    return _PROMPT.format(categories=", ".join(ABSA_CATEGORIES), text=(text or "")[:1000])


def _call_gemini(prompt: str) -> str:
    """The actual LLM call. Imported lazily so the module loads without the SDK/key."""
    import google.generativeai as genai

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel(_model_name())
    return model.generate_content(prompt).text


def _parse(raw: str) -> dict:
    """Parse the model's reply into a clean {'aspects': [...]} dict.

    Tolerates markdown code fences and stray prose around the JSON, and drops any
    aspect whose category/sentiment isn't in our controlled vocabulary.
    """
    if not raw:
        return {"aspects": []}
    text = raw.strip()
    # Strip ```json ... ``` fences if present.
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    # Grab the outermost JSON object.
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {"aspects": []}
    try:
        data = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        # The model returned something brace-shaped but not valid JSON. Treat as empty
        # rather than raising — _parse should always hand back a clean dict.
        return {"aspects": []}

    clean = []
    for asp in data.get("aspects", []):
        cat = asp.get("category")
        sent = (asp.get("sentiment") or "").lower()
        if cat in ABSA_CATEGORIES and sent in _VALID_SENTIMENTS:
            clean.append({"category": cat, "sentiment": sent})
    return {"aspects": clean}


def classify(text: str, caller=_call_gemini) -> dict:
    """Classify one mention into aspects. Returns {'aspects': [...]}, or None on failure.

    `caller` is injectable so tests can run without the network or an API key.
    Any error (network, rate limit, bad JSON) returns None — ABSA must never break a run.
    """
    if not text:
        return None
    try:
        return _parse(caller(_build_prompt(text)))
    except Exception:
        return None
