# Research-Brief: Phase 2 — Liga-Anmeldesystem (`prostratsix/leagues`)

Stand: 2026-07-12 · Basis: `feat/league-settings` (aufgesetzt auf `feat/team-system`) ·
Bezug: [`../leagues-and-htmx.md`](../leagues-and-htmx.md), Abschnitt "Phase 2" ·
Geprüfte Versionen (`pyproject.toml`): Django `6.0.6`, Python `3.14`, `celery==5.6.3`,
`django-celery-beat==2.9.0`, `psycopg[c]==3.3.4`.

---

## 1. Empfohlener Ansatz

**`select_for_update()` auf die `League`-Zeile ist die richtige Wahl — der Plan liegt hier
richtig, aber aus einem anderen Grund als er nennt.** Die Alternative "Counter-Feld +
`F()`-Update + `CheckConstraint`" wurde ernsthaft geprüft: `CheckConstraint` kann in
PostgreSQL grundsätzlich **nicht** über mehrere Zeilen aggregieren (das ist eine
PostgreSQL-Einschränkung, keine Django-Limitation — CHECK-Bedingungen müssen laut
PostgreSQL-Doku "immutable" für die eine geprüfte Zeile sein), das heißt ein
`slots_taken`-Zähler wäre ein denormalisiertes Feld, dessen Konsistenz mit der echten
`COUNT(*)` der `CONFIRMED`-Registrierungen **niemand außer eurem eigenen Code garantiert**
— jeder vergessene Dekrement bei `withdraw_team` oder jede manuelle Admin-Korrektur an
`LeagueRegistration.status` (per `queryset.update()`, das der Plan in 2.7 explizit als
Admin-Antipattern nennt) lässt den Zähler stumm auseinanderlaufen, ohne dass die DB das
je bemerkt. Ein `F()`-Update mit bedingtem `WHERE slots_taken < max_teams` (Compare-and-
Swap) wäre lock-frei und skaliert besser, aber bei den hier relevanten Größenordnungen
(≤ 32 Teams, ein kurzer Anmelde-Peak um eine Deadline) ist der Durchsatzgewinn irrelevant,
während die zusätzliche Fehlerquelle (Zähler-Drift) real ist. Postgres-Advisory-Locks
(`pg_advisory_xact_lock`) lösen ein anderes Problem (Koordination ohne dedizierte Zeile,
z. B. über Tabellen hinweg) und wären hier nur zusätzliche, unbelegte Komplexität, da mit
`League` bereits eine natürliche Zeile zum Sperren existiert. **Fazit: `select_for_update()`
bleibt die Empfehlung**, mit zwei Korrekturen gegenüber dem Plan-Wortlaut (Abschnitt 4):
Django ignoriert die Sperre auf SQLite **lautlos statt mit Fehler** (belegt in Abschnitt 2.1),
und `notify_many()` darf **nicht** innerhalb des gesperrten Transaktionsblocks laufen,
sondern gehört hinter `transaction.on_commit()` (Abschnitt 2.4) — sonst wird die Liga-Zeile
für die gesamte Dauer von Notification-Inserts und Celery-Enqueues gesperrt, und im
Produktivbetrieb (nicht mit `CELERY_TASK_ALWAYS_EAGER`) kann ein Celery-Worker eine
Notification abfragen, **bevor** die Transaktion, die sie erzeugt hat, überhaupt committet ist.

---

## 2. Konkrete Code-Muster

### 2.1 `select_for_update()` — Quellcode-Beleg statt Vermutung

Geprüft an `.venv/Lib/site-packages/django/db/models/sql/compiler.py:840`:

```python
if self.query.select_for_update and features.has_select_for_update:
    if (
        self.connection.get_autocommit()
        and features.supports_transactions
    ):
        raise TransactionManagementError(
            "select_for_update cannot be used outside of a transaction."
        )
    ...
```

