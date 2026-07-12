# Arbeitsweise: Research → Developer → Reviewer

Gilt für den Plan in [`leagues-and-htmx.md`](./leagues-and-htmx.md).

Jede Phase durchläuft dieselbe Kette. Die Agents reden **nicht** direkt
miteinander — sie übergeben über **Dateien im Repo**. Das ist der Punkt: Ein
Subagent startet immer mit leerem Kontext, kennt also weder das Gespräch noch die
Ergebnisse seines Vorgängers. Ein Handoff-Artefakt, das im Repo liegt, kann er
lesen. Ein "der Agent davor hat gesagt…" existiert für ihn nicht.

```
┌─ RESEARCH (Sonnet 5, read-only) ────────────────────────────┐
│  Sammelt Best Practices, prüft Versionen, liest den Code     │
│  → schreibt  docs/plans/research/<phase>.md                  │
└──────────────────────────────┬───────────────────────────────┘
                               │  GATE 1: ich prüfe den Brief
                               ▼
┌─ DEVELOPER (Opus 4.8) ───────────────────────────────────────┐
│  Implementiert nach Plan + Brief, schreibt Tests + Doku       │
│  → Code, Migrationen, Tests, docs/*.rst, Commits              │
└──────────────────────────────┬───────────────────────────────┘
                               │  GATE 2: Tests laufen grün?
                               ▼
┌─ REVIEWER (Opus 4.8) ────────────────────────────────────────┐
│  Sucht Bugs, prüft Doku gegen Code, meldet — fixt NICHT       │
│  → schreibt  docs/plans/review/<phase>.md                     │
└──────────────────────────────┬───────────────────────────────┘
                               │  GATE 3: Findings sichten
                               ▼
                    Fixes → zurück an den Developer
                    (per SendMessage, mit erhaltenem Kontext)
```

## Modellwahl

| Rolle | Modell | Warum |
| --- | --- | --- |
| Research | **Sonnet 5** | Breites Sammeln und Zusammenfassen — schnell und günstig, genau der Job. |
| Developer | **Opus 4.8** | Schreibt den Code. Hier zahlt sich das stärkere Modell aus. |
| Reviewer | **Opus 4.8** | Bugs finden ist die schwierigste der drei Aufgaben. Ein schwächeres Modell übersieht genau die Race Condition, um die es hier geht. |

## Regeln, ohne die die Kette bricht

1. **Der Reviewer fixt nicht.** Er meldet nur. Wer den eigenen Code reviewt und
   sofort umbaut, prüft danach nicht mehr, ob der Umbau stimmt. Fixes gehen per
   `SendMessage` zurück an den Developer-Agent — der hat seinen Kontext noch und
   weiß, warum er es so gebaut hat.
2. **Jeder Agent bekommt den Plan-Pfad, nicht den Plan-Inhalt.** Er liest die
   Datei selbst. Sonst kopiere ich bei jeder Phase 400 Zeilen in den Prompt.
3. **Ein Agent pro Schritt, synchron.** Kein Parallelisieren — der Developer
   braucht den Brief, der Reviewer braucht den Code.
4. **Testbefehl steht in jedem Prompt.** Lokal ohne Docker läuft:
   `uv run pytest --no-migrations` (SQLite). Der Race-Condition-Test aus Phase 2
   braucht Postgres und wird entsprechend markiert.

## Phasen-Zuschnitt

Phase 2 ist für einen einzelnen Developer-Lauf zu groß. Sie bekommt **einen**
Research-Brief, aber **drei** Developer/Reviewer-Runden:

| Lauf | Umfang | Deliverable |
| --- | --- | --- |
| **1** | HTMX-Polling + Bell-Partial | npm/Gulp, Endpoint, Partial, Tests |
| **2a** | Notifications erweitern | `LEAGUE`-Kategorie, `email_league`, `notify_many` + Tests |
| **2b** | Leagues-Kern | Models, Migrationen, Services, Admin + Tests |
| **2c** | Leagues-UI | Views, URLs, Templates, Celery-Tasks, `docs/leagues.rst` |

2a ist bewusst klein und isoliert: Es ändert eine bestehende App, die Teams schon
benutzen. Wenn hier etwas kaputtgeht, will ich es sehen, bevor 2b darauf aufbaut.

---

# Die Prompts

## Research (vor jeder Phase)

`Agent` · `subagent_type: general-purpose` · `model: sonnet` · `run_in_background: false`

