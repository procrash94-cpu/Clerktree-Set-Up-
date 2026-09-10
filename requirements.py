#!/usr/bin/env python3
"""
requirements.py — Anforderungsobjekte aus dem Chunk-Bestand.

Schliesst die zentrale Luecke der Pipeline: heute geht die Kette
Dokument -> Chunk -> Satz -> Anzeige. Fuer die Entwicklungsakte, die
Konfliktpruefung und den Excel-Export braucht es dazwischen ein Objekt
mit Identitaet: die Anforderung.

Kein Sprachmodell. Nur Regeln, regulaere Ausdruecke und Woerterbuecher.
Nur Standardbibliothek.

    python requirements.py                      # alles, schreibt out/requirements.jsonl
    python requirements.py --project A07614000  # nur ein Projekt
    python requirements.py --show 15            # Tabelle auf der Konsole
    python requirements.py --self-test          # ohne Korpus pruefbar

Ausgabefelder je Anforderung
----------------------------
req_id        stabile Kennung dieses Wortlauts
subject_key   Kennung des *Gegenstands* ohne Zahlenwerte.
              Damit findet die Akte spaeter zusammen, was dieselbe
              Anforderung in zwei Fassungen ist:
              "Taktzeit max. 32 s"  und  "Taktzeit max. 28 s"
              haben denselben subject_key, aber verschiedene req_id.
binding       MUSS | SOLL | KANN | VERBOT | INFO
negated       True, wenn der Satz eine Verneinung enthaelt
operator      LE | GE | LT | GT | EQ | RANGE | None
value         Zahl (deutsches Format korrekt gelesen)
value_max     zweite Zahl bei RANGE
unit          mm, s, bar, V, IP.. usw.
tolerance     z. B. "+/- 0,05 mm"
clause        Gliederungsnummer, falls der Satz mit einer beginnt
category      Disziplin bzw. Station (kommt aus router.py)
measurable    True, wenn Operator, Wert und Einheit vorliegen
needs_question offene Stelle beim Kunden ("wird noch festgelegt", "t.b.d.")
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

try:  # router liegt daneben; die Datei laeuft auch ohne ihn
    from router import Router
except Exception:  # pragma: no cover
    Router = None  # type: ignore

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"


# ===================================================================== 1 Saetze
# Abkuerzungen, hinter denen der Punkt KEIN Satzende ist. Ohne diese Liste
# zerschneidet der Splitter "max. 32 s" oder "z. B. Schutzart" mitten im Satz.
ABBREV = {
    "z", "b", "bzw", "ca", "evtl", "ggf", "inkl", "exkl", "max", "min", "mind",
    "nr", "abs", "art", "vgl", "sog", "u", "a", "d", "h", "s", "o", "usw",
    "etc", "tel", "fa", "hr", "dr", "ing", "pos", "art", "kap", "abb", "tab",
    "rev", "ver", "zzgl", "lt", "gem", "bspw", "einschl",
}

_SENT_END = re.compile(r"([.!?:;])\s+(?=[A-ZÄÖÜ0-9])")


def split_sentences(text: str) -> list[str]:
    """Deutscher Satz-Splitter, der Abkuerzungen und Dezimalzahlen respektiert."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []

    # Punkte schuetzen, die keine Satzenden sind
    protected = text
    # Dezimal- und Tausendertrennung: 1.070,5
    protected = re.sub(r"(\d)\.(\d)", "\\1\x00\\2", protected)
    # Gliederungsnummern: 3.2.1
    protected = re.sub(r"(\d)\.(?=\d)", "\\1\x00", protected)
    # Abkuerzungen
    def _abbr(m: re.Match) -> str:
        word = m.group(1)
        return f"{word}\x00 " if word.lower().strip(" ") in ABBREV else m.group(0)
    protected = re.sub(r"\b([A-Za-zÄÖÜäöüß]{1,7})\.\s", _abbr, protected)

    parts = _SENT_END.split(protected)
    # re.split mit Gruppe liefert [satz, zeichen, satz, zeichen, ...]
    sentences, buf = [], ""
    for i, piece in enumerate(parts):
        if i % 2 == 0:
            buf = piece
        else:
            sentences.append((buf + piece).replace("\x00", "."))
            buf = ""
    if buf:
        sentences.append(buf.replace("\x00", "."))
    return [s.strip() for s in sentences if len(s.strip()) > 12]


