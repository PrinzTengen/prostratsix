# Review Lauf 2b — `prostratsix/leagues`

**Umfang:** `git diff b857236..HEAD` (5 Commits: `432393a`, `5474f1a`, `2c33d99`, `25c1477`, `ddc7618`)
**Branch:** `feat/league-settings`
**Reviewer:** Review-Agent, kein Code geändert (Arbeitsbaum nach allen Mutationsläufen wieder `clean`)

---

## Testzahlen

| Lauf | Ergebnis |
| --- | --- |
| `prostratsix/leagues` allein | **32 passed, 1 skipped** (der Race-Test) |
| Gesamtsuite | **126 passed, 3 failed, 1 skipped** — die 3 Fehlschläge sind die bekannten, vorbestehenden in `users/tests/test_views.py`, der Diff fasst `users` nicht an |
| `ruff check prostratsix/leagues` | All checks passed |
| `manage.py check` | System check identified no issues |
| `makemigrations --check --dry-run` | No changes detected |

### Verifikation der Mutationstests des Developers

Fünf Guards entfernt/vertauscht, Suite jeweils neu gelaufen. **Die Behauptung „genau ein Test wird rot" hält in allen fünf Fällen:**

| Mutation | roter Test |
| --- | --- |
| Permission-Guard hinter den Full-Check verschoben | `test_permission_is_checked_before_capacity` |
| `is_full`-Re-Check nach dem Lock entfernt | `test_a_full_league_raises_league_full_error` |
| Status-Re-Check in `start_league()` entfernt | `test_starting_twice_notifies_only_once` |
| `AlreadyRegistered`-Guard entfernt | `test_registering_twice_is_rejected` |
| `min_roster_size`-Guard entfernt | `test_a_roster_below_the_minimum_is_rejected` |

Jeweils exakt ein Fehlschlag, kein Kollateralschaden. Gute Arbeit.

### Zweite Mutationsrunde — überlebende Mutanten (Lücken, siehe T1–T4)

| Mutation | Ergebnis |
| --- | --- |
| `withdraw_team`: `TERMINAL_STATUSES`-Check entfernt | **kein Test wird rot** |
| `_transition`: `TERMINAL_STATUSES`-Check entfernt | **kein Test wird rot** |
| `_confirmed_players`: Dedupe entfernt | **kein Test wird rot** |
| `_transition`: Idempotenz-Early-Return entfernt | **kein Test wird rot** |
| `is_registration_window_open`: `closes_at` ignoriert | rot (`test_registration_after_the_window_closed_is_rejected`) ✓ |

---

## Messung: Query-Anzahl (die N+1-Frage aus 2a)

Gemessen mit `CaptureQueriesContext` + `django_capture_on_commit_callbacks(execute=True)`:

| Aufruf | 5 Spieler | 20 Spieler |
| --- | --- | --- |
| `register_team()` — Service-Kern | **9 Queries** | **9 Queries** |
| davon `notify_many()` | **2** (1× `bulk_create` Notification, 1× `SELECT` Preferences) | **2** |
| `start_league()` — Service-Kern | **7 Queries** | **7 Queries** |

**Kein N+1. Konstant, unabhängig von der Kadergröße.** Die Queryset-Konstruktion ist korrekt:
`TeamMembership.objects.filter(team=team).select_related("user")` (services.py:149-151) liefert
vollständig geladene `User`-Objekte, kein `.only()`/`.defer()`; `_roster_players()` und
`_confirmed_players()` ebenso (`select_related("user")`, services.py:369 / 384). Der 2a-Aufwand
für `notify_many()` ist **nicht** zunichte.

> Der linear wachsende Anteil in der Rohmessung (18 → 33 Queries) stammt vollständig aus
> `CELERY_TASK_ALWAYS_EAGER = True` in der Test-Fixture: der eager ausgeführte
> `send_notification_email`-Task lädt pro Empfänger seine `Notification` per PK nach. In
> Produktion ist das ein `delay_on_commit()`-Enqueue ohne Query. Kein Finding.

