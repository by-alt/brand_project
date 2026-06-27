# Dashboards

Two ways to view the data — pick whichever fits how you're deploying.

## 1. Streamlit app (dynamic, reads the DB live) — `app.py`

The full interactive app. Needs Python running.

```bash
streamlit run dashboard/app.py
```

Deploy free on Streamlit Community Cloud (connect your GitHub repo). Filters, root-cause,
anomaly-annotated trend, ABSA, and example complaints, served straight from the database.

## 2. Static interactive dashboard (no server) — `index.html`

A single self-contained HTML page with the same look and the same filters, written in
plain JavaScript. Host it anywhere static (GitHub Pages, Netlify, S3) — no Python needed.

- On its own it shows **built-in sample data** (so it's a great preview/portfolio link).
- When a `web_data.json` file sits next to it, it loads your **real** mentions instead and
  flips the badge to "Live data".

### Make it show your real data

```bash
python scripts/run_all.py          # collects + scores, and writes dashboard/web_data.json
# (or just: python scripts/export_dashboard.py  to refresh the JSON from existing data)
```

Then host the `dashboard/` folder. To preview locally (a plain file:// open will fall back
to sample data because browsers block local fetch, so use a tiny server):

```bash
cd dashboard && python -m http.server 8000   # then open http://localhost:8000/
```

### Deploying to GitHub Pages

Point Pages at the `dashboard/` folder (or copy `index.html` + `web_data.json` to your
Pages branch). The daily GitHub Action regenerates `web_data.json`, so commit it (or have
the Action commit it) to keep the hosted page current.

> The static dashboard uses one theme per mention and the most recent ~10 weeks, so it's a
> lightweight view. For the complete analysis, the Streamlit app and `scripts/insights.py`
> remain the source of truth.
