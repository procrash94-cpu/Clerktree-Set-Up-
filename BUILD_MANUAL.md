# Gluth "KI im Wertstrom" — Use Case 0
## Complete End-to-End Build Manual (Power Platform + Copilot Studio)

**Audience:** a build-team member starting from zero — no prior AI experience — doing everything by hand through the browser UI.
**Outcome if followed end to end:** a working, read-only technical-sales assistant that drafts *Summary / Checkliste / Übersicht* with citations, offers precedent search, and has a Power Apps reviewer surface — all inside the Gluth Microsoft 365 tenant.
**Time to first working pilot:** ~3–4 focused days once licences and data access exist.

> **Scope:** Phase 1 only. No cost figures, no Angebot text, no writing back to any system. Everything read-only and internal. What each part does and why is in the interview transcript and the concept deck — this manual is the *how*.

---

## Table of contents
0. [Before you touch anything — the five blockers](#0)
1. [Accounts, licences, environment](#1)
2. [SharePoint — the document library the AI reads](#2)
3. [Ingest / normalise — make the corpus machine-readable](#3)
4. [Copilot Studio — build the agent](#4)
5. [The three templates + precedent search (Topics)](#5)
6. [Power Apps — reviewer surface + correction log](#6)
7. [Power Automate — optional glue](#7)
8. [Testing & the Phase-1 gate](#8)
9. [Publish to Teams (pilot go-live)](#9)
10. [Governance from day one](#10)
11. [Deliberately NOT in Phase 1](#11)
12. [Troubleshooting](#12)
13. [Build-order checklist](#13)
14. [Phase 2 preview](#14)

---

<a id="0"></a>
## 0. Before you touch anything — the five blockers

None are technical. You can do §1–§3 (setup + ingest) without them, but you cannot pilot until all five are answered.

1. **Who owns the Gluth Datenbank export, and in what format?** → blocks ingestion.
2. **Are we permitted to index Bosch / BMW inquiry documents?** → contractual, per customer.
3. **Is Copilot Studio licensed and provisioned in the tenant?** → determines start date.
4. **Who signs off the EU AI Act risk classification?** → compliance/legal (in force 02.08.2026).
5. **Which two sales engineers pilot, with how much time?** → ~3 days for examples, ~2h/week during pilot.

> **Access from outside Germany** (e.g. India): must be authorised via company VPN or a Conditional Access exception *before* first sign-in. Every sign-in logs your IP and country in Entra; a location policy can block you outright; and unexpected-country access is a TISAX finding. Get it authorised — do not work around it.

---

<a id="1"></a>
## 1. Accounts, licences, environment

### 1.1 What you need
| Thing | Why | Who provides |
|---|---|---|
| Work account in the Gluth tenant | everything runs here | Gluth IT |
| Microsoft 365 Copilot **or** Copilot Studio licence | build the agent | admin assigns |
| Power Apps licence (per-user or per-app) | reviewer app | admin assigns |
| Power Platform environment with **Dataverse** | store queue + correction log | admin creates (§1.3) |
| SharePoint Team site | holds indexed documents | you create (§2) |
| AI Builder credits (optional) | OCR at scale | admin assigns |

### 1.2 Portals — bookmark all of these
- Microsoft 365 admin center — https://admin.microsoft.com
- Power Platform admin center — https://admin.powerplatform.microsoft.com
- Copilot Studio — https://copilotstudio.microsoft.com
- Power Apps maker — https://make.powerapps.com
- Power Automate — https://make.powerautomate.com
- SharePoint — https://\<tenant\>.sharepoint.com
- Teams admin — https://admin.teams.microsoft.com

### 1.3 Create the environment (admin task)
1. Power Platform admin center → **Environments → + New**.
2. Name `Gluth-KI-Wertstrom-Pilot`. Region **Europe**. Type **Production**.
3. **Create a database → Yes** (this provisions Dataverse). Set language DE/EN.
4. **Save** → wait for status **Ready**.
- Docs: https://learn.microsoft.com/power-platform/admin/create-environment

> Use **one** environment for the whole pilot. The agent, the app, and the correction log must live together. Do not build in your personal default environment.

---

<a id="2"></a>
## 2. SharePoint — the document library the AI reads

Copilot Studio (Phase 1) indexes SharePoint. **The AI can only ever cite what lives here and extracts cleanly.**

### 2.1 Create the site
1. https://\<tenant\>.sharepoint.com → **Create site → Team site**.
2. Name `KI-Wertstrom-Wissensbasis`. Privacy **Private**. Language German.
3. Members: build team + the two pilot engineers only. Nobody else.
- Docs: https://learn.microsoft.com/sharepoint/create-site-collection

### 2.2 Build the folder/library structure (mirror the corpus)
Inside **Documents**, create one library (or folder) per source category. The folder itself becomes metadata later, so don't dump everything in one place.
```
/Datenbank        Gluth products, past project scopes
/Beispiele        the ~30 Anfrage → Angebot gold pairs
/Normen           DIN / ISO licensed PDFs
/CE-Doku          conformity statements
/Anfragen-Mail    the .msg inquiries, converted (see §3)
/Zeichnungen      TIFF / scan drawings, OCR'd (see §3)
```

### 2.3 Add filter columns — before uploading
On each library: **Add column** →
- `Kunde` — Choice: Bosch / BMW / Dräxlmaier / BMG / Intern
- `Projektnummer` — Text, e.g. `A07622000`
- `Phase-Ordner` — Choice: 2_Anfrage … 8_Nachtrag
- `Dokumentdatum` — Date
- `Version` — Text
- Docs: https://learn.microsoft.com/sharepoint/dev/general-development/column-formatting

> **The `Kunde` column keeps a Bosch inquiry out of a BMW answer.** Fill it on every single file — this is the customer-separation rule TISAX depends on.

---

<a id="3"></a>
## 3. Ingest / normalise — make the corpus machine-readable

This is **70% of the real work**. SharePoint reads PDF/Word/Excel/PowerPoint text natively but **fails silently on `.msg`, TIFF, scanned PDFs, and oversized files**. Fix each before indexing.

### 3.1 Native — just upload
`.pdf` (text), `.docx`, `.xlsx`, `.pptx` → drop into the right library, set `Kunde` + `Projektnummer`.
**Never upload:** `Thumbs.db`, `~$…` lock files, `.lnk` shortcuts, `.url` files.

### 3.2 `.msg` e-mails (the "short inquiry" case) — must convert
SharePoint can't read Outlook message bodies.
1. Open each `.msg` in Outlook → **File → Save As → HTML** (or print to PDF).
2. Save into `/Anfragen-Mail` as `2023-09-25_Draexlmaier_Anfrage.pdf`, set columns.
- Only ~17 files → manual is fine for the pilot. Bulk `.msg` parsing via Power Automate is a Phase-2 nicety.

### 3.3 TIFF + scanned PDFs (drawings) — must OCR
These have no text layer.
1. Acrobat → **Scan & OCR → Recognize Text → Save As PDF**.
   No Acrobat? AI Builder text recognition: https://learn.microsoft.com/ai-builder/prebuilt-text-recognition
2. Save the searchable PDF into `/Zeichnungen`, set columns.

### 3.4 Oversized files (> ~15 MB) — the parser truncates silently
- Large `.pptx` concept decks → **File → Export → PDF** (smaller, parses better).
- CAD (`.stp`, `.STEP`, `.ipt`) → **out of scope, do not upload.** Geometry isn't text.

### 3.5 Build the gold set (with a sales engineer, ~2–3 days)
Pick ~30 inquiries where you have **both** the Anfrage and the final Angebot. Put each pair in `/Beispiele`, named to link:
```
A07622000_Anfrage.pdf
A07622000_Angebot_final.pdf
```
This defines "what good looks like" and becomes your test set in §8.

### 3.6 Verify extraction — don't skip
Wait ~30–60 min after upload for indexing. In SharePoint search, type a phrase you know is *inside* a file (e.g. `Volumenstrom 12000`). Returns the file → indexed. Nothing → extraction failed; revisit §3.2–3.4.

---

<a id="4"></a>
## 4. Copilot Studio — build the agent

Open https://copilotstudio.microsoft.com → **top-right environment picker → select `Gluth-KI-Wertstrom-Pilot`.** (Wrong environment is the #1 beginner mistake.)

### 4.1 Create
1. **Create → New agent.** Name `Gluth Vertriebs-Assistent`. Language German.
2. Instructions (paste — the core guardrail):
   > *You assist Gluth technical sales. You ONLY use information retrieved from the connected knowledge sources. You NEVER answer from your own knowledge. Every statement must cite the source document. You never produce prices, offer text, or send anything to a customer. If no source supports an answer, say "Kein passender Beleg gefunden" and stop.*
- Docs: https://learn.microsoft.com/microsoft-copilot-studio/fundamentals-get-started

### 4.2 Turn OFF general knowledge — the most important toggle
**Settings → Generative AI** → choose the **strict / grounded** style, **disable "Use general knowledge" / web search**, set **Content moderation → High**. This forces search-first, never write-from-memory.
- Docs: https://learn.microsoft.com/microsoft-copilot-studio/nlu-boost-conversations

### 4.3 Add the SharePoint knowledge source
**Knowledge → + Add knowledge → SharePoint** → paste the site URL (§2.1) → add each library (`/Datenbank`, `/Beispiele`, `/Normen`, `/CE-Doku`, `/Anfragen-Mail`, `/Zeichnungen`). Wait for **Ready** per source.
- Docs: https://learn.microsoft.com/microsoft-copilot-studio/knowledge-add-sharepoint

### 4.4 Enforce citations
**Settings → Generative AI → enable in-text citations / references.** Confirm answers show source links in the test pane.
- Docs: https://learn.microsoft.com/microsoft-copilot-studio/nlu-generative-answers

### 4.5 Customer separation (pick one)
- **Simple (pilot):** each pilot engineer has SharePoint permission only to their customer's folders; the agent inherits their permissions, so a Bosch reviewer physically cannot retrieve BMW docs.
- **Filtered (better, before wider rollout):** use the `Kunde` column as a knowledge filter per topic.

---

<a id="5"></a>
## 5. The three templates + precedent search (as Topics)

Templates = **Topics** with a fixed prompt. Each uses a **Generative answer** node scoped to the SharePoint libraries. **Topics → + Add a topic → Create from blank** for each.

### 5.1 Topic "Summary"
Triggers: `Zusammenfassung`, `summary`, `fasse Anfrage zusammen`.
```
Erstelle eine Zusammenfassung NUR aus den gefundenen Belegen. Struktur:
1. Kunde + Projektreferenz
2. Angefragter Umfang (5–8 Stichpunkte)
3. Vom Kunden genannte technische Randbedingungen
4. Explizit NICHT im Umfang
5. Offene Fragen an den Kunden
Jeder Stichpunkt MUSS die Quellseite zitieren. Erfinde nichts.
```

### 5.2 Topic "Checkliste"
```
1. Zutreffende Normen mit Abschnittsnummern
2. CE-relevante Punkte zu prüfen
3. Fehlende Informationen vom Kunden
4. Intern zu beteiligende Abteilungen (AV, PM, PTL)
5. Mind. 3 vergleichbare frühere Projekte
Normen NIEMALS ohne Quelllink nennen.
```

### 5.3 Topic "Übersicht"
```
1. Schnittstellen und Umfangsgrenzen
2. Annahmen, auf denen das Angebot beruht
3. Ähnliche gelieferte Projekte MIT Abweichungen (Delta)
4. Risiko-Flags (ungewöhnliche Anforderung, kein Präzedenzfall)
5. Empfohlener nächster Schritt
Immer mehrere Vergleichsprojekte zeigen, nie nur ein "bestes".
```

### 5.4 Topic "Ähnliche Projekte" — precedent search (the headline feature)
Triggered by a free-text machine description. Answers *"did we ever build something like this?"* — the thing sales asked for most.
```
Suche in /Beispiele und /Datenbank die ähnlichsten früheren Projekte zu folgender
Beschreibung. Liste je Treffer: Projektnummer, Kunde, warum ähnlich, Quelllink. Nie erfinden.
```
- Topic authoring: https://learn.microsoft.com/microsoft-copilot-studio/authoring-create-edit-topics

### 5.5 Test in the pane
Right-side **Test** panel: paste a real Anfrage excerpt, run each topic. Confirm (a) structure followed, (b) every claim has a link, (c) it refuses when nothing matches.

---

<a id="6"></a>
## 6. Power Apps — reviewer surface + correction log

Copilot Studio is the engine; Power Apps is where reviewers work and where every correction is logged.

### 6.1 Dataverse tables (https://make.powerapps.com → **Tables → + New table**)

**Table `Inquiry` (Anfrage)**
| Column | Type |
|---|---|
| ProjectNumber | Text |
| Customer | Choice (Bosch/BMW/Dräxlmaier/BMG/Intern) |
| Stage | Choice (2_Anfrage…8_Nachtrag) |
| Status | Choice (New/Awaiting review/Full review/Approved/Won/Lost) |
| Confidence | Choice (High/Low) |
| AssignedTo | Lookup (User) |

**Table `Draft`**
| Column | Type |
|---|---|
| Inquiry | Lookup → Inquiry |
| TemplateType | Choice (Summary/Checkliste/Übersicht) |
| GeneratedText | Multiline text |
| ConfidenceScore | Number |

**Table `Correction`** *(the drift early-warning)*
| Column | Type |
|---|---|
| Draft | Lookup → Draft |
| Field | Text |
| ReviewerAction | Multiline |
| RouteTag | Choice (Drift/Content/OK) |
| Reviewer | Lookup (User) |
| Timestamp | DateTime |
- Docs: https://learn.microsoft.com/power-apps/maker/data-platform/create-edit-entities

### 6.2 Build the app
**+ Create → Blank canvas app** (Tablet layout). Screens:
- **Queue** — gallery on `Inquiry`, sorted by Status: project, customer, stage, confidence pill, status.
- **Review** — opens a Draft: generated text with clickable citations + **Approve** and **Correct** buttons. Correct → writes a row to `Correction`.
- **Correction log** — gallery on `Correction`, filterable, highlights `Drift` rows.
- **Dashboard** — KPI tiles from stored metrics.
- Docs: https://learn.microsoft.com/power-apps/maker/canvas-apps/get-started-test-drive

### 6.3 Connect the agent to the app
Embed the published Copilot Studio agent (or call it via connector) so reviewers trigger drafting from inside the app.
- Docs: https://learn.microsoft.com/microsoft-copilot-studio/copilot-studio-agent-builder

---

<a id="7"></a>
## 7. Power Automate — optional glue (nice, not required for pilot)

- **New inquiry intake:** flow triggers when a file lands in `/Anfragen-Mail` or a new inquiry email arrives → creates an `Inquiry` row → sets `AssignedTo` by customer (BMW→Pilot A, Bosch→Pilot B, else Admin).
- **Bulk `.msg` conversion** (replaces §3.2 manual step at scale).
- Docs: https://learn.microsoft.com/power-automate/getting-started

---

<a id="8"></a>
## 8. Testing & the Phase-1 gate

Copilot Studio has **no offline eval harness**, so measure manually — this is the honest substitute.
1. Take the ~30 gold inquiries (§3.5). Run all three topics on each.
2. Reviewer scores one thing in the app: **was the correct source found? yes/no.**
3. Read the rate off the `Correction` table after 30 inquiries.

**Gate to go wider:** correct source found **≥ 90%** AND pilots report **net time saved**. If not met → fix ingest/retrieval before going wider. **Do not proceed on schedule pressure.**

**Success metrics (define now, measure later):**
- ≥ 90% correct source retrieved
- ≥ 40% time saved per inquiry
- 100% of claims carry a source link
- 10% of outputs blind re-checked, forever

---

<a id="9"></a>
## 9. Publish to Teams (pilot go-live)

1. Copilot Studio → **Channels → Microsoft Teams → Turn on**.
2. **Publish** the agent.
3. Submit for admin approval if required, then share the Teams link with the two pilot engineers.
- Docs: https://learn.microsoft.com/microsoft-copilot-studio/publication-add-bot-to-microsoft-teams

---

<a id="10"></a>
## 10. Governance from day one

- **Correction log reviewed monthly as a team** — the only early warning that the knowledge base went stale or a norm changed.
- **10% blind re-check** — 1 in 10 outputs, fixed schedule, redone by hand and compared. Never optional.
- **Named freshness owner** — one person re-syncs Normen/CE when versions change.
- **Prototypenschutz** — exclude inquiries referencing Testfahrzeuge / unreleased parts until cleared.
- **Read-only, always** — the agent queries; it never writes back to Datenbank, PDM, ERP.

---

<a id="11"></a>
## 11. Deliberately NOT in Phase 1

| Not built | Why / when |
|---|---|
| Cost figures / Kostencheck | Phase 2 — built as an ERP *lookup*, never a generated number. |
| Angebot text | The binding offer stays human. |
| Concept PowerPoint | The creative read of the machine is a human task; the assistant only feeds it. |
| Spec-diff & reference-version drift | Need real code — Phase 2 on Azure. |

---

<a id="12"></a>
## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Agent answers without citations | Citations toggle off, or general knowledge on | §4.2 + §4.4 |
| Agent invents facts | General knowledge not disabled | §4.2 |
| A document never appears in answers | Extraction failed (`.msg`/TIFF/oversized) | §3.2–3.4, re-verify §3.6 |
| Bosch docs surface in a BMW session | Missing `Kunde` filter / wrong permissions | §2.3 + §4.5 |
| Knowledge source stuck "Processing" | Large library / first index | wait; check file count and sizes |
| Can't sign in from India | Conditional Access location block | authorise VPN / CA exception (§0) |
| Built in wrong place | Personal default environment | rebuild in the pilot environment (§1.3) |

---

<a id="13"></a>
## 13. Build-order checklist

- [ ] Environment + Dataverse created (§1.3)
- [ ] SharePoint site + libraries + `Kunde`/`Projektnummer` columns (§2)
- [ ] Corpus cleaned, `.msg`/TIFF converted, gold set built, extraction verified (§3)
- [ ] Agent created, general knowledge OFF, citations ON (§4)
- [ ] SharePoint knowledge added + Ready (§4.3)
- [ ] Three templates + precedent search built & tested (§5)
- [ ] Dataverse tables + reviewer app + correction log (§6)
- [ ] (Optional) intake + assignment flow (§7)
- [ ] 30-inquiry manual eval ≥ 90% (§8)
- [ ] Published to Teams for 2 pilots (§9)
- [ ] Monthly review + 10% re-check scheduled (§10)

---

<a id="14"></a>
## 14. Phase 2 preview (after the gate clears)

Azure OpenAI + Azure AI Search, read-only ERP (ap+ PROD) and PDM (PRO.FILE 8.7) connections, **Kostencheck as a lookup** (the model identifies which positions apply, ERP returns the numbers, the model only assembles and cites), **spec-diff** between revisions, **reference-version drift** checking, and a **real evaluation harness** for the 90% gate.

**Nothing built in Phase 1 is thrown away:** the SharePoint-normalised corpus from §3 is the direct input to Azure AI Search, and the Power Apps reviewer surface + correction log carry straight over.

---

*Faster. Smarter. Leaner. — KI im Wertstrom · Aufbruch 2026 +X*