```
Du bist Research-Agent für ein Django-6-Projekt (ProStratSix, R6-Esport-Liga).
Du schreibst KEINEN Produktivcode und änderst KEINEN bestehenden Code.
Dein einziges Deliverable ist ein Research-Brief als Datei.

Kontext, den du zuerst liest:
- docs/plans/leagues-and-htmx.md   (der Umsetzungsplan — Abschnitt "<PHASE>")
- Die im Plan genannten bestehenden Dateien, um die Konventionen zu verstehen:
  prostratsix/teams/services.py, prostratsix/notifications/services.py

Aufgabe: Recherchiere die Best Practices für <PHASE-BESCHREIBUNG>.

Konkret zu klären:
<HIER DIE PHASENSPEZIFISCHEN FRAGEN — siehe unten>

Beachte:
- Die Stack-Versionen stehen in pyproject.toml und package.json. Prüfe die
  Doku zu GENAU diesen Versionen. Django 6.0, Python 3.14.
  Antworte NICHT aus dem Gedächtnis, wenn es um API-Details geht — such es nach.
- Wo dein Fund dem Plan widerspricht, sag das explizit und begründe es.
  Der Plan ist nicht heilig, er ist eine Hypothese.
- Interessiert mich nicht: allgemeine Tutorials, "was ist HTMX"-Erklärungen.
  Interessiert mich: Fallstricke, Versionsdetails, Sicherheitsaspekte,
  konkrete Code-Muster, die in Django 6 tatsächlich funktionieren.

Schreibe den Brief nach docs/plans/research/<phase>.md mit dieser Struktur:
  1. Empfohlener Ansatz (max. 1 Absatz)
  2. Konkrete Code-Muster (kopierfähig, kommentiert)
  3. Fallstricke — was schiefgeht und woran man es merkt
  4. Abweichungen vom Plan (falls vorhanden) + Begründung
  5. Quellen (URLs)

Antworte mir am Ende NUR mit: Pfad des Briefs + die 3 wichtigsten Erkenntnisse
in je einem Satz. Keine Zusammenfassung des ganzen Briefs.
```

### Phasenspezifische Fragen — Lauf 1 (HTMX)

```
- htmx.org 2.x: Wie bindet man es in eine Gulp-Pipeline ein, die bereits
  Bootstrap aus node_modules bündelt? Eigenes Bundle oder mit rein?
- Polling-Muster "Element ersetzt sich selbst per hx-swap=outerHTML":
  Was sind die bekannten Stolperfallen (Trigger geht verloren, doppelte
  Requests, Memory-Leak bei langen Sessions)?
- hx-trigger mit Bedingung [document.visibilityState === 'visible'] —
  korrekte Syntax und Verhalten beim Tab-Wechsel?
- CSRF mit HTMX + Django 6: htmx:configRequest vs. hx-headers.
  Was ist der aktuell empfohlene Weg?
- Response-Header HX-Trigger für Client-Side-Events: Format, Mehrfach-Events.
- Bootstrap-5-Dropdown in einem Element, das per HTMX ausgetauscht wird:
  Gibt es Probleme mit verwaisten Event-Listenern / Popper-Instanzen?
  (Das ist mein größter Verdacht auf einen versteckten Bug.)
- Barrierefreiheit: aria-live für einen sich aktualisierenden Zähler?
```

### Phasenspezifische Fragen — Lauf 2 (Leagues)

```
- Django 6: select_for_update() zum Schutz gegen Überbuchung einer Kapazität
  ("letzter freier Slot"). Korrektes Muster, Zusammenspiel mit transaction.atomic,
  Verhalten bei nowait/skip_locked. Gibt es einen besseren Weg (z. B. eine
  CheckConstraint oder ein Counter-Feld mit F()-Expression)?
- Wie testet man diese Race Condition zuverlässig in pytest-django gegen Postgres?
  (Threads? TransactionTestCase? pytest-django-Fixtures?)
- Partielle UniqueConstraint mit condition=Q(status=...) — Fallstricke bei
  Statuswechseln in Django 6.
- bulk_create + Notifications: Was passiert mit auto_now_add und mit Signals?
  (Unsere notifications-App hat signals.py — prüfen, ob bulk_create sie umgeht
  und ob das ein Problem ist!)
- django-celery-beat: periodische Tasks für Status-Übergänge zeitgesteuert
  registrieren — Migrations-basiert oder per beat_schedule in den Settings?
  Was ist bei diesem Setup (config/celery_app.py) idiomatisch?
- Zeitzonen: USE_TZ ist an. Fallstricke bei Anmeldefenstern und Deadlines.
```

---

## Developer (nach jedem Research)

`Agent` · `subagent_type: developer` · `model: opus` · `run_in_background: false`