# ============================================================ 2 Verbindlichkeit
# WICHTIG: Verneinung wird VOR der KANN-Liste geprueft.
# "darf nicht verwendet werden" ist ein Verbot, keine Option.
# Laeuft es ueber die KANN-Liste, wird aus einem Verbot eine Freiheit —
# das ist der teuerste Fehler, den diese Datei machen koennte.

NEG = re.compile(
    r"\b(nicht|kein|keine|keinen|keiner|keinem|keines|nie|niemals|"
    r"unzul[aä]ssig|untersagt|verboten|ausgeschlossen|ohne)\b", re.I)

MUSS = re.compile(
    r"\b(muss|m[uü]ssen|ist zu\b|sind zu\b|hat zu\b|haben zu\b|zwingend|"
    r"verbindlich|erforderlich|zwingend erforderlich|vorgeschrieben|"
    r"zu gew[aä]hrleisten|einzuhalten|sicherzustellen|verpflichtend|"
    r"wird gefordert|gefordert wird|shall\b|must\b)\b", re.I)

SOLL = re.compile(r"\b(soll|sollen|sollte|sollten|angestrebt|vorzugsweise|should\b)\b", re.I)

KANN = re.compile(
    r"\b(kann|k[oö]nnen|darf|d[uü]rfen|optional|wahlweise|nach Bedarf|"
    r"bei Bedarf|freigestellt|may\b|can\b)\b", re.I)

VERBOT_DIREKT = re.compile(
    r"(darf\s+(?:\w+\s+){0,3}?nicht|d[uü]rfen\s+(?:\w+\s+){0,3}?nicht|"
    r"ist\s+(?:\w+\s+){0,2}?unzul[aä]ssig|sind\s+(?:\w+\s+){0,2}?unzul[aä]ssig|"
    r"ist untersagt|sind untersagt|ist verboten|nicht zul[aä]ssig|"
    r"nicht gestattet|nicht erlaubt|shall not|must not|darf kein)", re.I)


def classify_binding(sentence: str) -> tuple[str, bool]:
    """Gibt (Verbindlichkeit, verneint) zurueck."""
    negated = bool(NEG.search(sentence))

    # 1. Verbot hat immer Vorrang
    if VERBOT_DIREKT.search(sentence):
        return "VERBOT", True

    # 2. Verneintes MUSS bleibt ein Verbot ("es muss vermieden werden")
    if MUSS.search(sentence):
        return ("VERBOT", True) if negated and re.search(
            r"\b(vermieden|unterbleiben|verhindert|ausgeschlossen)\b", sentence, re.I
        ) else ("MUSS", negated)

    if SOLL.search(sentence):
        return "SOLL", negated

    # 3. KANN erst hier — und nur, wenn der Satz nicht verneint ist
    if KANN.search(sentence) and not negated:
        return "KANN", False
    if KANN.search(sentence) and negated:
        return "VERBOT", True

    return "INFO", negated


# ================================================================== 3 Zahlen
# Deutsches Format: 1.070 sind tausendsiebzig, 0,05 sind null Komma null fuenf.
# Wird das amerikanisch gelesen, liegen Werte um Faktor 1000 daneben.

UNITS = (
    r"mm²|cm²|m²|mm³|m³|"
    r"µm|um|mm|cm|dm|m\b|km|"
    r"mg|g\b|kg|t\b|"
    r"ms|s\b|sek|min|h\b|Std|"
    r"°C|°|K\b|"
    r"mbar|bar|Pa|kPa|MPa|N/mm²|Nm|N\b|"
    r"mV|V\b|kV|mA|A\b|kW|MW|W\b|VA|Hz|kHz|"
    r"dB\(A\)|dB|"
    r"ml|l/min|l\b|m/s|mm/s|U/min|min-1|"
    r"%|Stk|St\.|Zoll|IP\d{2}|IP\s?\d{2}"
)
_NUM = r"\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+(?:,\d+)?|\d+(?:\.\d+)?"