**Closure-Falle geprüft, nicht vorhanden:** die `on_commit`-Lambdas (services.py:199, 260, 331)
fangen `players` / `title` / `message` / `league` ein; keine dieser Variablen wird nach dem
Erzeugen des Lambdas noch neu gebunden, und es gibt keine Schleife um ein `on_commit` herum.
`league` wird zwar in Zeile 134 auf die gesperrte Instanz umgebunden — aber *vor* dem Lambda.
Sauber.

---

## Findings

| Schwere | Anzahl |
| --- | --- |
| Kritisch | **0** |
| Hoch | **2** |
| Mittel | **3** |
| Niedrig | **4** |
| Test-Lücken (gesammelt) | **1 Block, 8 Punkte** |

**Es gibt keinen kritischen Fund.** Kein Datenverlust-, kein Überbuchungs-, kein
Rechte-Bug im Produktionspfad. Die beiden Hochs betreffen (H1) die Belastbarkeit der
einzigen Absicherung gegen Überbuchung und (H2) eine Projekt-Einstellung, die die im
Code dokumentierte Lock-Semantik aushebelt.

---

### H1 — Der Race-Test würde einen fehlenden Lock **nicht zuverlässig** fangen

**Datei:** `prostratsix/leagues/tests/test_services.py:450-502`, insbesondere `471-483`

**Was falsch ist.**
Die `threading.Barrier(2)` steht in `register()` **vor** dem ersten DB-Zugriff des Threads
(test_services.py:476). Der erste DB-Zugriff ist `team.has_management_rights(actor)` in
`register_team()` (services.py:121) — und weil Django-Connections thread-lokal sind (was der
Test mit `connections.close_all()` in Zeile 483 selbst anerkennt), **öffnet dieser erste Query
eine brandneue Postgres-Connection**: TCP-Connect + Auth + ggf. TLS. Auf einem CI-Runner sind
das je Thread einige Millisekunden, und vor allem: die beiden Handshakes brauchen
*unterschiedlich lange*.

Die Barrier synchronisiert die Threads also an einem Punkt, der noch **eine Connection-Öffnung
und fünf Round-Trips** vom kritischen Abschnitt entfernt liegt. Der kritische Abschnitt selbst
(`SELECT … FOR UPDATE` → `COUNT` → 2× `INSERT` → `COMMIT`) dauert gegen ein lokales Postgres
grob 1 ms. Die Streuung zwischen zwei parallelen Connection-Handshakes liegt in derselben
Größenordnung oder darüber.

**Reproduktionsszenario (der False-Green).**
`.select_for_update()` in services.py:134 löschen, gegen Postgres laufen lassen.
Damit der Test trotzdem grün wird, muss Thread B beim `COUNT` (services.py:166) bereits
`slots_taken == 1` sehen — dafür reicht es, dass B **zwei Statements hinter A** liegt, denn
Bs `COUNT` ist sein 4. Statement in der Transaktion, As `COMMIT` das 6. Ein um wenige
Millisekunden langsamerer Connection-Handshake bei B genügt dafür vollständig. B bekommt
`LeagueFullError`, A `"registered"`, `outcomes == ["LeagueFullError", "registered"]` →
**Test grün, obwohl der Lock fehlt und die Liga in Produktion überbucht würde.**

Ohne Lock ist der Test also ein *probabilistischer* Detektor — und die Wahrscheinlichkeit,
dass er den Bug verpasst, ist nicht vernachlässigbar klein, sondern hängt an einer
Connection-Handshake-Streuung, die den ganzen kritischen Abschnitt überdeckt. Ein Test, der
den Bug, gegen den er allein absichert, nur manchmal sieht, ist als Regressionsschutz
nicht brauchbar.

Was der Test *richtig* macht (geprüft, kein Finding): `transactional_db` ist gesetzt
(sonst hinge Thread B ewig); jeder Thread bekommt tatsächlich seine eigene Connection
(thread-lokal + `connections.close_all()` im `finally`); Exceptions werden **nicht**
verschluckt (siehe aber M2); `sorted()` liefert `["LeagueFullError", "registered"]` korrekt
(ASCII `L` < `r`).

