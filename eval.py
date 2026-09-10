#!/usr/bin/env python3
"""
Retrieval evaluation — the measurement the Phase-1 gate depends on.

Without this, "hybrid is better than BM25" is an opinion. With it, every
retrieval change is a number that moved or didn't.

Two query sets:

  auto   known-item queries generated from the corpus itself. A distinctive
         sentence is reduced to shuffled keywords, so it rewards neither exact
         phrase matching nor pure semantics. Large, unbiased, reproducible
         (fixed seed).
  hand   realistic queries written the way a sales engineer would actually
         ask, with the document that must come back.

Metrics are the standard IR set: Recall@k, MRR, nDCG@10.

    python eval.py                       # all modes, both sets
    python eval.py --mode hybrid --n 200
    python eval.py --set hand
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
from collections import defaultdict

import search as search_mod

SEED = 20260824
STOP = set("""
der die das den dem des ein eine einer eines und oder aber auch nicht ist sind
war waren sein wird werden kann können muss müssen soll sollen darf dürfen
für von mit bei nach aus vor über unter zwischen durch gegen ohne um am im
zu zur zum als wie dass wenn dann noch nur schon sehr mehr alle allen jeder
this that with from have has been will shall must can may the and are for
was were not but you all any its their there where which when what who
werden wurde worden sowie bzw ggf ca etc siehe gemäß laut jeweils dabei
""".split())

# Realistic queries → the document that must be retrieved.
# Every target here was verified by hand against the corpus.
HAND_QUERIES = [
    ("Pinhole Erkennung Kamera Oberflächenprüfung", "A07614000"),
    ("pinhole detection BMW Leipzig", "A07614000"),
    ("Kabel ablängen abisolieren crimpen", "A07616000"),
    ("wire pre-assembly processing automatic line", "A07616000"),
    ("Magnetanker Montage Solenoid Armature", "R07591"),
    ("Bosch Rodez assembly solenoid", "R07591"),
    ("Montagelinie GSBC 2K EPM", "A07622000"),
    ("Arbeitshöhe der Montagelinie in Millimetern", "A07622000"),
    ("Schalldruckpegel Grenzwert dB", "A07622000"),
    ("zulässiger Bauraum Länge Breite Höhe der Maschine", "A07616000"),
    ("Mercedes-Benz Teilenummern Klemmen", "A07616000"),
    ("Geheimhaltungsverpflichtung Vertraulichkeit", "A07614000"),
    ("IT security questionnaire IEC 62443", "R07591"),
    ("Fettdosierung Schmierung Station", "A07622000"),
    ("Schutzeinrichtung Sicherheit Norm ISO 13849", "A07616000"),
    ("Taktzeit Zykluszeit Sekunden", "A07616000"),
    ("Angebot Konzept Präsentation Stationen", "R07591"),
    ("Prüftechnik Kamera Bildverarbeitung", "A07614000"),
]


def tokenize(text: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-zÄÖÜäöüß][\w\-]{3,}", text.lower())
            if w not in STOP]


def build_auto_queries(chunks, n: int, rng: random.Random):
    """Known-item queries: shuffled keywords from one chunk, target = its file."""
    # only chunks with enough distinctive content
    pool = [c for c in chunks if len(c["text"]) > 400 and c["project_no"]]
    rng.shuffle(pool)
    queries = []
    for c in pool:
        words = tokenize(c["text"])
        # prefer rarer, longer terms — what a person would actually type
        uniq = sorted(set(words), key=lambda w: (-len(w), w))[:14]
        if len(uniq) < 6:
            continue
        picked = rng.sample(uniq, k=min(7, len(uniq)))
        rng.shuffle(picked)
        queries.append({
            "query": " ".join(picked),
            "target_path": c["source_path"],
            "target_project": c["project_no"],
        })
        if len(queries) >= n:
            break
    return queries


def rank_docs(query: str, mode: str, depth: int = 50):
    """Return ranked (source_path, project_no) — deduped, best rank first."""
    chunks = search_mod.load_chunks()
    if mode == "bm25":
        ids = search_mod.bm25_rank(query, depth)
    elif mode == "dense":
        ids = search_mod.dense_rank(query, depth)
    else:
        lex = search_mod.bm25_rank(query, depth)
        den = search_mod.dense_rank(query, depth)
        scores = search_mod.rrf([lex, den])
        ids = sorted(scores, key=lambda d: -scores[d])[:depth]
        if mode == "rerank":
            # hybrid supplies recall; the cross-encoder supplies precision
            ids = search_mod.rerank(query, ids, top_k=30)

    seen, out = set(), []
    for i in ids:
        c = chunks[i]
        key = c["source_path"]
        if key in seen:
            continue
        seen.add(key)
        out.append((key, c["project_no"]))
    return out


def evaluate(queries, mode: str, level: str = "path", ks=(1, 3, 5, 10)):
    """level: 'path' = exact document, 'project' = right project."""
    hits_at = defaultdict(int)
    rr_sum = 0.0
    ndcg_sum = 0.0
    n = len(queries)

    for q in queries:
        ranked = rank_docs(q["query"], mode)
        target = q["target_path"] if level == "path" else q["target_project"]
        pos = None
        for idx, (path, proj) in enumerate(ranked):
            got = path if level == "path" else proj
            if got == target:
                pos = idx
                break
        if pos is not None:
            for k in ks:
                if pos < k:
                    hits_at[k] += 1
            rr_sum += 1.0 / (pos + 1)
            if pos < 10:
                ndcg_sum += 1.0 / math.log2(pos + 2)   # single relevant doc

    return {
        "n": n,
        "mode": mode,
        "level": level,
        **{f"recall@{k}": hits_at[k] / n if n else 0.0 for k in ks},
        "mrr": rr_sum / n if n else 0.0,
        "ndcg@10": ndcg_sum / n if n else 0.0,
    }


def print_table(rows):
    hdr = f'{"mode":<10}{"set":<7}{"level":<9}{"n":>5}{"R@1":>8}{"R@3":>8}{"R@5":>8}{"R@10":>8}{"MRR":>8}{"nDCG@10":>9}'
    print("\n  " + hdr)
    print("  " + "─" * len(hdr))
    for r in rows:
        print(f'  {r["mode"]:<10}{r["set"]:<7}{r["level"]:<9}{r["n"]:>5}'
              f'{r["recall@1"]:>8.3f}{r["recall@3"]:>8.3f}{r["recall@5"]:>8.3f}'
              f'{r["recall@10"]:>8.3f}{r["mrr"]:>8.3f}{r["ndcg@10"]:>9.3f}')
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="all",
                    choices=["all", "bm25", "dense", "hybrid", "rerank"])
    ap.add_argument("--set", dest="qset", default="all",
                    choices=["all", "auto", "hand"])
    ap.add_argument("--n", type=int, default=120, help="auto queries")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    chunks = search_mod.load_chunks()
    rng = random.Random(SEED)
    auto = build_auto_queries(chunks, args.n, rng)
    hand = [{"query": q, "target_project": p, "target_path": None}
            for q, p in HAND_QUERIES]

    modes = ["bm25", "dense", "hybrid", "rerank"] if args.mode == "all" else [args.mode]
    rows = []

    if args.qset in ("all", "auto"):
        print(f"\n  AUTO — {len(auto)} known-item queries "
              f"(shuffled keywords, seed {SEED})")
        for m in modes:
            r = evaluate(auto, m, level="path")
            r["set"] = "auto"
            rows.append(r)

    if args.qset in ("all", "hand"):
        print(f"  HAND — {len(hand)} realistic queries "
              f"(scored at project level)")
        for m in modes:
            r = evaluate(hand, m, level="project")
            r["set"] = "hand"
            rows.append(r)

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print_table(rows)
        best = max(rows, key=lambda r: r["ndcg@10"])
        print(f'  Best on nDCG@10: {best["mode"]} ({best["set"]} set) '
              f'= {best["ndcg@10"]:.3f}\n')


if __name__ == "__main__":
    main()
