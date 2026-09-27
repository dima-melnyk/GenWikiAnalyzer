---
name: wikipedia-trends
description: Analyze Wikipedia pageview trends to help B2C product teams decide which topics to build and which languages to launch in. Use this whenever the user asks about interest, popularity, growth, or demand for a topic on Wikipedia, wants to compare a topic across language editions (e.g. "is X trending in Polish vs Czech Wikipedia"), asks whether a topic is worth adding to a product or app, wants to prioritize which language market or content topic to expand into, or asks for a shareable chart/report on Wikipedia reader interest. Also use it for follow-up questions that refine an earlier such request (different date range, additional languages, a related topic) — don't start from scratch, see "Handling follow-ups" below.
license: See LICENSE.txt
---

# Wikipedia Trends

Turns Wikimedia's public pageview data into a defensible answer to "should
we invest in this topic / this language?" — with a chart, a one-page PDF,
and an honest confidence rating so the user knows how much to trust the
number, not just what the number is.

## The two-script workflow

Almost every request is exactly two tool calls. Both scripts live in
`scripts/` and are run with `python3 <script>.py <args>`, each printing one
JSON object to stdout — read that JSON, don't parse stdout as prose.

```
Step 1 (only if you need it — see below): resolve_topic.py
  topic in any language  →  exact article title per requested language wiki

Step 2 (always): analyze.py
  project:title pairs + date range  →  trend stats + chart.png + report.pdf
```

### Step 1 — resolve the topic to per-language article titles

Skip this step entirely if the user already gave you exact article titles
(e.g. from a previous turn, or they pasted a Wikipedia URL). Otherwise:

```bash
cd scripts
python3 resolve_topic.py --query "intermittent fasting" --langs pl,cs,uk
```