**Vorschlag.**
1. Connection **vor** der Barrier aufwärmen, damit die Barrier wirklich am kritischen
   Abschnitt synchronisiert:
   ```python
   def register(team, key):
       try:
           connections["default"].ensure_connection()   # Handshake VOR die Barrier
           barrier.wait()
           services.register_team(league, team.owner, team)
   ```
2. Den Ausgang **deterministisch** machen, statt auf Scheduling zu hoffen. Ein Sync-Punkt
   *innerhalb* des Locks geht nicht (mit Lock würde er deadlocken) — stattdessen den Gewinner
   künstlich langsam machen, an einer Stelle **nach** dem `SELECT … FOR UPDATE` und **vor**
   dem `COUNT`. `get_team_registration()` (services.py:142) ist genau so eine Stelle:
   ```python
   real = services.get_team_registration

   def slow_for_a(league_, team_):
       if team_ == team_a:
           time.sleep(0.5)          # A hält die Transaktion offen
       return real(league_, team_)

   monkeypatch.setattr(services, "get_team_registration", slow_for_a)
   ```
   **Mit** Lock: B blockiert 0,5 s am `SELECT … FOR UPDATE`, sieht danach `count == 1` →
   `LeagueFullError`. **Ohne** Lock: B rauscht durch, zählt 0, legt an → zwei bestätigte
   Registrierungen → Test *deterministisch* rot. Erst damit ist der Test das, was er sein soll.
3. Zusätzlich: `assert not thread.is_alive()` nach dem `join(timeout=30)` (siehe M2).

---

### H2 — `ATOMIC_REQUESTS = True` hebelt die im Code dokumentierte Lock-Lebensdauer aus

**Dateien:** `config/settings/base.py:50` (Bestandscode) ⇄ `prostratsix/leagues/services.py:125-134`
(Kommentar 130-131), `230-233`, `308-309`; `prostratsix/leagues/admin.py:88-113`

**Was falsch ist.**
Das Projekt setzt global `DATABASES["default"]["ATOMIC_REQUESTS"] = True`. Damit läuft **jeder
HTTP-Request** — die Admin-Action von 2b und **jede View aus 2c** — bereits in einer offenen
Transaktion. Das `with transaction.atomic():` in den Services ist dort folglich **kein
Transaktionsblock, sondern ein Savepoint**. Konsequenzen:

- **Der Kommentar in services.py:130-131 ist schlicht falsch**, und zwar genau in dem Kontext,
  auf den es ankommt:
  > „The lock is held until this block commits, so nothing slow (e-mail, HTTP, Celery enqueue)
  > may run inside it"

  Postgres gibt Row-Locks **nicht** beim Release eines Savepoints frei, sondern erst beim
  Commit der *äußersten* Transaktion. Unter `ATOMIC_REQUESTS` wird die `League`-Zeile also vom
  `SELECT … FOR UPDATE` **bis zum Ende des gesamten Requests** gehalten — inklusive
  Template-Rendering, Middleware und allem, was eine 2c-View sonst noch tut.
- **`LeagueAdmin._run()` (admin.py:96-98) hält N Locks gleichzeitig.** Die Schleife ruft pro
  ausgewählter Liga einen Service auf; jeder nimmt einen Row-Lock, und keiner gibt ihn wieder
  her, bis der Admin-Request committet. Eine Action auf 20 Ligen sperrt 20 `League`-Zeilen für
  die Dauer des ganzen Requests. Jede parallele Anmeldung auf eine dieser Ligen blockiert
  solange.
- **Gate-1 #2 hält trotzdem — aber aus einem anderen Grund als dem dokumentierten.**
  `transaction.on_commit()` feuert unter `ATOMIC_REQUESTS` am Ende des *Requests*. Da der Lock
  ebenfalls erst mit diesem Commit fällt, läuft `notify_many()` weiterhin *nach* dem Commit und
  *außerhalb* des Locks. Die Anforderung ist also erfüllt; die Begründung im Code („der Block
  committet") stimmt nur im Celery-Pfad (2c, kein Request → `atomic()` *ist* äußerste
  Transaktion) und nicht im Admin-/View-Pfad.
