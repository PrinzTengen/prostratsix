# Plan: HTMX-Live-Notifications + Liga-System

Stand: 2026-07-12 · Basis-Branch: `feat/team-system`

> **Umsetzung:** siehe [`workflow.md`](./workflow.md) — jede Phase läuft durch
> Research (Sonnet 5) → Developer (Opus) → Reviewer (Opus), mit Gates dazwischen.

Zwei Phasen. Phase 1 (HTMX) ist unabhängig und klein — sie kann zuerst gemergt
werden. Phase 2 (Leagues) baut auf dem bestehenden Service-Layer-Muster der
`teams`-App auf und nutzt in der UI die HTMX-Bausteine aus Phase 1.

**Entschiedene Rahmenbedingungen**

| Frage | Entscheidung |
| --- | --- |
| Mindestkader | `min_roster_size`, Default **5**, Prüfung `>=` (pro Liga hochsetzbar) |
| Anmeldung | **Sofort bestätigt** — kein Approval-Workflow im MVP |
| Kader | **Snapshot** beim Anmelden (`LeagueRosterEntry`) |
| Notifications | **Neue Kategorie** `LEAGUE` + `email_league` in den Preferences |

---

# Phase 1 — HTMX-Polling für Notifications

## Ziel

Das Badge in der Navbar aktualisiert sich ohne Reload. Zusätzlich bekommt die
Navbar ein Glocken-Dropdown mit den letzten 5 Notifications — die
`recent_notifications` liefert der Context-Processor bereits, sie werden
aktuell in **keinem** Template verwendet.

## 1.1 HTMX ausliefern

Das Projekt hostet Bootstrap selbst und zieht es per Gulp aus `node_modules`.
HTMX genauso einbinden, **nicht** per CDN (bleibt konsistent, keine externe
Abhängigkeit zur Laufzeit):

1. `npm install htmx.org --save`
2. In `gulpfile.js` den bestehenden Vendor-Task erweitern, sodass
   `node_modules/htmx.org/dist/htmx.min.js` mit in `prostratsix/static/js/vendors.js`
   gebündelt wird (bzw. als separates `htmx.js` kopiert wird — je nachdem, wie der
   Task aktuell gebaut ist; Bootstrap wird dort schon so behandelt).
3. In `prostratsix/templates/base.html` im `{% block javascript %}` einbinden.
   **Wichtig:** HTMX braucht `defer` genau wie die Vendors, und muss vor
   `project.js` geladen werden.

**Kein `django-htmx`-Package nötig.** Für reines Polling + Swap reicht Vanilla-HTMX.
Falls später serverseitig zwischen HTMX- und Vollseiten-Requests unterschieden
werden soll, prüft man `request.headers.get("HX-Request")` — dafür lohnt keine
neue Dependency.

## 1.2 CSRF

HTMX schickt bei `hx-post` keinen CSRF-Token mit. Einmalig global in
`prostratsix/static/js/project.js` setzen:

```js
document.body.addEventListener("htmx:configRequest", (event) => {
    event.detail.headers["X-CSRFToken"] = document.querySelector(
        "[name=csrfmiddlewaretoken]",
    )?.value;
});
```

Dazu ein verstecktes `{% csrf_token %}` in `base.html` direkt nach `<body>`, damit
der Selector auf jeder Seite etwas findet.

## 1.3 Neuer Endpoint: Notification-Bell

**`prostratsix/notifications/views.py`** — neue View:

```python
class NotificationBellView(LoginRequiredMixin, TemplateView):
    """Render just the navbar bell (badge + last 5). Polled by HTMX."""

    template_name = "notifications/partials/bell.html"

    def get_context_data(self, **kwargs):
        qs = Notification.objects.for_user(self.request.user)
        return super().get_context_data(
            unread_notifications_count=qs.unread().count(),
            recent_notifications=list(qs[:5]),
            **kwargs,
        )
```

