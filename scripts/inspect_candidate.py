#!/usr/bin/env python3
"""Show the corpus evidence behind one candidate, ready to paste into a record.

Curation needs the same three things every time: the highest-scored mentions,
their permalinks, and the fullname (`t1_…`/`t3_…`) that joins a record back to
the harvest. Grepping the JSONL by hand gets those wrong often enough to be
worth a script.

Usage:
    uv run scripts/inspect_candidate.py "Darn Tough"
    uv run scripts/inspect_candidate.py Zojirushi --limit 15 --durable-only
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HARVEST = REPO / "harvest" / "reddit"

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_candidates import DURABILITY_RE, NEGATIVE_RE, load_jsonl  # noqa: E402


def iso(created_utc) -> str:
    if not created_utc:
        return "?"
    return dt.datetime.fromtimestamp(created_utc, dt.timezone.utc).date().isoformat()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("phrase")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--durable-only", action="store_true")
    ap.add_argument("--min-score", type=int, default=0)
    args = ap.parse_args()

    pattern = re.compile(re.escape(args.phrase), re.I)
    hits = []

    for sub_dir in sorted(p for p in HARVEST.iterdir() if p.is_dir()):
        for post in load_jsonl(sub_dir / "posts.jsonl"):
            text = f"{post.get('title') or ''}\n{post.get('selftext') or ''}"
            if pattern.search(text):
                hits.append(("t3_" + post["id"], post, text, sub_dir.name))
        for comment in load_jsonl(sub_dir / "comments.jsonl"):
            text = comment.get("body") or ""
            if pattern.search(text):
                hits.append(("t1_" + comment["id"], comment, text, sub_dir.name))

    filtered = [
        h
        for h in hits
        if (h[1].get("score") or 0) >= args.min_score
        and (not args.durable_only or DURABILITY_RE.search(h[2]))
    ]
    filtered.sort(key=lambda h: h[1].get("score") or 0, reverse=True)

    print(f"{len(hits)} mentions of {args.phrase!r}, {len(filtered)} after filters\n")
    for ref, rec, text, sub in filtered[: args.limit]:
        marks = []
        if DURABILITY_RE.search(text):
            marks.append("durability")
        if NEGATIVE_RE.search(text):
            marks.append("negative")
        print(f"--- {ref}  r/{sub}  score={rec.get('score')}  {iso(rec.get('created_utc'))}"
              f"  [{', '.join(marks) or 'neutral'}]")
        print(f"    https://www.reddit.com{rec.get('permalink') or ''}")
        body = " ".join(text.split())
        print(f"    {body[:600]}{'…' if len(body) > 600 else ''}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