- **Der Race-Test bildet das nicht ab:** er ruft den Service direkt auf, dort ist `atomic()`
  tatsächlich äußerste Transaktion. Die getestete Lock-Lebensdauer ist nicht die produktive.

Weder `docs/plans/leagues-and-htmx.md` noch `docs/plans/research/phase-2-leagues.md` erwähnen
`ATOMIC_REQUESTS` — der Research-Brief hat es übersehen.

**Reproduktionsszenario.**
Admin → Ligen → 2 Ligen selektieren → Action „Start the selected leagues" → währenddessen in
einer zweiten Session `SELECT * FROM pg_locks WHERE mode = 'ExclusiveLock'`: beide `League`-Zeilen
sind gesperrt, bis der Admin-Request durch ist. Analog wird in 2c eine `register_team`-View die
`League`-Zeile über das komplette Template-Rendering hinweg gesperrt halten.

**Vorschlag.**
1. Kommentare in services.py:126-133 (und die analogen bei `withdraw_team`/`start_league`/`_transition`)
   korrigieren: Lock-Lebensdauer = äußerste Transaktion = Request-Ende, **nicht** Blockende.
2. Für 2c bewusst entscheiden (Gate-Frage an den Menschen): entweder die Register-/Withdraw-Views
   per `@transaction.non_atomic_requests` aus `ATOMIC_REQUESTS` herausnehmen, damit der Service
   wieder die äußerste Transaktion ist und der Lock nur so lange hält, wie er soll — oder
   `ATOMIC_REQUESTS` bewusst akzeptieren und die Konsequenz (Lock über den ganzen Request)
   dokumentieren.
3. In `_run()` (admin.py:88) mindestens dokumentieren, dass eine Batch-Action alle betroffenen
   Zeilen gleichzeitig sperrt.

---

### M1 — Der Admin kann `LeagueRegistration` direkt anlegen/ändern und umgeht damit jeden Service

**Datei:** `prostratsix/leagues/admin.py:14-18` (`LeagueRegistrationInline`), `116-123` (`LeagueRegistrationAdmin`)

**Was falsch ist.**
`_run()` (admin.py:88-94) begründet sorgfältig, warum die *Status-Übergänge der Liga* nicht per
`queryset.update()` laufen dürfen — „an update() would flip the column and tell nobody". Genau
das ist über das Registrierungs-Formular aber weiterhin möglich, denn weder das Inline noch
`LeagueRegistrationAdmin` machen `status` readonly oder verbieten das Anlegen:

- **CONFIRMED → WITHDRAWN im Inline:** gibt den Slot frei, verschickt **keine** Notification.
  Genau der Fehlermodus, gegen den `_run()` sich wehrt — durch die Hintertür wieder da.
- **Registrierung im Inline anlegen:** legt **keinen Roster-Snapshot** an (nur `register_team()`
  schreibt `LeagueRosterEntry`, services.py:176-185), prüft weder `max_teams` noch
  `min_roster_size`. `slots_taken` zählt die Zeile mit — aber `_confirmed_players()`
  (services.py:379-386) liest ausschließlich Roster-Einträge, also bekommt beim späteren
  `start_league()` **kein einziger Spieler dieses Teams eine Benachrichtigung**, lautlos.

**Reproduktionsszenario.**
Admin → League → Inline „Registrations" → „Add another" → Team wählen, Status `confirmed` →
Speichern. `league.slots_taken` == 1, `registration.roster.count()` == 0. Danach Action
„Start the selected leagues" → `Notification.objects.filter(category=LEAGUE).count()` == 0.

**Vorschlag.**
`LeagueRegistrationInline`: `readonly_fields = ["team", "status", "registered_by", "created_at"]`
und `has_add_permission() -> False` (Anlegen gehört ausschließlich in `register_team()`).
`LeagueRegistrationAdmin`: `status` readonly, oder — falls Disqualifikation per Admin gewollt ist
— dafür eine Admin-Action bauen, die einen (noch zu schreibenden) `disqualify_team()`-Service
aufruft, der den Slot freigibt *und* benachrichtigt.