Die View berechnet die Werte **selbst** und verlässt sich nicht auf den
Context-Processor — der Partial soll unabhängig vom globalen Context testbar sein.

**`prostratsix/notifications/urls.py`**:

```python
path("bell/", NotificationBellView.as_view(), name="bell"),
```

## 1.4 Partial mit Self-Swap-Polling

**Neu: `prostratsix/templates/notifications/partials/bell.html`**

Das Element ersetzt sich per `hx-swap="outerHTML"` selbst — dadurch trägt das neue
Markup den `hx-trigger` wieder mit sich und das Polling läuft endlos weiter.

```html
{% load i18n %}
<li class="nav-item dropdown"
    id="notification-bell"
    hx-get="{% url 'notifications:bell' %}"
    hx-trigger="every 30s [document.visibilityState === 'visible'], notifications:refresh from:body"
    hx-swap="outerHTML">
  <a class="nav-link position-relative dropdown-toggle"
     href="{% url 'notifications:list' %}"
     data-bs-toggle="dropdown">
    {% translate "Notifications" %}
    {% if unread_notifications_count %}
      <span class="badge text-bg-danger rounded-pill">{{ unread_notifications_count }}</span>
    {% endif %}
  </a>
  <ul class="dropdown-menu dropdown-menu-end">
    {% for notification in recent_notifications %}
      <li>
        <a class="dropdown-item {% if not notification.is_read %}fw-bold{% endif %}"
           href="{% url 'notifications:mark_read' notification.pk %}">
          {{ notification.title }}
        </a>
      </li>
    {% empty %}
      <li><span class="dropdown-item-text text-muted">{% translate "No notifications" %}</span></li>
    {% endfor %}
    <li><hr class="dropdown-divider" /></li>
    <li>
      <a class="dropdown-item" href="{% url 'notifications:list' %}">{% translate "Show all" %}</a>
    </li>
  </ul>
</li>
```

Zwei Details, die leicht übersehen werden:

- **`[document.visibilityState === 'visible']`** stoppt das Polling in
  Hintergrund-Tabs. Ohne das feuern 20 offene Tabs 20× alle 30 s gegen deinen Server.
- **`notifications:refresh from:body`** als zweiter Trigger: Damit kann jede andere
  Aktion (z. B. eine erfolgreiche Liga-Anmeldung) das Badge sofort aktualisieren,
  indem der Server den Response-Header `HX-Trigger: notifications:refresh` setzt.
  Kein Warten auf den nächsten 30-s-Tick.

**`prostratsix/templates/navbar/offcanvas.html`**: den bestehenden Notifications-
`<li>`-Block (Zeile 33–41) ersetzen durch
`{% include "notifications/partials/bell.html" %}`.

## 1.5 Mark-read per HTMX (optional, aber billig)

`MarkReadView` und `MarkAllReadView` so erweitern, dass sie bei einem
HTMX-Request statt eines Redirects den neu gerenderten Bell-Partial
zurückgeben — dann verschwindet das Badge sofort beim Klick.

## 1.6 Performance-Notiz

`unread_notifications` läuft als Context-Processor bei **jedem** Render und macht
zwei Queries (`count()` + Slice der letzten 5). Mit 30-s-Polling kommt pro
eingeloggtem User ~2 Queries/30 s dazu. Bei kleiner Nutzerzahl irrelevant. Sobald
es weh tut: Zähler in Redis cachen und beim `notify()` invalidieren — der
Redis-Stack läuft durch Celery ohnehin.

## 1.8 Gate-1-Entscheidungen (verbindlich, nach dem Research-Brief)

Der Brief `docs/plans/research/phase-1-htmx.md` gilt bei Konflikten vor diesem
Plan. Zusätzlich sind diese vier Punkte **fest in den Umfang von Lauf 1**:

1. **Bootstrap-Dispose-Handler** (Brief 2.5) — bestätigtes Memory-Leak,
   `htmx:beforeCleanupElement` + `bootstrap.Dropdown.getInstance(el)?.dispose()`.
