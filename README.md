# Brand Health Tracker

An automated voice-of-customer pipeline that monitors public customer complaints
about Indian consumer apps, detects when problems spike, and tests whether those
signals predict drops in the brands' app-store ratings.

> **Status:** All phases complete — pull, clean, score, ratings, anomaly alerts,
> root-cause, benchmarking, automation, validation, and a dashboard.

---

## The business question

**Which Indian consumer-app brands are losing customer goodwill fastest, on which
issues, and does that erosion show up in their app-store ratings before the
companies would otherwise notice?**

This is the kind of early-warning system a customer-experience or brand team would
actually use — instead of waiting for ratings to fall, they'd see the complaint
spike that causes it, days earlier.

---

## Brands tracked

Chosen to give two clean, apples-to-apples competitive comparisons (within-category
benchmarking is far more meaningful than comparing a food app to an e-commerce app):

| Brand    | Competitive set        | Why included                          |
|----------|------------------------|---------------------------------------|
| Zomato   | Food & quick commerce  | Benchmarked vs Swiggy, Blinkit, Zepto |
| Swiggy   | Food & quick commerce  | Benchmarked vs Zomato, Blinkit, Zepto |
| Blinkit  | Food & quick commerce  | Quick commerce vs Zepto               |
| Zepto    | Food & quick commerce  | Quick commerce vs Blinkit             |
| Meesho   | E-commerce             | Benchmarked vs Flipkart, Amazon       |
| Flipkart | E-commerce             | Benchmarked vs Meesho, Amazon         |
| Amazon   | E-commerce             | Benchmarked vs Meesho, Flipkart       |

> **Two competitive sets, ranked separately.** Each set is benchmarked on its own, since
> a food app and a marketplace have different complaint patterns. Brands and sets live in
> `config/definitions.py` and are easy to change.

---

## How it works (target architecture)

```
Google Play reviews ─┐  (primary, high-volume, on-topic, months of history)
Reddit (PRAW) ───────┼─▶ raw mentions ─▶ clean + tag ─▶ SQLite ─▶ analysis ─▶ dashboard + report
                     │
Play star ratings ───┘  (joined as the outcome metric)
```

Two complaint sources feed one pipeline. **Google Play review text** is the primary,
high-volume source — plentiful, on-topic, and going back months, so weekly trends and
anomaly baselines have depth immediately. **Reddit** adds an independent signal. Every
mention carries a `source`, so analyses can pool sources (sentiment, themes, anomalies,
benchmarking) or separate them (the rating correlation uses Reddit complaints vs Play
ratings to stay a genuine cross-source check). The whole thing runs daily via GitHub
Actions, so the dataset grows on its own.

---

## Repo structure

```
brand-health-tracker/
├── config/          # configuration (credentials loader, brand & theme definitions)
├── data/            # collected data (SQLite db + raw files) — gitignored
├── scripts/         # pipeline scripts (data pull, cleaning, analysis)
├── notebooks/       # exploratory analysis notebooks
├── dashboard/       # the Streamlit / BI dashboard
├── docs/            # METRICS.md — all metric definitions
├── .env.example     # template for your Reddit credentials
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Metric definitions

All metrics are defined explicitly in [`docs/METRICS.md`](docs/METRICS.md).
Defining these up front — and being able to justify them — is the core of doing
this like a real analyst. Read that file before writing any analysis code.

---

## Setup (do this once)

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Register a Reddit app and get your credentials (see
   [`docs/REDDIT_SETUP.md`](docs/REDDIT_SETUP.md)).
3. Copy `.env.example` to `.env` and paste your credentials in:
   ```bash
   cp .env.example .env
   ```
   `.env` is gitignored — your secrets never get committed.

---

## Running the pipeline (Phase 1)

Once your `.env` is filled in (see setup above), pull data with:

```bash
python scripts/pull_data.py                  # ~50 posts per term, last month
python scripts/pull_data.py --limit 100 --comments 30 --time week
```

It searches the configured subreddits for each brand, matches mentions on a
**whole-word** basis (so "Zomato" won't match inside "zomatouniverse"), and stores
posts and their top comments in `data/brand_health.db`. Re-running is safe — it
skips anything already collected, so the dataset just grows over time.

### Verify it without going live

The logic is covered by an offline test suite that uses mock Reddit objects — no
credentials or network needed:

```bash
python tests/test_pipeline.py
```

It checks brand matching, false-positive rejection, multi-brand posts, de-duplication,
and the full pull flow.

## Cleaning the data (Phase 2)

After pulling, clean and structure it:

```bash
python scripts/clean_data.py
```

This reads the raw `mentions` table and writes an analysis-ready `clean_mentions`
table. The raw table is **never modified**, so cleaning is fully reproducible — change
a rule and re-run, and the clean table is rebuilt from scratch.

Cleaning **drops**: deleted/removed content, bot posts (known bot accounts + bot
phrasing), empty/noise text, false matches (where the brand only appeared inside a
stripped URL), and duplicates. It **normalizes**: HTML entities, Unicode, markdown
links and formatting, bare URLs, and whitespace. Case is deliberately preserved
because Phase 3's sentiment scoring reads capitalization as emphasis.

Run `python tests/test_cleaning.py` to verify the cleaning logic against realistic
messy input.

## Roadmap

This project follows a phased build (MVP first, then upgrades). See the full roadmap
for the step-by-step plan and what's essential vs. stretch.

## Full pipeline commands

```bash
pip install -r requirements.txt

python scripts/fetch_reviews.py   # 1. PRIMARY source: Google Play review text (high volume)
python scripts/pull_data.py       # 1b. Reddit mentions (independent, lower volume)
python scripts/fetch_ratings.py   # 1c. snapshot Google Play star ratings (outcome metric)
python scripts/clean_data.py      # 2. clean + structure
python scripts/analyze.py         # 3. sentiment + themes + aggregation
python scripts/insights.py        # 5-8. ratings link, anomalies, root-cause, benchmark

python scripts/run_all.py         # or run the whole pipeline in one go
streamlit run dashboard/app.py    # 10. the dashboard
python scripts/validate.py        # 9. prove anomaly detection catches a known spike
```

Run the test suites anytime (no network needed):

```bash
python tests/test_pipeline.py    # Phase 1 (Reddit pull)
python tests/test_cleaning.py    # Phase 2
python tests/test_analysis.py    # Phase 3
python tests/test_insights.py    # Phases 5-8
python tests/test_reviews.py     # Google Play review ingestion
```

## Automation (Phase 4)

`.github/workflows/daily.yml` runs the pipeline daily and commits the updated database
back to the repo, so the dataset grows on its own. To enable it, add three repository
secrets (Settings -> Secrets and variables -> Actions): `REDDIT_CLIENT_ID`,
`REDDIT_CLIENT_SECRET`, and `REDDIT_USER_AGENT`.

## Telling the story

`docs/REPORT.md` is a report template — fill it with your real numbers and lead with the
finding and recommendation. That write-up plus the dashboard is what a hiring manager sees.

## Resume bullets (fill in your real numbers)

- Built an automated voice-of-customer pipeline (Python, SQL, GitHub Actions) tracking
  four consumer brands; complaint spikes [predicted app-rating drops with ~N days' lead].
- Designed anomaly-detection alerting that flags abnormal complaint themes weekly,
  replacing manual dashboard monitoring, validated against a known spike event.
- Shipped a layered data pipeline (raw -> clean -> scored) with a 67-check test suite
  and a Streamlit dashboard for competitive benchmarking.
