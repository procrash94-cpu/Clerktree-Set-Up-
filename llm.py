#!/usr/bin/env python3
"""
llm.py — Schnittstelle zu einem Sprachmodell. Austauschbar, standardmaessig AUS.

Zweck: Der gesamte Weg zum Modell wird jetzt gebaut, damit spaeter nur eine
Adresse in llm_config.json getauscht werden muss — lokal oder Azure. Die
Weboberflaeche, die Datenhaltung und der Wortlaut der Anweisung bleiben gleich.

Grundsaetze, die im Code stehen und nicht nur in der Dokumentation:

  1. Ohne "enabled": true geht nichts hinaus. Der Auslieferungszustand ist aus.
  2. Ein Modellname, der "cloud" enthaelt, wird abgelehnt. Immer. Auch lokal.
  3. --dry-run zeigt Byte fuer Byte, was gesendet wuerde, ohne zu senden.
     Das ist der Nachweis fuer die IT-Sicherheit.
  4. Das Modell bekommt ausschliesslich die uebergebenen Saetze. Es darf
     umformulieren, nicht ergaenzen. Der belegbare Teil bleibt der Urtext.

    python llm.py --health
    python llm.py --dry-run --demo
    python llm.py --demo                 # nur wenn enabled true ist
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "llm_config.json"

DEFAULT_CONFIG = {
    "enabled": False,
    "backend": "ollama",
    "base_url": "http://127.0.0.1:11434",
    "model": "mistral-nemo",
    "api_key": "",
    "deployment": "",
    "api_version": "2024-10-21",
    "timeout_s": 120,
    "temperature": 0.1,
    "max_tokens": 800,
}

# Harte Sperre. Ein Modellname mit "cloud" darf nie angesprochen werden,
# auch wenn er wie ein lokales Modell aussieht.
FORBIDDEN_IN_MODEL = ("cloud",)


class LLMUnavailable(RuntimeError):
    pass


class LLM:
    def __init__(self, config: dict):
        self.cfg = {**DEFAULT_CONFIG, **(config or {})}
        self._guard_model()

    @classmethod
    def load(cls, path: str | Path = CONFIG_PATH) -> "LLM":
        p = Path(path)
        cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        return cls(cfg)

    # -------------------------------------------------------------- Sperren
    def _guard_model(self) -> None:
        name = str(self.cfg.get("model", "")).lower()
        for bad in FORBIDDEN_IN_MODEL:
            if bad in name:
                raise LLMUnavailable(
                    f'Modellname "{self.cfg.get("model")}" enthält "{bad}". '
                    f"Abgelehnt: ein solches Modell kann Daten an einen Dienst "
                    f"weitergeben. Bitte ein eindeutig lokales Modell eintragen."
                )

    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled"))

    # -------------------------------------------------------------- Zustand
    def health(self) -> dict:
        state = {
            "enabled": self.enabled(),
            "backend": self.cfg["backend"],
            "base_url": self.cfg["base_url"],
            "model": self.cfg["model"],
            "reachable": False,
            "note": "",
        }
        if not self.enabled():
            state["note"] = ("ausgeschaltet — es wird nichts gesendet. "
                             "Zum Einschalten enabled auf true setzen.")
            return state
        try:
            url = (self.cfg["base_url"].rstrip("/") + "/api/tags"
                   if self.cfg["backend"] == "ollama"
                   else self.cfg["base_url"])
            with urllib.request.urlopen(url, timeout=5) as r:
                state["reachable"] = 200 <= r.status < 400
        except Exception as e:
            state["note"] = f"nicht erreichbar: {e.__class__.__name__}"
        return state

    # ------------------------------------------------------------ Anweisung
    SYSTEM = (
        "Du bist ein technischer Redakteur in einem Maschinenbauunternehmen. "
        "Du formulierst ausschliesslich die dir vorgelegten Saetze um. "
        "Regeln, die du nicht brechen darfst: "
        "1. Du fuegst keine Angabe hinzu, die nicht im Text steht. "
        "2. Du aenderst keine Zahl, keine Einheit und keinen Normnamen. "
        "3. Du entfernst keine Verneinung — aus 'darf nicht' wird nie 'darf'. "
        "4. Du behaeltst jede Quellenangabe in eckigen Klammern bei. "
        "5. Wenn eine Angabe fehlt, schreibst du 'nicht angegeben' und "
        "erfindest nichts."
    )

    def build_prompt(self, requirements: list[dict], task: str = "summary") -> str:
        lines = []
        for r in requirements:
            src = f'[{r.get("file","?")}, S. {r.get("page","?")}]'
            lines.append(f'- {r.get("text","").strip()} {src}')
        body = "\n".join(lines)

        if task == "summary":
            instruction = (
                "Fasse die folgenden woertlich uebernommenen Anforderungen zu "
                "einem zusammenhaengenden Absatz in gutem Deutsch zusammen. "
                "Behalte jede Quellenangabe bei."
            )
        elif task == "questions":
            instruction = (
                "Formuliere aus den folgenden Anforderungen die Rueckfragen an "
                "den Kunden, die sich aus offenen oder widerspruechlichen "
                "Angaben ergeben. Keine Rueckfrage erfinden."
            )
        else:
            instruction = (
                "Gib die folgenden Anforderungen unveraendert in klarer "
                "Listenform wieder."
            )
        return f"{instruction}\n\n{body}"

    def preview(self, requirements: list[dict], task: str = "summary") -> dict:
        """Was wuerde gesendet? Fuer --dry-run und fuer die IT-Sicherheit."""
        prompt = self.build_prompt(requirements, task)
        return {
            "would_send_to": self.cfg["base_url"],
            "model": self.cfg["model"],
            "system": self.SYSTEM,
            "prompt": prompt,
            "characters": len(self.SYSTEM) + len(prompt),
            "items": len(requirements),
        }

    # ------------------------------------------------------------- Aufrufen
    def generate(self, requirements: list[dict], task: str = "summary") -> str:
        if not self.enabled():
            raise LLMUnavailable(
                "Das Sprachmodell ist ausgeschaltet. Der woertliche Teil der "
                "Pipeline arbeitet unabhaengig davon weiter."
            )
        self._guard_model()
        prompt = self.build_prompt(requirements, task)
        backend = self.cfg["backend"]
        if backend == "ollama":
            return self._call_ollama(prompt)
        if backend in ("azure", "openai"):
            return self._call_openai_style(prompt)
        raise LLMUnavailable(f'Unbekannter Backend-Typ: {backend}')

    def _post(self, url: str, payload: dict, headers: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.cfg["timeout_s"]) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise LLMUnavailable(f"HTTP {e.code}: {e.read()[:300]!r}") from e
        except Exception as e:
            raise LLMUnavailable(f"{e.__class__.__name__}: {e}") from e

    def _call_ollama(self, prompt: str) -> str:
        url = self.cfg["base_url"].rstrip("/") + "/api/chat"
        out = self._post(url, {
            "model": self.cfg["model"],
            "stream": False,
            "options": {"temperature": self.cfg["temperature"]},
            "messages": [{"role": "system", "content": self.SYSTEM},
                         {"role": "user", "content": prompt}],
        }, {"Content-Type": "application/json"})
        return (out.get("message") or {}).get("content", "").strip()

    def _call_openai_style(self, prompt: str) -> str:
        """Azure OpenAI und jede kompatible Schnittstelle.

        Der einzige Unterschied zum lokalen Betrieb: Adresse, Kopfzeile und
        Bereitstellungsname. Anweisung, Aufbau und Auswertung bleiben gleich —
        deshalb ist der Wechsel spaeter eine Konfigurationsaenderung.
        """
        base = self.cfg["base_url"].rstrip("/")
        if self.cfg["backend"] == "azure":
            url = (f'{base}/openai/deployments/{self.cfg["deployment"]}'
                   f'/chat/completions?api-version={self.cfg["api_version"]}')
            headers = {"Content-Type": "application/json",
                       "api-key": self.cfg["api_key"]}
        else:
            url = f"{base}/v1/chat/completions"
            headers = {"Content-Type": "application/json",
                       "Authorization": f'Bearer {self.cfg["api_key"]}'}
        out = self._post(url, {
            "model": self.cfg["model"],
            "temperature": self.cfg["temperature"],
            "max_tokens": self.cfg["max_tokens"],
            "messages": [{"role": "system", "content": self.SYSTEM},
                         {"role": "user", "content": prompt}],
        }, headers)
        return out["choices"][0]["message"]["content"].strip()


# ==================================================================== CLI
DEMO_REQS = [
    {"text": "Die Taktzeit muss max. 32 s betragen.",
     "file": "Beispiel_Lastenheft.pdf", "page": 14},
    {"text": "Der Schaltschrank ist in Schutzart IP54 auszuführen.",
     "file": "Beispiel_Lastenheft.pdf", "page": 21},
    {"text": "Der Not-Halt muss nach Kategorie 3 ausgeführt sein.",
     "file": "Beispiel_Lastenheft.pdf", "page": 7},
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Schnittstelle zum Sprachmodell")
    ap.add_argument("--config", default=str(CONFIG_PATH))
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="zeigt, was gesendet würde — sendet nichts")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--task", default="summary",
                    choices=["summary", "questions", "list"])
    a = ap.parse_args()

    try:
        llm = LLM.load(a.config)
    except LLMUnavailable as e:
        print("Abgelehnt:", e)
        return 2

    if a.health:
        for k, v in llm.health().items():
            print(f"{k:<10} {v}")
        return 0

    if a.dry_run:
        p = llm.preview(DEMO_REQS if a.demo else [], a.task)
        print("Es würde gesendet an:", p["would_send_to"])
        print("Modell:              ", p["model"])
        print("Zeichen insgesamt:   ", p["characters"])
        print("-" * 72)
        print(p["system"])
        print("-" * 72)
        print(p["prompt"])
        print("-" * 72)
        print("Nichts gesendet.")
        return 0

    if a.demo:
        try:
            print(llm.generate(DEMO_REQS, a.task))
        except LLMUnavailable as e:
            print("Nicht ausgeführt:", e)
            return 1
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