2. **`visibilitychange`-Trigger zusätzlich** (Brief, Fallstricke). Ohne ihn ist
   das Badge nach Rückkehr in den Tab bis zu 30 s veraltet — das ist genau der
   Moment, in dem der User hinschaut. Voller Trigger:
   ```
   hx-trigger="every 30s [<COND>],
               visibilitychange[document.visibilityState === 'visible'] from:document,
               notifications:refresh from:body"
   ```
3. **Nicht swappen, solange das Dropdown offen ist.** Der Brief nennt es als
   UX-Ruckler: Tickt der Timer, während der User die Liste geöffnet liest,
   klappt das Menü kommentarlos zu. Die Trigger-Bedingung `<COND>` aus Punkt 2
   lautet daher:
   ```js
   document.visibilityState === 'visible'
     && !document.querySelector('#notification-bell .dropdown-menu.show')
   ```
4. **Kein `aria-live`** (Brief 2.8 — würde alle 30 s feuern oder Session-State
   brauchen). Stattdessen bekommt das Badge ein statisches
   `<span class="visually-hidden">` mit der Bedeutung der Zahl, damit ein
   Screenreader beim Navigieren wenigstens „3 ungelesene Benachrichtigungen"
   vorliest statt einer nackten „3". Echte Live-Ansagen sind ein eigenes Ticket.

## 1.7 Tests

`prostratsix/notifications/tests/test_notifications.py` erweitern:

- `bell`-Endpoint verlangt Login (302 für Anonymous).
- Rendert den korrekten Unread-Count.
- Zeigt maximal 5 Einträge.
- Zeigt nur Notifications des eingeloggten Users (Fremd-Notification darf nicht auftauchen).

## Aufwand Phase 1

Klein — 1 Arbeitstag inkl. Tests und Gulp-Anpassung.

---

# Phase 2 — Liga-System (`prostratsix/leagues`)

## Architektur-Prinzip

Exakt das Muster der `teams`-App: **dünne Views, Service-Layer besitzt alle
Geschäftsregeln**, Fehler als Exception-Hierarchie, die die Views in
`django.contrib.messages` übersetzen. Ligen werden **im Django-Admin** angelegt —
kein Frontend-Create-View im MVP.

## 2.1 Notification-Kategorie erweitern

Spiegelt exakt Commit `1ba61aa` ("feat: add team notification category").

**`prostratsix/notifications/models.py`**

```python
class NotificationCategory(models.TextChoices):
    SECURITY = "security", _("Security")
    GENERAL = "general", _("General")
    TEAM = "team", _("Team")
    LEAGUE = "league", _("League")      # neu
```

```python
class NotificationPreference(models.Model):
    ...
    email_league = models.BooleanField(
        default=True,
        verbose_name=_("E-mail me about league events"),
    )

    def allows_email(self, category: str) -> bool:
        if category == NotificationCategory.SECURITY:
            return self.email_security
        if category == NotificationCategory.TEAM:
            return self.email_team
        if category == NotificationCategory.LEAGUE:   # neu
            return self.email_league
        return self.email_general
```

Dazu: Migration, Feld in `NotificationPreferenceForm` und in
`templates/notifications/preferences.html`.

## 2.2 Bulk-Notify (wichtig!)

Eine Liga-Anmeldung benachrichtigt **alle** Spieler eines Teams (5–10 User), und
ein Liga-Start benachrichtigt alle Spieler aller Teams (bei 16 Teams × 6 Spieler
= ~100 User). Das bestehende `notify()` macht pro User ein `INSERT` plus einen
`get_or_create` auf die Preferences — das sind bei 100 Usern 300+ Queries.

**Neu in `prostratsix/notifications/services.py`:**

