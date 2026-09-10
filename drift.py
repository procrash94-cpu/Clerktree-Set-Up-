#!/usr/bin/env python3
"""
Reference & version drift — the gap named in the interview:

    "Normally we have the latest customer documents somewhere in our files,
     but we really don't check them. That's a gap we have."

Three findings, all deterministic, no model:

  MULTIPLE_VERSIONS   we hold the same document at more than one version
                      — which one was the offer written against?
  STALE_REFERENCE     a document cites version X, but the newest we hold is Y
  NOT_HELD            a standard is cited but we hold no copy of it, so no
                      one can check whether it changed

Versions come from two places, both reliable in this corpus:
  * the filename        LH_04K_000051_V1.02.pdf  ·  Y152058_..._008.pdf
  * an explicit inline  "Lastenheft Nr.: LH_04K_000051, Version: 1.01"

    python drift.py
    python drift.py --kind STALE_REFERENCE
    python drift.py --project A07616000
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"
CHUNKS = OUT / "chunks.jsonl"

# ---- document identifiers used by these customers -------------------------
# NOTE: no trailing \b — '_' is a word character, so 'Y152058_LH_...' would
# never match. Use a negative lookahead on digits instead.
DOC_ID_PATTERNS = [
    re.compile(r"\b(LH_\d+K_\d+)(?!\d)", re.I),              # Dräxlmaier
    re.compile(r"\b(Y\d{6})(?!\d)"),                          # Knorr / BMG
    re.compile(r"\b(N\d{4}[A-Z]?(?:-\d+)*)(?!\d)"),           # Bosch
    re.compile(r"\b(GS\s?\d{5}(?:-\d+)*)(?!\d)", re.I),       # BMW
    re.compile(r"\b((?:DIN\s+)?(?:EN\s+)?(?:IEC|ISO|VDE)\s*\d{3,5}(?:-\d+)*)(?!\d)", re.I),
    re.compile(r"\b(20\d{2}/\d{1,3}/(?:EG|EU))(?!\d)"),       # directives
]

# version inside a filename:  _V1.02  ·  _008  ·  v2  ·  V1.46
FN_VERSION = [
    re.compile(r"[_\s]v(\d+(?:\.\d+)?)\b", re.I),
    re.compile(r"_(\d{3})(?=\.[a-z0-9]+$)", re.I),
]

# explicit inline statement of a document's own version
INLINE_VERSION = re.compile(
    r"(?:Nr\.?|No\.?)?\s*[:\-]?\s*({ids})[^\n]{{0,40}}?"
    r"\b(?:Version|Rev\.?|Revision|Ausgabe|Stand)\s*[:\-]?\s*(\d{{1,3}}(?:\.\d{{1,3}})?)"
    .format(ids=r"LH_\d+K_\d+|Y\d{6}|N\d{4}[A-Z]?"), re.I)

PUBLIC_STANDARD = re.compile(r"^(DIN|EN|IEC|ISO|VDE|20\d{2}/)", re.I)


def norm_id(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.strip().upper()).replace("–", "-")


def parse_version(v: str) -> tuple:
    """'1.02' -> (1,2); '008' -> (8,). Comparable across styles."""
    return tuple(int(x) for x in re.findall(r"\d+", v)) or (0,)


def newest_of(entries) -> tuple[tuple | None, str]:
    """Highest version among entries, returned as (tuple, original string)."""
    best, best_s = None, ""
    for e in entries:
        if e.get("vtuple") and (best is None or e["vtuple"] > best):
            best, best_s = e["vtuple"], e["version"]
    return best, best_s


def load_chunks() -> list[dict]:
    if not CHUNKS.exists():
        raise SystemExit(f"Missing {CHUNKS}. Run extract.py first.")
    with CHUNKS.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


# ------------------------------------------------------------------ held
def held_documents(chunks) -> dict[str, list[dict]]:
    """Documents physically on file, with the version parsed from the filename."""
    files: dict[str, dict] = {}
    for c in chunks:
        files.setdefault(c["source_path"], c)

    held: dict[str, list[dict]] = defaultdict(list)
    for path, c in files.items():
        fn = c["filename"]
        ids = []
        for pat in DOC_ID_PATTERNS[:4]:      # customer doc ids only
            ids += [norm_id(m.group(1)) for m in pat.finditer(fn)]
        if not ids:
            continue
        ver = None
        for pat in FN_VERSION:
            m = pat.search(fn)
            if m:
                ver = m.group(1)
                break
        for did in set(ids):
            held[did].append({
                "version": ver, "vtuple": parse_version(ver) if ver else None,
                "filename": fn, "path": path, "project_no": c["project_no"],
            })
    return held


# ------------------------------------------------------------ referenced
def referenced_documents(chunks, project: str | None = None):
    """Document ids mentioned in text, plus any explicitly stated version."""
    refs: dict[str, list[dict]] = defaultdict(list)
    for c in chunks:
        if project and c["project_no"] != project:
            continue
        text = c["text"]

        stated: dict[str, str] = {}
        for m in INLINE_VERSION.finditer(text):
            stated[norm_id(m.group(1))] = m.group(2)

        for pat in DOC_ID_PATTERNS:
            for m in pat.finditer(text):
                did = norm_id(m.group(1))
                if len(did) < 5:
                    continue
                refs[did].append({
                    "version": stated.get(did),
                    "citing_file": c["filename"],
                    "citing_path": c["source_path"],
                    "page": c["page"],
                    "project_no": c["project_no"],
                })
    return refs


# --------------------------------------------------------------- findings
def analyse(chunks, project: str | None = None) -> list[dict]:
    held = held_documents(chunks)
    refs = referenced_documents(chunks, project)
    findings: list[dict] = []

    # 1. same document held at more than one version
    for did, entries in held.items():
        versions = {e["version"] for e in entries if e["version"]}
        if len(versions) > 1:
            _, newest_s = newest_of(entries)
            # one row per distinct path — the same version can sit in two folders
            uniq = {e["path"]: e for e in entries}
            findings.append({
                "kind": "MULTIPLE_VERSIONS",
                "severity": "high",
                "doc_id": did,
                "detail": f'{len(versions)} Versionen auf Ablage: '
                          f'{", ".join(sorted(versions))}',
                "newest": newest_s,
                "files": [{"filename": e["filename"], "path": e["path"],
                           "version": e["version"], "project_no": e["project_no"]}
                          for e in sorted(uniq.values(),
                                          key=lambda x: (x["version"] or "", x["path"]))],
                "project_no": entries[0]["project_no"],
            })

    # 2. a document cites a version older than the newest we hold
    for did, entries in refs.items():
        stated = {e["version"] for e in entries if e["version"]}
        if not stated or did not in held:
            continue
        newest, newest_s = newest_of(held[did])
        if not newest:
            continue
        for sv in stated:
            if parse_version(sv) < newest:
                cite = next(e for e in entries if e["version"] == sv)
                findings.append({
                    "kind": "STALE_REFERENCE",
                    "severity": "high",
                    "doc_id": did,
                    "detail": f'zitiert Version {sv}, neueste auf Ablage ist '
                              f'{newest_s}',
                    "newest": newest_s,
                    "cited_version": sv,
                    "files": [{"filename": cite["citing_file"],
                               "path": cite["citing_path"], "page": cite["page"],
                               "project_no": cite["project_no"]}],
                    "project_no": cite["project_no"],
                })

    # 3. cited standard with no local copy — nobody can check it changed
    for did, entries in refs.items():
        if did in held or not PUBLIC_STANDARD.match(did):
            continue
        if len(entries) < 3:
            continue
        by_proj = defaultdict(int)
        for e in entries:
            by_proj[e["project_no"]] += 1
        first = entries[0]
        findings.append({
            "kind": "NOT_HELD",
            "severity": "medium",
            "doc_id": did,
            "detail": f'{len(entries)} Verweise in {len(by_proj)} Projekt(en), '
                      f'keine lokale Kopie — Änderungen unbemerkt',
            "newest": "",
            "files": [{"filename": first["citing_file"], "path": first["citing_path"],
                       "page": first["page"], "project_no": first["project_no"]}],
            "project_no": first["project_no"],
            "ref_count": len(entries),
        })

    order = {"MULTIPLE_VERSIONS": 0, "STALE_REFERENCE": 1, "NOT_HELD": 2}
    findings.sort(key=lambda f: (order[f["kind"]], -f.get("ref_count", 0), f["doc_id"]))
    return findings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=None)
    ap.add_argument("--kind", default=None,
                    choices=["MULTIPLE_VERSIONS", "STALE_REFERENCE", "NOT_HELD"])
    args = ap.parse_args()

    findings = analyse(load_chunks(), args.project)
    if args.kind:
        findings = [f for f in findings if f["kind"] == args.kind]

    print(f"\n  REFERENCE & VERSION DRIFT"
          f"{' · ' + args.project if args.project else ''}")
    print("  " + "─" * 74)
    if not findings:
        print("  Nichts gefunden.\n")
        return

    icons = {"MULTIPLE_VERSIONS": "⚑", "STALE_REFERENCE": "⚠", "NOT_HELD": "○"}
    for f in findings:
        print(f'\n  {icons[f["kind"]]} {f["kind"]:<18} {f["doc_id"]}')
        print(f'     {f["detail"]}')
        names = [fl["filename"] for fl in f["files"]]
        for fl in f["files"][:4]:
            pg = f' p.{fl["page"]}' if fl.get("page") else ""
            # when the same filename repeats, show the folder that distinguishes it
            loc = ""
            if names.count(fl["filename"]) > 1 and fl.get("path"):
                loc = f'   ({"/".join(fl["path"].split("/")[1:-1])[:44]})'
            print(f'       └─ [{fl["project_no"]}] {fl["filename"][:58]}{pg}{loc}')

    from collections import Counter
    tally = Counter(f["kind"] for f in findings)
    print(f'\n  {len(findings)} findings — '
          + " · ".join(f"{k}: {v}" for k, v in tally.items()) + "\n")


if __name__ == "__main__":
    main()
