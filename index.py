#!/usr/bin/env python3
"""
Stage 1 — build the search indexes over out/chunks.jsonl.

  Tier 0  BM25 lexical index          (no neural net at all)
  Tier 1  dense embeddings, optional  (local ONNX, no torch, no network at query time)

    python index.py            # Tier 0 only
    python index.py --embed    # Tier 0 + Tier 1

Tier 1 downloads a 320 MB German/English model on first run, then works offline.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent / "out"
CHUNKS = OUT / "chunks.jsonl"
BM25_DIR = OUT / "bm25"
VEC_FILE = OUT / "vectors.npy"

# German/English bilingual, 0.32 GB. Their docs are exactly this mix.
# Best quality, but 8k context makes it slow on CPU (~45 min for 7.5k chunks).
EMBED_MODEL = "jinaai/jina-embeddings-v2-base-de"
# ~5x faster, 0.22 GB, still multilingual. Good for iterating / a first demo.
FAST_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def load_chunks() -> list[dict]:
    if not CHUNKS.exists():
        raise SystemExit(f"Missing {CHUNKS}. Run extract.py first.")
    with CHUNKS.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def build_bm25(chunks: list[dict]) -> None:
    import bm25s
    import Stemmer

    t0 = time.time()
    corpus = [c["text"] for c in chunks]
    # German stemmer: the corpus is majority German, and compound-heavy.
    stemmer = Stemmer.Stemmer("german")
    tokens = bm25s.tokenize(corpus, stopwords="de", stemmer=stemmer, show_progress=False)

    retriever = bm25s.BM25()
    retriever.index(tokens, show_progress=False)
    BM25_DIR.mkdir(parents=True, exist_ok=True)
    retriever.save(str(BM25_DIR))
    print(f"  Tier 0  BM25 index   {len(corpus):,} chunks   {time.time()-t0:.1f}s")


def build_embeddings(chunks: list[dict], model_name: str = EMBED_MODEL) -> None:
    from fastembed import TextEmbedding

    t0 = time.time()
    print(f"  Tier 1  loading {model_name} …", flush=True)
    model = TextEmbedding(model_name=model_name)

    texts = [c["text"] for c in chunks]
    vecs, done = [], 0
    for v in model.embed(texts, batch_size=16):
        vecs.append(v)
        done += 1
        if done % 500 == 0:
            print(f"          embedded {done:,}/{len(texts):,}", flush=True)

    arr = np.asarray(vecs, dtype=np.float32)
    # Normalise once so cosine similarity is a plain dot product at query time.
    arr /= np.linalg.norm(arr, axis=1, keepdims=True) + 1e-12
    np.save(VEC_FILE, arr)
    # Queries must be embedded with the same model the index was built with.
    (OUT / "vectors.meta.json").write_text(
        json.dumps({"model": model_name, "dim": int(arr.shape[1]),
                    "count": int(arr.shape[0])}, indent=2), encoding="utf-8")
    print(f"  Tier 1  vectors      {arr.shape}   {time.time()-t0:.1f}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embed", action="store_true",
                    help="also build Tier 1 dense embeddings")
    ap.add_argument("--fast", action="store_true",
                    help="use the smaller/faster multilingual model")
    ap.add_argument("--model", default=None,
                    help="explicit fastembed model name (overrides --fast)")
    ap.add_argument("--skip-bm25", action="store_true")
    args = ap.parse_args()

    chunks = load_chunks()
    print(f"\nIndexing {len(chunks):,} chunks\n")
    if not args.skip_bm25:
        build_bm25(chunks)
    if args.embed:
        model = args.model or (FAST_MODEL if args.fast else EMBED_MODEL)
        build_embeddings(chunks, model)
    else:
        print("  Tier 1  skipped (pass --embed to build)")
    print("\nDone.\n")


if __name__ == "__main__":
    main()
