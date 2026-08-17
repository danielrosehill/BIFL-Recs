# Methodology

How a Reddit comment becomes a row in the directory, and what the repo refuses
to do along the way.

## The pipeline

```
config/sources.yaml
      │
      ▼
scripts/harvest_reddit.py ──▶ harvest/reddit/<sub>/posts.jsonl
                              harvest/reddit/<sub>/comments.jsonl   (gitignored)
      │
      ▼
scripts/extract_candidates.py ──▶ dist/candidates.csv     ranked leads + quotes
      │
      ▼  (curation — human or agent, never automatic)
data/recommendations/<division>.yaml
      │
      ▼
scripts/build.py ──▶ dist/recommendations.json
                     dist/recommendations.csv
                     docs/directory.md
```

The break in the middle is deliberate. Extraction ranks *leads*; it does not
write records. Nothing reaches `data/` without a person or an agent having read
the actual quotes behind it.

## Reddit access

Unauthenticated Reddit is not usable from here. Every unauthenticated request —
`www.reddit.com/....json`, `old.reddit.com/....json`, plain `oauth.reddit.com` —
returns **HTTP 403 with a full HTML block page as the body**, so a fetcher that
only looks at the body sees ~190 KB of markup and can easily be read as a
successful page load. Checked 2026-08-17 from an Israeli residential IP; a US
egress failed at the connection layer rather than returning anything.

What works is application-only OAuth:

```
POST https://www.reddit.com/api/v1/access_token
  Basic auth: <client_id>:<client_secret>
  grant_type=client_credentials
→ 200 {"access_token": "...", "expires_in": 86400}

GET https://oauth.reddit.com/r/<sub>/top?t=all&limit=100&raw_json=1
  Authorization: bearer <token>
→ 200
```

No user password is involved — `client_credentials` is enough for every read
endpoint used here. Credentials come from `REDDIT_CLIENT_ID` /
`REDDIT_CLIENT_SECRET`, falling back to the 1Password references named in
`config/credentials.example`.

Two API limits shape the harvester:

- Any single listing caps at roughly 1000 items no matter what `limit` and
  `after` ask for. Coverage comes from harvesting several listings
  (`top/all`, `top/year`, `top/month`, `hot`) and merging on post id, not from
  paging one listing harder.
- The documented ceiling is 100 requests/minute. `RedditClient` self-throttles
  to about 55/min and backs off on 429 and 5xx.

## What counts as evidence

Every record carries at least one evidence entry, and each entry carries the
permalink and the verbatim sentence the claim rests on. A record with no
evidence is not a record — `scripts/build.py` fails the build.

Scores are recorded as numbers, not as verdicts. A 40,000-point post is
evidence that many people liked reading it, which is not the same as evidence
that the product lasts. The most-upvoted content on r/BuyItForLife is often a
complaint about a company reneging on a lifetime guarantee, which is why
`verdict: avoid` is first-class rather than an afterthought.

## What this repo will not do

- **No invented records.** If the corpus does not support a claim, the claim
  does not get written down. `confidence: low` exists for leads worth chasing;
  fabrication does not.
- **No absolute prices.** Prices date badly and vary by region. `price_band`
  records where an item sits relative to its category instead.
- **No scraping of sources that forbid it.** `config/sources.yaml` lists
  several non-Reddit sources with `harvester: null` — those are read by hand
  and used as corroboration.
- **No aggregate-as-answer.** "Highly upvoted" and "frequently mentioned" are
  signals about the discussion. The claim being checked is about the object.

## Known weaknesses

- **Survivorship bias is the whole subreddit.** People post the 1988 Accord
  that still runs, not the 1988 Accord that rusted out in 1996. Nothing in this
  pipeline corrects for that; `confidence` and `caveats` are where it gets
  acknowledged per record.
- **Candidate extraction is a capitalisation heuristic.** It over-produces on
  sentence openers and proper nouns that are not products, and under-produces on
  lowercase brand names. The stopword list in `extract_candidates.py` is the
  main lever; expect to keep extending it.
- **A brand is not a product.** Much of the corpus praises a maker, not a model,
  and quality often changes after an acquisition. `model: null` plus a caveat is
  the honest encoding; splitting into per-model records once evidence supports
  it is the intended direction.
- **Old recommendations go stale silently.** `available_new` and `successor`
  exist because the sub's longest-running complaint is recommendations that can
  only be bought second-hand. `reviewed` records when a claim was last checked.
