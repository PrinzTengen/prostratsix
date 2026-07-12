# Review Lauf 2a — `notify_many` + LEAGUE-Kategorie

**Umfang:** `git diff f881291..HEAD` (`d28071f`, `111a3f6`, `0658ee2`)
**Reviewer:** Review-Agent (kein Code geändert)
**Testlauf:** `3 failed, 91 passed` — die 3 Fehlschläge sind die bekannten,
vorbestehenden `users/tests/test_views.py`-Fälle. Alle Notification-Tests grün.
**`makemigrations --check`:** `No changes detected`, Exit 0 — CI-sauber.
**`ruff check prostratsix/notifications/`:** `All checks passed`.

| Schwere | Anzahl |
| --- | --- |
| HIGH | 1 |
| MEDIUM | 1 |
| LOW | 4 |
| Positiv / kein Finding | 4 |

---

## Vorab: die Lazy-Load-Frage — **gemessen, der Verdacht bestätigt sich NICHT**

`notification.recipient.email` (services.py:118) löst **keinen** zusätzlichen Query aus,
auch nicht in Produktion. Grund: `Notification(recipient=user)` (services.py:86) schreibt
das User-Objekt über den `ForwardManyToOneDescriptor` in
`notification._state.fields_cache["recipient"]`. `bulk_create()` gibt **dieselben**
Instanzen zurück, die es bekommen hat — der Cache überlebt. Woher die User-Objekte kommen
(frisch gebaut vs. aus der DB geladen) ist dafür **irrelevant**, weil der Cache beim
Konstruieren der `Notification` gesetzt wird, nicht beim Laden des Users.

Gemessen mit einem Probe-Test, der die User **frisch aus der DB** lädt (`list(User.objects.all()[:20])`),
also genau das Produktionsszenario, das der Verdacht beschreibt:

```
users frisch aus der DB geladen, keine Preference vorhanden:
  20 User -> 3 Queries   (INSERT notifications, SELECT preferences, INSERT preferences)
   1 User -> 3 Queries
users frisch aus der DB, Preferences existieren bereits:
   5 User -> 2 Queries   (INSERT notifications, SELECT preferences)
```

Die Behauptung des Developers ist **korrekt**, und der committete Test
(`test_query_count_does_not_grow_with_the_number_of_users`) ist **kein Scheinriese**: er
würde eine echte N+1-Regression fangen, weil ein Lazy-Load pro Notification aus 3/3
Queries ein 4/23 machen würde und `assert len(for_twenty) == len(for_one)` bricht.

**Eine echte Lücke gibt es trotzdem** — siehe LOW-3: wenn ein Caller die User mit
`.only()`/`.defer()` lädt, ist `email` deferred und der N+1 kommt zurück. Gemessen:
`User.objects.only("id")`, 5 User → **8 Queries** statt 3.

---

## HIGH-1 — Race auf `NotificationPreference.bulk_create()` → `IntegrityError`, verliert die Notifications einer kompletten Liga lautlos

**`prostratsix/notifications/services.py:101–111`**

```python
preferences = {
    preference.user_id: preference
    for preference in NotificationPreference.objects.filter(user__in=users)   # <- SELECT
}
missing = [
    NotificationPreference(user=user)
    for user in users
    if user.pk not in preferences
]
for preference in NotificationPreference.objects.bulk_create(missing):        # <- INSERT
    preferences[preference.user_id] = preference
```

`NotificationPreference.user` ist ein `OneToOneField` (models.py:60) → `UNIQUE` auf
`user_id`. Zwischen dem `SELECT` und dem `bulk_create` liegt ein offenes Fenster. Fügt in
diesem Fenster **irgendwer** die Preference-Zeile eines der User ein, knallt das
`bulk_create`.

**Reproduziert (gemessen, SQLite):**

```
IntegrityError: UNIQUE constraint failed: notifications_notificationpreference.user_id
```

**Wer schreibt in diesem Fenster? Nicht nur ein zweites `notify_many`:**

1. **Ein ganz normaler Seitenaufruf.** `NotificationPreferenceView.get_object()`
   (views.py:112–116) macht `NotificationPreference.objects.get_or_create(user=...)`.
   Ein User, der während des Liga-Starts seine Benachrichtigungs-Einstellungen öffnet,
   reicht. Das ist kein exotischer Fall — das ist ein Klick.