Zwei Dinge, die **anders** sind als der Plan-Wortlaut suggeriert ("select_for_update()
funktioniert nicht auf SQLite"):

- Der `FOR UPDATE`-Zusatz wird nur angehängt, wenn `features.has_select_for_update` `True`
  ist. Für SQLite ist das der Fall
  (`.venv/Lib/site-packages/django/db/backends/base/features.py:50`:
  `has_select_for_update = False`, **nicht** von
  `django/db/backends/sqlite3/features.py` überschrieben — geprüft, kein Treffer beim
  Grep). Das heißt: `select_for_update()` auf SQLite wirft **keinen Fehler**. Es wird
  **kommentarlos zu einer normalen `SELECT`** ohne jede Zeilensperre. Ein Race-Condition-
  Test würde auf SQLite also nicht crashen, sondern **fälschlich grün durchlaufen** (und
  dabei zusätzlich durch SQLites eigenen, globalen Datei-Schreib-Lock verfälscht — siehe
  Abschnitt 3).
- `TransactionManagementError` fliegt nur, wenn autocommit aktiv **und** die Datenbank
  Transaktionen unterstützt. Innerhalb von `@transaction.atomic` ist Autocommit aus,
  der Guard greift also nicht — `select_for_update()` **muss** aber trotzdem in einem
  `atomic()`-Block stehen, sonst hält Django den Lock nur bis zum Ende des impliziten
  Autocommits der einzelnen `SELECT`-Anweisung, was die Sperre nutzlos macht (Postgres
  gibt Zeilensperren beim Commit/Rollback der *Transaktion* frei, nicht beim Ende der
  Query).

`nowait`/`skip_locked` sind für `register_team()` **nicht** die richtige Wahl:
`nowait=True` wirft sofort `DatabaseError`, wenn die Zeile gesperrt ist — bei einer
Anmeldung, die nur wenige Millisekunden blockiert (kein externer I/O innerhalb der
Sperre, siehe 2.4), ist "kurz warten, dann normal weiterlaufen" die bessere UX als ein
harter Fehler. `skip_locked` ist für Queue-artige Auswahl **mehrerer** Kandidatenzeilen
gedacht (z. B. Job-Queues) — hier gibt es nur eine Zeile, die gesperrt werden soll.

**Isolation Level:** Postgres-Default `READ COMMITTED` reicht, und zwar aus einem
konkreten Grund, den man kennen sollte, um dem Muster zu vertrauen: Unter `READ COMMITTED`
bekommt **jede einzelne Anweisung** einen frischen Snapshot. Wenn Transaktion B durch
`select_for_update()` auf der `League`-Zeile blockiert, bis Transaktion A committet, sieht
B's `SELECT ... FOR UPDATE` **nach dem Entsperren** automatisch den von A committeten
Zustand (nicht den Stand von B's Transaktionsbeginn) — genau deshalb funktioniert
`league = League.objects.select_for_update().get(pk=league.pk)` gefolgt von einem
`league.is_full`-Recheck als Race-Schutz. Unter `REPEATABLE READ`/`SERIALIZABLE` wäre das
**anders**: Postgres würde in diesem Fall keine stillen alten Daten liefern, sondern beim
Zugriff auf eine zwischenzeitlich geänderte, gesperrte Zeile einen
Serialisierungsfehler werfen (`could not serialize access due to concurrent update`), den
der Code dann per Retry behandeln müsste. Für dieses Projekt (Default-Isolation, kein
`isolation_level`-Override in `config/settings/*.py` — geprüft) ist der einfache Re-Check
nach dem Lock der korrekte, ausreichende Weg.

### 2.2 `services.register_team()` — mit dem Lock **und** dem Commit-Hook korrekt kombiniert

```python
# prostratsix/leagues/services.py
from django.db import transaction


def register_team(league: League, actor, team: Team) -> LeagueRegistration:
    if not team.has_management_rights(actor):
        raise LeaguePermissionError(...)

    with transaction.atomic():
        # Serialisiert alle gleichzeitigen Anmeldungen auf DIESE Liga. Der Lock
        # wird erst beim Commit/Rollback dieses `with`-Blocks freigegeben —
        # daher darf hier nichts Langsames (E-Mail-Versand, HTTP) laufen.
        league = League.objects.select_for_update().get(pk=league.pk)

        if not league.is_registration_open:
            raise RegistrationClosedError(...)
        if league.registrations.filter(team=team, status=CONFIRMED).exists():
            raise AlreadyRegisteredError(...)

        members = list(
            TeamMembership.objects.filter(team=team).select_related("user"),
        )
        if len(members) < league.min_roster_size:
            raise RosterTooSmallError(...)
        if league.is_full:  # Re-Check NACH dem Lock — das ist der eigentliche Schutz
            raise LeagueFullError(...)

        registration = LeagueRegistration.objects.create(
            league=league, team=team, registered_by=actor, status=CONFIRMED,
        )
        LeagueRosterEntry.objects.bulk_create([
            LeagueRosterEntry(registration=registration, user=m.user, role=m.role)
            for m in members
        ])

        # WICHTIG: erst NACH dem Commit benachrichtigen — siehe 2.4.
        transaction.on_commit(
            lambda: notify_many(
                [m.user for m in members],
                title=_("Your team is competing in %(league)s") % {"league": league.name},
                message=_("%(team)s has been registered for %(league)s.") % {
                    "team": team.name, "league": league.name,
                },
                category=NotificationCategory.LEAGUE,
                level=NotificationLevel.SUCCESS,
                url=_absolute_url(league.get_absolute_url),
            ),
        )
    return registration
```

Zwei Abweichungen vom Plan-Snippet, beide begründet in Abschnitt 4: `transaction.atomic()`
als `with`-Block statt Dekorator (damit exakt der Teil mit dem Lock die Grenze der
Transaktion ist, nicht die ganze Funktion inkl. Permission-Guard — der Guard braucht
keinen Lock und keine Transaktion), und `notify_many` hinter `transaction.on_commit()`.

### 2.3 `notify_many()` — `bulk_create`, Signals, `auto_now_add`, PKs (Kernfrage 3)

**Antwort auf die Signal-Frage: `prostratsix/notifications/signals.py` hat KEIN
`post_save`-Signal auf `Notification`.** Gelesen, komplett: die Datei registriert
ausschließlich `@receiver`-Handler auf **allauth-Signalen**
(`password_changed`, `password_reset`, `email_changed`, `email_added`,
`authenticator_added`), die ihrerseits `notify()` **aufrufen** — sie reagieren nicht
*auf* das Anlegen einer `Notification`. Es gibt in der gesamten Codebase keinen
`@receiver(post_save, sender=Notification)` (grep über `prostratsix/` bestätigt: die
einzigen Treffer für "signals" sind `notifications/signals.py`, `notifications/apps.py`,
`users/signals.py`, `users/apps.py` und der Test — keiner hängt an `Notification`).
**`bulk_create` umgeht hier also nichts, was heute existiert — kein stiller Datenfehler.**
Das ist aber eine Momentaufnahme, kein architektonisches Versprechen: Sollte später
jemand einen `post_save`-Handler auf `Notification` ergänzen (z. B. für Websocket-Push),
bricht `bulk_create` ihn lautlos — Docstring-Warnung an `notify_many()` ist billig und
sollte rein.

**`auto_now_add` funktioniert mit `bulk_create` — belegt am Compiler, nicht vermutet:**
`.venv/Lib/site-packages/django/db/models/sql/compiler.py:1744` (`pre_save_val`) ruft für
jedes Feld jedes Objekts `field.pre_save(obj, add=True)` auf, **auch im
`bulk_create`-Pfad** (`assemble_as_sql`, Zeile 1808–1814). `DateTimeField.pre_save`
(`django/db/models/fields/__init__.py:1648`) setzt bei `auto_now_add and add` per
`setattr` den aktuellen `timezone.now()`-Wert — das passiert unabhängig vom Signal-Pfad,
weil es eine reine Feld-Methode ist, kein Signal. `Notification.created_at` wird also bei
`bulk_create` korrekt gesetzt (der `bulk_create`-Docstring in Django selbst warnt nur vor
"kein `save()`, keine Signale, keine automatische PK bei Autoincrement ohne
RETURNING" — `auto_now_add` steht da bewusst **nicht** in der Liste der Dinge, die
ausbleiben).

**`bulk_create` gibt auf PostgreSQL die PKs zurück — ebenfalls am Code, nicht an der
Doku:** derselbe Docstring in `query.py:774-777`: *"We currently set the primary keys on
the objects when using PostgreSQL via the RETURNING ID clause."* PostgreSQL hat
`can_return_rows_from_bulk_insert = True` (Feature-Flag), SQLite **auch** ab Django 4.2+
(3.35+ SQLite-Version mit `RETURNING`-Unterstützung) — für dieses Projekt heißt das:
**sowohl** auf Postgres in Produktion **als auch** im lokalen SQLite-Testsetup bekommt ihr
befüllte `.id`-Attribute nach `bulk_create()` zurück, `notification.id` in
`send_notification_email.delay(notification.id)` ist also nutzbar, ohne dass ihr die
Objekte neu aus der DB laden müsst.

```python
# prostratsix/notifications/services.py — Ergänzung
def notify_many(  # noqa: PLR0913
    users,
    title: str,
    message: str = "",
    *,
    level: str = NotificationLevel.INFO,
    category: str = NotificationCategory.GENERAL,
    url: str = "",
    send_email: bool = True,
) -> list[Notification]:
    """Create notifications for many users in a bounded number of queries.

    Uses ``bulk_create`` — this bypasses ``Model.save()`` and any future
    ``post_save`` signal on :class:`Notification` (there is none today, see
    ``prostratsix/notifications/signals.py``). ``auto_now_add`` and returned
    primary keys work correctly on PostgreSQL and SQLite (RETURNING clause);
    do not assume the same on backends without it (e.g. older MySQL).
    """
    users = list(users)
    if not users:
        return []

    notifications = Notification.objects.bulk_create([
        Notification(recipient=u, title=title, message=message,
                     level=level, category=category, url=url)
        for u in users
    ])

    if not send_email:
        return notifications

    prefs = {p.user_id: p for p in NotificationPreference.objects.filter(user__in=users)}
    missing = [NotificationPreference(user=u) for u in users if u.pk not in prefs]
    for pref in NotificationPreference.objects.bulk_create(missing):
        prefs[pref.user_id] = pref

    from .tasks import send_notification_email  # noqa: PLC0415

    for notification in notifications:
        pref = prefs[notification.recipient_id]
        if notification.recipient.email and pref.allows_email(category):
            # delay_on_commit(), nicht delay() — siehe 2.4.
            send_notification_email.delay_on_commit(notification.id)

    return notifications
```

### 2.4 `.delay()` vs. `.delay_on_commit()` — der Fund, der nicht im Plan steht

Der Plan ruft `notify_many()` **innerhalb** von `register_team()`s
`@transaction.atomic`-Block auf und benutzt `.delay()`. Das ist im Grundsatz ein bekanntes
Django+Celery-Problem: Ein `.delay()`-Call sendet die Task sofort an den Broker (Redis);
läuft ein separater Celery-Worker-Prozess sie ab, **bevor** die äußere Transaktion
committet ist, sieht der Worker (andere DB-Connection, anderer Snapshot) die
`Notification`-Zeile noch nicht — `send_notification_email` bricht mit
`Notification.DoesNotExist` verfrüht ab (der aktuelle Task fängt das per `try/except` ab
und tut nichts — kein Crash, aber die E-Mail geht nie raus, **lautlos**).

**Der Fix ist in diesem Projekt bereits vorhanden, ohne zusätzliche Konfiguration:**
`config/celery_app.py:7` setzt `DJANGO_SETTINGS_MODULE`, bevor `Celery("prostratsix")`
instanziiert wird. Celerys Django-Fixup
(`.venv/Lib/site-packages/celery/fixups/django.py:52-63`, `fixup()`) installiert sich
automatisch, sobald diese Env-Var gesetzt ist, und setzt
(`fixups/django.py:84-85`):

```python
if not self.app._custom_task_cls_used:
    self.app.task_cls = 'celery.contrib.django.task:DjangoTask'
```

`DjangoTask` (`.venv/Lib/site-packages/celery/contrib/django/task.py`) bringt zwei
Methoden mit, die exakt für dieses Problem gebaut sind:

```python
def delay_on_commit(self, *args, **kwargs) -> None:
    """Call delay() with Django's on_commit()."""
    transaction.on_commit(functools.partial(self.delay, *args, **kwargs))
```

Weil `config/celery_app.py` **kein** eigenes `task_cls` setzt, ist dieser Fixup bereits
aktiv — jeder `@shared_task` in diesem Projekt (auch `send_notification_email` selbst) ist
schon heute eine `DjangoTask`-Instanz. `send_notification_email.delay_on_commit(...)`
funktioniert **ohne jede Konfigurationsänderung**, sowohl direkt in `notify_many()` als
auch — wie in 2.2 gezeigt — zusätzlich über einen äußeren `transaction.on_commit()`, der
den ganzen `notify_many()`-Aufruf selbst erst nach dem Registrierungs-Commit startet
(damit auch der `Notification.objects.bulk_create()` nicht innerhalb der
League-Sperre läuft). **Randnotiz, kein Scope für diesen Lauf:** `notifications/services.py`s
bestehendes `notify()` benutzt weiterhin `.delay()` (Zeile 38) und wird u. a. von
`teams/services.py`s `@transaction.atomic`-Funktionen aufgerufen (`invite_member`,
`transfer_ownership`, …) — dasselbe latente Problem existiert dort schon. Das zu ändern
ist ein separates Ticket, aber es sollte nicht in Phase 2 **neu eingeführt** werden.

### 2.5 Race-Condition-Test — Postgres-only, `TransactionTestCase`, Skip anhand des DB-Vendors

**CI stellt Postgres tatsächlich bereit — geprüft in `.github/workflows/ci.yml`:** Der
`pytest`-Job baut per `docker/bake-action` `docker-compose.local.yml` (Target `django`)
und führt `docker compose -f docker-compose.local.yml run django pytest` aus.
`docker-compose.local.yml` definiert einen `postgres`-Service
(`compose/production/postgres/Dockerfile`, mit `.envs/.local/.postgres`) als Dependency
des `django`-Service — **CI läuft also nicht auf SQLite**, sondern gegen echtes Postgres.
Ein Postgres-only-Test läuft in CI folglich ganz normal mit, ohne Sonderbehandlung, solange
er nicht durch einen Marker o. Ä. übersprungen wird.

**Lokal (siehe Memory-Notiz `local-test-postgres-constraint`) läuft ihr auf
SQLite ohne Docker.** Der sauberste Skip ist ein `pytest.mark.skipif` gegen
`django.db.connection.vendor` — kein neuer Custom-Marker nötig (der müsste sonst in
`pyproject.toml` registriert werden, was hier fehlt und `PytestUnknownMarkWarning` gäbe):

```python
import pytest
from django.db import connection

only_postgres = pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="select_for_update() is a no-op on SQLite; this test would pass "
    "for the wrong reason (see docs/plans/research/phase-2-leagues.md §2.1).",
)


@only_postgres
class TestConcurrentRegistration:
    def test_only_one_team_wins_the_last_slot(self, transactional_db):
        ...
```

**`TransactionTestCase` (bzw. pytest-djangos `transactional_db`-Fixture), nicht
`TestCase`.** `pytest.mark.django_db` (bzw. das Standard-`db`-Fixture) wickelt jeden Test
in eine Transaktion, die am Ende zurückgerollt wird — damit läuft der komplette Test
faktisch in **einer** offenen Transaktion, und ein zweiter Thread, der versucht, dieselbe
Zeile zu sperren, blockiert **für immer** auf einen Commit, der nie kommt (Deadlock/Timeout
im Test). `transactional_db` (pytest-django) entspricht Djangos `TransactionTestCase`:
jeder Thread committet echt, die Datenbank wird nach dem Test per `flush` geleert statt
per Rollback.

Muster, das tatsächlich funktioniert (Threads + `threading.Barrier`, nicht `time.sleep`):

```python
import threading

@only_postgres
def test_concurrent_registration_only_one_wins(transactional_db, django_db_blocker):
    league = LeagueFactory(max_teams=1, status=REGISTRATION_OPEN, ...)
    team_a, team_b = team_with_players(5), team_with_players(5)
    barrier = threading.Barrier(2)
    results = {}

    def register(team, key):
        barrier.wait()  # beide Threads starten register_team() so zeitgleich wie möglich
        with django_db_blocker.unblock():
            try:
                services.register_team(league, team.owner, team)
                results[key] = "ok"
            except services.LeagueError as exc:
                results[key] = type(exc).__name__

    t1 = threading.Thread(target=register, args=(team_a, "a"))
    t2 = threading.Thread(target=register, args=(team_b, "b"))
    t1.start(); t2.start()
    t1.join(); t2.join()

    outcomes = list(results.values())
    assert outcomes.count("ok") == 1
    assert outcomes.count("LeagueFullError") == 1
```

Wichtige Details, ohne die der Test flaky wird:
- `threading.Barrier(2)` statt `time.sleep(...)`: garantiert, dass beide Threads den
  `register_team()`-Aufruf **im selben Moment** starten, statt auf eine willkürliche
  Sleep-Dauer zu hoffen, die auf einem langsamen CI-Runner nicht ausreicht.
- Jeder Thread braucht **seine eigene DB-Connection** — Django-Connections sind nicht
  Thread-safe. pytest-djangos `django_db_blocker.unblock()` pro Thread (oder alternativ
  `django.db.connections.close_all()` am Threadanfang, damit Django pro Thread neu
  verbindet) ist hier das Standardmuster; ohne das teilen sich beide Threads dieselbe
  Connection und der Test testet nichts.
- **Kein `@pytest.mark.django_db` zusätzlich auf denselben Test setzen** — das würde die
  äußere Transaktions-Wrapping-Logik von `TestCase` reaktivieren. `transactional_db`
  (bzw. `pytest.mark.django_db(transaction=True)`) ist exklusiv zu verwenden.

### 2.6 Partielle `UniqueConstraint` — `LeagueRegistration` exakt wie `TeamInvitation`

```python
class Meta:
    constraints = [
        models.UniqueConstraint(
            fields=["league", "team"],
            condition=models.Q(status=RegistrationStatus.CONFIRMED),
            name="unique_confirmed_registration",
        ),
    ]
```

Funktioniert 1:1 wie `TeamInvitation.unique_pending_invitation`
(`prostratsix/teams/models.py:159-163`) — Postgres pflegt einen partiellen Index, der nur
Zeilen mit `status = 'confirmed'` enthält; sobald eine Zeile per `withdraw_team()` auf
`withdrawn` wechselt, fällt sie automatisch aus dem Index und eine neue `CONFIRMED`-Zeile
für dasselbe `(league, team)`-Paar ist wieder erlaubt.

**Fallstricke bei Statuswechseln:**
- **`.update()` auf einem Queryset umgeht `save()`, aber NICHT die DB-Constraint.** Der
  partielle Unique-Index ist ein echter Postgres-Index, kein Python-Check — er greift bei
  `save()` genauso wie bei `.update()`, weil beide letztlich dieselbe SQL-`UPDATE`/`INSERT`
  ausführen. Der Plan-Punkt "Admin-Actions rufen Services, nicht `queryset.update()`" ist
  trotzdem richtig — aber der Grund ist **nicht** die Constraint (die hält so oder so),
  sondern dass `queryset.update()` `notify_many()` nicht auslöst.
- **Der Constraint-Wert wird beim Erzeugen der Migration in einen literalen String
  eingefroren, nicht als lebende Referenz auf das Enum.** `Q(status=RegistrationStatus.CONFIRMED)`
  wird beim `makemigrations`-Lauf zu `Q(status="confirmed")` deconstructed. Benennt ihr
  später den Statuswert um (z. B. `CONFIRMED = "confirmed"` → `"active"`), ohne die
  Migration zu erneuern, filtert die Constraint stillschweigend auf einen Wert, den keine
  Zeile mehr je annimmt — der Schutz ist dann faktisch **deaktiviert**, ohne dass
  `makemigrations --check` (das CI läuft, s. `ci.yml:70`) etwas anderes meldet als "Modell
  und Migrationen sind synchron", weil die neue Migration ja korrekt erzeugt würde. Der
  einzige verlässliche Fang dafür ist ein Test, der explizit die `IntegrityError` bei
  Doppelanmeldung erwartet (aus der Test-Tabelle im Plan bereits vorgesehen: "Doppelanmeldung
  → `AlreadyRegisteredError`" — dieser Test deckt indirekt auch die Constraint ab, **wenn**
  der Guard im Service jemals wegfällt und die DB als letzte Verteidigungslinie greifen muss).
- `full_clean()` wird von `save()` **nicht** automatisch aufgerufen (weder bei `Model.save()`
  noch bei `bulk_create`) — die Python-seitige "schöne" `ValidationError`-Meldung, die
  `Model.validate_constraints()` bieten würde, kommt hier also nie zustande; ein
  Constraint-Verstoß landet immer als roher `django.db.utils.IntegrityError`. Der
  Service-Layer muss die Existenzprüfung (`.filter(...).exists()`) **vor** dem `.create()`
  selbst machen, exakt wie im Plan-Snippet — die Constraint ist reines Backstop gegen die
  Race Condition zwischen Check und Insert, nicht der primäre Fehlerpfad.

### 2.7 `django-celery-beat` — Migrations-basiert, nicht `beat_schedule`

`config/celery_app.py` definiert **kein** `app.conf.beat_schedule` und
`config/settings/base.py:314` setzt bereits
`CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"`. Das ist die
Weiche, die die Entscheidung eigentlich schon trifft — aber es lohnt sich, das
Zusammenspiel am Scheduler-Code zu belegen, weil es einen echten Fallstrick enthält:

`.venv/Lib/site-packages/django_celery_beat/schedulers.py:257-259`:

```python
def setup_schedule(self):
    self.install_default_entries(self.schedule)
    self.update_from_dict(self.app.conf.beat_schedule)
```

`update_from_dict` (Zeile 472-484) ruft für jeden Eintrag
`ModelEntry.from_entry(name, app=self.app, **entry_fields)` auf, was intern
(Zeile 191-195) `PeriodicTask._default_manager.update_or_create(name=name, defaults=...)`
macht. **Das heißt: Würdet ihr `sync_league_statuses` stattdessen über
`CELERY_BEAT_SCHEDULE` in den Settings definieren, würde JEDER Neustart von `celerybeat`
den zugehörigen `PeriodicTask`-Datenbankeintrag per `update_or_create` auf die
Settings-Werte zurücksetzen** — inklusive eines `enabled=True`, falls jemand die Task per
Django-Admin (django-celery-beats eigentlicher Zweck) temporär deaktiviert hatte. Für
statische, nie im Admin anzufassende System-Tasks ist das harmlos; für
`sync_league_statuses`, das potenziell mal pausiert werden soll (z. B. während eines
manuellen Eingriffs an einer Liga), ist das ein stiller Reset bei jedem Deploy/Neustart.

**Empfehlung:** `PeriodicTask`/`IntervalSchedule`-Objekte per **Data-Migration** mit
`get_or_create` (nicht `update_or_create`) anlegen — dann bleiben spätere
Admin-Änderungen über Neustarts hinweg erhalten:

```python
# prostratsix/leagues/migrations/0002_periodic_tasks.py
from django.db import migrations


def create_periodic_tasks(apps, schema_editor):
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    schedule, _created = IntervalSchedule.objects.get_or_create(
        every=5, period="minutes",
    )
    PeriodicTask.objects.get_or_create(
        name="leagues.sync_league_statuses",
        defaults={
            "task": "prostratsix.leagues.tasks.sync_league_statuses",
            "interval": schedule,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("django_celery_beat", "0019_alter_periodictasks_options"),  # aktuellste beat-Migration prüfen
        ("leagues", "0001_initial"),
    ]
    operations = [migrations.RunPython(create_periodic_tasks, migrations.RunPython.noop)]
```

**Idempotenz des Tasks selbst (unabhängig vom Scheduling):** Bei einem 5-Minuten-Takt
muss `sync_league_statuses` idempotent sein, weil zwei Läufe theoretisch überlappen
können (langsamer DB-Zugriff, Worker-Restart mitten im Lauf). Das richtige Muster ist ein
**bedingtes `UPDATE`, das den Statuswechsel selbst als Gate benutzt** — nicht "hole alle
Ligen mit `starts_at <= now`, iteriere, rufe `start_league()` auf", sondern:

```python
@shared_task
def sync_league_statuses():
    now = timezone.now()
    # .update() statt Schleife: EIN atomarer UPDATE pro Übergang, der nur Zeilen trifft,
    # die noch NICHT im Zielstatus sind — ein zweiter, überlappender Lauf findet dann
    # keine passenden Zeilen mehr und tut nichts (statt ein zweites Mal zu benachrichtigen).
    started = League.objects.filter(
        status=LeagueStatus.REGISTRATION_CLOSED, starts_at__lte=now,
    )
    for league in started:
        # start_league() macht selbst KEINEN Status-Filter-Read-Then-Write auf
        # Anwendungsebene — es muss selbst noch mal prüfen/sperren, weil zwischen dem
        # .filter() oben und dem Aufruf hier ein anderer Prozess denselben Datensatz schon
        # umgeschaltet haben könnte. Innerhalb von start_league(): dieselbe
        # select_for_update()-Zeilensperre wie bei register_team(), und die
        # Status-Prüfung ("nur wenn noch REGISTRATION_CLOSED") passiert dort NOCH EINMAL,
        # nach dem Lock.
        start_league(league)
```

Der eigentliche Schutz gegen "zweimal gestartet, zwei Notifications" gehört in
`start_league()` selbst (analog `register_team()`): Zeile sperren, Status noch mal prüfen,
erst dann umschalten und benachrichtigen. Der Task selbst filtert nur grob vor — die
Garantie liegt im Service, nicht im Task-Scheduling. `django-celery-beat`s
`DatabaseScheduler` hat außerdem `beat_max_loop_interval` und einen `last_run_at`
Zeitstempel je `PeriodicTask`, verhindert also bereits auf Celery-Ebene, dass Beat selbst
denselben Task doppelt in kurzer Folge einreiht — das schützt aber nicht vor zwei
gleichzeitig laufenden Beat-Prozessen (Deploy mit zwei Replikas) oder vor einem manuell
erneut ausgelösten Lauf, weshalb die DB-seitige Idempotenz in `start_league()` trotzdem
nötig bleibt.

### 2.8 Zeitzonen — konkrete Fallstricke bei Admin-Eingabe

`USE_TZ = True`, `TIME_ZONE = "Europe/Berlin"` (`config/settings/base.py:27,42`),
`CELERY_TIMEZONE = TIME_ZONE` (Zeile 285, nur gesetzt weil `USE_TZ` an ist). Alle
gespeicherten Datetimes sind UTC in der DB, werden aber in Templates/Admin/Forms in
`TIME_ZONE` **serverweit** (nicht pro Nutzer, es gibt keinen `timezone.activate()`-Call im
Projekt — geprüft) dargestellt.

- **Der Admin zeigt/erwartet `registration_closes_at` in `Europe/Berlin`, nicht in der
  Browser-Zeitzone des Admins.** Ein Admin, der z. B. aus den USA arbeitet und "20:00"
  einträgt, meint (wahrscheinlich) seine eigene Ortszeit — Django interpretiert es aber
  als 20:00 Berlin-Zeit. Das ist kein Django-6-spezifischer Bug, sondern **das erwartete
  Verhalten** von `TIME_ZONE` als Server-Default ohne Per-User-Activation — aber es ist
  der mit Abstand häufigste "die Liga hat 1-2 Stunden zu früh/spät geschlossen"-Bugreport
  in Multi-Timezone-Teams. Für ein MVP mit einer einzigen Admin-Zeitzone (Europe/Berlin,
  vermutlich die Realität hier) ist das unkritisch, aber es gehört als Kommentar an
  `registration_closes_at` im Model, damit niemand versehentlich einen internationalen
  Admin-Kreis annimmt.
- **DST-Übergänge (Europe/Berlin, letzter Sonntag im März/Oktober).** Ein Admin, der eine
  `registration_closes_at` exakt in die "fehlende Stunde" beim Frühjahrs-Übergang legt
  (z. B. 02:30 Uhr, die an diesem Tag nicht existiert), bekommt von Djangos
  `AdminSplitDateTime`-Widget/`zoneinfo` eine automatische Vor- oder Zurückverschiebung —
  in der Praxis für Ligafenster irrelevant (niemand plant einen Anmeldeschluss um 02:30
  nachts), aber bei automatisch generierten Default-Werten (z. B. "+7 Tage ab jetzt" via
  `timedelta`) theoretisch möglich, wenn der Startzeitpunkt nahe an einem DST-Wechsel
  liegt — `timedelta`-Arithmetik auf aware Datetimes rechnet in **absoluten** Sekunden,
  nicht in Wandzeit, das Ergebnis kann dadurch einen "unrunden" Wanduhr-Zeitpunkt ergeben
  (z. B. 19:57 statt 20:00), wenn zwischen Start und Ziel ein DST-Wechsel liegt. Für
  Anmeldefenster mit Tages-Granularität irrelevant, aber ein guter Grund, DateTimeFields
  im Admin **immer** explizit setzen zu lassen statt automatisch aus `timedelta`-Defaults
  zu berechnen.
- **`League.objects.filter(registration_closes_at__lte=now)` funktioniert unabhängig von
  der Server-`TIME_ZONE`**, weil sowohl die gespeicherten Werte als auch `timezone.now()`
  in UTC verglichen werden — das ist die eine Stelle, die *nicht* kaputtgehen kann, wenn
  man konsequent `timezone.now()` statt `datetime.now()` benutzt (Ruff-Regel `DTZ` ist im
  Projekt bereits aktiv, `pyproject.toml:97` — ein nacktes `datetime.now()` würde also
  schon beim Lint auffallen).
- **`CheckConstraint(registration_closes_at > registration_opens_at)` aus dem Plan** ist
  zeitzonenrobust, weil beide Seiten aware Datetimes sind und der Vergleich in UTC
  passiert — kein gesonderter Fallstrick hier, nur zur Vollständigkeit erwähnt, weil es
  im Plan (2.3) explizit als Constraint gefordert ist.

### 2.9 `HX-Trigger` + Progressive Enhancement (Anschluss an Phase 1)

**Bestätigt, mit Beleg aus dem Phase-1-Brief statt neu hergeleitet**
(`docs/plans/research/phase-1-htmx.md`, Abschnitt 2.7): *"Der Request, der den Header
setzt, muss selbst ein htmx-Request sein … ein reiner Server-Redirect ohne
htmx-Beteiligung setzt zwar den Header, aber niemand liest ihn."* Für den Register-Button
heißt das konkret: Es muss ein **echtes `<form method="post" action="{% url '...' %}">`**
mit `hx-post` auf demselben Endpoint sein, kein bloßer `<button hx-post="...">` ohne
`<form>`-Fallback — sonst funktioniert die Anmeldung **ganz ohne JS gar nicht** (der Button
tut ohne htmx nichts, wie es der Phase-1-Reviewer bei `MarkReadView`/`MarkAllReadView`
bereits explizit als "in Ordnung, weil `href`-Fallback vorhanden" abgenommen hat — hier
gibt es aber keinen Link-Fallback für einen POST, nur ein `<form>`):

```html
<!-- prostratsix/templates/leagues/partials/registration_panel.html -->
<form method="post"
      action="{% url 'leagues:register' league.slug %}"
      hx-post="{% url 'leagues:register' league.slug %}"
      hx-target="#registration-panel"
      hx-swap="outerHTML">
  {% csrf_token %}
  <button type="submit" class="btn btn-primary" {% if not can_register %}disabled{% endif %}>
    {% translate "Register team" %}
  </button>
</form>
```

Ohne JS: normaler POST, Server macht `register_team()`, redirected auf `league_detail`
(Full-Page-Reload, Badge aktualisiert sich erst beim nächsten 30s-Poll oder beim
Seitenreload durch den Context-Processor). Mit htmx: derselbe POST via `hx-post`, Server
antwortet **entweder** mit gerendertem `registration_panel.html` (200, ersetzt sich
selbst) **und** setzt zusätzlich den Header:

```python
def post(self, request, slug):
    league = get_object_or_404(League, slug=slug)
    try:
        services.register_team(league, request.user, services.get_user_team(request.user))
    except services.LeagueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Team registered."))

    if request.headers.get("HX-Request") == "true":
        response = render(request, "leagues/partials/registration_panel.html", {...})
        response["HX-Trigger"] = "notifications:refresh"
        return response
    return redirect(league.get_absolute_url())
```

Kein `django-htmx`-Package nötig (Phase-1-Entscheidung gilt weiter) —
`request.headers.get("HX-Request") == "true"` reicht, exakt wie im
`MarkReadView`/`MarkAllReadView`-Muster aus Phase 1.

---

## 3. Fallstricke — Zusammenfassung

| Fallstrick | Woran man's merkt |
|---|---|
| `select_for_update()` auf SQLite ist ein stiller No-Op, kein Fehler | Race-Test läuft lokal grün, ohne dass er irgendetwas beweist — nur der Postgres-Lauf in CI zählt |
| `notify_many()`/`.delay()` innerhalb der League-Sperre bzw. vor dem Commit | In Produktion (nicht Eager-Mode) verlorene E-Mails ohne Exception; im Test unsichtbar, weil `CELERY_TASK_ALWAYS_EAGER` + gleiche Connection das Problem verdeckt |
| `CELERY_BEAT_SCHEDULE` in Settings statt Migration | Jeder Beat-Neustart setzt Admin-Änderungen an der `PeriodicTask` lautlos zurück (`update_or_create`, s. 2.7) |
| Enum-Wert in `condition=Q(status=...)` umbenannt, Migration nicht erneuert | Partieller Index filtert auf einen nie mehr erreichten Wert — Doppelanmeldungen werden wieder möglich, ohne dass `makemigrations --check` etwas meldet |
| `TestCase`/`pytest.mark.django_db` statt `transactional_db` für den Race-Test | Zweiter Thread blockiert für immer auf einen Commit, der nie kommt (Test hängt/Timeout) |
| Admin trägt `registration_closes_at` in eigener statt Server-Zeitzone ein | Anmeldeschluss "verschiebt" sich um die Zeitzonendifferenz — kein Crash, nur falsches Verhalten |
| `queryset.update()` in Admin-Actions statt Service-Aufruf | Constraint hält weiterhin, aber `notify_many()` läuft nicht — Spieler bekommen keine Benachrichtigung über den Statuswechsel |

---

## 4. Abweichungen vom Plan

1. **"`select_for_update()` funktioniert nicht auf SQLite" ist ungenau.** Es funktioniert
   syntaktisch (kein Fehler), sperrt aber nichts — das ist gefährlicher als ein Fehler,
   weil ein Test dadurch falsch-grün werden kann, statt laut zu scheitern. Betrifft
   Abschnitt 2.1/3.
2. **`notify_many()` gehört nicht in den `@transaction.atomic`-Block mit dem Lock**, wie es
   das Plan-Snippet in Abschnitt 2.4 zeigt (`register_team` ruft `notify_many` direkt vor
   dem `return`, noch innerhalb des impliziten Atomic-Scopes). Grund: Zeilensperre + DB-
   Writes für Notifications + Celery-Enqueue alles in einer Sperre verlängert die
   Sperrzeit unnötig und riskiert das `.delay()`-vor-Commit-Problem aus Abschnitt 2.4.
   Empfehlung: `transaction.on_commit(...)` um den `notify_many`-Aufruf, und
   `send_notification_email.delay_on_commit(...)` statt `.delay()` in `notify_many`
   selbst (kostenlos verfügbar, Celery-Django-Fixup ist bereits aktiv, s. 2.4).
3. **`django-celery-beat`: Migrations-basiertes `PeriodicTask.objects.get_or_create`
   statt `CELERY_BEAT_SCHEDULE` in Settings.** Der Plan lässt das offen ("Analog zu
   notifications/tasks.py, eingehängt über django_celery_beat"); diese Recherche
   empfiehlt explizit die Migrationsvariante, weil `CELERY_BEAT_SCHEDULE` bei jedem
   Beat-Neustart über `update_or_create` Admin-Änderungen zurücksetzt (Abschnitt 2.7).
4. **Kein Widerspruch, aber Verschärfung:** Die im Plan genannte Alternative
   ("CheckConstraint oder Counter-Feld mit `F()`-Expression") wurde ernsthaft geprüft und
   verworfen, nicht nur pro forma erwähnt — Begründung in Abschnitt 1. Postgres-Advisory-
   Locks wurden zusätzlich (im Plan nicht erwähnt) geprüft und ebenfalls verworfen, da sie
   hier kein Problem lösen, das `select_for_update()` nicht schon löst.

---

## 5. Quellen

**Projektinterne Quellen (direkt gelesen, Ground Truth):**
- `.venv/Lib/site-packages/django/db/models/sql/compiler.py` (`select_for_update`-SQL-Erzeugung
  Zeile 838-891, `pre_save_val`/`assemble_as_sql` für `bulk_create` Zeile 1744-1814)
- `.venv/Lib/site-packages/django/db/models/query.py` (`bulk_create`-Docstring und
  Implementierung Zeile 749-829, `_prepare_for_bulk_create` Zeile 679-693)
- `.venv/Lib/site-packages/django/db/models/fields/__init__.py` (`DateTimeField.pre_save`
  Zeile 1648-1654, `auto_now_add`-Handling)
- `.venv/Lib/site-packages/django/db/backends/base/features.py` (`has_select_for_update = False`
  Default, Zeile 50)
- `.venv/Lib/site-packages/django/db/backends/{postgresql,sqlite3}/features.py` (Feature-Flags
  je Backend geprüft, SQLite überschreibt `has_select_for_update` nicht)
- `.venv/Lib/site-packages/celery/fixups/django.py` (`fixup()`/`DjangoFixup.install()`,
  Zeile 52-89 — automatische `task_cls`-Umstellung auf `DjangoTask`)
- `.venv/Lib/site-packages/celery/contrib/django/task.py` (`DjangoTask.delay_on_commit`/
  `apply_async_on_commit`)
- `.venv/Lib/site-packages/django_celery_beat/schedulers.py` (`DatabaseScheduler.setup_schedule`
  Zeile 257-259, `update_from_dict`/`ModelEntry.from_entry` Zeile 190-195, 472-484)
- `prostratsix/notifications/signals.py`, `prostratsix/notifications/models.py`,
  `prostratsix/notifications/services.py`, `prostratsix/notifications/tasks.py`,
  `prostratsix/notifications/context_processors.py`
- `prostratsix/teams/services.py`, `prostratsix/teams/models.py`,
  `prostratsix/teams/admin.py`, `prostratsix/teams/views.py`,
  `prostratsix/teams/tests/test_services.py`
- `config/celery_app.py`, `config/settings/base.py` (Zeile 27, 42, 81, 283-314),
  `config/settings/test.py`
- `.github/workflows/ci.yml` (Postgres via `docker-compose.local.yml` im `pytest`-Job)
- `docker-compose.local.yml` (`postgres`-Service, Dependency von `django`/`celeryworker`/`celerybeat`)
- `pyproject.toml` (Versionen, `ruff` `DTZ`-Regelsatz Zeile 97, keine `[tool.pytest.ini_options]`-Marker)
- `prostratsix/conftest.py` (keine registrierten Custom-Marker)
- `docs/plans/research/phase-1-htmx.md` (Abschnitt 2.7, `HX-Trigger`-Voraussetzung),
  `docs/plans/review/phase-1-htmx.md` (Fehlerklassen, die vermieden werden sollen:
  fehlende Regressionstests für stille Fehlermodi, Guard-Reihenfolge, N+1)
- Memory: `local-test-postgres-constraint.md` (lokales SQLite-Testsetup ohne Docker)

**Externe Quellen:**
- https://www.postgresql.org/docs/current/ddl-constraints.html (CHECK-Constraints dürfen
  keine anderen Zeilen/Tabellen referenzieren — PostgreSQL-Doku, Abschnitt 5.5)
- https://docs.celeryq.dev/en/stable/userguide/tasks.html (Celery 5.6, allgemeine Task-Doku)
- https://testdriven.io/blog/celery-database-transactions/ (`delay_on_commit()`-Empfehlung,
  Celery 5.4+, bestätigt gegen den tatsächlichen Celery-5.6.3-Quellcode im `.venv`)
- https://dev.to/k4ml/django-fixing-race-condition-when-queuing-with-oncommit-hook-7ae
  (Race Condition Task-vor-Commit, Community-Bestätigung des Musters)
- https://medium.com/@onurmaciit/race-condition-in-django-269029a3d4fd,
  https://medium.com/@alexandre.laplante/djangos-select-for-update-with-examples-and-tests-caff09414766
  (Bestätigung: `TransactionTestCase`/`transactional_db` statt `TestCase` für
  Race-Condition-Tests, `select_for_update` wird auf SQLite ignoriert)
- https://www.ines-panker.com/2024/12/17/advisory-locks.html,
  https://django-pglock.readthedocs.io/en/1.5.1/advisory/ (Advisory-Lock-Einsatzgebiete
  zur Abgrenzung gegen `select_for_update`, Abschnitt 1)
