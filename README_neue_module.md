# Neue Module — Fundament für Daniels Wünsche

**Stand 09.09.2026.** Fünf neue Dateien. **An `store.py`, `serve.py`, `ui.html`,
`search.py` oder sonst etwas Bestehendem wurde nichts geändert.** Alles läuft
für sich allein und nur mit der Standardbibliothek — nichts zu installieren.

Ablegen in `pipeline\` neben die vorhandenen Dateien.

| Datei | Was sie tut |
|---|---|
| `requirements.py` | Macht aus Sätzen **Anforderungsobjekte** mit Identität |
| `router.py` | Ordnet jede Anforderung einem **Fach oder einer Station** zu |
| `routing_config.json` | Die Wörterbücher — hier steht alles Fachliche |
| `routing_config_stationen_beispiel.json` | Dieselbe Datei, auf Stationen umgebaut — der Beweis, dass der Tausch funktioniert |
| `llm.py` + `llm_config.json` | Schnittstelle zum Sprachmodell. **Standardmäßig aus** |
| `beispiel_chunks.jsonl` | Erfundener Demobestand, damit alles ohne echte Kundendaten vorführbar ist |

---

## Sofort ausprobieren

```
cd C:\Users\Park\Downloads\Gluth-main\Gluth-main\pipeline
python requirements.py --self-test
python router.py --demo
python requirements.py --chunks beispiel_chunks.jsonl --out out\demo.jsonl --show 20
```

Auf dem echten Bestand:

```
python requirements.py --show 20
python requirements.py --project A07614000 --show 30
```

---

## Warum zuerst `requirements.py` und nicht die Akte

Heute geht die Kette **Dokument → Chunk → Satz → Anzeige**. Ein Satz hat keine
Identität: er ist ein Stück Text an einer Stelle. Damit lässt sich nicht sagen,
dass *dieselbe Anforderung* in der neuen Fassung einen anderen Wert hat — genau
das ist aber Daniels Kernwunsch.

`requirements.py` schiebt eine Ebene dazwischen: **Dokument → Klausel →
Anforderung**. Jede Anforderung bekommt zwei Kennungen:

- **`req_id`** — dieser Wortlaut, genau so
- **`subject_key`** — der **Gegenstand ohne die Zahlenwerte**

Das ist der Kniff, der die Entwicklungsakte ohne KI möglich macht:

```
"Die Taktzeit muss max. 32 s betragen."   req_id A   subject_key  X
"Die Taktzeit muss max. 28 s betragen."   req_id B   subject_key  X   ← gleiche Sache
"Die Schutzart soll mindestens IP54 …"    req_id C   subject_key  Y
```

Gleicher `subject_key`, andere `req_id` = **dieselbe Anforderung in zwei
Fassungen**. Daraus baut die Akte in der nächsten Runde die Versionskette,
ohne dass ein Modell irgendetwas „verstehen" muss.

### Die Felder je Anforderung

`binding` (MUSS · SOLL · KANN · VERBOT · INFO) · `negated` · `clause`
(Gliederungsnummer) · `operator` (LE · GE · LT · GT · EQ · RANGE) · `value` ·
`value_max` · `unit` · `tolerance` · `category` · `measurable` ·
`needs_question` — dazu die volle Herkunft: Projekt, Phase, Datei, Seite,
Chunk. **Jede Anforderung bleibt bis zur Seite belegbar.**

### Zwei Fallen, die im Code abgefangen sind

**1. Verneinung vor der KANN-Liste.** „Der Schaltschrank **darf nicht** außerhalb
der Halle stehen" enthält das Wort *darf*. Läuft der Satz über die KANN-Liste,
wird aus einem Verbot eine Freiheit — der teuerste Fehler, den diese Datei
machen könnte. Deshalb wird das Verbot zuerst geprüft. Steht als Kommentar im
Code, damit es beim Umbauen niemand versehentlich umdreht.

**2. Deutsche Zahlen.** `1.070` sind tausendsiebzig, `0,05` ist null Komma null
fünf. Amerikanisch gelesen liegen Werte um Faktor 1.000 daneben. Und `min` ist
zugleich eine **Einheit** — ohne Sonderbehandlung wird aus „200 l/min nicht
überschreiten" ein „mindestens 200". Beide Fälle stehen im Selbsttest.

---

## `router.py` — der austauschbare Teil

Die Logik kennt **kein einziges Fachwort**. Alles Fachliche steht in der
JSON-Datei. Reihenfolge der Entscheidung:

1. **Überschrift** — steht der Satz unter „Station 3", gilt Station 3. Schlägt jedes Stichwort.
2. **Stichworte** — Treffer werden gezählt und gewichtet.
3. **Gleichstand** — höhere Priorität gewinnt. *Sicherheit steht bewusst oben*: lieber einmal zu viel zugeordnet als eine übersehene Schutzanforderung.
4. **Kein Treffer** — `default`.

### Der Umbau auf Stationen

Wenn Herr Genau seine Liste nennt, wird in `routing_config.json` nur
`label`, `keywords` und `header_patterns` ersetzt. **An `router.py` ändert sich
keine Zeile.** Wie das aussieht, zeigt
`routing_config_stationen_beispiel.json` — vier Stationen plus Sicherheit.
Vergleich:

```
python requirements.py --chunks beispiel_chunks.jsonl --show 8
python requirements.py --chunks beispiel_chunks.jsonl --show 8 --config routing_config_stationen_beispiel.json
```

Dieselben Sätze, andere Zuordnung, gleicher Code. Genau das ist im Meeting
vorführbar.

### Konfiguration prüfen lassen

```
python router.py --check
```

Findet die Fehler, die man beim Umbauen macht: doppelte Schlüssel, ein Stichwort
in zwei Eimern (der Treffer wird dann zufällig), zu kurze Stichworte.

### Eine bekannte Eigenheit

Bei zusammengesetzten Wörtern entscheidet der **erste** Wortteil.
„Sicherheitsventil" landet bei *Sicherheit*, nicht bei *Pneumatik*. Das ist
meistens richtig und im Zweifel die sichere Seite. Soll ein bestimmtes Kompositum
woanders hin, wird es einfach als eigenes Stichwort in den gewünschten Eimer
geschrieben.

---

## `llm.py` — gebaut, aber aus

Vier Grundsätze stehen **im Code**, nicht nur in der Dokumentation:

1. Ohne `"enabled": true` geht **nichts** hinaus. Auslieferungszustand ist aus.
2. Ein Modellname, der `cloud` enthält, wird **abgelehnt** — immer, auch lokal.
3. `--dry-run` zeigt Zeichen für Zeichen, was gesendet **würde**, ohne zu senden.
4. Das Modell darf **umformulieren, nicht ergänzen**: keine Zahl ändern, keine Verneinung entfernen, keine Quellenangabe streichen, bei fehlender Angabe „nicht angegeben".

```
python llm.py --health
python llm.py --dry-run --demo
```

Der Wechsel von lokal auf Azure ist später eine Änderung an `llm_config.json`:
`backend` auf `azure`, `base_url`, `deployment`, `api_key`. Anweisung, Aufbau
und Auswertung bleiben identisch.

**Offener Punkt, den du kennen musst:** Ollama steht **nicht** auf Gluths
Freigabeliste — dort stehen M365 Copilot, Copilot Studio und Azure OpenAI. Der
`--dry-run` ist deshalb im Meeting das bessere Argument als ein laufendes
lokales Modell: er zeigt die fertige Anbindung, ohne dass etwas Nichtfreigegebenes
installiert sein muss.

---

## Was als Nächstes kommt (Runde 2)

Alles darunter setzt auf `requirements.jsonl` auf und ist jetzt schnell zu bauen:

1. **`akte.py`** — SQLite-Schema für die Entwicklungsakte: eine Zeile je Fassung, verkettet über `subject_key`, mit `version_number`, Quelle und Zeitpunkt. Neue Datei, `store.py` bleibt unangetastet.
2. **Konfliktprüfung** — neuer Satz aus einem Protokoll gegen den bestehenden Bestand. Zwei Stufen: gleicher `subject_key` findet den Treffer sofort und ohne jede Rechnerei; nur wenn das nichts liefert, wird die Ähnlichkeitssuche aus `search.py` befragt.
3. **Excel- und Word-Ausgabe** — Anforderungsliste mit Datei und Seite je Zeile, Rückfragenliste, Normenübersicht.
4. **Ansicht in der Oberfläche** — Karten statt flacher Liste, Klick öffnet die Historie. Braucht Änderungen an `ui.html` und `serve.py`; die bespreche ich vorher mit dir.
5. **PRO.FILE 7.8** — erst, wenn wir wissen, welche Schnittstelle die Version tatsächlich anbietet. Bis dahin ist die Excel-Ausgabe der Übergabeweg.

Reihenfolge-Empfehlung fürs Meeting: **1 und 3 zuerst.** Die Akte ist der
sichtbare Beweis für „lückenlose Dokumentation", die Excel-Liste ist das, was
der Vertrieb am nächsten Tag benutzt.

---

## Sicherheitshinweis zum Demobestand

`beispiel_chunks.jsonl` ist vollständig erfunden — Muster Maschinenbau GmbH,
Projekt A99001000. **Kein Satz stammt aus einer echten Kundenspezifikation.**
Für Vorführungen und Screenshots immer diesen Bestand verwenden, nie die echten
Projektordner.