```
Du implementierst Lauf <N> für das Django-Projekt ProStratSix.

Lies zuerst, in dieser Reihenfolge:
1. docs/plans/leagues-and-htmx.md      — der Plan, Abschnitt "<ABSCHNITT>"
2. docs/plans/research/<phase>.md      — der Research-Brief. Wo der Brief vom
   Plan abweicht, GILT DER BRIEF — er ist die jüngere Information.
3. prostratsix/teams/ (services.py, views.py, models.py, admin.py, tests/)
   — das ist der Stil, in dem du schreibst. Dünne Views, Service-Layer besitzt
   die Regeln, Exception-Hierarchie, Guards zuerst.

Umfang dieses Laufs (NICHT mehr, NICHT weniger):
<KONKRETE DATEILISTE AUS DER PHASEN-TABELLE>

Anforderungen:
- Tests sind Teil der Lieferung, nicht optional. Muster:
  prostratsix/teams/tests/test_services.py
- Doku ist Teil der Lieferung: Docstrings an allen Service-Funktionen +
  docs/<app>.rst nach dem Vorbild von docs/teams.rst.
- Übersetzbare Strings mit gettext_lazy as _, wie überall sonst im Projekt.
- Ruff und djlint müssen durchlaufen (siehe pyproject.toml).
- Tests lokal ausführen mit:  uv run pytest --no-migrations
  (SQLite, kein Docker nötig. Tests, die Postgres brauchen — z. B. der
  select_for_update-Test — mit einem Skip-Marker versehen und im Docstring
  begründen.)
- Commits: ein Commit pro logischer Einheit, Präfixe wie im bisherigen
  Verlauf (feat:, docs:). Nicht pushen.

Wenn dir im Plan etwas falsch oder unmöglich erscheint: STOPP, implementiere
nicht dran vorbei, sondern melde es zurück. Ein "hab ich anders gelöst" ohne
Rückfrage ist teurer als eine Nachfrage.

Antworte am Ende mit: geänderte Dateien, Testergebnis (Zahlen), und allem,
wovon du abgewichen bist.
```

## Reviewer (nach jedem Developer-Lauf)

`Agent` · `subagent_type: general-purpose` · `model: opus` · `run_in_background: false`

```
Du reviewst die Änderungen von Lauf <N> im Django-Projekt ProStratSix.
Du ÄNDERST KEINEN CODE. Du meldest Findings. Fixen macht jemand anderes.

Grundlage:
- git diff <BASE-COMMIT>..HEAD     — das ist der Umfang deines Reviews
- docs/plans/leagues-and-htmx.md   — was gebaut werden sollte
- docs/plans/research/<phase>.md   — die Fallstricke, die bekannt waren

Prüfe in dieser Reihenfolge (die ersten beiden Punkte sind die wichtigen):

1. KORREKTHEIT / BUGS
   - Race Conditions und fehlende Transaktionsgrenzen
   - Guards in der falschen Reihenfolge (Permission-Check muss VOR
     Existenz-Checks laufen — sonst leakt man Informationen)
   - N+1-Queries, besonders in ListViews und in den Notification-Services
   - Fehlerpfade: Was passiert bei gleichzeitigen Requests, leeren Querysets,
     gelöschten Objekten, abgelaufenen Fristen?
   - Zeitzonen (USE_TZ ist an)
   - Fehlende oder falsche Migrationen

2. TESTS
   - Gibt es für JEDEN Guard im Service-Layer einen Test?
   - Testen die Tests das Verhalten oder nur, dass der Code nicht crasht?
   - Nenne konkret die fehlenden Fälle.
   - Führe die Suite aus: uv run pytest --no-migrations

3. DOKU
   - Stimmt docs/<app>.rst mit dem tatsächlichen Code überein? Prüfe jede
     Behauptung im Doc gegen den Code — veraltete Doku ist schlimmer als keine.
   - Haben alle Service-Funktionen einen Docstring, der sagt, WANN sie wirft?
   - Ist der README/die Doku angepasst, falls sich das Setup geändert hat
     (neue npm-Dependency, neuer Celery-Task)?

4. KONVENTIONEN
   - Weicht der Code vom Muster der teams-App ab? Wo, und ist das begründet?

Schreibe die Findings nach docs/plans/review/<phase>.md, nach Schwere sortiert,
je Finding: Datei:Zeile, was ist falsch, was passiert dadurch konkret
(Reproduktionsszenario), Vorschlag.

Antworte mir mit: Anzahl Findings je Schweregrad + die kritischen im Klartext.
Wenn nichts Kritisches da ist, sag das klar — erfinde keine Findings,
um beschäftigt auszusehen.
```

---

# Gates — was ich zwischen den Schritten prüfe

**Gate 1 (nach Research):** Widerspricht der Brief dem Plan? Dann entscheide ich,
bevor Code entsteht — nicht danach. Besonders bei Lauf 2: Falls die Recherche
einen besseren Weg als `select_for_update` findet, ändert das den Kern des
Services.

**Gate 2 (nach Developer):** Laufen die Tests? Ist der Umfang eingehalten? Ein
Developer-Agent, der "noch schnell" die Nachbardatei umbaut, macht den Review
wertlos.

**Gate 3 (nach Reviewer):** Findings sichten, Kritisches per `SendMessage` zurück
an den Developer-Agent (der hat seinen Kontext noch), Kleinkram sammle ich.
Erst wenn der Review sauber ist, geht der nächste Lauf los.

# Was NICHT über Agents läuft

- **`git push` und PR-Erstellung** — mache ich, nach deiner Freigabe.
- **Entscheidungen aus der Tabelle im Plan** (Kadergröße, Approval, Snapshot,
  Notification-Kategorie) — die sind getroffen, kein Agent verhandelt sie neu.
- **Der finale Merge nach `main`.**