---

### M2 — Race-Test: Nicht-`LeagueError`-Exceptions aus den Threads sind unsichtbar, hängende Threads blockieren pytest

**Datei:** `prostratsix/leagues/tests/test_services.py:471-492`

**Was falsch ist.**
`except services.LeagueError` fängt nur die Service-Fehler. Wirft ein Thread etwas anderes —
`IntegrityError`, `OperationalError: deadlock detected`, `BrokenBarrierError` —, stirbt der
Thread, `results[key]` bleibt ungesetzt. Der Test wird dadurch zwar **rot** (die Assertion in
Zeile 495 vergleicht dann gegen eine unvollständige Liste) — verschluckt wird also nichts, das
ist gut —, aber die Fehlermeldung lautet dann `assert ['registered'] == ['LeagueFullError',
'registered']`, während der eigentliche Traceback nur über `threading.excepthook` auf stderr
landet. Debuggen in CI wird damit unnötig teuer.

Dazu: `thread.join(timeout=30)` (Zeile 492) wird **nicht** von einem `is_alive()`-Check gefolgt.
Ein echt verklemmter Thread (Lock nie freigegeben) läuft als Non-Daemon-Thread weiter — pytest
hängt am Session-Ende auf ihm, nach einem bereits roten Test.

**Reproduktionsszenario.**
In `register_team()` künstlich ein `IntegrityError` provozieren (z. B. `LeagueRosterEntry`
doppelt anlegen) → Test scheitert mit einer irreführenden Assertion statt mit dem
`IntegrityError`.

**Vorschlag.**
```python
errors = []

def register(team, key):
    try:
        ...
    except services.LeagueError as error:
        results[key] = type(error).__name__
    except BaseException as error:          # noqa: BLE001
        errors.append(error)
    else:
        results[key] = "registered"
    finally:
        connections.close_all()

...
for thread in threads:
    thread.join(timeout=30)
    assert not thread.is_alive(), "thread deadlocked — the lock was never released"
assert not errors, errors
```

---

### M3 — `slots_taken` / `is_full` / `is_registration_open` benutzen die Admin-Annotation nicht

**Dateien:** `prostratsix/leagues/models.py:104-131`, `prostratsix/leagues/admin.py:44-59`

**Was falsch ist.**
`LeagueAdmin.get_queryset()` annotiert `confirmed_count` — und das funktioniert auch, aber **nur**
weil `slots_display()` (admin.py:59) die Annotation *direkt* liest (`obj.confirmed_count`). Die
Properties selbst wissen nichts davon: `slots_taken` (models.py:104-105) feuert weiterhin bei
jedem Aufruf ein eigenes `COUNT`, und `is_full` / `slots_free` / `is_registration_open` hängen
alle daran.

Die Annotation ist also eine Insellösung für genau eine Admin-Spalte. **Anschlussfähigkeit an
2c:** das Liga-Listen-Template wird `{% if league.is_registration_open %}` pro Zeile auswerten —
und damit ein `COUNT` **pro Liga** absetzen. Die vorhandene Annotation hilft dort nicht, weil
kein Property sie anfasst.

**Reproduktionsszenario.**
2c-Listen-View mit 30 Ligen, Template ruft `league.is_registration_open` → 30 zusätzliche
`COUNT(*)`-Queries, obwohl die Liga-Liste in einer einzigen annotierten Query zu haben wäre.

**Vorschlag.**
Die Annotation in das Property hineinziehen, dann profitieren Admin *und* 2c automatisch:
```python
@property
def slots_taken(self) -> int:
    # Benutzt eine vorhandene .annotate(confirmed_count=…), sonst zählt es selbst.
    annotated = getattr(self, "confirmed_count", None)
    if annotated is not None:
        return annotated
    return self.confirmed_registrations.count()
```
`slots_display()` kann dann wieder auf `obj.slots_taken` zurückgehen (wie im Plan §2.7), und 2c
braucht nur noch dieselbe `.annotate()` im Listen-Queryset.