```python
def notify_many(users, title, message="", *, level=..., category=..., url="", send_email=True):
    """Create notifications for many users with a bounded number of queries."""
    users = list(users)
    notifications = Notification.objects.bulk_create([
        Notification(recipient=u, title=title, message=message,
                     level=level, category=category, url=url)
        for u in users
    ])

    if not send_email:
        return notifications

    # Preferences in EINEM Query holen, fehlende in EINEM bulk_create anlegen.
    prefs = {p.user_id: p for p in NotificationPreference.objects.filter(user__in=users)}
    missing = [NotificationPreference(user=u) for u in users if u.pk not in prefs]
    for pref in NotificationPreference.objects.bulk_create(missing):
        prefs[pref.user_id] = pref

    from .tasks import send_notification_email
    for notification in notifications:
        pref = prefs[notification.recipient_id]
        if notification.recipient.email and pref.allows_email(category):
            send_notification_email.delay(notification.id)

    return notifications
```

`notify()` bleibt unverändert bestehen — `teams` nutzt es weiter für 1:1-Fälle.

## 2.3 Models — `prostratsix/leagues/models.py`

### `League`

| Feld | Typ | Zweck |
| --- | --- | --- |
| `uuid` | UUIDField, unique | konsistent zu User/Team |
| `name` | CharField(150), unique | |
| `slug` | SlugField, unique, auto aus name | URL-Key wie bei Team |
| `description` | TextField, blank | |
| `banner` | ImageField, blank | `league_image_upload_path` analog `team_image_upload_path` |
| `region` | CharField(100), blank | |
| `status` | TextChoices | `draft` / `registration_open` / `registration_closed` / `running` / `finished` / `cancelled` |
| `max_teams` | PositiveIntegerField | Kapazität |
| `min_roster_size` | PositiveIntegerField, default **5** | Prüfung `>=` |
| `registration_opens_at` | DateTimeField | |
| `registration_closes_at` | DateTimeField | |
| `starts_at` | DateTimeField, null | |
| `ends_at` | DateTimeField, null | |
| `created_at` | auto_now_add | |

Methoden/Properties:

```python
@property
def confirmed_registrations(self):
    return self.registrations.filter(status=RegistrationStatus.CONFIRMED)

@property
def slots_taken(self) -> int:
    return self.confirmed_registrations.count()

@property
def slots_free(self) -> int:
    return max(self.max_teams - self.slots_taken, 0)

@property
def is_full(self) -> bool:
    return self.slots_free == 0

@property
def is_registration_open(self) -> bool:
    now = timezone.now()
    return (
        self.status == LeagueStatus.REGISTRATION_OPEN
        and self.registration_opens_at <= now < self.registration_closes_at
        and not self.is_full
    )

def get_absolute_url(self) -> str:
    return reverse("leagues:detail", kwargs={"slug": self.slug})
```

`Meta.constraints`: `CheckConstraint(registration_closes_at > registration_opens_at)`
und `CheckConstraint(max_teams > 0)`.

### `LeagueRegistration`

| Feld | Typ |
| --- | --- |
| `league` | FK(League, related_name="registrations") |
| `team` | FK(Team, related_name="league_registrations") |
| `registered_by` | FK(User, SET_NULL, null) |
| `status` | `confirmed` / `withdrawn` / `disqualified` |
| `created_at` | auto_now_add |

**Constraint (kritisch):**

```python
models.UniqueConstraint(
    fields=["league", "team"],
    condition=models.Q(status=RegistrationStatus.CONFIRMED),
    name="unique_confirmed_registration",
)
```

Exakt dasselbe Muster wie `unique_pending_invitation` bei den Teams: ein Team kann
nach einem Rückzug erneut anmelden, aber nie zweimal gleichzeitig drinstehen.

### `LeagueRosterEntry` (Kader-Snapshot)

| Feld | Typ |
| --- | --- |
| `registration` | FK(LeagueRegistration, related_name="roster") |
| `user` | FK(User, related_name="league_roster_entries") |
| `role` | CharField — kopiert aus `TeamMembership.role` zum Zeitpunkt der Anmeldung |

`UniqueConstraint(["registration", "user"])`. Damit ist dokumentiert, **wer** zum
Anmeldezeitpunkt im Kader stand — Grundlage für spätere Match-Aufstellungen und
Spielerstatistiken.

