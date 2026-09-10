#!/usr/bin/env python3
"""
Precedent search over the Gluth corpus.

  Tier 0  BM25 lexical               always available
  Tier 1  dense embeddings           used automatically if vectors.npy exists
  hybrid  Reciprocal Rank Fusion     standard, no tuning, no LLM

Results are grouped into PROJECTS (what a sales engineer actually asks for:
"did we ever build something like this?"), each with the passages that matched.

    python search.py "Schläuche ablängen, fetten, beidseitig Fittings crimpen"
    python search.py "pinhole detection camera" --mode bm25
"""
from __future__ import annotations

import argparse
import json
from functools import lru_cache
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent / "out"
CHUNKS = OUT / "chunks.jsonl"
BM25_DIR = OUT / "bm25"
VEC_FILE = OUT / "vectors.npy"
EMBED_MODEL = "jinaai/jina-embeddings-v2-base-de"

RRF_K = 60  # standard Reciprocal Rank Fusion constant


@lru_cache(maxsize=1)
def load_chunks() -> list[dict]:
    with CHUNKS.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


@lru_cache(maxsize=1)
def load_bm25():
    import bm25s
    import Stemmer
    retriever = bm25s.BM25.load(str(BM25_DIR), load_corpus=False)
    return retriever, Stemmer.Stemmer("german")


@lru_cache(maxsize=1)
def load_vectors():
    if not VEC_FILE.exists():
        return None
    return np.load(VEC_FILE)


@lru_cache(maxsize=1)
def load_embedder():
    from fastembed import TextEmbedding
    # Always use the model the index was actually built with.
    meta_file = OUT / "vectors.meta.json"
    name = EMBED_MODEL
    if meta_file.exists():
        name = json.loads(meta_file.read_text(encoding="utf-8")).get("model", name)
    return TextEmbedding(model_name=name)


def bm25_rank(query: str, top_k: int) -> list[int]:
    import bm25s
    retriever, stemmer = load_bm25()
    tokens = bm25s.tokenize([query], stopwords="de", stemmer=stemmer,
                            show_progress=False)
    n = len(load_chunks())
    idx, _ = retriever.retrieve(tokens, k=min(top_k, n), show_progress=False)
    return [int(i) for i in idx[0]]


def dense_rank(query: str, top_k: int) -> list[int]:
    vecs = load_vectors()
    if vecs is None:
        return []
    q = next(iter(load_embedder().embed([query])))
    q = np.asarray(q, dtype=np.float32)
    q /= np.linalg.norm(q) + 1e-12
    sims = vecs @ q
    return np.argsort(-sims)[:top_k].tolist()


# Cross-encoder reranker: multilingual, runs locally on ONNX, no LLM.
# Small, fast, CPU-friendly. English-trained (MS MARCO) — whether it helps a
# German corpus is an empirical question, answered by eval.py, not assumed.
RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-12-v2"


@lru_cache(maxsize=1)
def load_reranker():
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    return TextCrossEncoder(model_name=RERANK_MODEL)


def rerank(query: str, doc_ids: list[int], top_k: int = 20) -> list[int]:
    """
    Re-score candidates with a cross-encoder that reads query and passage
    together. Hybrid retrieval supplies recall; this supplies precision.
    """
    if not doc_ids:
        return []
    chunks = load_chunks()
    cand = doc_ids[:max(top_k, 1)]
    texts = [chunks[i]["text"][:1800] for i in cand]
    scores = list(load_reranker().rerank(query, texts))
    order = sorted(range(len(cand)), key=lambda j: -scores[j])
    return [cand[j] for j in order] + doc_ids[len(cand):]


