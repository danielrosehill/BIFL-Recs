#!/usr/bin/env python3
"""Harvest Reddit posts and comments into newline-delimited JSON.

Output layout (one directory per subreddit, plain JSONL so the corpus stays
greppable and diffable):

    harvest/reddit/<subreddit>/posts.jsonl
    harvest/reddit/<subreddit>/comments.jsonl

Both files are keyed by `id` and re-runs merge rather than append blindly, so
running this repeatedly grows the corpus instead of duplicating it.

Usage:
    uv run scripts/harvest_reddit.py                 # everything in config/sources.yaml
    uv run scripts/harvest_reddit.py --subreddit BuyItForLife
    uv run scripts/harvest_reddit.py --skip-comments
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reddit_client import RedditClient, RedditError  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "config" / "sources.yaml"
OUT = REPO / "harvest" / "reddit"

# Reddit hands back ~100 fields per post, most of them about awards, media
# embeds and moderation. These are the ones a recommendation can be built from.
POST_FIELDS = (
    "id",
    "title",
    "selftext",
    "author",
    "score",
    "upvote_ratio",
    "num_comments",
    "created_utc",
    "permalink",
    "url",
    "link_flair_text",
    "over_18",
    "stickied",
)

COMMENT_FIELDS = (
    "id",
    "link_id",
    "parent_id",
    "body",
    "author",
    "score",
    "created_utc",
    "permalink",
    "depth",
)


def _project(record: dict, fields: tuple[str, ...]) -> dict:
    return {f: record.get(f) for f in fields}


def load_jsonl(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out = {}
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                rec = json.loads(line)
                out[rec["id"]] = rec
    return out


def write_jsonl(path: Path, records: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Sorted by id so a re-harvest produces a minimal diff rather than a
    # reshuffled file.
    with path.open("w") as fh:
        for key in sorted(records):
            fh.write(json.dumps(records[key], sort_keys=True) + "\n")


def harvest_posts(client: RedditClient, sub: dict) -> dict[str, dict]:
    name = sub["name"]
    path = OUT / name / "posts.jsonl"
    posts = load_jsonl(path)
    before = len(posts)

    for listing in sub["listings"]:
        sort = listing["sort"]
        limit = listing["posts_limit"]
        params = {"t": listing["t"]} if "t" in listing else {}
        print(f"  {name}: {sort} {params.get('t', '')} (up to {limit})", flush=True)
        count = 0
        for post in client.listing(f"/r/{name}/{sort}", limit=limit, **params):
            record = _project(post, POST_FIELDS)
            record["subreddit"] = name
            record["harvest_listing"] = f"{sort}:{params.get('t', 'na')}"
            posts[record["id"]] = record
            count += 1
        print(f"    {count} posts seen, {len(posts)} unique held", flush=True)

    write_jsonl(path, posts)
    print(f"  {name}: posts {before} -> {len(posts)}", flush=True)
    return posts


def _walk_comments(children: list, out: list) -> None:
    """Flatten Reddit's nested comment tree, dropping `more` placeholders."""
    for child in children:
        if child.get("kind") != "t1":
            continue
        data = child["data"]
        out.append(data)
        replies = data.get("replies")
        if isinstance(replies, dict):
            _walk_comments(replies.get("data", {}).get("children", []), out)


def harvest_comments(client: RedditClient, sub: dict, posts: dict, rules: dict) -> None:
    name = sub["name"]
    path = OUT / name / "comments.jsonl"
    comments = load_jsonl(path)
    before = len(comments)

    candidates = [
        p
        for p in posts.values()
        if (p.get("score") or 0) >= rules["min_score"]
        and (p.get("num_comments") or 0) >= rules["min_comments"]
    ]
    candidates.sort(key=lambda p: p.get("score") or 0, reverse=True)
    candidates = candidates[: rules["max_posts"]]

    already = {c.get("link_id") for c in comments.values()}
    todo = [p for p in candidates if f"t3_{p['id']}" not in already]
    print(
        f"  {name}: {len(candidates)} posts qualify, {len(todo)} still need comments",
        flush=True,
    )

    for i, post in enumerate(todo, 1):
        try:
            payload = client.get(
                f"/r/{name}/comments/{post['id']}",
                depth=rules["depth"],
                limit=rules["limit"],
                sort="top",
            )
        except RedditError as exc:
            print(f"    [{i}/{len(todo)}] {post['id']} failed: {exc}", flush=True)
            continue
        flat: list = []
        if len(payload) > 1:
            _walk_comments(payload[1].get("data", {}).get("children", []), flat)
        for c in flat:
            record = _project(c, COMMENT_FIELDS)
            record["subreddit"] = name
            comments[record["id"]] = record
        if i % 25 == 0 or i == len(todo):
            print(
                f"    [{i}/{len(todo)}] {len(comments)} comments held",
                flush=True,
            )
            write_jsonl(path, comments)

    write_jsonl(path, comments)
    print(f"  {name}: comments {before} -> {len(comments)}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subreddit", action="append", help="limit to these subreddits")
    ap.add_argument("--skip-comments", action="store_true")
    ap.add_argument("--skip-posts", action="store_true")
    args = ap.parse_args()

    config = yaml.safe_load(CONFIG.read_text())["reddit"]
    subs = config["subreddits"]
    if args.subreddit:
        wanted = {s.lower() for s in args.subreddit}
        subs = [s for s in subs if s["name"].lower() in wanted]
        if not subs:
            print("no matching subreddit in config/sources.yaml", file=sys.stderr)
            return 2

    client = RedditClient.login()
    print(f"authenticated; harvesting {len(subs)} subreddit(s)", flush=True)

    for sub in subs:
        print(f"r/{sub['name']} ({sub['role']})", flush=True)
        if args.skip_posts:
            posts = load_jsonl(OUT / sub["name"] / "posts.jsonl")
        else:
            posts = harvest_posts(client, sub)
        if not args.skip_comments:
            harvest_comments(client, sub, posts, config["comments"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
