# Gluth RFQ — local retrieval pipeline

Runs entirely on one laptop. **No LLM anywhere in this codebase.** No API keys,
no cloud calls, nothing leaves the machine. Built to be useful during the
two-week hold on the AI component — and to be the foundation the AI part sits
on afterwards.

## What it does

| Capability | Needs an LLM? | Where |
|---|---|---|
| Extract text — **every format in the corpus, 100%** | no | `extract.py` |
| OCR scanned PDFs, TIFF and JPG (macOS Vision, on-device) | no | `ocr.py` |
| Precedent search — *"did we ever build something like this?"* | no | `search.py` |
| Referenced-norm extraction | no | `norms.py` |
| **Reference & version drift** | no | `drift.py` |
| Spec-diff between two revisions of a Lastenheft | no | `specdiff.py` |
| **Summary / Checkliste / Übersicht** | **no** | `artifacts.py` — see below |
| Queue, review, corrections, triage — persisted | no | `store.py` (SQLite) |
| **Retrieval evaluation** — Recall@k, MRR, nDCG | no | `eval.py` |
| *Rewriting* those artifacts into prose | yes | not built — the part on hold |

### The artifacts are extractive, not generative

`artifacts.py` builds Summary / Checkliste / Übersicht **by extraction**. Every
line is a verbatim sentence from a real document, carrying the file and page it
came from. Nothing is written by a model, so nothing can be hallucinated — the
concept deck's guardrail ("may only assemble text from retrieved passages") is
enforced structurally rather than by prompt.

Classification is rule-based requirements engineering, all inspectable:

| Rule | Matches |
|---|---|
| requirement | deontic modals — `muss`, `ist zu`, `vorzusehen`, `shall`, `must`, … |
| constraint | a measurable value with a unit — `1.070 mm`, `75 dB(A)`, `25-28°C` |
| open point | explicit TBD — `noch zu klären`, `abzustimmen`, `not specified` |
| boilerplate | dropped — copyright, "Seite x von y", confidentiality footers |

Confidence is a **retrieval property** (item count + citation rate), not a model
opinion.

> **New to this codebase?** Read [`LEARNBOOK.md`](LEARNBOOK.md) — a guided
> walkthrough of how the system thinks, why each design decision was made, and
> the bugs that shaped it. This README covers how to *run* it; the learnbook
> covers how to *understand* it.

## Quick start

Everything runs locally. Nothing leaves the machine, no API keys, no LLM.

**Requirements:** macOS (the OCR step uses the built-in Vision framework),
Python 3.11+, ~3 GB free disk for models, 8 GB RAM is enough.

```bash
# from the repository root
python3 -m venv pipeline/.venv
pipeline/.venv/bin/pip install -r pipeline/requirements.txt
```

Then run the four steps in order. Steps 1–3 are one-time; after that you only
run step 4.

```bash
cd pipeline

# 1. extract text from every document in the corpus   (~20 s)
.venv/bin/python extract.py

# 2. OCR anything with no text layer, then re-extract  (~35 s)
.venv/bin/python ocr.py
.venv/bin/python extract.py

# 3. build the search index                            (~8 min incl. model download)
.venv/bin/python index.py --embed --fast

# 4. start the app
.venv/bin/python -m uvicorn serve:app --port 8800
```

Open **http://localhost:8800**

> Step 3 without `--embed` builds only the BM25 index and takes under a second.
> The app works that way too — it just falls back to lexical-only search.

### Expected output after step 2

```
  Extraction rate: 236/236 = 100% of content files
```

If you see less, a dependency is missing — check the `By format` table it
prints for which handler failed.

## Two copies of the UI — which is which

| File | Live? | Use it for |
|---|---|---|
| `pipeline/ui.html` | **yes** | the real thing. Talks to the local API, shows real corpus data. Served at `http://localhost:8800`. |
| `ai_rfq_engineering_intelligence_platform.html` (repo root) | no | the standalone design prototype with mock data. Opens in a browser with no server, useful for showing the layout only. |

They look nearly identical. If numbers on screen never change and search
returns the same three results regardless of the query, you are looking at the
prototype, not the live app.

## Command-line tools

Each module also runs standalone, which is the fastest way to sanity-check the
corpus without the UI.

```bash
cd pipeline

# precedent search — "did we ever build something like this?"
.venv/bin/python search.py "Kabel ablängen abisolieren crimpen"
.venv/bin/python search.py "pinhole detection camera" --mode rerank

# the three artifacts for one project
.venv/bin/python artifacts.py A07622000 --artifact summary
.venv/bin/python artifacts.py R07591   --artifact checkliste

# which standards does the corpus reference?
.venv/bin/python norms.py --min-hits 4

# version drift — stale references, duplicate versions, uncheckable standards
.venv/bin/python drift.py
.venv/bin/python drift.py --kind STALE_REFERENCE

# what changed between two revisions of a spec (numeric changes only)
.venv/bin/python specdiff.py --auto --numeric

# measure retrieval quality
.venv/bin/python eval.py --n 100
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Missing out/chunks.jsonl` | step 1 not run | run `extract.py` |
| Search returns nothing | index not built | run `index.py` |
| UI shows "Local API not reachable" | server not running | run step 4 |
| UI changes don't appear | browser cache | hard-reload; the server already sends `no-store` |
| Model download stalls | unauthenticated Hugging Face rate limit | set `HF_TOKEN` and retry |
| `ModuleNotFoundError: Vision` | not on macOS, or pyobjc missing | OCR is macOS-only; skip `ocr.py` |

