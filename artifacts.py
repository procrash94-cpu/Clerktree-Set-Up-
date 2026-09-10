#!/usr/bin/env python3
"""
Summary / Checkliste / Übersicht — built by EXTRACTION, not generation.

Every line returned is a verbatim sentence from a real document, carrying the
file and page it came from. Nothing is written by a model, so nothing can be
hallucinated. This is the same guardrail the concept deck demands ("may only
assemble text from retrieved passages") — enforced structurally rather than
by prompt.

Classification is rule-based requirements engineering:
  requirement  German/English deontic modals (muss, ist zu, shall, must …)
  constraint   a measurable value with a unit
  open point   an explicit TBD / noch zu klären / not specified

    python artifacts.py A07622000
    python artifacts.py A07622000 --artifact checkliste
"""
from __future__ import annotations

import argparse
import re

import norms as norms_mod
import search as search_mod

# --- rule-based classifiers (deterministic, inspectable) -------------------

REQUIREMENT_RE = re.compile(
    r"\b(muss|müssen|ist zu\b|sind zu\b|hat zu\b|haben zu\b|vorzusehen|"
    r"sicherzustellen|einzuhalten|erforderlich|zwingend|"
    r"shall\b|must\b|is required|are required|has to\b|have to\b|mandatory)\b",
    re.I)

CONSTRAINT_RE = re.compile(
    r"\b\d[\d.,]*\s?(mm|cm|m|m²|m³|m³/h|kg|g|t|s|ms|min|h|°C|K|bar|mbar|Pa|"
    r"kW|W|V|A|Hz|%|Nm|l|ml|ppm|dB|µm|um|inch|\"|zoll|stk|st\.|takt)\b", re.I)

OPEN_RE = re.compile(
    r"\b(tbd|t\.b\.d|to be (defined|determined|clarified|agreed)|"
    r"noch zu klären|abzustimmen|festzulegen|offen(?!sichtlich)|"
    r"nicht spezifiziert|nicht definiert|not specified|not defined|"
    r"keine angabe|wird nachgereicht|klärungsbedarf)\b", re.I)

# Boilerplate that is true of every document and tells a reviewer nothing.
NOISE_RE = re.compile(
    r"(eigentum der|urheberrecht|copyright|vertraulich|confidential|"
    r"alle rechte|all rights reserved|seite \d+ von|page \d+ of|"
    r"ausdrucke sind nur|dieses dokument|printed copies|"
    r"unterliegen nicht dem änderungsdienst)", re.I)

SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n")

ANFRAGE_PHASES = ("2", "1")   # inquiry-side folders
OFFER_PHASES = ("3", "8")     # our own offer / addendum


def sentences(text: str):
    for raw in SENT_SPLIT.split(text):
        s = " ".join(raw.split())
        if 30 <= len(s) <= 400 and not NOISE_RE.search(s):
            yield s


def project_chunks(project_no: str, phases: tuple[str, ...] | None = None):
    out = []
    for c in search_mod.load_chunks():
        if c["project_no"] != project_no:
            continue
        if phases and c.get("phase_no") not in phases:
            continue
        out.append(c)
    return out


def _cite(c: dict, text: str) -> dict:
    return {
        "text": text,
        "filename": c["filename"],
        "source_path": c["source_path"],
        "page": c["page"],
        "phase": f'{c["phase_no"]}_{c["phase_name"]}' if c.get("phase_no") else "",
    }


def collect(chunks, pattern, limit=8, extra=None):
    """Verbatim sentences matching a rule, de-duplicated, with citations."""
    seen, out = set(), []
    for c in chunks:
        for s in sentences(c["text"]):
            if not pattern.search(s):
                continue
            if extra and not extra.search(s):
                continue
            key = s.lower()[:90]
            if key in seen:
                continue
            seen.add(key)
            out.append(_cite(c, s))
            if len(out) >= limit:
                return out
    return out


# --------------------------------------------------------------- artifacts

def build_summary(project_no: str) -> dict:
    anfrage = project_chunks(project_no, ANFRAGE_PHASES) or project_chunks(project_no)
    meta = anfrage[0] if anfrage else {}
    scope = collect(anfrage, REQUIREMENT_RE, limit=8)
    constraints = collect(anfrage, CONSTRAINT_RE, limit=6, extra=REQUIREMENT_RE) \
        or collect(anfrage, CONSTRAINT_RE, limit=6)
    open_pts = collect(anfrage, OPEN_RE, limit=6)

    return {
        "artifact": "Summary",
        "project_no": project_no,
        "method": "extractive — verbatim passages, no generation",
        "sections": [
            {"title": "Kunde & Projektreferenz", "items": [{
                "text": f'{meta.get("customer","—")} · {project_no} '
                        f'{meta.get("project_name","")}'.strip(),
                "filename": "(Ordnerstruktur)", "source_path": "", "page": "",
                "phase": "", "derived": True}]},
            {"title": "Angefragter Umfang", "items": scope},
            {"title": "Technische Randbedingungen", "items": constraints},
            {"title": "Offene Punkte / Klärungsbedarf", "items": open_pts,
             "flag": bool(open_pts)},
        ],
    }