---

### L1 — `_absolute_url()` schluckt `NoReverseMatch` dauerhaft; kein Test prüft die URL — weder leer noch gefüllt

**Datei:** `prostratsix/leagues/services.py:62-73` (Zwilling: `prostratsix/teams/services.py:48-59`)

**Zur konkreten Frage aus dem Auftrag: es ist *keine* Falle.** Geprüft: **kein** Test in
`prostratsix/leagues/tests/` macht irgendeine Aussage über `notification.url`. Es gibt also
nichts, was die heute leere URL festschreibt und nach 2c falsch würde. Sauber.

**Was trotzdem ein Problem ist:** die Kehrseite. Es gibt auch **keinen** Test, der prüft, dass
die URL *gefüllt* ist. Sobald 2c `leagues:detail` registriert, greift der `except NoReverseMatch:
return ""`-Pfad still weiter — ein Tippfehler in `get_absolute_url()` (models.py:96-97), ein
umbenannter URL-Name, ein vergessener `app_name` in `urls.py` liefert dann für immer leere
Notification-URLs, ohne dass irgendetwas rot wird. Der Zwilling in `teams/services.py:50-52`
behauptet in seinem Docstring bis heute „while the `teams` URL routes are not yet registered" —
die gibt es längst; der Swallow ist dort bereits toter, aber scharfer Code. Der Duplikats-Hinweis
(services.py:58-61) ist vorbildlich gesetzt, das ist nicht das Thema.

**Vorschlag für 2c:** Test ergänzen —
`assert notification.url.endswith(league.get_absolute_url())` — und erwägen, `_absolute_url()`
nach dem Landen der Routen wieder scharf zu stellen (`NoReverseMatch` durchreichen), damit der
Fehler laut wird statt leise.

---

### L2 — Auto-Slug ohne Kollisions- und Nicht-ASCII-Behandlung

**Datei:** `prostratsix/leagues/models.py:91-94`

`slugify("Pro League – 2026")` und `slugify("Pro League 2026")` ergeben beide `pro-league-2026`;
`slug` ist `unique=True` → `IntegrityError`, im Admin ein 500 statt eines Formularfehlers. Ein
rein kyrillischer/asiatischer Name slugifiziert zu `""` — der zweite solche Fall kollidiert auf
dem leeren Slug.

**Kein neu eingeführter Bug:** `prostratsix/teams/models.py:75-78` hat exakt dasselbe Muster. Das
Muster wurde korrekt gespiegelt — aber damit steht dieselbe Kante jetzt an zwei Stellen.
Reproduktion: zwei Ligen anlegen, deren Namen sich nur in Satzzeichen unterscheiden.
Vorschlag: Suffix-Schleife (`slug-2`, `slug-3`) in einem gemeinsamen Helper, gemeinsam mit
`_absolute_url()` beim Anlegen von `prostratsix/core/utils.py` erledigen.

---

### L3 — Der Überbuchungsschutz hängt an READ COMMITTED, ohne dass das irgendwo festgenagelt ist

**Datei:** `prostratsix/leagues/services.py:164-168`

