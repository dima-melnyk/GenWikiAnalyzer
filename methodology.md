# Methodology & API reference

Read this file when you need to explain *why* a number is what it is, when a
result looks surprising, or when extending the skill's analysis. For normal
day-to-day use, `SKILL.md` alone is enough.

## Data source

- **Per-article pageviews**: `GET https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{project}/{access}/{agent}/{article}/{granularity}/{start}/{end}`
- **Whole-wiki totals** (for normalization): same host, `.../aggregate/{project}/{access}/{agent}/all-agents/{granularity}/{start}/{end}`
- No API key. Wikimedia does require a descriptive `User-Agent` header (set
  once in `wiki_utils.USER_AGENT` — edit the contact info if you redeploy
  this skill under your own identity).
- Reliable data starts **2015-07**. Anything requested before that returns
  `404`, which `wiki_utils.fetch_per_article` turns into `{"error": "not_found"}`
  rather than raising.
- `agent=user` (the script's default) excludes bot/spider traffic. This is
  almost always what you want for "are people interested in X" — leave it
  unless the user specifically wants total server load.
- Full API docs: https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/reference/page-views.html

## Why titles are resolved through Wikidata, not guessed

Article titles differ by language in ways that aren't a matter of
translation or capitalization — "Intermittent fasting" is *"Post
przerywany"* on pl.wikipedia and *"Přerušovaný půst"* on cs.wikipedia.
Guessing a title (or asking a model to translate it) risks a silent 404 or,
worse, hitting a real but wrong article. Wikidata attaches one item (a QID)
to all the language editions of "the same" article; `resolve_topic.py` uses
`wbsearchentities` to find the QID for a free-text topic, then
`wbgetentities` to read its `sitelinks` — the authoritative per-language
title map. A language missing from `sitelinks` is itself a data point: the
topic has literally never been written up on that wiki.

## Trend estimation

1. **Log-space slope, not raw views.** Views are fit on `log(views)`, so
   the fitted slope translates directly into a percentage growth rate
   instead of an absolute "views per month" number that means nothing
   without more context.
2. **Theil-Sen, not OLS.** The slope is the *median of all pairwise
   slopes* (`scipy.stats.theilslopes`), which is robust to the single
   viral week that would otherwise dominate a least-squares fit. See
   `trend_stats.test_growing_series_with_one_spike_still_up_but_flagged`
   for a worked example: a steady 4%/month climb with one 15x spike
   injected still reports "up", not a spurious mega-trend.
3. **Significance via Kendall's tau.** `scipy.stats.kendalltau(time_index,
   views)` gives the same statistic as the (nonparametric) Mann-Kendall
   trend test, which makes no assumption that the noise is Gaussian — a
   safer default than a t-test for count data that is routinely spiky and
   right-skewed. `trend_direction` is only "up"/"down" when p < 0.05;
   otherwise it's "flat", even if the raw slope is nonzero.
4. **Spike detection** flags months whose month-over-month log-jump is a
   robust outlier (MAD-based z-score > 4). Spike months are reported by
   timestamp and their **share of total views** is computed — a trend
   where 50%+ of all views sit in one flagged month is a different claim
   ("one news cycle") than the same slope spread evenly across 24 months
   ("steady growth"), even though both might product a positive Theil-Sen
   slope.
5. **Confidence rating (High/Medium/Low)** is a simple points score built
   from: series length (≥24 points is best — it covers two full seasonal
   cycles), the trend p-value, and the spike share of total views. It is
   deliberately conservative — a short series or a spike-dominated series
   is capped at Low/Medium regardless of how large the slope looks. The
   exact thresholds are in `trend_stats.analyze_series`; each rating comes
   with plain-language `confidence_reasons` so you're never presenting a
   bare label without the reasoning behind it.

## Normalization (`--normalize`)

Comparing raw view counts across language wikis is misleading — pl.wikipedia
and, say, be-tarask.wikipedia have wildly different total readerships, so a
"bigger" raw number mostly measures wiki size. `--normalize` also fetches
each wiki's total traffic for the same period and computes the article's
`share_of_wiki_traffic_pct` per period — a fairer cross-language comparison
of *relative* interest. Report this alongside, not instead of, the raw trend,
since both are useful: raw growth answers "is interest growing", share
answers "how does this compare to everything else that language's readers
read."

## Caching

Every HTTP response is cached to `data/cache/` for 24h (article/aggregate
data) or 7 days (Wikidata lookups), keyed by the exact request URL+params.
This means:
- Re-running the same `analyze.py` call (e.g. after the agent decides to
  regenerate the PDF with a different title) doesn't re-hit the network.
- A **follow-up question that changes the date range or adds a language**
  is *not* currently served from cache for the unchanged portion — it's a
  fresh request. See `references/roadmap.md` for the incremental-fetch
  design that would fix this for larger-scale use.

## Known limitations (be upfront about these with the user)

- Pageviews measure *reading interest*, not willingness to pay or product
  demand — always frame conclusions as "worth investigating further," not
  "guaranteed market."
- A rising trend can reflect one-time press coverage, not durable interest
  — that's exactly what spike detection and confidence rating are for, but
  they reduce the risk, not eliminate it. When `confidence` is "low",
  say so plainly rather than leading with the growth percentage.
- Article existing ≠ topic exists in that culture's discourse at all — and
  article *not* existing in a language is itself informative (nobody has
  bothered to write it yet), separate from "exists but low traffic."
- Redirects: `per-article` pageviews are attributed to the *canonical*
  title. If Wikidata's sitelink title is itself a redirect target that
  later changed, the series can show an artificial jump at the rename
  date — cross-check any large sudden jump against the article's edit
  history before treating it as organic interest if the user pushes on it.
