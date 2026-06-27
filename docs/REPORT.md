# Brand Health Report — Template

> Fill this in with **your real numbers** after the pipeline has collected a few
> weeks of data. The structure is what matters: lead with the finding and the
> recommendation, then show the evidence. Write it as if briefing a product manager.
> The bracketed example below is illustrative — replace it.

---

## TL;DR (write this last, put it first)

> _Example:_ Across four delivery apps over six weeks, **delivery-time complaints are
> the single biggest driver of negative sentiment**, and weeks with complaint spikes
> were followed by measurable app-rating dips. The clearest action is to treat a
> delivery-complaint spike as an early-warning signal for the CX team.

## The question

Which consumer-app brands are losing customer goodwill fastest, on which issues, and
does it show up in their app-store ratings before the company would otherwise notice?

## What I found

1. **Biggest complaint theme.** _[e.g. Delivery accounts for X% of all complaints,
   ahead of Refunds (Y%) and App bugs (Z%).]_
2. **Who's worst affected.** _[e.g. Brand A's complaint rate runs N× Brand B's on
   refunds.]_
3. **The leading-indicator link.** _[e.g. complaint spikes preceded a rating drop of
   ~0.X stars, correlation r = -0.XX over W weeks.]_

## Evidence

- **Method:** collected public Reddit mentions of each brand via the official API,
  cleaned and normalized them, scored sentiment with VADER, tagged complaint themes by
  keyword, and aggregated weekly. App-store ratings were captured daily as the outcome
  metric. _(See README and docs/METRICS.md for exact definitions.)_
- **Charts to include:** weekly net-sentiment trend per brand; theme breakdown bar
  chart; the anomaly-alert table; the complaints-vs-rating scatter/correlation.
- **Spot-checks:** _[paste 2-3 real example mentions per theme so the reader trusts the
  labels.]_

## Recommendation

> _Example:_ Stand up a weekly alert on delivery-complaint volume per city. When it
> breaches the normal range, route it to the logistics + CX teams before it reaches the
> app store. Prioritize the region/brand with the strongest complaint-to-rating link.

## Honest limitations

- Reddit skews toward certain demographics; it's a signal, not a census.
- VADER misses sarcasm and Hinglish nuance; the complaint flag compensates by also
  using theme keywords.
- The rating correlation strengthens as more weeks accumulate; early weeks are
  directional, not conclusive.

## What I'd build next

Root-cause auto-summaries per city, a sled of additional brands, and a lightweight
model to replace keyword theming once there's enough labeled data.
