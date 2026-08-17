#!/usr/bin/env python3
"""Populate data/quotes.json from the harvested corpus.

Quotes are extracted by tooling rather than typed by hand, for one reason: a
hand-copied quote is a paraphrase waiting to happen, and the whole value of an
evidence entry is that the sentence is exactly what the source said. Every
`evidence[].ref` in data/ is looked up in the corpus and its text captured
verbatim.

`data/quotes.json` is committed. It is what keeps the directory readable after
`harvest/**/comments.jsonl` has been cleaned up or has drifted — a deleted
comment stays quotable here.

Usage:
    uv run scripts/fill_quotes.py            # add quotes for refs not yet held
    uv run scripts/fill_quotes.py --refresh  # re-read every ref from the corpus
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "recommendations"
HARVEST = REPO / "harvest" / "reddit"
QUOTES = REPO / "data" / "quotes.json"

MAX_QUOTE = 500


def wanted_refs() -> dict[str, str | None]:
    """Every ref cited in data/, mapped to the brand it was cited for.

    The brand is used to pick which sentence of a long comment to quote.
    """
    refs: dict[str, str | None] = {}
    for path in sorted(DATA.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for rec in doc.get("recommendations") or []:
            for ev in rec.get("evidence") or []:
                ref = ev.get("ref")
                if ref:
                    refs.setdefault(ref, rec.get("brand"))
    return refs


def load_corpus() -> dict[str, dict]:
    corpus: dict[str, dict] = {}
    if not HARVEST.exists():
        return corpus
    for sub_dir in sorted(p for p in HARVEST.iterdir() if p.is_dir()):
        for name, prefix, text_fields in (
            ("posts.jsonl", "t3_", ("title", "selftext")),
            ("comments.jsonl", "t1_", ("body",)),
        ):
            path = sub_dir / name
            if not path.exists():
                continue
            with path.open() as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    rec["_fields"] = text_fields
                    rec["_sub"] = sub_dir.name
                    corpus[prefix + rec["id"]] = rec
    return corpus


def pick_quote(rec: dict, brand: str | None) -> str:
    fields = rec["_fields"]
    if fields == ("title", "selftext"):
        # A post's title is the claim; the body is context. Quote the title,
        # extending into the body only when the title is a bare label.
        title = " ".join((rec.get("title") or "").split())
        body = " ".join((rec.get("selftext") or "").split())
        if len(title) >= 60 or not body:
            return title[:MAX_QUOTE]
        return f"{title} — {body}"[:MAX_QUOTE]

    text = " ".join((rec.get("body") or "").split())
    if not brand:
        return text[:MAX_QUOTE]

    # Quote the sentence that actually mentions the brand, plus the one after
    # it, so a claim does not lose its qualifier.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for i, sentence in enumerate(sentences):
        if re.search(re.escape(brand), sentence, re.I):
            chunk = " ".join(sentences[i : i + 2])
            return chunk[:MAX_QUOTE]
    return text[:MAX_QUOTE]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    refs = wanted_refs()
    held = json.loads(QUOTES.read_text()) if QUOTES.exists() else {}
    todo = [r for r in refs if args.refresh or r not in held]

    if not todo:
        print(f"{len(refs)} refs cited, all already in {QUOTES.relative_to(REPO)}")
        return 0

    corpus = load_corpus()
    if not corpus:
        print(
            "harvest/ is empty — run scripts/harvest_reddit.py first "
            "(the comment corpus is gitignored)",
        )
        return 1

    missing = []
    for ref in todo:
        rec = corpus.get(ref)
        if rec is None:
            missing.append(ref)
            continue
        created = rec.get("created_utc")
        held[ref] = {
            "quote": pick_quote(rec, refs[ref]),
            "score": rec.get("score"),
            "date": dt.datetime.fromtimestamp(created, dt.timezone.utc)
            .date()
            .isoformat()
            if created
            else None,
            "url": "https://www.reddit.com" + (rec.get("permalink") or ""),
            "subreddit": rec.get("_sub"),
        }

    QUOTES.write_text(json.dumps(held, indent=2, sort_keys=True) + "\n")
    print(f"{len(refs)} refs cited, {len(held)} quoted, {len(missing)} not in corpus")
    for ref in missing:
        # Not an error: a ref can point at a post outside the current harvest
        # window, or at a comment that has since been deleted.
        print(f"  missing: {ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
