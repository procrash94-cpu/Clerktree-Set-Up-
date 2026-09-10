#!/usr/bin/env python3
"""
Spec-diff — "we got a new version of the Lastenheft, what actually changed?"

Straight text diff over the extracted corpus. stdlib only, no model, no network.
Every reported change is literally a line from the two documents.

    python specdiff.py --list
    python specdiff.py "CDC Solenoïd Armature Assembly V1 .pdf" \
                       "CDC Solenoïd Armature Assembly V2.1.pdf"
    python specdiff.py --auto        # guess version pairs by filename
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"
CHUNKS = OUT / "chunks.jsonl"

VERSION_TOKEN = re.compile(r"(v\s?\d+(?:\.\d+)?|_\d{3}\b|rev\.?\s?\d+)", re.I)
NOISE = re.compile(r"^\s*(seite|page)\s+\d+|^\s*\d+\s*$", re.I)


def load_docs() -> dict[str, list[dict]]:
    if not CHUNKS.exists():
        raise SystemExit(f"Missing {CHUNKS}. Run extract.py first.")
    docs: dict[str, list[dict]] = defaultdict(list)
    with CHUNKS.open(encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            docs[c["filename"]].append(c)
    return docs


def doc_lines(chunks: list[dict]) -> list[str]:
    """Flatten a document to comparable lines, dropping page furniture."""
    lines: list[str] = []
    for c in sorted(chunks, key=lambda x: (str(x["page"]), x["id"])):
        for raw in c["text"].splitlines():
            s = " ".join(raw.split())
            if len(s) > 12 and not NOISE.match(s):
                lines.append(s)
    # De-dup consecutive repeats caused by chunk overlap.
    out: list[str] = []
    for s in lines:
        if not out or out[-1] != s:
            out.append(s)
    return out


# Numbers that carry engineering meaning (ignores bare list numbering).
NUM_RE = re.compile(r"\d[\d.,]*\s?(?:mm|cm|m\b|kg|g\b|s\b|ms|min|h\b|°C|K\b|bar|"
                    r"Pa|kW|W\b|V\b|A\b|Hz|%|Nm|l\b|dB|µm|x\s?\d)", re.I)


def numeric_change(pair) -> bool:
    """True when the numbers on the two sides actually differ."""
    olds, news = pair
    grab = lambda ls: set(re.findall(r"\d[\d.,]*", " ".join(ls)))  # noqa: E731
    a, b = grab(olds), grab(news)
    if a == b:
        return False
    return bool(NUM_RE.search(" ".join(olds)) or NUM_RE.search(" ".join(news)))


def stem(name: str) -> str:
    s = VERSION_TOKEN.sub("", name.lower())
    return re.sub(r"[\s_\-.]+", " ", s.replace(".pdf", "")).strip()


def find_pairs(docs: dict[str, list[dict]]) -> list[tuple[str, str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for name in docs:
        if VERSION_TOKEN.search(name):
            groups[stem(name)].append(name)
    pairs = []
    for _, names in groups.items():
        if len(names) >= 2:
            names.sort()
            for i in range(len(names) - 1):
                pairs.append((names[i], names[i + 1]))
    return pairs


def diff(docs, a_name: str, b_name: str) -> dict:
    if a_name not in docs:
        raise SystemExit(f"Not in corpus: {a_name}")
    if b_name not in docs:
        raise SystemExit(f"Not in corpus: {b_name}")

    a, b = doc_lines(docs[a_name]), doc_lines(docs[b_name])
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)

    added, removed, changed = [], [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            added += b[j1:j2]
        elif tag == "delete":
            removed += a[i1:i2]
        elif tag == "replace":
            changed += [(a[i1:i2], b[j1:j2])]

    return {
        "a": a_name, "b": b_name,
        "a_lines": len(a), "b_lines": len(b),
        "similarity": sm.ratio(),
        "added": added, "removed": removed, "changed": changed,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--list", action="store_true", help="list documents")
    ap.add_argument("--auto", action="store_true", help="guess version pairs")
    ap.add_argument("--max", type=int, default=12, help="lines shown per section")
    ap.add_argument("--numeric", action="store_true",
                    help="only changes where a NUMBER differs — dimensions, "
                         "tolerances, cycle times. Cuts renumbering/bullet noise.")
    args = ap.parse_args()

    docs = load_docs()

    if args.list:
        for name in sorted(docs):
            print(f"  {len(docs[name]):>4} chunks  {name}")
        return

    pairs = find_pairs(docs) if args.auto else []
    if args.files:
        if len(args.files) != 2:
            raise SystemExit("give exactly two filenames, or use --auto")
        pairs = [(args.files[0], args.files[1])]
    if not pairs:
        print("\n  No version pairs detected. Use --list, or name two files.\n")
        return

    for a_name, b_name in pairs:
        d = diff(docs, a_name, b_name)
        if args.numeric:
            d["changed"] = [c for c in d["changed"] if numeric_change(c)]
            d["added"] = [s for s in d["added"] if NUM_RE.search(s)]
            d["removed"] = [s for s in d["removed"] if NUM_RE.search(s)]
        print("\n" + "=" * 78)
        print(f"  A  {d['a']}   ({d['a_lines']} lines)")
        print(f"  B  {d['b']}   ({d['b_lines']} lines)")
        print(f"  similarity {d['similarity']*100:.1f}%   "
              f"+{len(d['added'])} added   -{len(d['removed'])} removed   "
              f"~{len(d['changed'])} changed blocks")
        print("=" * 78)

        if d["added"]:
            print(f"\n  ADDED IN B  ({len(d['added'])})")
            for s in d["added"][:args.max]:
                print(f"    + {s[:150]}")
            if len(d["added"]) > args.max:
                print(f"    … {len(d['added'])-args.max} more")

        if d["removed"]:
            print(f"\n  REMOVED FROM A  ({len(d['removed'])})")
            for s in d["removed"][:args.max]:
                print(f"    - {s[:150]}")
            if len(d["removed"]) > args.max:
                print(f"    … {len(d['removed'])-args.max} more")

        if d["changed"]:
            print(f"\n  CHANGED  ({len(d['changed'])} blocks)")
            for olds, news in d["changed"][:args.max // 2 or 1]:
                for s in olds[:2]:
                    print(f"    - {s[:150]}")
                for s in news[:2]:
                    print(f"    + {s[:150]}")
                print()
    print()


if __name__ == "__main__":
    main()