Der Kommentar benennt die Abhängigkeit korrekt („READ COMMITTED hands us their result") — das
ist gut. Nur: nichts erzwingt sie. Stellt jemand die Isolationsstufe auf `REPEATABLE READ`
(`DATABASES["default"]["OPTIONS"]["isolation_level"]`), bricht der Schutz **stillschweigend**:
die `League`-Zeile selbst wird nie ge-`UPDATE`-t, also läuft das `SELECT … FOR UPDATE` des
zweiten Threads nach dem Entblocken *ohne* Serialisierungsfehler durch — und sein anschließendes
`COUNT` liest den Snapshot vom Transaktionsbeginn, sieht 0 freie Slots belegt und bucht über.
Heute unkritisch (Postgres-Default ist READ COMMITTED), aber es ist eine scharfe Kante ohne
Schutzblech.
**Vorschlag:** Ein Satz in `docs/leagues.rst` (2c) und/oder ein Assert im Race-Test:
`assert connection.get_connection_params()`-Isolationsstufe == READ COMMITTED.

---

### L4 — Race-Test verstößt gegen Brief §2.5 („kein zusätzlicher `django_db`-Marker")

**Datei:** `prostratsix/leagues/tests/test_services.py:28` (`pytestmark = pytest.mark.django_db`) + `:451`

Das modulweite `pytestmark` gilt auch für den Race-Test, der zusätzlich `transactional_db`
anfordert. Der Brief verbietet das ausdrücklich („Kein `@pytest.mark.django_db` zusätzlich auf
denselben Test setzen — das würde die äußere Transaktions-Wrapping-Logik reaktivieren").

**Es ist heute harmlos** — geprüft im installierten Quellcode: `pytest_django/fixtures.py:234-238`
ver-oder-t `"transactional_db" in request.fixturenames` in das `transactional`-Flag hinein, der
Test bekommt also tatsächlich eine `TransactionTestCase`. (pytest-django 4.12.0.) Kein Hänger.

**Aber:** die Korrektheit hängt an einem Implementierungsdetail von pytest-django, gegen die
ausdrückliche Anweisung des Briefs. Ändert sich das, hängt der Test auf Postgres **für immer**
statt rot zu werden.
**Vorschlag:** `@pytest.mark.django_db(transaction=True)` explizit auf den Test (überschreibt das
modulweite Mark sauber) und das `transactional_db`-Argument streichen — oder den Test in ein
eigenes Modul ohne `pytestmark` ziehen.

---

### T — Test-Lücken (konkret benannt)

Die vier überlebenden Mutanten aus Runde 2 sind belegte Lücken, nicht Vermutungen:

1. **`withdraw_team()`, `TERMINAL_STATUSES`-Zweig (services.py:235) — ungetestet.** Kein Test
   zieht ein Team aus einer `FINISHED`/`CANCELLED`-Liga zurück.
   `test_cannot_withdraw_once_the_league_runs` deckt nur `RUNNING` ab. Mutation belegt: den
   `or league.status in TERMINAL_STATUSES` entfernen — **kein Test wird rot**.
2. **`_transition()`, `TERMINAL_STATUSES`-Zweig (services.py:352) — ungetestet.** Die Docstrings
   von `open_registration()`/`close_registration()` (services.py:280, 291) versprechen
   ausdrücklich „Raises LeagueError: the league is running, **finished or cancelled**" — die
   Hälfte „finished or cancelled" beweist niemand. Mutation belegt es.
3. **`test_reopening_an_already_open_league_is_a_no_op` (test_services.py:419-422) ist vakuum.**
   Der Test wird auch grün, wenn man den Early-Return in `_transition()` (services.py:350-351)
   löscht — dann fällt der Code einfach durch und setzt den Status auf denselben Wert. Der Test
   prüft den Rückgabestatus, nicht die Idempotenz. Der Name lügt. (Bei `start_league()` ist die
   Idempotenz *echt* getestet — `test_starting_twice_notifies_only_once`, Mutation bestätigt.)
   Vorschlag: über `django_assert_num_queries` belegen, dass kein `UPDATE` läuft.
4. **`_confirmed_players()`-Dedupe (services.py:387-390) ungetestet.** Der Docstring begründet die
   Deduplizierung ausführlich („nobody wants the league-start notification twice"), Mutation:
   Dedupe entfernen → kein Test wird rot.
5. **`close_registration()`-Idempotenz fehlt** — das Gegenstück zu
   `test_reopening_an_already_open_league_is_a_no_op` gibt es nicht.
6. **Keine Admin-Tests.** Weder `_run()`s Fehlerpfad (eine von mehreren Ligen wirft → die anderen
   werden trotzdem verarbeitet, Fehlermeldung erscheint — das ist verhaltensrelevant und
   ungetestet) noch `get_queryset()`/`slots_display`.
7. **Keine Tests für die beiden `CheckConstraint`s** (`registration_closes_after_it_opens`,
   `league_has_at_least_one_slot`, models.py:76-85). Ein `IntegrityError`-Test dafür kostet vier
   Zeilen und ist die einzige Absicherung, dass die Constraints jemals in der DB ankommen.
8. **`RegistrationStatus.DISQUALIFIED` (models.py:32) ist toter Code** — kein Service setzt ihn,
   kein Test benutzt ihn. Entweder ein `disqualify_team()`-Service (mit Slot-Freigabe +
   Notification, siehe M1) oder ein Kommentar wie bei `requires_approval`, warum der Wert schon
   da ist.

**Positiv angemerkt zur Migration (kein Finding, aber ein Hinweis):** Der Enum-Wert ist in
`0001_initial.py:73` korrekt als Literal eingefroren (`models.Q(('status', 'confirmed'))`), und
`test_the_database_rejects_a_second_confirmed_registration` (test_services.py:213-227) ist der
vom Brief §2.6 geforderte Fang. Nur: lokal läuft die Suite mit `--no-migrations`, das Schema
kommt also aus den Models, nicht aus der Migration — der eigentliche „Enum umbenannt, Migration
vergessen"-Schutz greift ausschließlich in der CI (echte Migrationen). Gut zu wissen, nicht zu
ändern.

---

## Was explizit geprüft und in Ordnung ist

- **Gate-1 #2 (`notify_many()` außerhalb des Locks):** erfüllt. `transaction.on_commit()` ist in
  allen drei Services korrekt platziert (services.py:199, 260, 331), keine Closure-Falle, keine
  Schleife um ein `on_commit`.
- **Gate-1 #5 (Idempotenz im Service):** `start_league()` sperrt und prüft den Status **nach** dem
  Lock (services.py:309-313). Mutationstest bestätigt die Absicherung.
- **Gate-1 #1 (`select_for_update()` + Postgres-only-Skip mit Begründung im `reason=`):** erfüllt
  (test_services.py:37-41).
- **Gate-1 #3 (`django_capture_on_commit_callbacks`):** überall dort benutzt, wo Notifications
  geprüft werden, und in test_services.py:100-108 ausführlich begründet.
- **Guard-Reihenfolge:** Permission zuerst, vor dem Laden der Liga (services.py:121). Mutation
  bestätigt, dass genau ein Test das schützt.
- **`withdraw_team()` + Wiederanmeldung umgeht die Kapazität nicht:** der `is_full`-Re-Check läuft
  unter dem Lock mit frischem `COUNT` (services.py:166) — es gibt keinen gecachten Zähler, der
  driften könnte.
- **Partielle `UniqueConstraint`:** greift (auch auf SQLite — partielle Indizes werden dort
  unterstützt), Enum-Wert korrekt eingefroren, Rückzug → Wiederanmeldung funktioniert
  (test_services.py:242-256).
- **Docstrings:** alle Service-Funktionen haben einen `Raises:`-Block, der sagt **wann** geworfen
  wird, nicht nur **was** (services.py:100-120 ist geradezu vorbildlich). `_absolute_url()` ist als
  Dublette markiert (services.py:58-61). `requires_approval` ist erklärt (models.py:57-59).
  Abschnitt 3 des Auftrags: vollständig erfüllt, keine Findings.
- **Admin-Actions rufen die Services**, nicht `queryset.update()` (admin.py:88-98) — und `_run()`
  fängt `LeagueError` pro Liga ab und macht weiter. (Einschränkung: nur `LeagueError`; alles
  andere → 500 mit teilweise verarbeiteter Auswahl. Vertretbar, siehe aber H2 zur Lock-Menge.)
- **Abweichung vom Brief-Snippet, die eine Verbesserung ist:** Der Brief (§2.2) prüft
  `if not league.is_registration_open` — das schließt `is_full` mit ein, `LeagueFullError` wäre
  damit **nie erreichbar**. Der Developer hat die Property in
  `is_registration_window_open` + `is_registration_open` aufgeteilt (models.py:115-131) und damit
  einen echten Fehler des Briefs behoben. Richtig so, und dokumentiert.
