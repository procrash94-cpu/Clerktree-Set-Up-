# Learnbook — understanding this codebase

A guided read-through for someone who has never seen this project. It assumes
you can read Python but knows nothing about search or retrieval. By the end you
should be able to change any part of it and know what will break.

`README.md` tells you *how to run it*. This tells you *how it thinks*.

---

## Table of contents

1. [The one idea](#1-the-one-idea)
2. [The problem it solves](#2-the-problem-it-solves)
3. [The pipeline, end to end](#3-the-pipeline-end-to-end)
4. [Stage 0 — extraction](#4-stage-0--extraction-extractpy)
5. [Stage 0.5 — OCR](#5-stage-05--ocr-ocrpy)
6. [Stage 1 — indexing](#6-stage-1--indexing-indexpy)
7. [Stage 2 — retrieval](#7-stage-2--retrieval-searchpy)
8. [Stage 3 — the analysis layer](#8-stage-3--the-analysis-layer)
9. [Stage 4 — serving and state](#9-stage-4--serving-and-state)
10. [Measurement](#10-measurement-evalpy)
11. [Design decisions and why](#11-design-decisions-and-why)
12. [Bugs we hit, and what they teach](#12-bugs-we-hit-and-what-they-teach)
13. [How to extend it](#13-how-to-extend-it)
14. [Glossary](#14-glossary)

---

## 1. The one idea

> **Retrieval is not the AI part. Generation is.**

A "RAG" system has two halves. *Retrieval* finds relevant passages — that is a
search problem, solved with maths that predates modern AI. *Generation* writes
prose from them — that is the language model, and it is the half that can
hallucinate, that carries liability, and that a compliance department will ask
questions about.

This codebase is **the entire first half, and none of the second**. There is no
language model anywhere in it. Nothing calls an API. Nothing leaves the machine.

That is not a limitation to apologise for. It is the correct build order: you
tune and measure retrieval first, then drop generation on top of something that
already works. Doing it the other way round means tuning blind.

Every module answers to one rule: **if you cannot point at the document it came
from, it does not go in the output.**

---

## 2. The problem it solves

Gluth builds custom assembly machines. A customer (BMW, Bosch, Dräxlmaier) sends
an *Anfrage* — an inquiry, usually a 50–90 page German specification plus a pile
of referenced standards. A sales engineer reads it and produces an *Angebot*, an
offer. That reading is the bottleneck.

Three specific pains, taken from an interview with the technical sales lead:

1. **"Did we ever build something like this?"** — the answer lives in twenty
   years of past projects and, in practice, in the memory of whoever is on
   holiday that week.
2. **"We get a new version of the specification and we don't compare them."**
   Described in the interview as *"a gap we have"*.
3. **The specs reference other documents** — norms, delivery standards — and
   nobody can check whether those changed.

None of these three need a language model. All three are implemented here.

---

## 3. The pipeline, end to end

```
   corpus on disk                    what runs                    what it produces
   ─────────────                     ─────────                    ────────────────

   *.pdf .docx .pptx  ──┐
   *.xlsx .msg .doc     │  extract.py  ──────────────────────►  out/chunks.jsonl
   *.xml .vsdx .7z      │  (+ ocr.py for scans/images)          out/manifest.json
   *.tif .jpg  ─────────┘                                       out/ocr/*.json
                                            │
                                            ▼
                                       index.py
                                            │
                        ┌───────────────────┴──────────────────┐
                        ▼                                      ▼
                  out/bm25/                              out/vectors.npy
                  (lexical)                              (semantic)
                        └───────────────────┬──────────────────┘
                                            ▼
                                        search.py
                                   BM25 + dense + RRF
                                   + optional rerank
                                            │
             ┌──────────────┬───────────────┼───────────────┬──────────────┐
             ▼              ▼               ▼               ▼              ▼
        artifacts.py     drift.py      specdiff.py      norms.py       eval.py
        Summary /        version       what changed     referenced     does any
        Checkliste /     drift         between two      standards      of this
        Übersicht                      revisions                       work?
             └──────────────┴───────────────┼───────────────┴──────────────┘
                                            ▼
                                        serve.py  ── FastAPI
                                            │        + store.py (SQLite)
                                            ▼
                                        ui.html  ── the workspace
```

Read that diagram once more before continuing. Everything below is a zoom into
one box.

---

## 4. Stage 0 — extraction (`extract.py`)

**Job:** turn a folder of binary documents into a list of text chunks, each
carrying enough metadata to be useful later.

**Why it is 70% of the work:** a retrieval system can only ever find what got
extracted. An elegant search algorithm over a corpus that silently dropped a
third of its documents is worse than a crude one over a complete corpus, because
the failure is invisible.

### The output record

Every chunk is one JSON line in `out/chunks.jsonl`:

```json
{
  "id":           "A07614000_.../2_Anfrage/spec.pdf#4:0",
  "project_no":   "A07614000",
  "project_name": "Pinholeerkennung",
  "customer":     "BMW",
  "phase_no":     "2",
  "phase_name":   "Anfrage",
  "source_path":  "A07614000_Pinholeerkennung/2_Anfrage/spec.pdf",
  "filename":     "spec.pdf",
  "page":         "4",
  "text":         "Werk Leipzig … Projekt Name: Pinholeerkennung …"
}
```

Everything downstream reads this one shape. If you understand this record, you
understand the data model.

### The trick: folders are free metadata

Gluth's file server uses a numbered convention:

```
A07622000_GSBC-2K/
  2_Anfrage/        the customer's inquiry
  3_Angebot/        our offer
  4_Kalkulation/    costing
  5_Zukaufteile/    bought-in parts
  6_Korrespondenz/  emails
  7_Auftrag/        the order
  8_Nachträge/      addenda
```

`derive_meta()` parses this. That gives every chunk a project number, a customer,
and a workflow phase **without any classifier**. A chunk from `3_Angebot` *is* a
labelled offer — no model needs to guess that. This is the single highest
value-per-line function in the codebase.

> **Lesson:** before reaching for a model, look at what structure already exists
> in the data. Filesystem conventions, naming schemes and folder hierarchies are
> labelled training data that someone already paid for.

### Format handlers

Each returns `list[(page_label, text)]`. Adding a format means writing one
function and adding one dict entry:

| Handler | Format | Notes |
|---|---|---|
| `extract_pdf` | `.pdf` | PyMuPDF, one entry per page |
| `extract_docx` | `.docx` | paragraphs **and table cells** |
| `extract_pptx` | `.pptx` | shapes, tables, and speaker notes |
| `extract_xlsx` | `.xlsx` `.xlsm` | one entry per worksheet, cells joined with ` \| ` |
| `extract_msg` | `.msg` | Outlook: headers + body + attachment names |
| `extract_doc` | `.doc` | shells out to macOS `textutil` |
| `extract_xml` | `.xml` | element text *and* attribute values |
| `extract_vsdx` | `.vsdx` | Visio is a ZIP; one entry per page XML |
| `extract_7z` | `.7z` | unpacks, then **recurses** into its own handlers |
| `extract_text` | `.txt` `.csv` `.md` | read as-is |

`extract_7z` is worth reading. It unpacks to a temp dir and runs each member
through `EXTRACTORS` again — so an archive of PDFs is handled by the PDF handler
with no special-casing. One archive in this corpus added 573 chunks that were
otherwise invisible.

### Detecting scans

A PDF can contain zero text and still look fine to a human — it is a photo of a
page. The heuristic:

```python
if total_chars / max(len(pages), 1) < SCAN_CHARS_PER_PAGE:   # 100
    → mark needs_ocr
```

Under 100 characters per page, it is a scan. That file is then handed to `ocr.py`.

### Chunking

`chunk_text()` splits at ~1200 characters with 150 characters of overlap,
preferring to break at a newline or space rather than mid-word.

Why overlap? A requirement that straddles a boundary would otherwise be split in
half and match neither chunk well. Overlap costs a little duplication and buys
back boundary recall.

Why 1200? It is roughly 300 German tokens — comfortably inside the embedding
model's 512-token limit, with headroom. See §12 for why this number was checked
rather than assumed.

### The manifest

`out/manifest.json` records **every file and what happened to it**:
`ok`, `needs_ocr`, `unsupported`, `skipped`, `out_of_scope`, `failed`, `empty`.

This is not logging — it is the product. "What fraction of your corpus is even
machine-readable?" is the first question anyone serious will ask, and this file
answers it. Current answer: **236/236 = 100%**.

---

## 5. Stage 0.5 — OCR (`ocr.py`)

**Job:** recover text from scans, TIFFs and JPGs.

It uses the **macOS Vision framework** — on-device, no model download, no
network, and German (`de-DE`) supported natively. On Apple silicon it is fast and
notably better on engineering drawings than a stock Tesseract install.

### The sidecar pattern

OCR does not modify the corpus. It writes a sidecar:

```
out/ocr/<sha1-of-source-path>.json
   { "source_path": …, "filename": …, "pages": [ {page, text}, … ] }
```

`extract.py` then picks those up automatically via `load_ocr_sidecars()` and
folds them in as if the file had always contained text — same metadata, same
chunking, same index.

Why this shape:

- **Idempotent.** Re-running skips files already done (`--force` overrides).
- **Non-destructive.** The original documents are never touched.
- **Debuggable.** You can read exactly what the OCR saw.
- **Composable.** Any other OCR engine can write the same JSON.

### Tiling — the part that matters

Vision **silently returns nothing** on very large images. Not an error, not a
warning: an empty result that looks exactly like "this image has no text".

One drawing in this corpus is 28,086 × 19,866 px — **558 megapixels**. It was
recorded as having no text for an entire day. It is in fact a Mercedes-Benz parts
table.

`_recognize_tiled()` fixes it: anything over 30 MP is split into a square-ish
grid of overlapping tiles, each OCR'd separately, with duplicate lines removed
(overlap means the same line can appear twice). That recovered 8,011 characters
from the "empty" file.

> **Lesson:** an API returning empty is not evidence of an empty input. When a
> result is suspiciously clean, verify the tool actually looked.

---

## 6. Stage 1 — indexing (`index.py`)

**Job:** build the two structures that make search fast.

### Tier 0 — BM25 (lexical)

BM25 is the classic keyword-relevance formula. A document scores highly when it
contains the query's rare terms often, adjusted for document length. It has no
neural network in it at all.

```python
stemmer = Stemmer.Stemmer("german")
tokens  = bm25s.tokenize(corpus, stopwords="de", stemmer=stemmer)
```

The German stemmer matters here: it collapses *Montage / Montagen / Montagelinie*
toward a shared root so they match each other. German compounds are the reason a
naive English tokenizer does badly on this corpus.

Builds in **under a second** for 8,000 chunks. There is no reason not to have it.

### Tier 1 — dense embeddings (semantic)

An embedding model converts text into a vector of numbers positioned so that
similar meanings sit near each other. This is what lets *"Absauganlage"* match
*"extraction unit"* without sharing a single character.

```python
model = TextEmbedding(model_name=…)     # ONNX via fastembed — no PyTorch
arr /= np.linalg.norm(arr, axis=1, keepdims=True)   # normalise once
np.save(VEC_FILE, arr)
```

Two details worth understanding:

- **Normalising at build time** means cosine similarity at query time is just a
  dot product — one matrix multiply, no division. For 8,000 vectors this is fast
  enough that no vector database is needed. `numpy` *is* the vector store.
- **`fastembed` runs ONNX, not PyTorch.** That is a deliberate choice for an
  8 GB laptop: a fraction of the memory and no multi-gigabyte torch install.

`out/vectors.meta.json` records which model built the index, and `search.py`
reads it to embed queries with the *same* model. Mixing models silently produces
garbage similarities — this file prevents that class of bug.

---

## 7. Stage 2 — retrieval (`search.py`)

**Job:** given a query, return the most relevant chunks — grouped into projects,
because "which project" is the unit a sales engineer thinks in.

### Four modes

| Mode | What it does |
|---|---|
| `bm25` | lexical only |
| `dense` | embeddings only |
| `hybrid` | both, fused with RRF |
| `rerank` | hybrid, then re-scored by a cross-encoder |

### Reciprocal Rank Fusion

How do you combine two rankings whose scores are on incompatible scales? You
ignore the scores and use only the ranks:

```python
score[doc] += 1.0 / (k + rank + 1)      # k = 60, the standard constant
```

A document ranked #1 by either method scores well; one ranked highly by *both*
scores best. No calibration, no tuning, no training. It is the standard method
for exactly this reason.

### Reranking

A **bi-encoder** (the embedding model) encodes query and document separately and
compares vectors — fast, because documents are pre-computed, but the model never
sees the two texts together.

A **cross-encoder** reads query and passage *concatenated* and scores the pair
directly. Far more accurate, far too slow to run over 8,000 chunks — so it runs
over the top ~30 that hybrid already surfaced.

The division of labour:

> **Hybrid supplies recall. The reranker supplies precision.**

This is not a slogan; it is what the measurements showed. See §10.

### Metadata filtering

```python
search(query, customer="Bosch")     # never returns a BMW document
```

This is not a UI convenience. Gluth is TISAX-assessed and per-customer
separation is a compliance requirement — *"a Bosch inquiry never surfaces in a
BMW answer"*. Enforcing it in the retriever rather than by hiding rows in the UI
is the difference between a control and a decoration.

---

## 8. Stage 3 — the analysis layer

Four modules that read `chunks.jsonl` and answer real questions. None uses a
model.

### `artifacts.py` — Summary / Checkliste / Übersicht

This is the conceptually interesting one.

The project brief calls for three drafted documents. Drafting sounds like a job
for a language model. It is not, if you are willing to accept **extraction
instead of generation**.

Every line in the output is a **verbatim sentence** from a real document,
carrying its file and page. Nothing is rewritten, so nothing can be
hallucinated. The guardrail in the original concept deck — *"the model may only
assemble text from retrieved passages"* — is enforced **structurally** rather
than by asking a model nicely.

Classification is rule-based requirements engineering:

| Rule | Regex catches | Example it finds |
|---|---|---|
| requirement | deontic modals — `muss`, `ist zu`, `vorzusehen`, `shall`, `must` | *"Die Arbeitshöhe … muss bei 1.070 mm liegen"* |
| constraint | a number followed by a unit | *"75 dB (A) unterschreiten"* |
| open point | explicit TBD — `noch zu klären`, `abzustimmen`, `not specified` | *"Cap torque parameters to be agreed"* |
| boilerplate | copyright, "Seite x von y", confidentiality footers | **dropped** |

Deontic modal verbs are how requirements are written in engineering German and
English. That is not a heuristic someone invented here — it is how the documents
are drafted, which is why a regex works so well.

**Confidence is a retrieval property, not an opinion.** It is computed from item
count and citation rate. Nothing "feels" confident.

The trade-off, stated honestly: a requirement phrased without a modal verb is
missed. That is a visible, fixable failure — unlike a model's mistakes, which
look identical to its successes.

### `drift.py` — the version gap

Answers *"are we working from the current document?"* Three findings:

| Finding | Meaning |
|---|---|
| `MULTIPLE_VERSIONS` | the same document is on file at two versions — which was the offer written against? |
| `STALE_REFERENCE` | a document cites version X while the newest held is Y |
| `NOT_HELD` | a standard is cited but no local copy exists, so nobody can check it changed |

The design lesson here is about **choosing the right signal**. A first attempt
looked for a `Rev`/`Version` marker near a norm ID in the body text. It found
**one hit in 7,500 chunks** — real specs list versions in reference *tables*,
not inline prose.

Versions in this corpus actually live in two reliable places:

1. **the filename** — `LH_04K_000051_V1.02.pdf`, `Y152058_…_008.pdf`
2. **an explicit statement** — `Lastenheft Nr.: LH_04K_000051, Version: 1.01`

Comparing *held* against *cited* is what produces the findings. On this corpus:
**1 / 1 / 62**.

### `specdiff.py` — what changed between revisions

Pure `difflib` over the extracted text of two documents. No model, no
embeddings.

`--numeric` is the feature that makes it usable: it keeps only changes where a
**number actually differs**, discarding renumbering and bullet-character churn.
On one real pair that cut 48 changed blocks to 11 — and put this at the top:

```
- Max.  8000 x 1000 x 2200mm     (L x W x H)
+ Max. 11000 x 7000 x 2200mm     (L x W x H)
```

The machine envelope grew seven times wider between two minor revisions of the
same specification. If the offer was quoted against the older one, the layout
and the price are both wrong.

### `norms.py` — referenced standards

Regex extraction of standard identifiers — `DIN EN ISO 12100`, `2006/42/EG`,
Bosch `N2580`, Knorr `Y506975`, BMW `GS 95024` — each with the file and page it
was found on. Deterministic and auditable: every hit points somewhere.

---

## 9. Stage 4 — serving and state

### `serve.py` — FastAPI

Thin. Every endpoint delegates to a module that also works from the command
line. That is deliberate: if the API is the only way to run something, you cannot
debug it.

Endpoint groups: corpus (`/api/health`, `/api/manifest`), retrieval
(`/api/search`), analysis (`/api/artifact/…`, `/api/drift`, `/api/specdiff`,
`/api/norms`), workflow (`/api/inquiries`, `/api/corrections`, `/api/decision`,
`/api/approve`), and `/` which serves the UI.

`/api/source` deserves a mention: it returns the real extracted text behind a
citation. That is what makes the citation chips in the UI *work* rather than
merely look official.

### `store.py` — SQLite

Four tables: `inquiry`, `correction`, `decision`, and a `review` log.

The queue is **seeded from the corpus** — `seed_from_corpus()` creates one
inquiry per project actually on disk. Nothing on any screen is fixture data.

`derive_stage()` is a small piece of domain modelling worth reading. Only
`2_`/`3_`/`7_`/`8_Nachträge` are workflow steps; `4_Kalkulation`,
`5_Zukaufteile` and `6_Korrespondenz` exist throughout a project's life and must
**not** be read as progress. An earlier version sorted folders alphabetically and
cheerfully reported a project's stage as "5_Zukaufteile".

The `correction` table is the governance mechanism: every reviewer correction is
logged with a route tag (`Drift` / `Content` / `OK`). Reviewed monthly, it is the
early warning that the knowledge base has gone stale.

### `ui.html` — the workspace

One file, seven screens, no build step. Every screen reads the API.

Queue · Review · Precedent Search · Version Drift · Correction Log · Dashboard ·
Triage.

The Review screen is the heart: extractive draft on the left with numbered
citation chips, real source text on the right. Clicking `[10]` opens page 10 of
the actual specification. Checking a claim becomes *"does the source say this?"*
— seconds — instead of reading the whole draft and judging whether it feels
right.

---

## 10. Measurement (`eval.py`)

**The most important module in the repository.**

Without it, every statement about retrieval quality is an opinion. With it, each
change is a number that moved or did not.

### Two query sets, neither trustworthy alone

- **auto** — known-item queries built from the corpus itself: a chunk is reduced
  to shuffled keywords and the correct answer is the file it came from. Large
  (100) and reproducible (fixed seed), but **biased toward lexical matching**,
  because the keywords are literally lifted from the target text.
- **hand** — realistic German/English queries written the way a sales engineer
  asks. Realistic but small (18), so small differences are noise.

Reporting both, with their biases stated, is the honest thing to do.

### Metrics

- **Recall@k** — was the right document in the top *k*?
- **MRR** — mean reciprocal rank; rewards putting it first.
- **nDCG@10** — position-weighted; the standard headline number.

### What it actually found

| mode | auto nDCG@10 | hand nDCG@10 | auto R@1 | hand R@1 |
|---|---|---|---|---|
| bm25 | **0.771** | 0.709 | 0.520 | 0.556 |
| dense | 0.360 | **0.924** | 0.110 | 0.889 |
| hybrid | 0.630 | 0.862 | 0.360 | 0.722 |
| hybrid + rerank | 0.729 | 0.892 | 0.470 | 0.833 |

Three things fall out of this table, none of which were guessable:

1. **Hybrid was worse than the best single retriever on both sets.**
   Equal-weight RRF dilutes whichever method is stronger for that query type.
2. **The two sets disagree about which is stronger** — keyword queries want
   BM25, natural-language queries want dense. That is a real property of the
   problem, not a flaw in the measurement.
3. **Hybrid's real strength is recall** (R@10 ≈ 0.94 on both). The answer is
   nearly always in the top ten, just not first — which is exactly what a
   reranker fixes. Adding one recovered **+16% nDCG and +31% R@1** over hybrid.

> **Lesson:** "hybrid search is better" is received wisdom that was false on this
> corpus until a reranker was added. Build the scoreboard before the feature.

---

## 11. Design decisions and why

**Why no vector database?**
8,195 vectors is a 13 MB numpy array. A dot product over it takes milliseconds.
Qdrant or LanceDB would add an operational dependency for no measurable gain at
this size. Revisit past ~1 M chunks.

**Why JSONL for chunks?**
Streamable, greppable, diffable, and readable in any language without a library.
You can inspect the corpus with `head` and `jq`. A binary format would save
space and cost far more in debuggability.

**Why sidecars for OCR instead of rewriting documents?**
The corpus is customer property. Never modify the input.

**Why do CLI and API share modules?**
Anything only reachable through HTTP is hard to debug. Every capability is a
function, wrapped twice.

**Why extractive rather than generative artifacts?**
Because it makes hallucination structurally impossible rather than
statistically unlikely — and because the AI component was on hold, which forced
a design that turned out to be more defensible anyway.

**Why is `out/` gitignored?**
It contains extracted text from confidential customer documents, including an
OCR'd BMW confidentiality agreement. It is derived data: delete it and rebuild
in about nine minutes.

---

## 12. Bugs we hit, and what they teach

Each of these was real, and each generalises.

### The `\b` that never matched

```python
re.compile(r"\b(Y\d{6})\b")     # never matches Y152058_LH_Standards.pdf
```

`_` is a **word character**, so there is no word boundary between `8` and `_`.
Every underscore-suffixed document ID was invisible, which is why drift detection
initially found nothing. Fix: `(?!\d)` instead of a trailing `\b`.

> Test regexes against your actual filenames, not against what you imagine they
> look like.

### Vision's silent empty result

A 558-megapixel drawing returned no text and no error. It looked identical to a
blank image. See §5.

> Distinguish "no result" from "the tool declined to work".

### The self-matching `pgrep`

```bash
until ! pgrep -f "index.py"; do sleep 10; done
```

The shell running this loop has `index.py` in its own command string, so `pgrep`
matched itself and the loop never exited — while the job it was waiting for had
finished long ago.

> A process that searches for a pattern can find itself.

### The browser cache that hid a fix

A UI change appeared not to work. The code was correct; the browser was serving a
cached `ui.html`. Fixed by sending `Cache-Control: no-store` from `/`.

> When a change "does nothing", verify the new code is actually running before
> debugging its logic.

### Alphabetical sorting as domain logic

Sorting phase folders and taking the last gave `5_Zukaufteile` as a project's
current stage. Fixed by modelling which folders are workflow steps and which are
support folders (§9).

> Sorting is not the same as understanding. Domain rules belong in code, not in
> `sorted()`.

### An embedding model that was pathologically slow

`jina-embeddings-v2-base-de` did not finish 500 of 7,500 chunks in 25 minutes —
its 8k-token context window appears to be padded per chunk. Swapped for a smaller
model that finished in about seven.

> Measure throughput on your own hardware before committing to a model.

### A truncation theory that was wrong

The embedding model was suspected of truncating our 1,200-character chunks. It
was checked: the limit is 512 tokens, the median chunk is ~332. **No truncation.**

> Included deliberately. Checking a hypothesis and finding it false is a result,
> and cheaper than acting on it.

---

## 13. How to extend it

**Add a document format**
Write `extract_xyz(path) -> list[(page_label, text)]`, add it to `EXTRACTORS`,
re-run `extract.py`. `extract_7z` will pick it up inside archives automatically.

**Swap the embedding model**
`index.py --embed --model <fastembed-name>`. The model name is written to
`vectors.meta.json` and `search.py` reads it, so queries stay consistent.
Then run `eval.py` — do not assume a newer model is better on *this* corpus.

**Add an analysis module**
Read `out/chunks.jsonl`, expose a `main()` for the CLI and one function for
`serve.py`. Follow `norms.py`; it is the smallest complete example.

**Add a UI screen**
Add a `<section id="screen-x">`, a sidebar `.side-link`, a title in
`PAGE_TITLES`, and a render function called from `switchScreen`.

**Change chunking**
`CHUNK_CHARS` / `CHUNK_OVERLAP` in `extract.py`. Re-extract, re-index,
**re-evaluate** — chunking changes retrieval quality more than most model swaps.

**When generation is allowed**
Nothing here needs to be undone. Retrieve with `search.py`, pass the passages to
a model, keep the citation binding, and use `eval.py` to prove retrieval did not
regress. The extractive artifacts become the fallback for low-confidence cases.

---

## 14. Glossary

| Term | Meaning |
|---|---|
| **BM25** | Classic keyword ranking formula. Rare terms in the query that appear often in a document score high. No neural network. |
| **Embedding** | A vector of numbers representing meaning; similar texts sit close together. |
| **Bi-encoder** | Encodes query and document separately. Fast, precomputable, less accurate. |
| **Cross-encoder** | Reads query and document together. Accurate, slow — used for reranking only. |
| **RRF** | Reciprocal Rank Fusion. Combines rankings using positions, not scores. |
| **Chunk** | A slice of a document, the unit that is indexed and retrieved. |
| **Recall@k** | Fraction of queries where the right document was in the top *k*. |
| **MRR** | Mean Reciprocal Rank — 1/position of the first correct result, averaged. |
| **nDCG** | Position-weighted relevance; the standard headline retrieval metric. |
| **ONNX** | A portable model format; runs without PyTorch, much lighter. |
| **Sidecar** | A side file holding derived data, leaving the original untouched. |
| **Lastenheft** | German: customer requirements specification. |
| **Anfrage / Angebot** | German: inquiry / offer. |
| **TISAX** | Automotive information-security assessment. Drives the per-customer separation rule. |
| **Deontic modal** | A verb expressing obligation (*muss*, *shall*) — how requirements are written. |

---

## Where to start reading

1. `extract.py` → `derive_meta()` and `EXTRACTORS` — the data model.
2. `search.py` → `search()` — how a query becomes results.
3. `artifacts.py` → `collect()` — extraction instead of generation, in ~20 lines.
4. `eval.py` → the table in §10 — why anything here is believable.

If you only read one function, read `derive_meta`. It is six lines that replace
a classifier.
