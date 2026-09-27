#!/usr/bin/env python3
"""
resolve_topic.py — turn a topic described in natural language into the
exact article title used on each requested language's Wikipedia.

Why this exists: article titles are NOT translations of each other, and
guessing them (e.g. capitalizing the English title) silently produces a
404 or, worse, a wrong-but-real article. Wikidata is the authoritative
cross-language mapping: every Wikipedia article about the same real-world
topic is attached to one Wikidata item (QID), which lists the exact
sitelink title per language.

Usage:
  python resolve_topic.py --query "intermittent fasting" --langs pl,cs,en
  python resolve_topic.py --query "Пост przerywany" --langs pl,cs --search-lang pl

Output (JSON to stdout):
  - If one strong, unambiguous match is found: the resolved titles.
  - If the search is ambiguous: a list of candidates for a human/agent
    to disambiguate, plus a note to re-run with --qid.
  - You can skip search entirely and go straight to titles with --qid.

Exit code is always 0; check the JSON "status" field programmatically.
"""
import argparse
import json
import sys

from wiki_utils import wikidata_search, wikidata_sitelinks


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--query", help="Topic in free text, any language (skip if using --qid)")
    ap.add_argument("--search-lang", default="en", help="Language to interpret --query in (default: en)")
    ap.add_argument("--langs", required=True, help="Comma-separated Wikipedia language codes to resolve titles for, e.g. pl,cs,uk")
    ap.add_argument("--qid", help="Skip search and resolve titles directly from a known Wikidata QID (e.g. Q1234)")
    ap.add_argument("--limit", type=int, default=5, help="Max candidates to return when ambiguous (default 5)")
    args = ap.parse_args()

    langs = [l.strip() for l in args.langs.split(",") if l.strip()]

    if not args.query and not args.qid:
        print(json.dumps({"status": "error", "message": "Provide --query or --qid"}, ensure_ascii=False))
        sys.exit(0)

    if args.qid:
        result = wikidata_sitelinks(args.qid, langs)
        result["status"] = "resolved"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    candidates = wikidata_search(args.query, language=args.search_lang, limit=args.limit)
    if not candidates:
        print(json.dumps({
            "status": "no_match",
            "message": f"No Wikidata entity found for '{args.query}'. Try a different phrasing, "
                       f"a different --search-lang, or ask the user for the exact article title.",
        }, ensure_ascii=False))
        return

    if len(candidates) == 1:
        top = candidates[0]
        result = wikidata_sitelinks(top["qid"], langs)
        result["status"] = "resolved"
        result["search_candidates_considered"] = 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # Multiple candidates: resolve titles for the top one for convenience,
    # but surface all of them — the calling agent should show the
    # description of the top 2-3 to the user if there's any topical
    # ambiguity (e.g. "fasting" could mean the practice, a film, a person).
    top = candidates[0]
    top_resolved = wikidata_sitelinks(top["qid"], langs)
    print(json.dumps({
        "status": "ambiguous",
        "message": "Multiple Wikidata entities matched. Best guess is included as 'best_guess'. "
                    "If it looks wrong, re-run with --qid <QID> using one of 'candidates'.",
        "best_guess": top_resolved,
        "candidates": candidates,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
