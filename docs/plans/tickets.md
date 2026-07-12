# Offene Tickets

Gefunden während Phase 1 und 2 (HTMX-Notifications + Liga-System), bewusst
**nicht** in den laufenden Läufen behoben, um den Umfang sauber zu halten.
Sortiert nach Auswirkung.

---

## 1. `notify()` verliert E-Mails in Produktion (`.delay()` vor dem Commit)

**Datei:** `prostratsix/notifications/services.py` (der `.delay()`-Aufruf in `notify()`)

`notify()` reiht die E-Mail per `.delay()` ein und wird aus den
`@transaction.atomic`-Funktionen der `teams`-App aufgerufen (`invite_member`,
`transfer_ownership`, …). Ein Celery-Worker kann die Task abarbeiten, **bevor**
die Transaktion committet ist — er findet die `Notification`-Zeile dann nicht,
und `send_notification_email` schluckt das `DoesNotExist` still. **Die E-Mail
geht lautlos verloren.**

In den Tests unsichtbar, weil `CELERY_TASK_ALWAYS_EAGER` das Problem verdeckt.

**Fix:** `.delay()` → `.delay_on_commit()` (steht ohne Konfigurationsaufwand zur
Verfügung, Celerys Django-Fixup ist aktiv — belegt in
`docs/plans/research/phase-2-leagues.md` §2.4).

**Warum es ein eigenes Ticket ist:** Die Umstellung zieht die Tests der
`teams`-App mit — jeder Test, der `mail.outbox` prüft, braucht dann
`django_capture_on_commit_callbacks(execute=True)`. `notify_many()` macht es
bereits richtig; die Inkonsistenz ist im Code kommentiert.

---

## 2. `UserFactory` erzeugt Kollisionen → latente Flakes in allen Apps

**Datei:** `prostratsix/users/tests/factories.py`

`UserFactory` zieht den Username von Faker und setzt
`django_get_or_create = ["username"]`. Bei einer Kollision wirft die Factory
also **nicht**, sondern gibt den **bestehenden** User zurück.

Gemessen: 300 angeforderte User ergeben **299 verschiedene** (3× wiederholt).

Konsequenz in Tests: entweder ein `IntegrityError` (eine zweite
`TeamMembership` für denselben User — `OneToOneField`), oder, subtiler, ein
„Außenstehender", der in Wahrheit Mitglied genau des Teams ist, gegen das er
keine Rechte haben darf. **Der Test prüft dann das Gegenteil dessen, was sein
Name behauptet, und wird trotzdem grün.**

Ist einmal real aufgetreten (`test_permission_is_checked_before_capacity`) und
ließ sich danach nicht reproduzieren — das typische Bild.

**Fix:** Sequenzierter Username statt Faker (`factory.Sequence`). Die
`leagues`-Tests umgehen es bereits über eine eigene `PlayerFactory`; die
geteilte `UserFactory` ist absichtlich unangetastet, weil sie **alle** Apps
betrifft.

---

## 3. Drei kaputte Tests auf `main`

**Datei:** `prostratsix/users/tests/test_views.py`

`TestUserDetailView::test_authenticated`,
`TestUserUpdateView::test_get_success_url`,
`TestUserRedirectView::test_redirects_to_detail_when_profile_complete`

Die Views wurden auf ein `uuid`-URL-Kwarg umgestellt, die Tests rufen
`UserDetailView` per `RequestFactory` aber weiterhin ohne `uuid=` auf:
`AttributeError: Generic detail view UserDetailView must be called with either
an object pk or a slug in the URLconf`.

**Warum es zählt:** Die Suite ist dauerhaft rot. Das verdeckt künftige echte
Regressionen — man gewöhnt sich an „die drei sind normal".

---

## 4. Open Redirect über `Notification.url`

**Datei:** `prostratsix/notifications/views.py` (`MarkReadView`)

`MarkReadView` leitet nach dem Markieren auf `notification.url` weiter — ein
`URLField`, das von `notify()` befüllt wird. Heute nicht ausnutzbar, weil alle
Aufrufer die URL serverseitig aus `reverse()` bauen. Sobald aber irgendwann eine
Notification mit einer vom Nutzer beeinflussbaren URL entsteht, ist es ein
Open Redirect.

**Fix:** Vor dem Redirect gegen `url_has_allowed_host_and_scheme()` prüfen, oder
nur relative Pfade speichern.

---

## 5. `allows_email()` — Default-Zweig ungetestet

**Datei:** `prostratsix/notifications/models.py`

Der Fallback-Zweig von `allows_email()` (alles, was weder `SECURITY`, `TEAM`
noch `LEAGUE` ist → `email_general`) hat keinen eigenen Unit-Test. Vorbestehende
Lücke, nicht durch Lauf 2a entstanden.

---

## 6. Read-only Admin für `LeagueRegistration` ist ungetestet

**Datei:** `prostratsix/leagues/admin.py`

Das Inline und die Change-Page von `LeagueRegistration` wurden read-only
gemacht, damit niemand am Service-Layer vorbei eine Registrierung ohne
Roster-Snapshot anlegt (sonst bekommt beim Liga-Start **kein** Spieler dieses
Teams eine Benachrichtigung). Es gibt dafür keinen Test — die Sperre kann also
lautlos wieder aufgehen.

**Fix:** `test_admin.py` in `leagues`, nach dem Vorbild von
`prostratsix/users/tests/test_admin.py`.

---

## 7. Veralteter Kommentar im Production-Dockerfile

**Datei:** `compose/production/django/Dockerfile` (Zeilen ~43–44)

Der Kommentar beschreibt den Static-Build nicht mehr korrekt. Kein Deploy-Bug —
Zeile 96 kopiert das gesamte `static/`-Verzeichnis, es funktioniert also. Nur
irreführend für den nächsten Leser.

---

## 8. `_absolute_url()` ist dupliziert

**Dateien:** `prostratsix/teams/services.py`, `prostratsix/leagues/services.py`

Bewusste Dublette, damit Lauf 2b nicht an einem Refactoring der `teams`-App
hängt. Gehört nach `prostratsix/core/utils.py`. In beiden Dateien als `NOTE:`
markiert.

---

## 9. Der Race-Test ist nie gegen Postgres gelaufen

**Datei:** `prostratsix/leagues/tests/test_services.py`
(`test_two_teams_racing_for_the_last_slot`)

Lokal übersprungen (`skipif` auf `connection.vendor != "postgresql"`), weil
`select_for_update()` auf SQLite ein stiller No-Op ist. Der Test wurde
nachweislich so gebaut, dass er einen fehlenden Lock **fängt** (auf SQLite —
das dort wegkompilierte `FOR UPDATE` dient als Gratis-Mutant, 3/3 rot). Aber
gegen echtes Postgres ist er noch **nie** gelaufen.

**To do:** Beim ersten CI-Durchlauf gezielt prüfen, dass er grün ist — und nicht
etwa hängt oder übersprungen wird.

---

## 10. Browser-Verifikation von Phase 1 steht aus

Polling, der Bootstrap-Dispose-Handler und der CSRF-Header wurden **nie in einem
echten Browser ausgeführt**. Django-Tests rendern HTML, sie führen kein
JavaScript aus — genau die drei Mechanismen, um die es in Phase 1 ging, kann die
Testsuite prinzipiell nicht abdecken.

**To do:** App starten, prüfen: Badge zählt ohne Reload hoch, Dropdown bleibt
beim Timer-Tick offen, Mark-Read wirft keinen 403.