2. **Zwei Ligen gleichzeitig.** Der 5-Minuten-Beat-Task (Gate-1 #4) startet zwei Ligen im
   selben Tick; ein Spieler ist in Teams beider Ligen und hat noch keine Preference.
3. **Zwei überlappende Task-Läufe** derselben Liga (Gate-1 #5 schützt die Liga-Zeile, nicht
   die Preference-Zeile).
4. **Eine Team-Registrierung parallel zum Liga-Start.**

**Was konkret passiert (Folgekette):**

Nach Gate-1 #2 wird `notify_many()` aus `transaction.on_commit(...)` heraus aufgerufen,
also **nachdem** die Liga committet ist. Die Exception in einem `on_commit`-Callback
propagiert aus dem äußeren `atomic()`-Exit heraus und bricht die restlichen Callbacks ab:

- Die Liga ist **gestartet und committet** — der Zustand ist nicht zurückrollbar.
- Der `bulk_create` der Notifications (Zeile 83) ist bereits durch → ein Teil der
  In-App-Notifications existiert, aber **keine einzige E-Mail** wurde enqueued (die
  `delay_on_commit`-Schleife ab Zeile 116 ist nie erreicht worden).
- Der Celery-Task schlägt fehl und wird retried → `start_league()` prüft nach dem Lock den
  Status (Gate-1 #5), sieht „läuft schon" und **kehrt sofort zurück**. `notify_many` wird
  **nie wieder** aufgerufen.
- Ergebnis: Liga läuft, ~100 Spieler bekommen **keine E-Mail**, niemand merkt es außer
  über einen Celery-Error im Log.

**Regression gegenüber `notify()`:** `notify()` benutzt `get_or_create()` (services.py:33).
Das fängt den `IntegrityError` intern ab und macht das `get` erneut — `notify()` ist
race-safe. `notify_many()` ist also **strikt unsicherer als die Funktion, die es ersetzt.**

**Vorschlag (empfohlen): die Preferences gar nicht erst schreiben.**

`notify_many()` **liest** die Preferences nur — es braucht die Zeile nicht. Ein frisch
konstruiertes `NotificationPreference()` hat exakt dieselben Defaults (alle `True`,
models.py:65–80) wie eine frisch angelegte Zeile. Die Zeile entsteht ohnehin von selbst,
sobald der User die Preferences-Seite öffnet (views.py:113).

```python
preferences = {
    preference.user_id: preference
    for preference in NotificationPreference.objects.filter(user__in=users)
}
# Users ohne Zeile: die Defaults gelten, wir schreiben nichts. Ein bulk_create()
# hier wäre ein Race gegen NotificationPreferenceView.get_object()'s get_or_create()
# — OneToOneField, also UNIQUE auf user_id.
defaults = NotificationPreference()

for notification in notifications:
    preference = preferences.get(notification.recipient_id, defaults)
    ...
```

Das entfernt das Race **vollständig**, spart einen Query (3 → 2) und ist
verhaltensidentisch. `MAX_NOTIFY_MANY_QUERIES` im Test wäre dann `2`.

**Vorschlag (falls das Schreiben aus einem Grund gewollt ist, den ich nicht sehe):**
`bulk_create(missing, ignore_conflicts=True)`. Zwei Nebenwirkungen, die dann in einen
Kommentar müssen: (a) Django liefert bei `ignore_conflicts` **keine PKs** zurück, und
(b) die Zeile, die das Race gewonnen hat, wird **nicht** nachgeladen — ein User, der genau
in diesem Fenster `email_league=False` gesetzt hat, bekommt die eine Mail trotzdem.

---

## MEDIUM-2 — `notify()` benutzt `.delay()`, `notify_many()` `.delay_on_commit()`, und `services.py` erklärt den Unterschied an keiner Stelle

**`prostratsix/notifications/services.py:38` vs. `:119`**

Dass `notify()` noch `.delay()` benutzt, ist die bekannte, bewusst aufgeschobene Altlast
(Plan 2.10, „Bekannter Altlast-Fund"). **Neu ist die Inkonsistenz im selben File**, zwölf
Zeilen auseinander:

```python
def notify(...):
    ...
            send_notification_email.delay(notification.id)          # Zeile 38

def notify_many(...):
    """... E-mails are enqueued with ``delay_on_commit()`` rather than ``delay()``:
    callers run inside ``transaction.atomic()``, and a Celery worker picking the
    task up before that transaction commits would not see the notification row
    yet. ``send_notification_email`` swallows the resulting ``DoesNotExist``, so
    the e-mail would go missing without a trace."""                 # Zeilen 73–77
    ...
            send_notification_email.delay_on_commit(notification.id)  # Zeile 119
```

Der Docstring von `notify_many` formuliert das Argument **als allgemeine Regel** — und
genau dieselbe Regel gilt für `notify()`, das ebenfalls aus `@transaction.atomic`-Funktionen
der `teams`-App gerufen wird. Ein Leser hat damit exakt zwei Schlüsse zur Auswahl, und
beide sind falsch:

1. „Zeile 38 ist ein Bug, den der Docstring drüber selbst beschreibt" → er ändert `notify()`
   auf `delay_on_commit()`, ohne die `django_capture_on_commit_callbacks`-Arbeit in den
   bestehenden Tests zu machen → `TestNotify`, `TestSignals` und die `teams`-Tests werden
   rot, und er dreht es genervt wieder zurück.
2. „Zwei Funktionen, zwei Enqueue-Arten — das ist Schlamperei" → er „vereinheitlicht"
   `notify_many` **zurück auf `.delay()`** und macht damit genau den Fehler rückgängig, den
   Gate-1 #2 explizit verboten hat.

Die Entscheidung ist heute **nur** in einem Test-Docstring festgehalten
(`test_notifications.py:77–84`, `test_sends_without_an_on_commit_hook`) und im Plan. Wer
`services.py` liest, sieht davon nichts.

**Vorschlag:** ein Kommentar direkt an Zeile 38, wo der Leser stolpert:

```python
            # Known debt, NOT an oversight: this should be delay_on_commit() too.
            # notify() is called from teams' @transaction.atomic services, so the
            # same lost-e-mail risk exists here today — see notify_many() below for
            # the full argument. Changing it means reworking every existing test to
            # use django_capture_on_commit_callbacks; own ticket, deliberately
            # deferred (plan 2.10). Do not "unify" notify_many back to delay().
            send_notification_email.delay(notification.id)
```

Und im `notify_many`-Docstring (Zeile 73) ein Halbsatz, der klarstellt, dass `notify()`
bewusst noch nicht nachgezogen ist.

---

## LOW-3 — Die Query-Garantie hängt an einer undokumentierten Caller-Bedingung: vollständig geladene User-Objekte

**`prostratsix/notifications/services.py:118`**

```python
if notification.recipient.email and preference.allows_email(category):
```

Kein Lazy-Load, solange `users` echte, vollständig geladene `User`-Instanzen sind (s. o.,
gemessen). **Aber:** lädt ein Caller die Spieler mit `.only()` / `.defer()` — die
naheliegende „Optimierung" für einen Liga-Start mit 100 Usern — ist `email` ein deferred
Field, und der Zugriff darauf feuert pro Notification einen Query.

**Gemessen:** `User.objects.only("id")`, 5 User → **8 Queries** (statt 3). Bei 100 Spielern:
**103 Queries**. Der Test fängt das nicht, weil er `UserFactory` benutzt.

Dazu kommt: die Formulierung `notification.recipient.email` **sieht aus wie** ein
Lazy-Load, obwohl sie keiner ist — sie war der Grund, warum dieser Verdacht überhaupt
aufkam. Das `user`-Objekt liegt bereits in der Hand.

**Vorschlag:** die Schleife über beide Listen führen, dann ist auf einen Blick sichtbar,
dass kein FK aufgelöst wird, und `strict=True` sichert nebenbei ab, dass `bulk_create` die
Reihenfolge erhalten hat:

```python
for user, notification in zip(users, notifications, strict=True):
    preference = preferences.get(user.pk, defaults)
    if user.email and preference.allows_email(category):
        send_notification_email.delay_on_commit(notification.id)
```

Plus eine Zeile im Docstring: *„`users` must be fully loaded `User` instances — a
`.only()`/`.defer()` queryset without `email` re-introduces the per-user query this
function exists to avoid."*

---

## LOW-4 — Preference-Zeilen werden auch für User ohne E-Mail-Adresse angelegt

**`prostratsix/notifications/services.py:105–109`**

`missing` wird aus **allen** Usern gebaut, ungefiltert. `notify()` fasst die Preferences
dagegen nur an, wenn der User überhaupt eine E-Mail-Adresse hat (`if send_email and
user.email:`, services.py:32). `notify_many()` schreibt also Zeilen, die `notify()` nie
schreiben würde, für User, die niemals eine Mail bekommen können — und bläht den
`bulk_create` unnötig auf.

Harmlos, aber eine stille Verhaltensdivergenz zwischen zwei Funktionen, die sonst
deckungsgleich sein sollen. **Erledigt sich vollständig, wenn HIGH-1 wie vorgeschlagen
gelöst wird** (dann wird gar nichts mehr geschrieben).

---

## LOW-5 — Fehlende Testfälle

Der Testsatz ist überdurchschnittlich gut (Guards für leere Liste, `send_email=False`,
Opt-out, User ohne E-Mail, PK/`created_at`, Query-Count, Form-Feld auf der Seite). Es
fehlen konkret:

1. **Der `missing == []`-Pfad** — alle User haben bereits eine Preference. Das ist der
   Pfad, der bei der **zweiten** Liga eines Users läuft, also der Normalfall im Betrieb, und
   er ist nie exerziert. `test_respects_the_league_opt_out` deckt nur den gemischten Fall
   (1 vorhanden, 1 fehlend) ab. Gemessen wären das 2 statt 3 Queries — d. h. der Fall bricht
   heute auch die Prämisse von `MAX_NOTIFY_MANY_QUERIES`, ohne dass ein Test es zeigt.
2. **Der Race selbst** (HIGH-1). Deterministisch testbar ohne Threads: die Preference-Zeile
   eines Users **nach** dem `filter()` und **vor** dem `bulk_create()` einfügen — z. B. per
   `mock.patch` auf `NotificationPreference.objects.filter`, oder schlicht als Unit-Test auf
   dem `bulk_create`-Aufruf. Nach dem Fix aus HIGH-1 wäre der passende Test: „ein User, der
   während des Aufrufs eine Preference-Zeile bekommt, sprengt `notify_many` nicht".
3. **Die Nebenwirkung selbst ist ungetestet.** Kein Test behauptet, dass `notify_many()`
   `NotificationPreference`-Zeilen anlegt (bzw. — nach dem Fix — dass es das **nicht** tut).
   Das Verhalten ist damit weder festgeschrieben noch bewusst offengelassen.
4. **`test_query_count_does_not_grow_with_the_number_of_users`** verlässt sich stillschweigend
   darauf, dass **keine** der beiden Gruppen eine Preference hat. Legt jemand später eine
   Fixture an, die Preferences erzeugt, fallen beide Zahlen auf 2, der Test bleibt grün —
   testet dann aber den `bulk_create`-Pfad gar nicht mehr. Ein Satz im Docstring genügt.
5. **`allows_email()` Default-Fall** (`category` ist keine der drei Sonderkategorien →
   `email_general`) ist nirgends direkt getestet. Vorbestehende Lücke, mit LEAGUE
   mitgespiegelt, aber jetzt mit vier Zweigen billiger zu treffen: ein Unit-Test über alle
   vier Kategorien + einen unbekannten String.

---

## Kein Finding — geprüft und in Ordnung

**Spiegelung von `1ba61aa` ist vollständig.** Alle vier Stellen, an denen `TEAM` vorkommt,
haben ein `LEAGUE`-Pendant bekommen: `admin.py` (`list_display`), `forms.py` (`fields`),
`models.py` (Choice + `email_league`-Feld + `allows_email`-Zweig), plus die Migration.
`templates/notifications/preferences.html` rendert `{{ form|crispy }}` und braucht deshalb
**keine** Änderung (der Plan verlangt sie in 2.1, sie ist faktisch unnötig) —
`test_page_offers_the_league_opt_out` schreibt genau das fest. `NotificationAdmin.list_filter`
ist choices-getrieben und zieht automatisch nach. **Nichts vergessen.**

**`category` beim `allows_email()`-Aufruf ist konsistent.** Zeile 118 übergibt den
Funktionsparameter `category`, und genau der wurde auf jeder Notification gespeichert
(Zeile 90). Kein Auseinanderlaufen möglich. (`notification.category` wäre marginal
robuster, ist aber Kosmetik.)

**`delay_on_commit()` außerhalb von `atomic()`** feuert korrekt sofort: Djangos
`transaction.on_commit()` führt den Callback im Autocommit-Modus direkt aus. Bestätigt
durch `test_sends_an_email_to_every_user` (grün).

**Migration ist korrekt und vollständig.** `makemigrations --check` meldet
`No changes detected`, Exit 0. Die Migration hängt korrekt an `0002_..._email_team_...`, legt
`email_league` an und aktualisiert die `choices` auf `Notification.category`.

**Doku im Diff ist gut** und geht über das Übliche hinaus: der `notify_many`-Docstring
erklärt **wann** man es statt `notify()` nimmt (Zeilen 56–59), warnt vor dem
`bulk_create`/`post_save`-Fallstrick (61–66, inkl. Verweis auf `signals.py`) und hält die
Begründung für `delay_on_commit` fest (73–77). Die Tests erklären
`django_capture_on_commit_callbacks` an einer Stelle ausführlich und verweisen von den
drei anderen dorthin. Einzige Lücke ist MEDIUM-2 (die Gegenseite, `notify()`, schweigt).

**`notify()` sollte NICHT intern `notify_many([user])` aufrufen.** Geprüft und bewusst
abgelehnt: es würde jeden bestehenden Caller (allauth-Signals, `teams`) klammheimlich von
`.delay()` auf `.delay_on_commit()` umstellen — also die Altlast-Entscheidung aus Gate-1
durch die Hintertür kippen und rund ein Dutzend bestehende Tests rot färben —, den
Rückgabetyp brechen (`Notification` vs. `list[Notification]`) und den 1-User-Fall von 2 auf
3 Queries heben. Die Duplikation der Signatur ist der günstigere Preis. Getrennt lassen.
