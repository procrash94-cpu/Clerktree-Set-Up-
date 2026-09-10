#!/usr/bin/env python3
"""
Norm & reference extraction — the "which standards does this spec pull in,
and are we citing the current revision?" gap raised in the interview.

Pure regex over the extracted corpus. No model, no network, deterministic,
fully auditable — every hit points back at a file and page.

    python norms.py                     # all referenced norms, by project
    python norms.py --project A07622000
    (version drift lives in drift.py — this module only extracts references)
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"
CHUNKS = OUT / "chunks.jsonl"

# Each pattern yields a normalised norm ID.
PATTERNS = [
    # DIN EN ISO 12100, EN ISO 13849-1, ISO 9001, DIN VDE 0100
    re.compile(r"\b((?:DIN\s+)?(?:EN\s+)?(?:IEC|ISO|VDE)\s*\d{3,5}(?:[-–]\d+)*)", re.I),
    re.compile(r"\b(DIN\s+\d{3,5}(?:[-–]\d+)*)", re.I),
    # Customer-internal standards: Bosch N2580, Knorr Y506975, BMW GS 95024
    re.compile(r"\b(N\s?\d{4}(?:[-–]\d+)*)\b"),
    re.compile(r"\b(Y\s?\d{6})\b"),
    re.compile(r"\b(GS\s?\d{5}(?:[-–]\d+)*)\b"),
    # Machinery directive
    re.compile(r"\b(20\d{2}/\d{1,3}/(?:EG|EU))\b"),
]

# A revision marker sitting near the norm reference.
REV_RE = re.compile(
    r"\b(?:Rev(?:ision)?\.?|Version|Ausgabe|Stand|Ver\.?)\s*[:\-]?\s*"
    r"(\d{1,3}(?:\.\d{1,3})?)\b", re.I)
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def normalise(raw: str) -> str:
    s = re.sub(r"\s+", " ", raw.strip().upper()).replace("–", "-")
    return re.sub(r"\s*-\s*", "-", s)


def load_chunks() -> list[dict]:
    if not CHUNKS.exists():
        raise SystemExit(f"Missing {CHUNKS}. Run extract.py first.")
    with CHUNKS.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def extract(chunks: list[dict], project: str | None = None) -> dict:
    """norm_id -> {project -> [ {file, page, revision, context} ]}"""
    found: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    for c in chunks:
        if project and c["project_no"] != project:
            continue
        text = c["text"]
        for pat in PATTERNS:
            for m in pat.finditer(text):
                norm_id = normalise(m.group(1))
                if len(norm_id) < 5:
                    continue
                window = text[max(0, m.start() - 90): m.end() + 90]
                rev = REV_RE.search(window)
                year = YEAR_RE.search(window)
                found[norm_id][c["project_no"] or "?"].append({
                    "file": c["filename"],
                    "path": c["source_path"],
                    "page": c["page"],
                    "revision": rev.group(1) if rev else "",
                    "year": year.group(0) if year else "",
                    "context": " ".join(window.split()),
                })
    return found


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=None)
    ap.add_argument("--min-hits", type=int, default=1)
    args = ap.parse_args()

    found = extract(load_chunks(), args.project)

    rows = []
    for norm_id, by_project in found.items():
        total = sum(len(v) for v in by_project.values())
        if total < args.min_hits:
            continue
        rows.append((norm_id, total, by_project))

    rows.sort(key=lambda r: -r[1])

    title = "REFERENCED NORMS & STANDARDS"
    scope = f" · project {args.project}" if args.project else ""
    print(f"\n  {title}{scope}")
    print("  " + "─" * 74)
    if not rows:
        print("  Nothing found.\n")
        return

    for norm_id, total, by_project in rows:
        print(f"\n  {norm_id:<26} {total:>4} refs")
        for proj, hits in sorted(by_project.items()):
            files = sorted({h["file"] for h in hits})
            print(f"      {proj:<12} {len(hits):>3} in {len(files)} file(s)")
            for f in files[:2]:
                ex = next(h for h in hits if h["file"] == f)
                print(f"        └─ {f} p.{ex['page']}")
    print(f"\n  {len(rows)} norms.\n")


if __name__ == "__main__":
    main()
