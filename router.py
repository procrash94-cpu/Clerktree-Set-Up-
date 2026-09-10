#!/usr/bin/env python3
"""
router.py — ordnet jede Anforderung einem Fach oder einer Station zu.

Die Logik kennt keine Fachbegriffe. Alles Fachliche steht in
routing_config.json. Sobald Herr Genau seine echte Stationsliste liefert,
wird nur diese Datei getauscht — an router.py selbst aendert sich nichts.

    python router.py --text "Der Schaltschrank benötigt 400 V Drehstrom."
    python router.py --demo
    python router.py --config stationen.json --demo

Reihenfolge der Entscheidung
----------------------------
1. Ueberschrift   Steht der Satz unter "Station 3", gilt Station 3.
                  Eine Ueberschrift schlaegt jedes Stichwort im Satz.
2. Stichworte     Treffer werden gezaehlt und gewichtet.
3. Gleichstand    Der Eimer mit der hoeheren Prioritaet gewinnt;
                  Sicherheit steht bewusst oben — lieber einmal zu viel
                  als eine uebersehene Schutzanforderung.
4. Kein Treffer   default aus der Konfiguration ("Unzugeordnet").

Ein Satz kann mehrere Faecher berühren. route() liefert das fuehrende
Fach, route_all() alle mit Punktzahl — die Weboberflaeche kann damit
Mehrfachzuordnungen anzeigen, ohne dass die Datenhaltung sich aendert.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "routing_config.json"


class Router:
    def __init__(self, config: dict):
        self.config = config
        self.default = config.get("default", "Unzugeordnet")
        self.buckets = config.get("buckets", [])
        self._compile()

    # ------------------------------------------------------------- laden
    @classmethod
    def from_file(cls, path: str | Path = DEFAULT_CONFIG) -> "Router":
        p = Path(path)
        return cls(json.loads(p.read_text(encoding="utf-8")))

    def _compile(self) -> None:
        """Stichworte einmalig in regulaere Ausdruecke uebersetzen."""
        self._kw: list[tuple[dict, re.Pattern]] = []
        self._hd: list[tuple[dict, re.Pattern]] = []
        for b in self.buckets:
            words = [re.escape(w) for w in b.get("keywords", []) if w.strip()]
            if words:
                # Wortanfang genuegt: "Sensor" trifft auch "Sensorik", "Sensoren".
                # Am Wortende bewusst offen — deutsche Komposita.
                self._kw.append((b, re.compile(r"(?<![a-zäöüß])(" + "|".join(words) + r")", re.I)))
            pats = [p for p in b.get("header_patterns", []) if p.strip()]
            if pats:
                self._hd.append((b, re.compile("|".join(pats), re.I)))

    # ------------------------------------------------------------ zuordnen
    def route_all(self, text: str, header: str = "") -> list[dict]:
        """Alle Treffer, absteigend nach Punktzahl."""
        text = text or ""
        results = []

        # 1 Ueberschrift schlaegt alles
        if header:
            for b, rx in self._hd:
                m = rx.search(header)
                if m:
                    return [{"key": b["key"], "label": b["label"],
                             "score": 1000, "hits": [m.group(0)],
                             "reason": "Ueberschrift"}]

        # 2 Stichworte zaehlen
        for b, rx in self._kw:
            hits = [m.group(0) for m in rx.finditer(text)]
            if not hits:
                continue
            weight = float(b.get("weight", 1.0))
            score = round(len(set(h.lower() for h in hits)) * weight, 2)
            results.append({"key": b["key"], "label": b["label"], "score": score,
                            "hits": sorted(set(hits), key=str.lower),
                            "reason": "Stichwort"})

        # 3 Sortierung: Punktzahl, dann Prioritaet aus der Konfiguration
        prio = {b["key"]: b.get("priority", 50) for b in self.buckets}
        results.sort(key=lambda r: (-r["score"], -prio.get(r["key"], 50), r["label"]))
        return results

    def route(self, text: str, header: str = "") -> tuple[str, list[str]]:
        """Fuehrendes Fach und die Woerter, die dazu gefuehrt haben."""
        r = self.route_all(text, header)
        if not r:
            return self.default, []
        return r[0]["label"], r[0]["hits"]

    def explain(self, text: str, header: str = "") -> str:
        """Lesbare Begruendung — fuer die Weboberflaeche und fuer Rueckfragen."""
        r = self.route_all(text, header)
        if not r:
            return f'{self.default} — kein Stichwort getroffen'
        head = r[0]
        rest = ", ".join(f'{x["label"]} ({x["score"]:g})' for x in r[1:3])
        line = f'{head["label"]} — {head["reason"]}: {", ".join(head["hits"][:6])}'
        return line + (f' · auch berührt: {rest}' if rest else "")

    # ------------------------------------------------------------- pruefen
    def check_config(self) -> list[str]:
        """Findet die Fehler, die man beim Umbau auf Stationen leicht macht."""
        problems, seen_keys, seen_words = [], set(), {}
        if not self.buckets:
            problems.append("Keine Eimer definiert.")
        for b in self.buckets:
            for req in ("key", "label"):
                if not b.get(req):
                    problems.append(f'Eimer ohne "{req}": {b}')
            k = b.get("key")
            if k in seen_keys:
                problems.append(f'Schlüssel doppelt vergeben: {k}')
            seen_keys.add(k)
            if not b.get("keywords") and not b.get("header_patterns"):
                problems.append(f'{k}: weder Stichworte noch Überschriften')
            for w in b.get("keywords", []):
                lw = w.lower().strip()
                if len(lw) < 3:
                    problems.append(f'{k}: Stichwort "{w}" ist zu kurz — '
                                    f'trifft zu viel')
                if lw in seen_words and seen_words[lw] != k:
                    problems.append(f'Stichwort "{w}" steht in {seen_words[lw]} '
                                    f'und in {k} — der Treffer wird zufällig')
                seen_words[lw] = k
        return problems


# =================================================================== Demo
DEMO = [
    ("Der Schaltschrank ist in Schutzart IP54 auszuführen.", ""),
    ("Die Greifer müssen das Bauteil formschlüssig aufnehmen.", ""),
    ("Der Betriebsdruck der Zylinder beträgt 6 bar.", ""),
    ("Die Ansteuerung erfolgt über eine SPS vom Typ S7-1500.", ""),
    ("Der Not-Halt muss nach Kategorie 3 ausgeführt sein.", ""),
    ("Die Taktzeit darf 32 s nicht überschreiten.", ""),
    ("Das Sicherheitsventil wird über den Schaltschrank angesteuert.", ""),
    ("Die Vorrichtung ist zu lackieren.", "Station 3 – Greifer"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Fach- und Stationszuordnung")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--text", default=None)
    ap.add_argument("--header", default="")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--check", action="store_true", help="Konfiguration prüfen")
    a = ap.parse_args()

    r = Router.from_file(a.config)

    if a.check:
        probs = r.check_config()
        print(f"Konfiguration: {len(r.buckets)} Eimer, Vorgabe „{r.default}“")
        for p in probs:
            print("  !", p)
        print("  keine Auffälligkeiten" if not probs else f"  {len(probs)} Hinweis(e)")
        return 1 if probs else 0

    if a.text:
        print(r.explain(a.text, a.header))
        return 0

    if a.demo:
        print(f'Konfiguration: {a.config}  ({len(r.buckets)} Eimer)\n' + "-" * 92)
        for text, header in DEMO:
            label, hits = r.route(text, header)
            src = f'[{header}] ' if header else ""
            print(f'{label:<22} {src}{text[:52]:<54} {", ".join(hits[:3])}')
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
