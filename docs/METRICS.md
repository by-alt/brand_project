# Metric Definitions

Every metric this project uses is defined here, with the reasoning behind it.
If a hiring manager asks "how did you define a complaint?", the answer is in this file.

> **Principle:** A metric you can't define and defend is a metric you can't trust.
> These definitions are deliberately simple so they're explainable and reproducible.
> Simple-and-defensible beats clever-and-opaque.

---

## 1. Brand mention

A **brand mention** is a single customer review or Reddit post/comment whose text
refers to one of the tracked brands.

- **Sources:** Google Play reviews (the primary, high-volume source) and Reddit
  (a secondary signal). Every mention carries its `source`.
- **Brands & competitive sets:** seven brands across two sets that are benchmarked
  *separately* — **Food & quick commerce** (Zomato, Swiggy, Blinkit, Zepto) and
  **E-commerce** (Meesho, Flipkart, Amazon) — because their complaint patterns differ
  and cross-set ranking would mislead.
- **Matching:** case-insensitive, whole-word match on the brand name and known variants
  (e.g., "blinkit", "grofers" → Blinkit). For Google Play reviews the brand is
  authoritative (we know which app was queried), so re-validation is skipped.

> **Known limitation — name collisions.** Short or generic brand names can match
> unrelated content that whole-word matching cannot filter out (e.g. "Zepto" also
> refers to the Zepto.js JavaScript library and the SI prefix "zepto-"). This adds
> some noise to those brands. Phase 2 cleaning can reduce it with context rules
> (e.g. drop a "zepto" mention if the text is clearly about programming), and it's
> noted as a caveat on any per-brand finding. Honest to flag rather than pretend
> the matcher is perfect.

---

## 2. Sentiment

Each mention gets a **sentiment score** (compound, -1..+1) and a **label**, using the
most reliable signal available for its source:

- **Google Play reviews** use the **1-5 star rating** as ground-truth sentiment, mapped
  linearly to the same scale (1->-1, 3->0, 5->+1). This is more accurate than running an
  English-lexicon model on Indian-English / Hinglish review text, and free to compute.
- **Reddit** mentions (no rating) use **VADER**, a rule-based model for short informal text.

Both produce a compound on the same scale, so downstream aggregation is uniform.
Classification (standard thresholds):
  - **Negative:** compound ≤ -0.05
  - **Neutral:** -0.05 < compound < 0.05
  - **Positive:** compound ≥ 0.05

> **Known limitation (state this honestly in your report):** VADER misses sarcasm
> and Hindi/Hinglish nuance, so Reddit sentiment is the noisier signal; the complaint
> flag hedges this with theme keywords. A missing star value falls back to VADER.

---

## 3. Complaint

A **complaint** is a mention that is **negative in sentiment** (compound ≤ -0.05)
**OR** matches one or more complaint-theme keywords (below). The OR catches
neutral-toned but clearly negative content like "still waiting for my refund."

---

## 4. Complaint themes

Each complaint is tagged into one or more **themes** using keyword rules.
Keyword-based tagging is chosen over ML because it's transparent and easy to defend.

| Theme              | Example trigger keywords                                  |
|--------------------|-----------------------------------------------------------|
| Delivery           | late, delayed, never arrived, delivery time, rider         |
| Pricing            | expensive, price hike, surge, charged extra, costly        |
| Refunds & payments | refund, money not returned, payment failed, charged twice  |
| App & tech         | crash, bug, app not working, glitch, can't log in          |
| Customer service   | no response, support useless, rude, no help                |
| Product/food quality | spoiled, stale, wrong item, missing item, quality        |
| Returns & replacement | want to return, return pickup, replacement, wrong size  |
| Counterfeit / damaged | fake, counterfeit, not original, defective, damaged     |

> The keyword lists live in `config/definitions.py` so they're easy to extend. A mention
> with no keyword match but negative sentiment is bucketed as **"Uncategorized negative."**
> The last two themes are e-commerce-oriented; they rarely fire on food/quick-commerce
> brands, which is expected.

> **LLM aspect layer (optional).** When a Gemini key is configured, an aspect-based
> sentiment pass (`scripts/absa.py`) adds richer categories *on top* of these keywords —
> including **UI bug** and **Feature request**, which keyword rules structurally can't
> detect. It supplements, never replaces, the keyword themes, and the pipeline runs
> identically without it.

---

## 5. Time grain

All trends are aggregated **weekly** (ISO week). Weekly smooths out daily noise while
still being responsive enough to catch a spike within a few days.

---

## 6. Headline brand-health metrics

Computed per brand, per week:

- **Net Sentiment** = (positive mentions - negative mentions) / total mentions.
  Ranges -1 to +1. The single-number summary of how a brand is perceived that week.
- **Complaint Rate** = negative mentions / total mentions. The share of conversation
  that is negative.
- **Theme Mix** = share of complaints in each theme. Shows *what* the problem is.

---

## 7. Anomaly (spike) definition

A theme is **flagged as anomalous** in a given week if its complaint count exceeds:

> rolling mean (trailing 4 weeks) + 2 × rolling standard deviation

This is a simple, explainable statistical threshold. It means "meaningfully above
this theme's recent normal range." The 4-week window and 2σ threshold are tunable
and should be stated as assumptions.

> Requires a few weeks of accumulated data before it's meaningful — which is exactly
> why the pipeline is automated to collect daily from day one.

---

## 8. Outcome metric (for validation)

**App-store rating** = the brand's current Google Play average rating, pulled over
time. This is the real business metric the complaint signal is tested against:
*do complaint spikes lead rating drops?* A confirmed lead-time relationship is the
project's headline finding.