## Regenerating from scratch

`out/` is derived data and is **not** committed — it contains extracted text
from confidential customer documents. Delete it and re-run steps 1–3 to rebuild:

```bash
rm -rf pipeline/out && cd pipeline && .venv/bin/python extract.py && \
  .venv/bin/python ocr.py && .venv/bin/python extract.py && \
  .venv/bin/python index.py --embed --fast
```

## The two tiers

**Tier 0 — BM25 lexical.** Zero neural networks. Defensible to any compliance
reviewer without a conversation. Builds in under a second. This is the default.

**Tier 1 — dense embeddings.** `fastembed` + ONNX, so no PyTorch and a small
memory footprint. The model runs locally and only ever produces vectors — it
does not generate text, so there is nothing to hallucinate. Combined with Tier 0
via Reciprocal Rank Fusion, a standard method with no tuning.

Models: `jinaai/jina-embeddings-v2-base-de` (German/English bilingual, 0.32 GB,
best quality) or `--fast` for `paraphrase-multilingual-MiniLM-L12-v2` (0.22 GB,
~5× faster, good enough to iterate on).

## Measuring retrieval — read this before tuning anything

`eval.py` is the only reason any retrieval claim in this repo is checkable.
It runs two query sets and reports the standard IR metrics:

- **auto** — known-item queries built from the corpus itself: a chunk is reduced
  to shuffled keywords, and the correct answer is the file it came from. Large
  and reproducible (fixed seed), but **biased toward lexical matching**, because
  the keywords are literally drawn from the target text.
- **hand** — realistic German/English queries written the way a sales engineer
  asks, scored at project level. Realistic but small (18), so treat differences
  of a few points as noise.

Neither set alone is trustworthy. Read them together.

```bash
cd pipeline && .venv/bin/python eval.py --n 100
```

### What the numbers actually said

The first run overturned an assumption that had been in this repo for a while:

| mode | auto nDCG@10 | hand nDCG@10 | auto R@1 | hand R@1 |
|---|---|---|---|---|
| bm25 | **0.771** | 0.709 | 0.520 | 0.556 |
| dense | 0.360 | **0.924** | 0.110 | 0.889 |
| hybrid (RRF) | 0.630 | 0.862 | 0.360 | 0.722 |
| hybrid + rerank | 0.729 | 0.892 | 0.470 | 0.833 |

**Hybrid was worse than the best single retriever on both sets.** Equal-weight
RRF dilutes whichever method is stronger for that query type — and the two sets
disagree about which that is. Keyword-ish queries want BM25; natural-language
queries want dense.

What hybrid *is* good at is recall: **R@10 ≈ 0.94 on both sets**. The right
answer is nearly always in the top ten, just not at rank one. That is precisely
the case a cross-encoder reranker exists to fix.

Adding one (`mode=rerank`) confirmed it: **+16% nDCG@10 and +31% R@1 over plain
hybrid on the auto set**, +3.5% / +15% on hand. It is still not better than the
single best retriever *for that query type* — but it is the only mode that is
near the top on **both**, i.e. the most robust default when you cannot know in
advance whether the user will type keywords or a sentence.

Caveat worth keeping: the reranker in use is `Xenova/ms-marco-MiniLM-L-12-v2`,
which is English-trained. It discriminates German correctly in spot checks
(+6.6 relevant vs −11.3 irrelevant) but a multilingual cross-encoder
(`jinaai/jina-reranker-v2-base-multilingual`, 1.1 GB) should do better. Its
download stalled against an unauthenticated Hugging Face endpoint; set
`HF_TOKEN` and re-measure before concluding anything.

## How drift detection works

The interview named this gap directly: *"we have the latest customer documents
somewhere in our files, but we really don't check them."*

An earlier attempt looked for a `Rev`/`Version` marker near a norm ID in the
text. That found **one** hit in 7,500 chunks — inline proximity is the wrong
signal. Versions in this corpus live in two reliable places instead:

1. **the filename** — `LH_04K_000051_V1.02.pdf`, `Y152058_..._008.pdf`
2. **an explicit statement** — `Lastenheft Nr.: LH_04K_000051, Version: 1.01`

Comparing what is *held* against what is *cited* gives three findings:

| Finding | Meaning |
|---|---|
| `MULTIPLE_VERSIONS` | the same document sits on file at two versions — which one was the offer written against? |
| `STALE_REFERENCE` | a document cites version X while the newest held is Y |
| `NOT_HELD` | a standard is cited but no local copy exists, so nobody can tell if it changed |

On this corpus: **1 / 1 / 57**.

