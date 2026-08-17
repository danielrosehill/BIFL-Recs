# BIFL-Recs

A directory of durable-goods recommendations, mined from r/BuyItForLife and
adjacent sources and reduced to structured records that carry their own
evidence.

The subreddit is a good corpus and a bad reference work. A recommendation there
is a comment: it is undated in practice, it is buried under a photograph of
someone's grandfather's axe, it rarely says which model, and it never says
whether the thing can still be bought new. This repo turns that corpus into
something that can be queried, joined onto an inventory, and re-checked.

## What is here

| Path | What it holds |
| --- | --- |
| `config/sources.yaml` | The harvest plan. Adding a source means editing this file. |
| `scripts/harvest_reddit.py` | Pulls posts and comments into `harvest/reddit/<sub>/*.jsonl`. |
| `scripts/extract_candidates.py` | Ranks product candidates out of the corpus into `dist/candidates.csv`. |
| `scripts/inspect_candidate.py` | Shows the corpus evidence behind one candidate, with permalinks and refs. |
| `data/recommendations/*.yaml` | The directory itself — curated records, one file per division. |
| `data/quotes.json` | Verbatim quotes for every cited ref, extracted by `scripts/fill_quotes.py`. |
| `schema/recommendation.schema.json` | What a record must contain. Enforced by the build. |
| `scripts/build.py` | Validates `data/`, merges quotes, writes `dist/` and `docs/directory.md`. |
| `docs/methodology.md` | How a comment becomes a record, and what this repo refuses to do. |
| `docs/directory.md` | Generated, human-readable index. |

## The record

Every entry names a product, states one durability claim, and links the
evidence it rests on:

```yaml
- id: vitamix-5200
  product: Vitamix 5200 blender
  brand: Vitamix
  model: "5200"
  homegoods_class: "100403"
  verdict: recommended
  claim: Motor and drive are rated for continuous commercial use and the machine is rebuildable rather than replaceable.
  service_life_reported: 15-25 years in domestic use
  repairability: parts available
  warranty: 7 years, domestic
  available_new: true
  confidence: high
  added: 2026-08-17
  evidence:
    - source: reddit
      subreddit: BuyItForLife
      kind: comment
      ref: t1_xxxxxxx
      url: https://www.reddit.com/r/BuyItForLife/comments/.../
      score: 412
      quote: "Mine is from 2004 and the only thing I've replaced is the drive socket, which is a $10 part."
```

`verdict` is one of `recommended`, `mixed`, `avoid`. `avoid` is first-class:
the highest-scoring content on the subreddit is frequently a company reneging
on a lifetime guarantee, and that is a durability finding too.

`homegoods_class` is the optional 6-digit class from
[HomeGoods-Taxonomy](../HomeGoods-Taxonomy), which lets a recommendation be
joined onto an inventory row. Division files here use the same two-digit
division ids.

## Running it

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -r <(echo -e "PyYAML\njsonschema")

source config/credentials.example      # or export the two variables yourself
uv run scripts/harvest_reddit.py       # posts + comments, ~30 min for a full pass
uv run scripts/extract_candidates.py   # -> dist/candidates.csv
uv run scripts/fill_quotes.py          # -> data/quotes.json for newly cited refs
uv run scripts/build.py                # validate data/, write dist/ and docs/directory.md
```

Quotes are extracted from the corpus by `fill_quotes.py` rather than typed into
the YAML, so an evidence quote is always exactly what the source said. Records
cite a ref (`t3_…`/`t1_…`) and the build attaches the text, score and date.

`scripts/build.py --check` validates without writing, and exits non-zero on a
schema violation or a duplicate id — it is the repo's test suite.

Reddit refuses every unauthenticated request from this network with an HTTP 403
whose body is a full HTML block page; authenticated access via
`client_credentials` works. The details, and the two API limits that shape the
harvester, are in [`docs/methodology.md`](docs/methodology.md).

The comment corpus is gitignored — it runs to tens of MB per full harvest and is
rewritten wholesale each time. Post metadata is committed, and every quote a
record depends on is copied into the record itself, so the directory does not
depend on the corpus being present.

## Status

Early. The harvester, the schema, the extraction pass and the build are working
end to end against real harvested data; the directory is being filled in
division by division from `dist/candidates.csv`.