# Achtung "min": das ist zugleich eine Einheit (Minuten, l/min, U/min).
# Deshalb hier nur "min." mit Punkt oder "mind"/"mindestens" — und nie
# direkt hinter einem Schraegstrich. Sonst wird aus "200 l/min" ein
# "mindestens 200" und die Anforderung kippt in ihr Gegenteil.
OP_LE = re.compile(r"(?<![/\w])(max\.|maximal|h[oö]chstens|nicht mehr als|bis zu|kleiner gleich|≤|<=)", re.I)
OP_GE = re.compile(r"(?<![/\w])(min\.|mind\.|mindestens|wenigstens|nicht weniger als|gr[oö]ßer gleich|≥|>=)", re.I)
# Formulierungen, die den Operator im Verb tragen. Sie werden zuerst geprueft,
# weil sie eine Verneinung enthalten und sonst falsch herum gelesen wuerden.
PHRASE_LE = re.compile(r"nicht\s+(?:zu\s+)?(?:ue|[uü])(?:berschreit|bersteig|berschritten)", re.I)
PHRASE_GE = re.compile(r"nicht\s+(?:zu\s+)?(?:unterschreit|unterschritten|unterbiet)", re.I)
OP_LT = re.compile(r"\b(kleiner als|unter|weniger als|<)\b", re.I)
OP_GT = re.compile(r"\b(gr[oö]ßer als|[uü]ber|mehr als|>)\b", re.I)
OP_EQ = re.compile(r"\b(genau|exakt|betr(?:ae|[aä])gt|entspricht|=)\b", re.I)
RANGE = re.compile(rf"({_NUM})\s*(?:bis|\.\.\.|–|-|to)\s*({_NUM})\s*({UNITS})")
TOL = re.compile(rf"([+±]/?-?\s*{_NUM}\s*(?:{UNITS})?|\+\s*{_NUM}\s*/\s*-\s*{_NUM})")
VALUE = re.compile(rf"({_NUM})\s*({UNITS})")
IPCODE = re.compile(r"\bIP\s?(\d{2})\b")


def parse_german_number(raw: str) -> float | None:
    """'1.070,5' -> 1070.5 · '0,05' -> 0.05 · '1.070' -> 1070 · '3.2' -> 3.2"""
    s = raw.strip()
    if not s:
        return None
    if "," in s:                      # Komma ist immer das Dezimaltrennzeichen
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):   # 1.070 / 12.500.000
        s = s.replace(".", "")
    # alles andere (3.2) bleibt, wie es ist
    try:
        return float(s)
    except ValueError:
        return None


def extract_measure(sentence: str) -> dict:
    """Operator, Wert, Einheit, Toleranz aus einem Satz."""
    out = {"operator": None, "value": None, "value_max": None,
           "unit": None, "tolerance": None}

    m = TOL.search(sentence)
    if m:
        out["tolerance"] = m.group(1).strip()

    m = RANGE.search(sentence)
    if m:
        out.update(operator="RANGE",
                   value=parse_german_number(m.group(1)),
                   value_max=parse_german_number(m.group(2)),
                   unit=m.group(3).strip())
        return out

    m = IPCODE.search(sentence)
    if m:
        out.update(value=float(m.group(1)), unit="IP")
        out["operator"] = detect_operator(sentence) or "EQ"
        return out

    m = VALUE.search(sentence)
    if m:
        out["value"] = parse_german_number(m.group(1))
        out["unit"] = m.group(2).strip()

    out["operator"] = detect_operator(sentence)
    return out


def detect_operator(sentence: str) -> str | None:
    # Verbformulierungen zuerst — sie tragen eine Verneinung in sich
    if PHRASE_LE.search(sentence):
        return "LE"
    if PHRASE_GE.search(sentence):
        return "GE"
    for rx, op in ((OP_LE, "LE"), (OP_GE, "GE"), (OP_LT, "LT"),
                   (OP_GT, "GT"), (OP_EQ, "EQ")):
        if rx.search(sentence):
            return op
    return None