Read the JSON `status` field:
- `"resolved"` → use `titles` directly as input to Step 2. Check
  `missing_langs`: an empty article for a requested language is itself a
  finding worth telling the user ("nobody has written this topic up in
  Ukrainian Wikipedia yet"), not an error to hide.
- `"ambiguous"` → multiple Wikidata entities matched (e.g. "fasting" could
  be the practice, a film, a person). Look at `candidates[].description`.
  If it's obvious which one the user means, proceed using `best_guess`.
  If genuinely unclear, ask the user to pick — don't silently guess on a
  real ambiguity.
- `"no_match"` → nothing found. Try rephrasing `--query`, try
  `--search-lang` matching the topic's likely origin language, or ask the
  user for the exact article title / a Wikipedia link.

`--langs` takes Wikipedia language codes (`pl`, `cs`, `uk`, `en`, `de`, ...
— these are the subdomain codes, e.g. `pl.wikipedia.org`). If the user
names a language in prose ("Polish", "Ukrainian"), map it to its code
yourself; don't ask the user for the code.

### Step 2 — fetch, analyze, and render

```bash
python3 analyze.py \
  --series "pl:Post_przerywany,cs:Přerušovaný_půst,uk:Інтервальне_голодування" \
  --start 2024-01 --end 2026-01 \
  --normalize \
  --title "Intermittent fasting: PL vs CZ vs UA" \
  --output-dir ../output
```

Key flags:
- `--series` — comma-separated `lang:Article_Title` pairs, straight from
  Step 1's `titles`, or from what the user/URL gave you. Spaces or
  underscores in titles both work.
- `--start` / `--end` — `YYYY-MM-DD` or `YYYY-MM`. Default is the last 24
  months if omitted — **24 months is deliberately the default**, not a
  suggestion: it's the minimum for the confidence rating to weigh in
  seasonality, so only shorten it if the user specifically wants a
  recent-window read (e.g. "did this spike this week") and say plainly
  that the trend read will be weaker as a result.
- `--normalize` — add this whenever comparing **across languages**. Raw
  view counts mostly reflect wiki size (pl.wikipedia vs. a small-language
  wiki), so cross-language comparisons without it are misleading. Not
  needed for a single-language, single-topic question.
- `--title` — used as the PDF/chart title and the output filename slug;
  make it descriptive of what's being compared.
- `--output-dir` — where `<slug>_chart.png` and `<slug>_report.pdf` land.
- `--granularity daily` — only for short, recent windows; monthly (default)
  is right for anything spanning a year or more.

The JSON on stdout has one entry per series under `"series"`, each with a
`"trend"` object (see field meanings below) and, when a fetch failed, an
`"error"`/`"hint"` instead — check for that per series rather than assuming
the whole call succeeded just because the process exited 0.

## Reading the trend output

Each series' `trend` object:

| field | meaning |
|---|---|
| `trend_direction` | `"up"` / `"down"` / `"flat"` — already accounts for statistical significance (p<0.05); a nonzero slope with `"flat"` means the noise is too large to call it a real trend |
| `annualized_growth_pct` | robust (Theil-Sen) growth rate, annualized — use this, not `total_change_pct`, as the headline number |
| `total_change_pct` | simple start-vs-end comparison — more intuitive but sensitive to noise at the endpoints; use as a secondary sanity check, not the headline |
| `confidence` | `"high"` / `"medium"` / `"low"` — **always state this alongside the number**, never report growth without it |
| `confidence_reasons` | plain-language reasons behind the rating — pull from here when explaining "why should I trust/not trust this" rather than inventing your own justification |
| `spike_months` / `spike_share_of_total_views_pct` | outlier months and how much of total traffic they account for — mention when non-trivial (the report PDF already surfaces this, but say it in your own summary too if it changes the takeaway) |
| `caveats` | standard methodology caveats (bot exclusion, pageviews ≠ willingness to pay, etc.) — the PDF includes these already; you don't need to repeat them verbatim in chat, but don't contradict them either |

**Never state a growth number without its confidence label.** "Interest in
X grew 40%/year (high confidence)" and "...grew 40%/year, though confidence
is low because it's mostly one viral month" are different claims — say
which one you actually found.

## Communicating results to the user

- Lead with the direction + confidence, not just the percentage: confidence
  is the whole point of doing statistics instead of eyeballing a chart.
  Convert the trend statistics into concrete decision-relevant language.
- Always surface the PDF/chart paths so the user can open and share them —
  don't just describe the chart in prose when a file was generated.
- If `missing_langs` came back non-empty in Step 1, mention it: an article
  not existing in a language is a real (if weak) signal that the topic
  hasn't reached that audience's Wikipedia yet.
- When comparing multiple languages/topics, rank them explicitly by
  `annualized_growth_pct` **among comparable-confidence entries** — don't
  let a high-growth-but-low-confidence entry outrank a modest-growth-
  high-confidence one without saying so.
- Ground "what to look into next" suggestions in the data you have (e.g.
  "the two lower-confidence languages had too little data — worth widening
  the date range before deciding") rather than generic advice.

## Handling follow-ups efficiently

Users will refine questions after the first answer — a wider date range,
one more language, a related topic, "now normalize that," etc. Treat these
as new `analyze.py` calls with the adjusted flags, not as a reason to
re-explain the whole workflow to the user or to re-run `resolve_topic.py`
for languages/articles you already resolved earlier in the conversation.
Responses are cached to disk for 24h, so re-running with the same
project/article/date range is cheap — you don't need to avoid it out of
efficiency concern, but you do still need to re-run (not reuse stale
numbers from memory) whenever a parameter actually changed.

If the user's follow-up is genuinely a new topic, go back to Step 1.

## Edge cases

- **Article not found / fetch error** — `analyze.py` reports this per
  series (`"error"` + `"hint"`) instead of failing the whole call. Don't
  guess a title yourself; re-run `resolve_topic.py` or ask the user.
- **Very new or very niche topic** — expect `"insufficient_data"` as the
  direction when there are fewer than 6 monthly data points. Say so
  plainly rather than forcing a direction out of too little data.
- **Language requested has no article at all** — this comes back as an
  empty entry in Step 1's `missing_langs`, not an analyze.py error; report
  it as "no article exists yet," which is informative on its own.
- **User wants more than ~5 languages/topics at once** — the chart and PDF
  table stay readable up to ~5-6 series; beyond that, either run separate
  reports per cluster of languages or note that the chart will get crowded
  and ask if they'd prefer that split.

## Deeper reference

For the statistical methodology (why Theil-Sen, why Kendall's tau, how the
confidence score is computed), the Wikidata resolution approach, API
constraints (data starts 2015-07, bot filtering, caching), and known
limitations to be upfront about — see `references/methodology.md`. Read it
before explaining *why* a result looks the way it does, or before changing
the analysis logic. For how to extend this skill toward bigger/harder
research questions, see `references/roadmap.md`.