def rrf(rankings: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    """Reciprocal Rank Fusion — combines rankings without score calibration."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


def search(query: str, mode: str = "auto", top_k: int = 60,
           project_limit: int = 5, per_project: int = 3,
           customer: str | None = None, project: str | None = None,
           phase: str | None = None) -> dict:
    """
    Returns projects ranked by best matching passages.

    customer/project/phase are hard filters applied to the candidate set.
    The customer filter is the TISAX separation rule ("a Bosch inquiry never
    surfaces in a BMW answer") enforced in retrieval, not just in the UI.
    """
    chunks = load_chunks()
    has_vectors = load_vectors() is not None

    def allowed(c: dict) -> bool:
        if customer and c.get("customer") != customer:
            return False
        if project and c.get("project_no") != project:
            return False
        if phase and str(c.get("phase_no")) != str(phase):
            return False
        return True

    filtering = any((customer, project, phase))

    if mode == "auto":
        mode = "hybrid" if has_vectors else "bm25"
    if mode in ("dense", "hybrid", "rerank") and not has_vectors:
        mode = "bm25"

    if mode == "bm25":
        ranked = bm25_rank(query, top_k)
        scores = {d: 1.0 / (i + 1) for i, d in enumerate(ranked)}
    elif mode == "dense":
        ranked = dense_rank(query, top_k)
        scores = {d: 1.0 / (i + 1) for i, d in enumerate(ranked)}
    else:
        lex, den = bm25_rank(query, top_k), dense_rank(query, top_k)
        scores = rrf([lex, den])
        ranked = sorted(scores, key=lambda d: -scores[d])
        if mode == "rerank":
            ranked = rerank(query, ranked, top_k=30)
            # reranked order is the ranking; give it a monotonic score
            scores = {d: 1.0 / (i + 1) for i, d in enumerate(ranked)}

    if filtering:
        ranked = [d for d in ranked if allowed(chunks[d])]

    # Group hits by project — the unit a sales engineer thinks in.
    projects: dict[str, dict] = {}
    for doc_id in ranked:
        c = chunks[doc_id]
        key = c["project_no"] or c["source_path"].split("/")[0]
        p = projects.setdefault(key, {
            "project_no": c["project_no"],
            "project_name": c["project_name"],
            "customer": c["customer"],
            "score": 0.0,
            "hits": [],
        })
        p["score"] += scores.get(doc_id, 0.0)
        # One hit per (file, page) — several chunks of the same page are one result.
        loc = (c["source_path"], c["page"])
        seen = p.setdefault("_seen", set())
        if len(p["hits"]) < per_project and loc not in seen:
            seen.add(loc)
            text = c["text"]
            p["hits"].append({
                "source_path": c["source_path"],
                "filename": c["filename"],
                "phase": f'{c["phase_no"]}_{c["phase_name"]}' if c["phase_no"] else "",
                "page": c["page"],
                "snippet": (text[:400] + "…") if len(text) > 400 else text,
            })

    out = sorted(projects.values(), key=lambda p: -p["score"])[:project_limit]
    for p in out:
        p.pop("_seen", None)
    return {"query": query, "mode": mode, "projects": out}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("--mode", default="auto",
                    choices=["auto", "bm25", "dense", "hybrid", "rerank"])
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()

    res = search(" ".join(args.query), mode=args.mode, project_limit=args.limit)

    print(f'\n  query : "{res["query"]}"')
    print(f'  mode  : {res["mode"]}')
    print("  " + "─" * 72)
    if not res["projects"]:
        print("  Kein passender Beleg gefunden.\n")
        return
    for i, p in enumerate(res["projects"], 1):
        label = p["project_no"] or "?"
        name = p["project_name"] or ""
        cust = f' · {p["customer"]}' if p["customer"] else ""
        print(f'\n  {i}. {label}  {name}{cust}      score {p["score"]:.4f}')
        for h in p["hits"]:
            loc = f' p.{h["page"]}' if h["page"] else ""
            print(f'     ├─ {h["filename"]}{loc}   [{h["phase"]}]')
            snippet = " ".join(h["snippet"].split())[:200]
            print(f'     │  {snippet}…')
    print()


if __name__ == "__main__":
    main()