## 2.4 Services — `prostratsix/leagues/services.py`

Fehler-Hierarchie analog `teams.services`:

```python
class LeagueError(Exception): ...
class LeaguePermissionError(LeagueError): ...      # Actor ist nicht Owner/Manager
class RegistrationClosedError(LeagueError): ...    # Fenster zu / Status falsch
class LeagueFullError(LeagueError): ...            # keine Slots mehr
class RosterTooSmallError(LeagueError): ...        # < min_roster_size
class AlreadyRegisteredError(LeagueError): ...
class NotRegisteredError(LeagueError): ...
```

### `register_team(league, actor, team) -> LeagueRegistration`

Die Kernfunktion. Reihenfolge der Guards ist bewusst gewählt (Permission zuerst,
damit ein Außenstehender nicht über Fehlermeldungen die Kapazität ausspähen kann):

```python
@transaction.atomic
def register_team(league: League, actor, team: Team) -> LeagueRegistration:
    if not team.has_management_rights(actor):
        raise LeaguePermissionError(...)

    # Zeilensperre gegen Race Condition (siehe unten)
    league = League.objects.select_for_update().get(pk=league.pk)

    if not league.is_registration_open:
        raise RegistrationClosedError(...)   # deckt auch is_full ab
    if league.registrations.filter(team=team, status=CONFIRMED).exists():
        raise AlreadyRegisteredError(...)

    members = list(
        TeamMembership.objects.filter(team=team).select_related("user")
    )
    if len(members) < league.min_roster_size:
        raise RosterTooSmallError(...)

    if league.is_full:
        raise LeagueFullError(...)

    registration = LeagueRegistration.objects.create(
        league=league, team=team, registered_by=actor, status=CONFIRMED,
    )
    LeagueRosterEntry.objects.bulk_create([
        LeagueRosterEntry(registration=registration, user=m.user, role=m.role)
        for m in members
    ])

    notify_many(
        [m.user for m in members],
        title=_("Your team is competing in %(league)s") % {"league": league.name},
        message=_("%(team)s has been registered for the league %(league)s.") % {...},
        category=NotificationCategory.LEAGUE,
        level=NotificationLevel.SUCCESS,
        url=_absolute_url(lambda: league.get_absolute_url()),
    )
    return registration
```

