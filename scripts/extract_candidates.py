#!/usr/bin/env python3
"""Rank product candidates out of the harvested corpus.

This does not decide anything. It surfaces the brand/product strings that
appear most often *next to durability language*, so that curation starts from
the corpus rather than from memory. Whether a candidate becomes a record in
data/ is a judgement made by a human or an agent reading the actual quotes.

Method, deliberately blunt and inspectable:

1. Take every harvested post title, selftext and comment body.
2. Pull candidate product names: runs of 1-3 capitalised tokens, plus
   capitalised-token-followed-by-model-number ("Vitamix 5200", "Sawyer Squeeze").
3. Score each candidate by the number of *distinct* items it appears in,
   weighted up when the same item also contains durability language, and
   weighted down when the containing item scored poorly.
4. Emit dist/candidates.csv with, for every candidate, the three highest-scored
   verbatim excerpts so the next reader can judge without re-reading the corpus.

Usage:
    uv run scripts/extract_candidates.py
    uv run scripts/extract_candidates.py --min-mentions 5 --top 400
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HARVEST = REPO / "harvest" / "reddit"
DIST = REPO / "dist"

# Phrases that mark a durability claim rather than a passing mention. Kept
# explicit and editable — this list is the single biggest lever on precision.
DURABILITY_PATTERNS = [
    r"\b(?:still|been)\s+(?:going|working|running|used?|use)\b",
    r"\b\d{1,2}\+?\s*(?:years?|yrs?|decades?)\b",
    r"\bsince\s+(?:19|20)\d{2}\b",
    r"\bbought\s+(?:it\s+)?in\s+(?:19|20)\d{2}\b",
    r"\blifetime\s+(?:warranty|guarantee)\b",
    r"\bhand(?:s)?\s+down\b|\bhanded\s+down\b|\binherited\b",
    r"\brepairable\b|\brebuild(?:able)?\b|\bparts?\s+are\s+available\b",
    r"\bnever\s+(?:failed|broken|died)\b",
    r"\bouhtlast|\boutlast(?:ed|s)?\b",
    r"\bbuy\s+it\s+for\s+life\b|\bbifl\b",
]
DURABILITY_RE = re.compile("|".join(DURABILITY_PATTERNS), re.I)

# Negative markers: the mention is about a failure or a company reneging. Kept
# so `avoid` candidates surface rather than being filtered out as noise.
NEGATIVE_RE = re.compile(
    r"\b(?:broke|broken|failed|garbage|junk|planned obsolescence|went downhill|"
    r"not what it used to be|refused|denied (?:my |the )?warranty|avoid)\b",
    re.I,
)

CANDIDATE_RE = re.compile(
    r"\b([A-Z][A-Za-z&'.-]{2,}(?:\s+[A-Z][A-Za-z&'.-]{1,}){0,2}"
    r"(?:\s+[A-Z0-9][A-Za-z0-9-]*\d[A-Za-z0-9-]*)?)"
)

# Words that pass the capitalisation test but never name a product. Sentence
# openers dominate this list, which is why it is long.
STOPWORDS = {
    "the", "this", "that", "these", "those", "they", "there", "then", "than",
    "and", "but", "for", "not", "you", "your", "yours", "our", "ours", "his",
    "her", "hers", "its", "their", "theirs", "who", "what", "when", "where",
    "why", "how", "all", "any", "some", "one", "two", "three", "just", "also",
    "even", "still", "very", "more", "most", "much", "many", "less", "least",
    "have", "has", "had", "was", "were", "been", "being", "will", "would",
    "can", "could", "should", "shall", "may", "might", "must", "does", "did",
    "yes", "no", "ok", "okay", "edit", "reddit", "bifl", "buy", "buying",
    "bought", "get", "got", "make", "made", "use", "used", "using", "great",
    "good", "best", "nice", "love", "thanks", "thank", "yeah", "yep", "nope",
    "honestly", "personally", "basically", "literally", "actually", "however",
    "although", "though", "because", "since", "after", "before", "while",
    "every", "each", "both", "either", "neither", "another", "same", "such",
    "here", "now", "today", "yesterday", "tomorrow", "never", "always",
    "usa", "america", "american", "europe", "european", "china", "chinese",
    "amazon", "ebay", "google", "youtube", "imgur",
    "op", "tl", "dr", "pro", "con", "lol", "imo", "imho", "fwiw", "ymmv",
    "my", "me", "we", "us", "it", "if", "in", "on", "at", "to", "of", "as",
    "so", "or", "do", "be", "is", "am", "are", "an", "a", "i",
}


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def iter_items():
    """Yield (text, score, url, kind, subreddit) for every harvested item."""
    for sub_dir in sorted(HARVEST.iterdir()) if HARVEST.exists() else []:
        if not sub_dir.is_dir():
            continue
        sub = sub_dir.name
        for post in load_jsonl(sub_dir / "posts.jsonl"):
            text = f"{post.get('title') or ''}\n{post.get('selftext') or ''}"
            yield (
                text,
                post.get("score") or 0,
                f"https://www.reddit.com{post.get('permalink') or ''}",
                "post",
                sub,
            )
        for comment in load_jsonl(sub_dir / "comments.jsonl"):
            yield (
                comment.get("body") or "",
                comment.get("score") or 0,
                f"https://www.reddit.com{comment.get('permalink') or ''}",
                "comment",
                sub,
            )


def candidates_in(text: str) -> set[str]:
    found = set()
    for match in CANDIDATE_RE.finditer(text):
        phrase = match.group(1).strip(" .,-'&")
        tokens = phrase.split()
        if not tokens:
            continue
        # Drop anything whose first token is a sentence opener rather than a name.
        if tokens[0].lower() in STOPWORDS:
            continue
        if all(t.lower() in STOPWORDS for t in tokens):
            continue
        if len(phrase) < 3 or phrase.isupper() and len(phrase) < 3:
            continue
        found.add(phrase)
    return found


def excerpt(text: str, phrase: str, width: int = 220) -> str:
    idx = text.find(phrase)
    if idx < 0:
        return text[:width].replace("\n", " ").strip()
    start = max(0, idx - width // 3)
    return text[start : start + width].replace("\n", " ").strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-mentions", type=int, default=3)
    ap.add_argument("--top", type=int, default=500)
    args = ap.parse_args()

    mentions: dict[str, int] = defaultdict(int)
    durable_hits: dict[str, int] = defaultdict(int)
    negative_hits: dict[str, int] = defaultdict(int)
    weight: dict[str, int] = defaultdict(int)
    subs: dict[str, set[str]] = defaultdict(set)
    quotes: dict[str, list[tuple[int, str, str]]] = defaultdict(list)

    items = 0
    for text, score, url, _kind, sub in iter_items():
        if not text or len(text) < 12:
            continue
        items += 1
        durable = bool(DURABILITY_RE.search(text))
        negative = bool(NEGATIVE_RE.search(text))
        for phrase in candidates_in(text):
            mentions[phrase] += 1
            subs[phrase].add(sub)
            weight[phrase] += max(score, 0) + (50 if durable else 0)
            if durable:
                durable_hits[phrase] += 1
            if negative:
                negative_hits[phrase] += 1
            quotes[phrase].append((score, excerpt(text, phrase), url))

    rows = []
    for phrase, count in mentions.items():
        if count < args.min_mentions or durable_hits[phrase] == 0:
            continue
        top_quotes = sorted(quotes[phrase], key=lambda q: q[0], reverse=True)[:3]
        rows.append(
            {
                "candidate": phrase,
                "mentions": count,
                "durability_mentions": durable_hits[phrase],
                "negative_mentions": negative_hits[phrase],
                "weight": weight[phrase],
                "subreddits": ";".join(sorted(subs[phrase])),
                "likely_verdict": "avoid"
                if negative_hits[phrase] > durable_hits[phrase]
                else "recommended",
                "quote_1": top_quotes[0][1] if top_quotes else "",
                "url_1": top_quotes[0][2] if top_quotes else "",
                "quote_2": top_quotes[1][1] if len(top_quotes) > 1 else "",
                "url_2": top_quotes[1][2] if len(top_quotes) > 1 else "",
                "quote_3": top_quotes[2][1] if len(top_quotes) > 2 else "",
                "url_3": top_quotes[2][2] if len(top_quotes) > 2 else "",
            }
        )

    rows.sort(key=lambda r: (r["durability_mentions"], r["weight"]), reverse=True)
    rows = rows[: args.top]

    DIST.mkdir(parents=True, exist_ok=True)
    out = DIST / "candidates.csv"
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["candidate"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"scanned {items} items, {len(mentions)} raw candidates")
    print(f"wrote {len(rows)} ranked candidates to {out.relative_to(REPO)}")
    for row in rows[:15]:
        print(
            f"  {row['durability_mentions']:>4} durability / {row['mentions']:>5} total  "
            f"{row['candidate']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
