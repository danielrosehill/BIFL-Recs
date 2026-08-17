#!/usr/bin/env python3
"""Validate data/ against the schema and build the published artefacts.

Outputs:
    dist/recommendations.json   whole directory, one array, machine-facing
    dist/recommendations.csv    flattened, one row per recommendation
    docs/directory.md           human-readable index, grouped by division

Exits non-zero on any schema violation or duplicate id, so this doubles as the
repo's test suite.

Usage:
    uv run scripts/build.py
    uv run scripts/build.py --check     # validate only, write nothing
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "recommendations"
SCHEMA = REPO / "schema" / "recommendation.schema.json"
DIST = REPO / "dist"
DOCS = REPO / "docs"
QUOTES = REPO / "data" / "quotes.json"


def load_files() -> list[tuple[Path, dict]]:
    return [(p, yaml.safe_load(p.read_text()) or {}) for p in sorted(DATA.glob("*.yaml"))]


def validate(files: list[tuple[Path, dict]]) -> list[str]:
    validator = Draft202012Validator(json.loads(SCHEMA.read_text()))
    errors: list[str] = []
    seen_ids: dict[str, Path] = {}

    for path, doc in files:
        rel = path.relative_to(REPO)
        for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
            location = "/".join(str(p) for p in err.path) or "<root>"
            errors.append(f"{rel}: {location}: {err.message}")

        # Filename must agree with the division id inside, or joins silently
        # attach recommendations to the wrong division.
        division = (doc.get("division") or {}).get("id")
        if division and not path.name.startswith(f"{division}-"):
            errors.append(
                f"{rel}: filename does not start with division id {division!r}"
            )

        for rec in doc.get("recommendations") or []:
            rec_id = rec.get("id")
            if not rec_id:
                continue
            if rec_id in seen_ids:
                errors.append(
                    f"{rel}: duplicate id {rec_id!r}, already used in "
                    f"{seen_ids[rec_id].relative_to(REPO)}"
                )
            else:
                seen_ids[rec_id] = path

    return errors


def merge_quotes(records: list[dict]) -> int:
    """Attach the verbatim quote, score and date held for each cited ref.

    Quotes live in data/quotes.json rather than in the YAML because they are
    extracted from the corpus by scripts/fill_quotes.py, not typed by hand — a
    hand-copied quote is a paraphrase waiting to happen. Anything set
    explicitly in the YAML wins, so a record can still trim a quote itself.
    """
    if not QUOTES.exists():
        return 0
    held = json.loads(QUOTES.read_text())
    missing = 0
    for rec in records:
        for ev in rec.get("evidence") or []:
            ref = ev.get("ref")
            if not ref:
                continue
            source = held.get(ref)
            if source is None:
                missing += 1
                continue
            for field in ("quote", "score", "date"):
                if ev.get(field) is None and source.get(field) is not None:
                    ev[field] = source[field]
    return missing


def flatten(files: list[tuple[Path, dict]]) -> list[dict]:
    out = []
    for _path, doc in files:
        division = doc.get("division") or {}
        for rec in doc.get("recommendations") or []:
            row = dict(rec)
            row["division_id"] = division.get("id")
            row["division_name"] = division.get("name")
            out.append(row)
    out.sort(key=lambda r: (r.get("division_id") or "", r["id"]))
    return out


def write_json(records: list[dict]) -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    (DIST / "recommendations.json").write_text(
        json.dumps(records, indent=2, sort_keys=True) + "\n"
    )


def write_csv(records: list[dict]) -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    fields = [
        "id", "division_id", "division_name", "brand", "product", "model",
        "verdict", "confidence", "service_life_reported", "repairability",
        "warranty", "country_of_manufacture", "price_band", "available_new",
        "successor", "homegoods_class", "claim", "caveats",
        "evidence_count", "primary_url", "added", "reviewed",
    ]
    with (DIST / "recommendations.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            row = dict(rec)
            evidence = rec.get("evidence") or []
            row["evidence_count"] = len(evidence)
            row["primary_url"] = evidence[0]["url"] if evidence else ""
            row["caveats"] = " | ".join(rec.get("caveats") or [])
            writer.writerow(row)


VERDICT_MARK = {"recommended": "✅", "mixed": "⚠️", "avoid": "❌"}


def write_markdown(records: list[dict]) -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Directory",
        "",
        "Generated by `scripts/build.py` — edit `data/recommendations/*.yaml`, not this file.",
        "",
        f"{len(records)} recommendations across "
        f"{len({r.get('division_id') for r in records})} divisions.",
        "",
    ]

    by_division: dict[tuple[str, str], list[dict]] = {}
    for rec in records:
        key = (rec.get("division_id") or "", rec.get("division_name") or "")
        by_division.setdefault(key, []).append(rec)

    for (div_id, div_name), recs in sorted(by_division.items()):
        lines += [f"## {div_id} {div_name}", ""]
        lines += [
            "| | Product | Verdict | Reported life | Repairability | Still sold new | Confidence | Sources |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for rec in sorted(recs, key=lambda r: (r["brand"], r["product"])):
            evidence = rec.get("evidence") or []
            links = " ".join(
                f"[{i + 1}]({e['url']})" for i, e in enumerate(evidence[:4])
            )
            available = {True: "yes", False: "no", None: "?"}[rec.get("available_new")]
            lines.append(
                "| {mark} | **{product}** | {verdict} | {life} | {repair} | {avail} | {conf} | {links} |".format(
                    mark=VERDICT_MARK.get(rec["verdict"], ""),
                    product=rec["product"],
                    verdict=rec["verdict"],
                    life=rec.get("service_life_reported") or "—",
                    repair=rec.get("repairability") or "unknown",
                    avail=available,
                    conf=rec["confidence"],
                    links=links or "—",
                )
            )
        lines.append("")
        for rec in sorted(recs, key=lambda r: (r["brand"], r["product"])):
            if rec.get("claim") or rec.get("caveats"):
                lines.append(f"**{rec['product']}** — {rec.get('claim', '')}")
                for caveat in rec.get("caveats") or []:
                    lines.append(f"  - Caveat: {caveat}")
                lines.append("")

    (DOCS / "directory.md").write_text("\n".join(lines).rstrip() + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="validate only")
    args = ap.parse_args()

    files = load_files()
    errors = validate(files)
    if errors:
        for err in errors:
            print(f"FAIL {err}", file=sys.stderr)
        print(f"\n{len(errors)} problem(s) in {len(files)} file(s)", file=sys.stderr)
        return 1

    records = flatten(files)
    missing = merge_quotes(records)
    print(f"OK {len(records)} recommendations in {len(files)} file(s)")
    if missing:
        print(
            f"note: {missing} cited ref(s) have no quote held — "
            "run scripts/fill_quotes.py against a fresh harvest"
        )
    if args.check:
        return 0

    write_json(records)
    write_csv(records)
    write_markdown(records)
    print("wrote dist/recommendations.json, dist/recommendations.csv, docs/directory.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
