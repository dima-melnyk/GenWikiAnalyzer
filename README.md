# wikipedia-trends

An [Agent Skill](https://agentskills.io/specification) that lets an AI agent
turn Wikimedia's public pageview data into a trend analysis, chart, and
one-page PDF report — built for B2C teams deciding which topic or language
market to invest in next. See `SKILL.md` for the agent-facing instructions;
this file is for a human looking at the repo.

## Layout

```
wikipedia-trends/
├── SKILL.md                    agent-facing instructions (read this first)
├── scripts/
│   ├── wiki_utils.py            HTTP + Wikimedia/Wikidata API wrappers, disk cache
│   ├── resolve_topic.py         CLI: topic (any language) → per-language article titles
│   ├── trend_stats.py           pure statistics: cleaning, spikes, trend, confidence
│   ├── analyze.py               CLI: fetch + analyze + chart + PDF, one call
│   └── report.py                chart (matplotlib) + one-page PDF (reportlab)
├── tests/
│   ├── test_trend_stats.py      10 tests against synthetic series
│   └── test_wiki_utils.py       9 tests for date/project normalization
├── references/
│   ├── methodology.md           why Theil-Sen / Kendall's tau / the confidence score
│   └── roadmap.md               how to extend this toward bigger/harder research
├── data/cache/                  disk cache for API responses (gitignore this)
├── requirements.txt
└── LICENSE.txt
```

## Quickstart

```bash
pip install -r requirements.txt
cd scripts
python3 resolve_topic.py --query "intermittent fasting" --langs pl,cs,uk
python3 analyze.py --series "pl:Post_przerywany,cs:Přerušovaný_půst" \
  --start 2024-01 --end 2026-01 --normalize \
  --title "Intermittent fasting: PL vs CZ" --output-dir ../output
```

## How this was built and checked

Built and reviewed with Claude (Claude Code / claude.ai). Concretely, what
that meant in practice and how the output was checked at each step:

- **Statistics module (`trend_stats.py`) — unit-tested, not eyeballed.**
  19 unit tests (`tests/`) run against synthetic pageview series with known
  properties: pure noise, steady compound growth, decline, a single
  artificial viral spike, and growth-with-one-spike-injected. The tests
  assert the *qualitative* claims the skill makes to a user — e.g. a
  smooth 5%/month series must come back `"up"` with `"high"`/`"medium"`
  confidence and p<0.05; a flat series with only sampling noise must
  **not** be called `"up"` or `"down"`; a series dominated by one spike
  must be flagged `"low"` confidence with the spike's share of views
  reported. This is what actually gives the confidence rating credibility
  — it's cheap to write a plausible-looking trend script, the tests are
  what confirm it behaves correctly on the cases the report depends on.
- **Report rendering — generated and visually inspected, twice.** The
  chart+PDF pipeline was run end-to-end against synthetic 24-month series
  in three languages (Polish/diacritics, Czech/diacritics, Ukrainian
  Cyrillic) and the resulting PDF was rasterized and viewed. First pass
  surfaced two real bugs that unit tests alone wouldn't have caught:
  (1) reportlab's default Helvetica font silently rendered Cyrillic and
  Czech diacritics as boxes — fixed by registering the DejaVu Sans TTF
  that already ships inside matplotlib (no new dependency); (2) the
  summary table's header cells were plain strings, which don't wrap in
  reportlab tables, so "Annualized growth" visually overlapped the next
  column — fixed by wrapping every cell in a `Paragraph`. Both fixes were
  re-verified by regenerating and re-viewing the PDF.
- **A live-path bug was caught by actually invoking the CLI, not just the
  library functions.** Running `analyze.py` end-to-end (even against a
  network-restricted sandbox, which returns HTTP 403 rather than a real
  API response — see below) surfaced that the date-normalization code
  padded a bare `YYYY-MM` into `...00` for the day component (e.g.
  `20240100`), an invalid calendar date. Unit-function tests of
  `trend_stats.py` in isolation wouldn't have caught this — it only shows
  up in the URL actually sent to the API. Fixed with a dedicated
  `normalize_date()` helper and 6 new unit tests, including one asserting
  the day component is never `"00"`.
- **What was *not* verified: a live call to the real Wikimedia API.** The
  development sandbox this was built in has no outbound network access
  (confirmed by 403s from a proxy, not real API errors), so the actual
  `requests.get()` round trip to `wikimedia.org`/`wikidata.org` could not
  be exercised here. Everything upstream of that call (URL construction,
  caching, retry logic) and everything downstream of it (parsing,
  statistics, rendering) was tested directly; the untested seam is
  specifically "does Wikimedia's API return exactly the JSON shape
  `wiki_utils.py` assumes." That shape is documented and stable, but
  please run the quickstart above with real network access before relying
  on this in production, and file an issue / open a PR if the live schema
  has drifted.
- **Running the full agent workflow on a fast/cheap model (e.g. Claude
  Haiku 4.5) was not performed** in this environment — doing so needs
  either the Claude Code CLI with `--model claude-haiku-4-5` and real
  network access, or an OpenRouter free-tier model, neither of which this
  sandbox has. The two-script design (topic resolution, then one
  fetch+analyze+render call, both emitting flat JSON) was specifically
  chosen to minimize the orchestration a small model has to get right —
  each script does one job, takes CLI flags rather than requiring
  generated code, and returns structured status fields (`"resolved"` /
  `"ambiguous"` / `"no_match"`, per-series `"error"`) so a smaller model
  doesn't have to infer what went wrong from prose. **Before treating this
  as done**, run it against a cheap tool-using model on a handful of the
  brief's example prompts and check whether it correctly reaches for
  `resolve_topic.py` before `analyze.py`, handles the `"ambiguous"` case
  by asking rather than guessing, and doesn't try to hand-write its own
  fetch code instead of calling the scripts.

## Running the tests

```bash
python3 -m unittest discover -s tests -v
# or, if pytest is installed:
python3 -m pytest tests/ -v
```

No network access is required for the test suite — it exercises
`trend_stats.py` and `wiki_utils.py`'s pure-Python helpers against
synthetic data.
