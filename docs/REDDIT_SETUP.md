# Registering your Reddit app (the one step only you can do)

You need three credentials for PRAW: a **client ID**, a **client secret**, and a
**user agent**. Getting them takes about 3 minutes.

## Steps

1. Make sure you're logged into your Reddit account at https://www.reddit.com
2. Go to **https://www.reddit.com/prefs/apps**
3. Scroll to the bottom and click **"are you a developer? create an app…"** (or
   "create another app…").
4. Fill in the form:
   - **name:** `brand-health-tracker` (anything is fine)
   - **type:** select **"script"** ← important, this is the right type for personal
     data pulling
   - **description:** optional, leave blank
   - **about url:** leave blank
   - **redirect uri:** enter `http://localhost:8080` (required, but unused for a
     script app)
5. Click **"create app"**.

## Where to find your credentials

After creating, you'll see your app listed:

- **client ID** — the short string shown *just under the app name* (under the words
  "personal use script"). It's about 14 characters.
- **client secret** — the longer string labeled **"secret"**.
- **user agent** — you make this up; use the format:
  `brand-health-tracker by u/YOUR_REDDIT_USERNAME`

## Put them in your .env file

Copy `.env.example` to `.env` and paste your three values in. The `.env` file is
gitignored, so these secrets will never be committed to GitHub.

```bash
cp .env.example .env
# then open .env and paste your values
```

> **Never commit your client secret.** If you ever paste it somewhere public by
> accident, go back to the apps page and regenerate it.

Once your `.env` is filled in, you're ready for Phase 1 — pulling your first data.