def build_checkliste(project_no: str) -> dict:
    chunks = search_mod.load_chunks()
    found = norms_mod.extract(chunks, project_no)

    norm_items = []
    for norm_id, by_project in sorted(
            found.items(), key=lambda kv: -sum(len(v) for v in kv[1].values())):
        hits = by_project.get(project_no) or next(iter(by_project.values()))
        if len(hits) < 2:
            continue
        h = hits[0]
        norm_items.append({
            "text": f'{norm_id}  ({len(hits)} Fundstellen)',
            "filename": h["file"], "source_path": h["path"], "page": h["page"],
            "phase": "", "context": h["context"][:220],
        })
        if len(norm_items) >= 10:
            break

    anfrage = project_chunks(project_no, ANFRAGE_PHASES) or project_chunks(project_no)
    missing = collect(anfrage, OPEN_RE, limit=6)

    ce = [n for n in norm_items
          if re.search(r"2006/42|13849|12100|60204|ISO\s*13849", n["text"], re.I)]

    comparables = comparable_projects(project_no, limit=3)

    return {
        "artifact": "Checkliste",
        "project_no": project_no,
        "method": "regex norm extraction + retrieval — deterministic",
        "sections": [
            {"title": "Zutreffende Normen & Standards", "items": norm_items},
            {"title": "CE-relevante Punkte", "items": ce,
             "note": "Sicherheits-/Maschinenrichtlinie-Bezüge aus den Belegen",
             "flag": not ce},
            {"title": "Fehlende Informationen vom Kunden", "items": missing,
             "flag": bool(missing)},
            {"title": "Vergleichbare frühere Projekte", "items": comparables},
        ],
    }


def comparable_projects(project_no: str, limit: int = 3) -> list[dict]:
    """Use this project's own requirement text as the query for precedent search."""
    own = project_chunks(project_no, ANFRAGE_PHASES) or project_chunks(project_no)
    seed = " ".join(i["text"] for i in collect(own, REQUIREMENT_RE, limit=4))
    if not seed:
        seed = " ".join(c["text"][:200] for c in own[:3])
    if not seed.strip():
        return []

    res = search_mod.search(seed[:1200], project_limit=limit + 2, per_project=1)
    out = []
    for p in res["projects"]:
        if p["project_no"] == project_no:
            continue
        hit = (p["hits"] or [{}])[0]
        out.append({
            "text": f'{p["project_no"]} · {p["project_name"]} '
                    f'({p["customer"]}) — Relevanz {p["score"]:.3f}',
            "filename": hit.get("filename", ""),
            "source_path": hit.get("source_path", ""),
            "page": hit.get("page", ""),
            "phase": hit.get("phase", ""),
            "context": hit.get("snippet", "")[:220],
        })
        if len(out) >= limit:
            break
    return out


def build_uebersicht(project_no: str) -> dict:
    chunks = project_chunks(project_no)
    phases = sorted({f'{c["phase_no"]}_{c["phase_name"]}'
                     for c in chunks if c.get("phase_no")})
    files = sorted({c["filename"] for c in chunks})
    comparables = comparable_projects(project_no, limit=4)

    scope_items = [{
        "text": f'{len(files)} Dokumente, {len(chunks)} Textabschnitte, '
                f'Phasen: {", ".join(phases) if phases else "—"}',
        "filename": "(Ordnerstruktur)", "source_path": "", "page": "",
        "phase": "", "derived": True}]

    anfrage = project_chunks(project_no, ANFRAGE_PHASES) or chunks
    assumptions = collect(anfrage, CONSTRAINT_RE, limit=5)

    risks = []
    if not comparables:
        risks.append({"text": "Kein vergleichbares Vorgängerprojekt gefunden — "
                              "erhöhtes Kalkulationsrisiko.",
                      "filename": "(Retrieval)", "source_path": "", "page": "",
                      "phase": "", "derived": True})
    open_pts = collect(anfrage, OPEN_RE, limit=4)
    risks.extend(open_pts)

    return {
        "artifact": "Übersicht",
        "project_no": project_no,
        "method": "corpus statistics + retrieval — no generation",
        "sections": [
            {"title": "Umfang & Schnittstellen", "items": scope_items},
            {"title": "Annahmen (messbare Werte aus den Belegen)",
             "items": assumptions},
            {"title": "Ähnliche gelieferte Projekte", "items": comparables,
             "note": "Immer mehrere Vergleichsprojekte, nie ein einziges „bestes“."},
            {"title": "Risiko-Flags", "items": risks, "flag": bool(risks)},
        ],
    }


BUILDERS = {
    "summary": build_summary,
    "checkliste": build_checkliste,
    "uebersicht": build_uebersicht,
}


def build(project_no: str, artifact: str = "summary") -> dict:
    fn = BUILDERS.get(artifact.lower())
    if not fn:
        raise ValueError(f"unknown artifact: {artifact}")
    doc = fn(project_no)
    cited = sum(1 for s in doc["sections"] for i in s["items"]
                if i.get("source_path"))
    total = sum(len(s["items"]) for s in doc["sections"])
    doc["citation_rate"] = round(100 * cited / total) if total else 0
    doc["item_count"] = total
    # Confidence is a retrieval property, not a model opinion.
    doc["confidence"] = "High" if total >= 12 and doc["citation_rate"] >= 70 \
        else ("Low" if total < 6 else "Medium")
    doc["llm_used"] = False
    return doc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project_no")
    ap.add_argument("--artifact", default="summary",
                    choices=list(BUILDERS))
    args = ap.parse_args()

    doc = build(args.project_no, args.artifact)
    print(f'\n  {doc["artifact"]} — {doc["project_no"]}')
    print(f'  {doc["method"]}')
    print(f'  {doc["item_count"]} Einträge · {doc["citation_rate"]}% mit Quelle '
          f'· Konfidenz {doc["confidence"]}')
    print("  " + "─" * 74)
    for sec in doc["sections"]:
        flag = "  ⚑" if sec.get("flag") else ""
        print(f'\n  {sec["title"]}{flag}')
        if not sec["items"]:
            print("     (nichts gefunden)")
        for it in sec["items"]:
            print(f'     • {it["text"][:150]}')
            if it.get("source_path"):
                pg = f' p.{it["page"]}' if it["page"] else ""
                print(f'       ↳ {it["filename"]}{pg}')
    print()


if __name__ == "__main__":
    main()
