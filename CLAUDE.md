# BIFL-Recs — working notes for agents

Read `docs/methodology.md` before adding records. It explains the one rule that
matters: extraction produces *leads*, curation produces *records*, and the two
are never the same step.

## Adding recommendations

1. `uv run scripts/extract_candidates.py` — or read the existing
   `dist/candidates.csv`. Columns carry the verbatim excerpt and permalink for
   the three highest-scored mentions, so a candidate can be judged without
   re-reading the corpus.
2. Open the corpus for the candidate if the excerpts are not enough:
   `grep -i "<brand>" harvest/reddit/*/comments.jsonl | head`. Re-harvest first
   if `harvest/` is empty — the comment corpus is gitignored.
3. Write the record into `data/recommendations/<division-id>-<slug>.yaml`,
   creating the file if that division has none yet. Division ids and names come
   from `../HomeGoods-Taxonomy/data/`; use the same two-digit ids.
4. `uv run scripts/build.py`. It fails on schema violations, duplicate ids, and
   filenames that disagree with the division id inside.

## Things that will catch you out

- **A 403 from Reddit looks like a page.** The block response is ~190 KB of
  HTML, so anything that only inspects the body reads it as content. Always
  check the status. Unauthenticated access does not work from here at all —
  `www`, `old`, and the `.json` suffix are all blocked. Use the OAuth path.
- **`limit=1000` does not get you 1000.** Reddit truncates any single listing
  at roughly 1000 items. Breadth comes from harvesting several listings and
  merging on post id, which is what `config/sources.yaml` does.
- **Upvotes are not evidence of durability.** Record `score` as a number and
  make the verdict from what the text actually says. The top-scoring post in
  the corpus is a complaint about a GPS vendor, not a recommendation.
- **Do not invent a record to fill a gap.** An empty division is a true
  statement about the corpus. `confidence: low` is for a real but thin lead.
- **Do not put prices in.** `price_band` only. Prices date and vary by region.

## Conventions

- `id` is a stable slug, unique across the whole directory, never re-used after
  a deletion.
- `model: null` where the claim is genuinely about a brand or a product line.
  Add a caveat saying so rather than inventing a model number.
- `evidence[].quote` is verbatim and trimmed. Do not paraphrase into it.
- Dates are absolute (`2026-08-17`), never "recently".
- `dist/` and `docs/directory.md` are build outputs. Edit `data/`, then rebuild.
