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
├── references/
│   ├── methodology.md           why Theil-Sen / Kendall's tau / the confidence score
│   └── roadmap.md               how to extend this toward bigger/harder research
```
