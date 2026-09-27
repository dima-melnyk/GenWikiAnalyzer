#!/usr/bin/env python3
"""
analyze.py — the main entry point. Fetches Wikipedia pageview trends for
one or more (language, article) pairs, computes a robust trend estimate
with a confidence rating, and produces a chart PNG + one-page PDF report,
plus a JSON summary to stdout for the calling agent to narrate to the user.

This is intentionally a single script that does fetch + analyze + render
in one call, so an agent only needs 1-2 tool calls per question (resolve
topic, then analyze) rather than orchestrating many small steps.

Usage:
  python analyze.py --series "pl:Post_przerywany,cs:Přerušovaný_půst" \\
      --start 2024-01 --end 2026-01 --title "Intermittent fasting: PL vs CZ" \\
      --normalize --output-dir ./output

  # Single series, default last-24-months window:
  python analyze.py --series "uk:Астрономія" --title "Astronomy interest in Ukrainian Wikipedia"

--series format: comma-separated lang:Article_Title pairs. Use the exact
title returned by resolve_topic.py (underscores or spaces both work).

Output: prints one JSON object to stdout with per-series trend stats and
the paths of the generated chart/PDF, and writes those files to
--output-dir (default: ./output, relative to the current directory).
"""
import argparse
import datetime as dt
import json
import os
import sys

from wiki_utils import fetch_per_article, fetch_aggregate, normalize_project
from trend_stats import analyze_series, to_dict
from report import make_chart, build_pdf_report


def _default_dates():
    end = dt.date.today().replace(day=1)
    start = (end - dt.timedelta(days=730)).replace(day=1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _period_label(start: str, end: str) -> str:
    """Format a period label. Accepts either YYYY-MM-DD... strings or
    Wikimedia's raw YYYYMMDDHH timestamps — both start with YYYYMM."""
    def fmt(s: str) -> str:
        digits = s.replace("-", "")
        return f"{digits[0:4]}-{digits[4:6]}" if len(digits) >= 6 else s
    return f"{fmt(start)} → {fmt(end)}"


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text)[:60].strip("_") or "report"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--series", required=True,
                     help="Comma-separated lang:Article_Title pairs, e.g. 'pl:Post_przerywany,cs:Přerušovaný_půst'")
    ap.add_argument("--start", help="YYYY-MM-DD (default: 24 months ago)")
    ap.add_argument("--end", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--granularity", choices=["daily", "monthly"], default="monthly")
    ap.add_argument("--normalize", action="store_true",
                     help="Also fetch each wiki's total traffic and report the article's share of "
                          "readership, so comparisons across very different-sized language wikis are fairer.")
    ap.add_argument("--title", default="Wikipedia interest trend", help="Report title")
    ap.add_argument("--output-dir", default="./output", help="Where to write chart.png and report.pdf")
    ap.add_argument("--no-pdf", action="store_true", help="Skip PDF generation, JSON summary only (faster)")
    args = ap.parse_args()

    start, end = args.start, args.end
    if not start or not end:
        d_start, d_end = _default_dates()
        start = start or d_start
        end = end or d_end

    pairs = []
    for chunk in args.series.split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            print(json.dumps({"status": "error",
                               "message": f"Bad --series entry '{chunk}', expected lang:Article_Title"}))
            sys.exit(1)
        lang, title = chunk.split(":", 1)
        pairs.append((lang.strip(), title.strip()))

    os.makedirs(args.output_dir, exist_ok=True)

    series_for_chart = {}
    rows = []
    per_series_json = []
    any_data = False

    for lang, article in pairs:
        project = normalize_project(lang)
        raw = fetch_per_article(project, article, start, end, granularity=args.granularity)
        label = f"{project} — {article.replace('_', ' ')}"

        if raw.get("error") and not raw.get("items"):
            per_series_json.append({
                "label": label, "lang": lang, "project": project, "article": article,
                "error": raw["error"],
                "hint": "Article may not exist on this wiki, or the title doesn't match exactly — "
                        "try resolve_topic.py to get the exact per-language title.",
            })
            rows.append({"label": label, "period": _period_label(start, end), "total_change_pct": None,
                         "annualized_growth_pct": None, "trend_direction": "no_data", "confidence": "low",
                         "confidence_reasons": [f"Could not fetch data: {raw['error']}"]})
            continue

        any_data = True
        series_for_chart[label] = raw["items"]
        trend = analyze_series(raw["items"])
        trend_dict = to_dict(trend)

        entry = {"label": label, "lang": lang, "project": project, "article": article, "trend": trend_dict}

        if args.normalize:
            agg = fetch_aggregate(project, start, end, granularity=args.granularity)
            if not agg.get("error"):
                agg_by_ts = {it["timestamp"]: it["views"] for it in agg["items"]}
                shares = []
                for it in raw["items"]:
                    total = agg_by_ts.get(it["timestamp"])
                    if total:
                        shares.append({"timestamp": it["timestamp"], "share_pct": round(it["views"] / total * 100, 5)})
                entry["share_of_wiki_traffic_pct"] = shares
                if shares:
                    entry["mean_share_of_wiki_traffic_pct"] = round(
                        sum(s["share_pct"] for s in shares) / len(shares), 5)
            else:
                entry["normalize_error"] = agg["error"]

        per_series_json.append(entry)
        rows.append({
            "label": label,
            "period": _period_label(trend_dict["start"] or start, trend_dict["end"] or end),
            "total_change_pct": trend_dict["total_change_pct"],
            "annualized_growth_pct": trend_dict["annualized_growth_pct"],
            "trend_direction": trend_dict["trend_direction"],
            "confidence": trend_dict["confidence"],
            "confidence_reasons": trend_dict["confidence_reasons"],
        })

    result = {
        "status": "ok" if any_data else "no_data",
        "title": args.title,
        "period": _period_label(start, end),
        "granularity": args.granularity,
        "normalized": args.normalize,
        "series": per_series_json,
        "chart_path": None,
        "pdf_path": None,
    }

    if any_data:
        slug = _slug(args.title)
        chart_path = os.path.join(args.output_dir, f"{slug}_chart.png")
        make_chart(series_for_chart, args.title, chart_path)
        result["chart_path"] = os.path.abspath(chart_path)

        if not args.no_pdf:
            global_caveats = rows[0]["confidence_reasons"] if False else []
            # collect caveats from the trend results (deduped)
            caveats = []
            for entry in per_series_json:
                for c in entry.get("trend", {}).get("caveats", []):
                    if c not in caveats:
                        caveats.append(c)
            pdf_path = os.path.join(args.output_dir, f"{slug}_report.pdf")
            build_pdf_report(
                out_path=pdf_path,
                title=args.title,
                subtitle=f"Wikipedia reader-interest trend · {_period_label(start, end)} · {args.granularity}",
                chart_path=chart_path,
                rows=rows,
                global_caveats=caveats or ["No additional caveats flagged for this data."],
                recommendation=(
                    "Cross-check any 'high' confidence growth claim against category-level context "
                    "(is the whole subject area growing, or just this article?) before committing "
                    "product investment based on it."
                ),
            )
            result["pdf_path"] = os.path.abspath(pdf_path)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