# ============================================================== 4 Sonstiges
CLAUSE = re.compile(r"^\s*((?:\d{1,2}\.){1,4}\d{0,2})\s+")
OPEN_Q = re.compile(
    r"\b(t\.?b\.?d\.?|tbd|noch (?:zu )?(?:kl[aä]ren|festzulegen|festlegen|definieren)|"
    r"wird noch|offen|n\.\s?n\.|nach Absprache|in Abstimmung|klärungsbedarf|"
    r"vom Kunden (?:zu )?(?:liefern|benennen)|k\.\s?A\.)\b", re.I)

# Saetze, die keine Anforderung sind — Kopfzeilen, Verteiler, Fusszeilen
NOISE = re.compile(
    r"^(seite|page|datum|date|von:|an:|cc:|betreff|subject|tel|fax|e-?mail|"
    r"inhaltsverzeichnis|anlage\s*\d|©|copyright|blatt)\b", re.I)


def is_requirement(sentence: str, binding: str) -> bool:
    if NOISE.match(sentence):
        return False
    if len(sentence) < 20 or len(sentence) > 600:
        return False
    if binding in ("MUSS", "SOLL", "KANN", "VERBOT"):
        return True
    # INFO nur, wenn ein messbarer Wert drinsteht — sonst ist es Fliesstext
    m = extract_measure(sentence)
    return bool(m["value"] is not None and m["unit"])


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


_STRIP_NUM = re.compile(rf"({_NUM})\s*({UNITS})")
_STRIP_WS = re.compile(r"[^a-zäöüß ]+")


def subject_key(sentence: str, project_no: str = "") -> str:
    """
    Kennung des Gegenstands, unabhaengig von den Zahlenwerten.

    Genau das macht die Entwicklungsakte ohne KI moeglich:
    "Die Taktzeit muss max. 32 s betragen."   ->  subject_key X
    "Die Taktzeit muss max. 28 s betragen."   ->  subject_key X  (dieselbe Anforderung)
    Die beiden Saetze bekommen verschiedene req_id, aber denselben subject_key —
    daraus baut store_akte.py die Versionskette.
    """
    s = sentence.lower()
    s = _STRIP_NUM.sub(" ", s)          # Zahlen samt Einheit raus
    s = re.sub(r"\d+", " ", s)          # restliche Ziffern raus
    s = _STRIP_WS.sub(" ", s)           # Satzzeichen raus
    stop = {"der", "die", "das", "den", "dem", "des", "ein", "eine", "einer",
            "eines", "und", "oder", "von", "vom", "für", "fuer", "mit", "bei",
            "ist", "sind", "wird", "werden", "muss", "müssen", "soll", "sollen",
            "kann", "können", "darf", "dürfen", "zu", "im", "in", "am", "an",
            "auf", "als", "nicht", "max", "min", "mindestens", "maximal", "es"}
    words = sorted({w for w in s.split() if len(w) > 3 and w not in stop})
    return _key(project_no + "|" + " ".join(words[:12]))


# ================================================================ 5 Objekt
@dataclass
class Requirement:
    req_id: str
    subject_key: str
    text: str
    binding: str
    negated: bool
    clause: str = ""
    operator: str | None = None
    value: float | None = None
    value_max: float | None = None
    unit: str | None = None
    tolerance: str | None = None
    category: str = ""
    category_hits: list[str] = field(default_factory=list)
    measurable: bool = False
    needs_question: bool = False
    # Herkunft — jede Anforderung bleibt bis zur Seite belegbar
    project_no: str = ""
    project_name: str = ""
    customer: str = ""
    phase: str = ""
    file: str = ""
    source_path: str = ""
    page: str | int | None = None
    chunk_id: str | int | None = None
    extracted_at: str = ""