> **Der wichtigste Punkt des ganzen Plans:** `select_for_update()` auf die
> League-Zeile. Ohne die Sperre können zwei Teams, die gleichzeitig auf den
> letzten freien Platz klicken, **beide** die `is_full`-Prüfung passieren und die
> Liga um einen Teilnehmer überbuchen. Eine `UniqueConstraint` kann das nicht
> abfangen, weil es hier um eine *Anzahl* geht, nicht um eine Dublette. Die
> Sperre serialisiert die Anmeldungen pro Liga — bei Anmelde-Deadlines ("alle
> klicken um 20:00 Uhr") ist genau das der Lastfall, der auftritt.
>
> Achtung: `select_for_update()` funktioniert **nicht** auf SQLite. Dieser Test
> muss gegen Postgres laufen (Docker), oder der Race-Condition-Test wird mit
> `pytest.mark.skipif` auf Postgres eingegrenzt.
> Siehe auch die Memory-Notiz zum lokalen SQLite-Setup.

`_absolute_url(get_path)` aus `teams/services.py` wird 1:1 gebraucht — am besten
in ein gemeinsames `prostratsix/core/utils.py` ziehen (kleines Refactoring) oder
zunächst duplizieren und im Ticket vermerken.

### Weitere Services

- **`withdraw_team(league, actor, team)`** — Status auf `withdrawn`, Roster-Snapshot
  bleibt als Historie erhalten, alle Spieler bekommen eine Notification
  (`level=WARNING`). Nur erlaubt, solange die Liga nicht `running` ist.
- **`get_team_registration(league, team)`** — Helper für die Template-Logik.
- **`open_registration(league)` / `close_registration(league)`** — Status-Übergänge,
  benutzt vom Admin und vom Celery-Task.
- **`start_league(league)`** — Status auf `running`, Notification an alle Spieler
  aller angemeldeten Teams (via `notify_many` über alle Roster-Einträge).

## 2.5 Celery-Tasks — `prostratsix/leagues/tasks.py`

Analog zu `notifications/tasks.py`, eingehängt über `django_celery_beat` (schon
installiert):

- **`sync_league_statuses`** (alle 5 Min): Ligen mit
  `registration_opens_at <= now` von `draft` → `registration_open`;
  Ligen mit `registration_closes_at <= now` → `registration_closed`;
  Ligen mit `starts_at <= now` → `running` (ruft `start_league` auf).
- **`send_registration_reminder`** (täglich): Ligen, deren Anmeldung in <24 h
  schließt → Notification an alle Team-Owner, deren Team noch nicht angemeldet ist
  und die genug Spieler haben. **Das ist der Feature-Hebel:** so füllen sich die Ligen.

## 2.6 Views & URLs — `prostratsix/leagues/`

`urls.py` (`app_name = "leagues"`):

```python
path("",                       views.league_list_view,     name="list"),
path("<slug:slug>/",           views.league_detail_view,   name="detail"),
path("<slug:slug>/register/",  views.league_register_view, name="register"),
path("<slug:slug>/withdraw/",  views.league_withdraw_view, name="withdraw"),
```

- **`LeagueListView`** (`ListView`, kein Login nötig — öffentliche Liga-Übersicht):
  offene Anmeldungen zuerst, dann laufende, dann beendete. `slots_taken` per
  `annotate(Count(...))` vorberechnen, sonst N+1.
- **`LeagueDetailView`** (`DetailView`, öffentlich): Teilnehmerliste, Slot-Anzeige,
  Countdown bis Anmeldeschluss. Im Context für eingeloggte User:
  `user_team`, `can_register` (Management-Rechte + Kadergröße ok + Fenster offen),
  `blocking_reason` (String für den deaktivierten Button — „Dein Kader hat nur 4
  Spieler" ist eine viel bessere UX als ein grau ausgegrauter Button ohne Grund).
- **`LeagueRegisterView`** (`LoginRequiredMixin`, nur POST): ruft
  `services.register_team()`, fängt `LeagueError` und übersetzt in
  `messages.error()`. Bei Erfolg Response-Header `HX-Trigger: notifications:refresh`
  setzen — dann aktualisiert sich das Badge aus Phase 1 sofort.
- **`LeagueWithdrawView`** analog.

**Templates** (in `prostratsix/templates/leagues/`):
`league_list.html`, `league_detail.html`,
`partials/registration_panel.html` (HTMX-Target: nach dem POST wird nur dieses
Panel neu gerendert — kein Full-Reload).

**Navbar**: Link „Leagues" in `offcanvas.html` ergänzen.
**Team-Detail**: Sektion „Ligen" mit den Anmeldungen des Teams.

## 2.7 Admin — `prostratsix/leagues/admin.py`

Das ist die *primäre* Oberfläche zum Anlegen einer Liga.

```python
@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    list_display = ["name", "status", "slots_display", "registration_closes_at", "starts_at"]
    list_filter = ["status", "region"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    inlines = [LeagueRegistrationInline]
    actions = ["open_registration", "close_registration"]

    @admin.display(description=_("Slots"))
    def slots_display(self, obj):
        return f"{obj.slots_taken} / {obj.max_teams}"
```

`LeagueRegistrationInline` mit `autocomplete_fields = ["team"]` (Team-Admin hat
bereits `search_fields` — Voraussetzung dafür ist erfüllt).

Admin-Actions rufen die **Services** auf, nicht `queryset.update()` — sonst
laufen die Notifications nicht.

## 2.8 Tests — `prostratsix/leagues/tests/`

`factories.py`: `LeagueFactory`, plus `team_with_players(n)`-Fixture (Team + n
Memberships).

**`test_services.py`** (das Herzstück, Muster von `teams/tests/test_services.py`):

| Test | Erwartung |
| --- | --- |
| Owner meldet Team mit 5 Spielern an | `CONFIRMED`, Roster hat 5 Einträge |
| **Alle 5 Spieler bekommen Notification** | `Notification.objects.filter(category=LEAGUE).count() == 5` |
| Team mit 4 Spielern | `RosterTooSmallError` |
| Nicht-Manager meldet an | `LeaguePermissionError` |
| Anmeldung vor `registration_opens_at` | `RegistrationClosedError` |
| Anmeldung nach `registration_closes_at` | `RegistrationClosedError` |
| Liga voll (`max_teams` erreicht) | `RegistrationClosedError` |
| Doppelanmeldung | `AlreadyRegisteredError` |
| Rückzug → Wiederanmeldung | erlaubt (Constraint greift nur bei `CONFIRMED`) |
| Rückzug benachrichtigt alle Spieler | `WARNING`-Notifications |
| E-Mail respektiert `email_league = False` | `len(mail.outbox) == 0` |
| **Nebenläufige Anmeldung auf letzten Slot** | genau eine gewinnt (nur Postgres) |

**`test_views.py`**: Register-View verlangt Login, verlangt POST, Nicht-Manager
bekommt Fehlermeldung, erfolgreicher Redirect.

**`test_tasks.py`**: `sync_league_statuses` schaltet Status korrekt um.

## 2.9 Doku

`docs/leagues.rst` nach dem Vorbild von `docs/teams.rst`
(`automodule:: prostratsix.leagues.services`) + Eintrag in `docs/index.rst`.

## Reihenfolge der Commits

Spiegelt bewusst deine Team-System-Commits:

1. `feat: add leagues app to installed apps in base settings`
2. `feat: add league notification category and email preference`
3. `feat: add notify_many for bulk notifications`
4. `feat: add league models with registration and roster snapshot`
5. `feat: add leagues service layer with registration lifecycle`
6. `feat: add league admin with registration inline`
7. `feat: add league views, urls and templates`
8. `feat: add celery tasks for league status transitions`
9. `docs: document the leagues app`

## Aufwand Phase 2

Realistisch 3–5 Arbeitstage inkl. Tests.

---

# Vorschläge — was noch mit rein kann

Sortiert nach Verhältnis Nutzen/Aufwand.

## Direkt mit einplanen (kostet fast nichts, spart später Umbau)

1. **`blocking_reason` im Detail-Context.** Statt eines grauen Buttons: „Dein Team
   braucht mindestens 5 Spieler (aktuell: 4)" / „Anmeldung endet in 3 Tagen" /
   „Liga ist voll". Der Service wirft die Exceptions schon — die Gründe müssen nur
   *vor* dem Klick auch abfragbar sein.
2. **`requires_approval`-Flag jetzt schon ins Model** (Default `False`), auch wenn
   der Approval-Flow erst später kommt. Spart dir eine Migration und eine
   Statuswert-Erweiterung mitten im Betrieb.
3. **Reminder-Task** (2.5) — der Unterschied zwischen einer Liga mit 6 und einer
   mit 16 Teams ist meist nur, ob die Leute an die Deadline erinnert wurden.
4. **Countdown auf der Detail-Seite** bis Anmeldeschluss. Reines JS, 20 Zeilen,
   erzeugt Dringlichkeit.

## Naheliegende nächste Ausbaustufen

5. **Warteliste.** Wenn die Liga voll ist, kann sich ein Team auf `waitlisted`
   setzen. Zieht ein Team zurück, rückt das erste Wartelisten-Team automatisch nach
   und alle seine Spieler bekommen eine Notification. Der Status-Enum ist dafür
   schon vorbereitet.
6. **Eligibility-Regeln pro Liga.** Du hast das Fundament bereits gebaut, es wird
   nur nicht genutzt:
   - `require_verified_gamertag` → prüft `UserProfile.tracker_link` (dein
     R6-Tracker-Check validiert den schon gegen `r6.tracker.network`!)
   - `require_over_18` → `User.check_over_18` / `is_over_18()` existiert bereits
   - `region` muss zu `Team.region` passen

   Das sind drei Boolean-Felder und drei Zeilen im Guard — und du hast einen
   echten Ligabetrieb-Regelsatz statt nur einer Anmeldeliste.
7. **Regelwerk + Zustimmung.** `League.rules` (TextField) und
   `LeagueRegistration.rules_accepted_at`. Ein Pflicht-Häkchen im Anmelde-Formular.
   Wichtig, sobald es um Disqualifikationen geht.
8. **Seasons.** `Season` als FK über der Liga (`Season 1 → Division 1, Division 2`).
   Erlaubt Auf-/Abstieg und macht Historie sinnvoll durchsuchbar.

## Der große Brocken — und der eigentliche Kern der Plattform

9. **Matches, Fixtures & Tabelle.** Sobald Teams in einer Liga stehen, ist der
   nächste logische Schritt der Spielplan:
   - `Match` (league, home_team, away_team, scheduled_at, status, result)
   - Round-Robin-Generator, der beim `start_league()` den Spielplan erzeugt
   - Ergebnismeldung durch beide Teams + Bestätigung/Dispute
   - Tabelle (Punkte, Rundendifferenz) als annotierte Query

   **Und damit schließt sich der Kreis zu den TODOs in deinem Code:** in
   `UserProfile` stehen `# rounds_played if rounds are implemented` und
   `# win_rate if rounds are implemented`. Genau die Felder fallen aus einem
   Match-System heraus — und der `LeagueRosterEntry`-Snapshot ist die
   Voraussetzung dafür, dass du Statistiken sauber einem Spieler *in einer
   bestimmten Liga* zuordnen kannst.

10. **Check-in vor dem Matchday.** 30 Min vor Spielbeginn müssen 5 Spieler
    einchecken, sonst Walkover. Klassisches Turnier-Feature, und mit deinem
    Notification-System (Reminder → Push → Check-in-Button) sofort umsetzbar.

## Integrationen (später)

11. **Discord-Webhook** bei Anmeldung/Spielplan. Du speicherst `Team.discord` und
    `UserProfile.discord` bereits, und Discord ist als Social-Login schon
    konfiguriert.
12. **DRF-Endpoints** für Ligen, Teilnehmer, Tabelle. `drf-spectacular` läuft
    schon — Grundlage für ein späteres Overlay/Widget für Streamer.
13. **iCal-Export** des Spielplans.
14. **`broadcast`-Command erweitern**, sodass er auf eine Liga zielen kann
    (`--league=summer-cup`). Der Command existiert bereits für globale
    Broadcasts.

---

# Offene Punkte / Risiken

- **`select_for_update` + SQLite.** Der Race-Condition-Test läuft lokal nicht ohne
  Postgres. Entweder Docker-Compose für die Testläufe nutzen oder den Test
  Postgres-only markieren.
- **Was passiert mit Anmeldungen, wenn ein Team gelöscht wird?** `delete_team()`
  existiert in `teams/services.py`. Vorschlag: Löschen blockieren, solange das Team
  in einer laufenden Liga (`running`) steht — `TeamPermissionError` mit klarer
  Meldung. Muss explizit implementiert und getestet werden, sonst reißt der
  `CASCADE` mitten in der Saison Löcher in den Spielplan.
- **Spieler verlässt das Team nach der Anmeldung.** Der Roster-Snapshot bleibt
  bestehen (gewollt), aber der Kader kann unter `min_roster_size` fallen.
  Vorschlag fürs MVP: nur eine Notification an den Owner („Dein Kader ist unter die
  Mindestgröße für Liga X gefallen"), keine automatische Disqualifikation.
