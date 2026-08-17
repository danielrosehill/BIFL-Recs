#!/usr/bin/env python3
"""Rank product candidates out of the harvested corpus.

This does not decide anything. It surfaces the brand/product strings that
appear most often *next to durability language*, so that curation starts from
the corpus rather than from memory. Whether a candidate becomes a record in
data/ is a judgement made by a human or an agent reading the actual quotes.

Method, deliberately blunt and inspectable:

1. Take every harvested post title, selftext and comment body.
2. Learn which words are actually proper nouns *from the corpus itself*: count
   how often each word appears lowercase versus capitalised mid-sentence. A
   word written lowercase most of the time is an English word that happened to
   start a sentence ("Mine", "People", "Like"); a word almost always
   capitalised is a name ("Patagonia", "Vitamix"). This replaces guessing at a
   stopword list, which does not survive contact with 40,000 comments.
3. Pull candidate product names: runs of 1-3 capitalised tokens, plus
   capitalised-token-followed-by-model-number ("Vitamix 5200", "Sawyer Squeeze").
4. Score each candidate by the number of *distinct* items it appears in,
   weighted up when the same item also contains durability language.
5. Emit dist/candidates.csv with, for every candidate, the three highest-scored
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
    r"\b([A-Z][A-Za-z&'.-]{1,}(?:\s+[A-Z][A-Za-z&'.-]{1,}){0,2}"
    r"(?:\s+[A-Z0-9][A-Za-z0-9-]*\d[A-Za-z0-9-]*)?)"
)

WORD_RE = re.compile(r"\b([A-Za-z][A-Za-z'-]{1,})\b")

# Contractions survive the capitalisation test and swamp everything else
# ("I've", "It's", "That's"), so they are rejected structurally rather than
# enumerated.
CONTRACTION_RE = re.compile(r"['’](?:s|m|d|re|ll|ve|t)\b", re.I)

# Proper nouns that are real names but never products. Small by design — the
# corpus-driven test in `learn_proper_nouns` does the heavy lifting, and every
# entry added here is a judgement that should be visible.
NON_PRODUCT_NAMES = {
    "reddit", "bifl", "buyitforlife", "amazon", "ebay", "google", "youtube",
    "imgur", "facebook", "instagram", "craigslist", "etsy", "walmart",
    "costco", "target", "goodwill", "aliexpress", "temu", "wirecutter",
    "usa", "america", "american", "europe", "european", "china", "chinese",
    "japan", "japanese", "germany", "german", "canada", "canadian", "uk",
    "britain", "british", "england", "india", "mexico", "vietnam", "taiwan",
    "christmas", "thanksgiving", "easter", "hanukkah", "covid", "reddit's",
    "edit", "tldr", "eli", "ymmv", "imho", "fwiw", "op",
    # Acronyms and units that pass the mid-sentence capital test because they
    # are never written lowercase. A general "reject all-caps" rule cannot be
    # used: IKEA and REI are real brands.
    "usb", "usd", "cad", "gbp", "eur", "led", "lcd", "oled", "gps", "wifi",
    "bluetooth", "diy", "iirc", "afaik", "psa", "oem", "abs", "pvc", "ptfe",
    "nsfw", "til", "eu", "vat", "ac", "dc", "hvac", "suv", "atv",
    # Places, which show up constantly in "made in …" arguments.
    "australia", "australian", "texas", "maine", "california", "vermont",
    "scotland", "ireland", "italy", "italian", "france", "french", "sweden",
    "swedish", "switzerland", "swiss", "korea", "korean", "thailand",
    "bangladesh", "portugal", "poland", "turkey", "brazil", "spain",
    # Common nouns that only ever appear capitalised as part of a compound
    # ("Dutch oven") or in bot output.
    "dutch", "delete", "remindme", "reminders", "bot", "mod", "mods",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "january", "february", "march", "april", "june", "july",
    "august", "september", "october", "november", "december",
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


def learn_proper_nouns(texts: list[str], min_mid_sentence: int = 3) -> set[str]:
    """Return the lowercased words the corpus treats as names.

    The discriminator is capitalisation *away from the start of a sentence*.
    Sentence-initial capitals carry no information — "Mine", "People", "Wow"
    and "Patagonia" all get one. Only a name keeps its capital in the middle of
    a sentence, so a word counts as a name here when it appears mid-sentence
    capitalised at least `min_mid_sentence` times and is capitalised in at
    least half of its mid-sentence appearances.

    This is why the stopword list stayed short: sentence openers, interjections
    and ordinary nouns are all removed by the same rule, learned from the
    corpus rather than guessed at in advance.
    """
    mid_upper: dict[str, int] = defaultdict(int)
    mid_lower: dict[str, int] = defaultdict(int)

    for text in texts:
        for match in WORD_RE.finditer(text):
            start = match.start()
            # Walk back over quotes, brackets and whitespace to the last
            # meaningful character; if it is terminal punctuation or nothing at
            # all, this word opens a sentence and tells us nothing.
            i = start - 1
            while i >= 0 and text[i] in " \t\"'“‘([*_>":
                i -= 1
            if i < 0 or text[i] in ".!?\n:;-–—•":
                continue
            word = match.group(1)
            if word[0].isupper():
                mid_upper[word.lower()] += 1
            else:
                mid_lower[word.lower()] += 1

    proper = set()
    for word, cap_count in mid_upper.items():
        if cap_count < min_mid_sentence:
            continue
        if cap_count / (cap_count + mid_lower[word]) >= 0.5:
            proper.add(word)
    return proper


def candidates_in(text: str, proper: set[str]) -> set[str]:
    found = set()
    for match in CANDIDATE_RE.finditer(text):
        phrase = match.group(1).strip(" .,-'&")
        if CONTRACTION_RE.search(phrase):
            continue
        tokens = phrase.split()
        if not tokens or len(phrase) < 3:
            continue
        lowered = [t.lower().strip(".,'&-") for t in tokens]
        if any(t in NON_PRODUCT_NAMES for t in lowered):
            continue
        # A phrase qualifies if at least one of its words is a name in this
        # corpus. "Le Creuset" passes on `creuset`; "Mine broke" passes on
        # neither and is dropped.
        if not any(t in proper for t in lowered):
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

    corpus = [
        (text, score, url, sub)
        for text, score, url, _kind, sub in iter_items()
        if text and len(text) >= 12
    ]
    proper = learn_proper_nouns([t for t, _, _, _ in corpus])
    print(f"learned {len(proper)} proper nouns from {len(corpus)} items")

    items = 0
    for text, score, url, sub in corpus:
        items += 1
        durable = bool(DURABILITY_RE.search(text))
        negative = bool(NEGATIVE_RE.search(text))
        for phrase in candidates_in(text, proper):
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