> Watch the regexes: a trailing `\b` after a document ID silently fails, because
> `_` is a word character — `Y152058_LH_...` never matches. Use `(?!\d)`.

## Metadata

The numbered folder taxonomy is free, high-quality metadata and is used as such.
Every chunk carries `project_no`, `customer`, `phase_no`/`phase_name`
(`2_Anfrage` … `8_Nachtrag`), `source_path`, `filename`, `page`. A chunk from
`3_Angebot` *is* a labelled offer — no classifier needed.

## API

| Endpoint | Purpose | Screen |
|---|---|---|
| `GET /api/health` | index status, which tiers are live | header |
| `GET /api/inquiries` | projects seeded from the corpus | Queue |
| `GET /api/artifact/{p}?kind=summary` | extractive Summary/Checkliste/Übersicht | Review |
| `GET /api/source?path=…&page=…` | real text behind a citation | Review |
| `POST /api/approve` | record an approval | Review |
| `POST /api/correction` | write to the correction log | Review |
| `GET /api/corrections` | audit log + stats | Correction Log |
| `GET /api/search?q=…&mode=…` | precedent search; `mode` = auto/bm25/dense/hybrid/rerank | Precedent Search |
| `GET /api/search?…&customer=…&project=…&phase=…` | hard metadata filters — **customer filter is the TISAX separation rule** | Precedent Search |
| `GET /api/stats` | measured corpus + review metrics | Dashboard |
| `POST /api/decision` | persist a bid / no-bid | Triage |
| `GET /api/drift` | version-drift findings | Version Drift |
| `GET /api/specdiff?a=…&b=…&numeric=true` | revision diff | Version Drift |
| `GET /api/norms?min_hits=2` | referenced norms and standards | — |
| `GET /api/manifest` | extraction coverage, incl. OCR backlog | — |
| `GET /` | the workspace UI (`ui.html`) | — |

## Every screen is live

| Screen | Reads |
|---|---|
| **Queue** | the 4 real projects, with real doc/chunk counts and derived stage |
| **Review** | extractive draft + click-through to the real source page |
| **Precedent Search** | BM25/hybrid over 7,599 chunks |
| **Version Drift** | drift findings + numeric spec-diff |
| **Correction Log** | SQLite, written by the Correct button |
| **Dashboard** | measured extraction rate, citation rate, format coverage |
| **Triage** | bid/no-bid persisted per project, routing by customer |

Nothing on any screen is mock data.

## Format coverage — 236/236 content files, 100%

| Format | Files | Handler |
|---|---|---|
| `.pdf` | 121 | PyMuPDF, falling back to OCR for scans |
| `.xlsx` `.xlsm` | 45 | openpyxl, sheet by sheet |
| `.pptx` | 25 | python-pptx incl. tables and speaker notes |
| `.msg` | 17 | extract-msg, headers + body + attachment names |
| `.jpg` `.tif` | 15 | macOS Vision OCR, tiled for huge drawings |
| `.docx` | 7 | python-docx incl. tables |
| `.doc` | 2 | macOS `textutil` (built in) |
| `.xml` | 2 | ElementTree, elements + attributes |
| `.7z` | 1 | py7zr, each member through its own handler |
| `.vsdx` | 1 | OPC/ZIP, one page per XML part |

Excluded by decision: CAD geometry (`.stp`, `.STEP`, `.ipt`) — not text.
Excluded as junk: `Thumbs.db`, `~$…` locks, `.lnk`, `.url`.

> Very large images need tiling. One drawing here is 28,086 × 19,866 px
> (558 MP); Vision returns **nothing** at that size rather than erroring, so
> `ocr.py` splits anything over 30 MP into overlapping tiles and de-duplicates
> the lines. Before tiling that file looked empty — it is in fact a
> Mercedes-Benz parts table.

## Known gaps
- **`jinaai/jina-embeddings-v2-base-de` is impractically slow on CPU here** —
  its 8k context window appears to be padded per chunk (>25 min without
  finishing 500 of 7,500). The shipped index uses `--fast`
  (`paraphrase-multilingual-MiniLM-L12-v2`, 7,599 vectors in ~7 min). Revisit
  jina only with batching/truncation configured.
- **Drift covers versioned documents, not undated norm editions.** A citation
  like "DIN EN ISO 12100" with no edition year is reported as `NOT_HELD`, which
  is correct but coarse. Catching "the 2010 edition was superseded" needs an
  external standards feed — out of scope locally.
- No reranker yet. `FlashRank` would slot in after retrieval.
- Chunking is fixed-size. `Docling` would give layout- and table-aware chunks
  and is the natural upgrade for the Lastenhefte.
- Extractive artifacts are only as good as the rules: a requirement phrased
  without a modal verb is missed, and boilerplate that survives the noise filter
  can still surface. This is visible and fixable, unlike a model's mistakes.

## Why this order

Retrieval is not the AI part — generation is. Building retrieval first means
that when the hold lifts, generation is dropped onto an index that is already
tuned and measured, rather than being tuned blind. It is also the correct build
order regardless of any hold.