def from_sentence(sentence: str, meta: dict, router=None) -> Requirement | None:
    binding, negated = classify_binding(sentence)
    if not is_requirement(sentence, binding):
        return None

    clause_m = CLAUSE.match(sentence)
    clause = clause_m.group(1) if clause_m else ""
    body = sentence[clause_m.end():] if clause_m else sentence

    meas = extract_measure(body)
    cat, hits = ("", [])
    if router is not None:
        cat, hits = router.route(body, header=meta.get("header", ""))

    return Requirement(
        req_id=_key(f'{meta.get("project_no","")}|{body}'),
        subject_key=subject_key(body, meta.get("project_no", "")),
        text=body.strip(),
        binding=binding,
        negated=negated,
        clause=clause,
        operator=meas["operator"],
        value=meas["value"],
        value_max=meas["value_max"],
        unit=meas["unit"],
        tolerance=meas["tolerance"],
        category=cat,
        category_hits=hits,
        measurable=bool(meas["operator"] and meas["value"] is not None and meas["unit"]),
        needs_question=bool(OPEN_Q.search(body)),
        project_no=meta.get("project_no", ""),
        project_name=meta.get("project_name", ""),
        customer=meta.get("customer", ""),
        phase=meta.get("phase", ""),
        file=meta.get("file", ""),
        source_path=meta.get("source_path", ""),
        page=meta.get("page"),
        chunk_id=meta.get("chunk_id"),
        extracted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


# ============================================================== 6 Korpuslauf
def load_chunks(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def chunk_meta(c: dict) -> dict:
    phase = ""
    if c.get("phase_no") or c.get("phase_name"):
        phase = f'{c.get("phase_no","")}_{c.get("phase_name","")}'.strip("_")
    return {
        "project_no": c.get("project_no", ""),
        "project_name": c.get("project_name", ""),
        "customer": c.get("customer", ""),
        "phase": phase,
        "file": c.get("filename") or Path(str(c.get("source_path", ""))).name,
        "source_path": c.get("source_path", ""),
        "page": c.get("page"),
        "chunk_id": c.get("id"),
        "header": c.get("header", ""),
    }


def build(chunks_path: Path, project: str | None = None,
          router=None) -> list[Requirement]:
    out: list[Requirement] = []
    seen: set[str] = set()
    for c in load_chunks(chunks_path):
        if project and c.get("project_no") != project:
            continue
        meta = chunk_meta(c)
        for sent in split_sentences(c.get("text", "")):
            r = from_sentence(sent, meta, router)
            if r and r.req_id not in seen:      # Dubletten aus Chunk-Ueberlappung
                seen.add(r.req_id)
                out.append(r)
    return out


# ================================================================ 7 Selbsttest
SELF_TEST = [
    # (Satz, Verbindlichkeit, Wert, Einheit, Operator)
    ("Die Taktzeit muss max. 32 s betragen.", "MUSS", 32.0, "s", "LE"),
    ("Die Taktzeit muss max. 28 s betragen.", "MUSS", 28.0, "s", "LE"),
    ("Der Schaltschrank darf nicht ausserhalb der Halle aufgestellt werden.",
     "VERBOT", None, None, None),
    ("Eine Beschichtung kann optional aufgebracht werden.", "KANN", None, None, None),
    ("Der Bauraum ist auf 1.070 mm begrenzt.", "INFO", 1070.0, "mm", None),
    ("Die Toleranz betraegt +/- 0,05 mm.", "INFO", 0.05, "mm", "EQ"),
    ("Die Schutzart soll mindestens IP54 erreichen.", "SOLL", 54.0, "IP", "GE"),
    ("Der Druck liegt zwischen 4 bis 6 bar.", "INFO", 4.0, "bar", "RANGE"),
    ("Die Lackfarbe wird noch festgelegt.", "INFO", None, None, None),
    # Die zwei Faelle, an denen sich der Operator leicht verdreht:
    ("Der Luftverbrauch soll 200 l/min nicht ueberschreiten.", "SOLL", 200.0,
     "l/min", "LE"),
    ("Die Haltekraft darf 50 N nicht unterschreiten.", "VERBOT", 50.0, "N", "GE"),
]


def self_test() -> int:
    print("Selbsttest requirements.py\n" + "-" * 68)
    fails = 0
    for sent, exp_bind, exp_val, exp_unit, exp_op in SELF_TEST:
        bind, neg = classify_binding(sent)
        meas = extract_measure(sent)
        ok_b = bind == exp_bind
        ok_v = (meas["value"] == exp_val)
        ok_u = ((meas["unit"] or None) == exp_unit)
        ok_o = ((meas["operator"] or None) == exp_op)
        flag = "ok  " if (ok_b and ok_v and ok_u and ok_o) else "FEHL"
        if flag == "FEHL":
            fails += 1
        print(f'{flag} {bind:<7} {str(meas["operator"]):<6} {str(meas["value"]):<8} '
              f'{str(meas["unit"]):<7} {sent[:44]}')
        if not ok_b:
            print(f"      erwartet Verbindlichkeit {exp_bind}")
        if not ok_v:
            print(f"      erwartet Wert {exp_val}")
        if not ok_u:
            print(f"      erwartet Einheit {exp_unit}")
        if not ok_o:
            print(f"      erwartet Operator {exp_op}")

    # Der wichtigste Test: gleiche Anforderung, anderer Wert -> gleicher subject_key
    a = subject_key("Die Taktzeit muss max. 32 s betragen.")
    b = subject_key("Die Taktzeit muss max. 28 s betragen.")
    c = subject_key("Die Schutzart soll mindestens IP54 erreichen.")
    print("-" * 68)
    print(f'{"ok  " if a == b else "FEHL"} subject_key gleich bei geaendertem Wert')
    print(f'{"ok  " if a != c else "FEHL"} subject_key verschieden bei anderer Sache')
    fails += (a != b) + (a == c)

    print("-" * 68)
    print("bestanden" if fails == 0 else f"{fails} Abweichung(en)")
    return 1 if fails else 0


# ==================================================================== 8 CLI
def main() -> int:
    ap = argparse.ArgumentParser(description="Anforderungen aus dem Chunk-Bestand")
    ap.add_argument("--chunks", default=str(OUT / "chunks.jsonl"))
    ap.add_argument("--out", default=str(OUT / "requirements.jsonl"))
    ap.add_argument("--config", default=str(HERE / "routing_config.json"))
    ap.add_argument("--project", default=None)
    ap.add_argument("--show", type=int, default=0, help="N Zeilen auf der Konsole")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        return self_test()

    chunks_path = Path(a.chunks)
    if not chunks_path.exists():
        print(f"Nicht gefunden: {chunks_path}\n"
              f"Zuerst extract.py und index.py laufen lassen, oder --chunks setzen.",
              file=sys.stderr)
        return 2

    router = None
    if Router is not None and Path(a.config).exists():
        router = Router.from_file(Path(a.config))

    reqs = build(chunks_path, a.project, router)

    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for r in reqs:
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

    # Zusammenfassung
    from collections import Counter
    bind = Counter(r.binding for r in reqs)
    cat = Counter(r.category or "—" for r in reqs)
    print(f"{len(reqs)} Anforderungen -> {out_path}")
    print("  Verbindlichkeit:", dict(bind))
    print("  Kategorie:      ", dict(cat))
    print(f"  messbar:         {sum(r.measurable for r in reqs)}")
    print(f"  mit Rückfrage:   {sum(r.needs_question for r in reqs)}")
    print(f"  Gegenstände:     {len({r.subject_key for r in reqs})} "
          f"(mehrfach belegt: "
          f"{len(reqs) - len({r.subject_key for r in reqs})})")

    if a.show:
        print("\n" + "-" * 100)
        for r in reqs[:a.show]:
            val = ""
            if r.value is not None:
                val = f'{r.operator or ""} {r.value:g} {r.unit or ""}'.strip()
            print(f'{r.binding:<7} {r.category[:12]:<13} {val:<16} '
                  f'{r.text[:52]:<54} {r.file[:22]}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
