# Review: Phase 1 — htmx-Notification-Bell (Lauf 1)

Stand: 2026-07-12 · Branch: `feat/league-settings` · Umfang: `git diff 8722ff3..HEAD`
(6 Commits, `3265d6d` … `fcfb0fd`) · Bezug: [`../leagues-and-htmx.md`](../leagues-and-htmx.md)
(Abschnitt 1.1–1.8), [`../research/phase-1-htmx.md`](../research/phase-1-htmx.md)

## Gesamteindruck

**Nichts Kritisches.** Der Kern der Umsetzung ist korrekt, und zwar auch dort, wo der
Research-Brief die größten Fallstricke verortet hat: der Bootstrap-Dispose-Handler
greift tatsächlich (belegt unten), die `hx-trigger`-Syntax ist gültiges htmx 2, die
Self-Swap-Kette bricht nicht ab, CSRF und Autorisierung sind sauber, `ruff` und
`djlint` sind grün, 13/13 Notification-Tests laufen durch.

Ein Punkt aus den **verbindlichen Gate-1-Entscheidungen (1.8) ist nur zur Hälfte
umgesetzt** (Finding 1). Dazu kommen eine unnötige Verdoppelung der DB-Last auf dem
Polling-Pfad (Finding 2) und eine fehlende Regression, die genau den vom Brief
vorhergesagten stillen Fehlermodus absichern würde (Finding 3). Der Rest sind
Kleinigkeiten.

## Testergebnis

```
uv sync --no-install-package psycopg --no-install-package psycopg-c
DATABASE_URL=sqlite://:memory: .venv/Scripts/python -m pytest --no-migrations --ds=config.settings.test
```

| | |
|---|---|
| Gesamt | **78 passed, 3 failed** |
| `prostratsix/notifications` | **13 passed, 0 failed** (2 Bestand + 11 neu) |
| `ruff check prostratsix/notifications/` | All checks passed |
| `djlint` (bell.html, base.html, offcanvas.html) | 0 errors |

Die 3 Fehlschläge liegen alle in `prostratsix/users/tests/test_views.py`
(`TestUserUpdateView::test_get_success_url`, `TestUserRedirectView::test_redirects_to_detail_when_profile_complete`,
`TestUserDetailView::test_authenticated`) und sind **vorbestehend** — der Diff fasst die
`users`-App nicht an. Ursache in einem Satz: die Views wurden auf ein
`uuid`-URL-Kwarg umgestellt (vgl. `{% url 'users:detail' request.user.uuid %}`), die
Tests rufen `UserDetailView` aber weiterhin ohne `uuid=` per `RequestFactory` auf, worauf
Djangos `SingleObjectMixin.get_object()` mit
`AttributeError: … must be called with either an object pk or a slug` abbricht.

---

# Findings

## Kritisch

Keine.

---

## Hoch

### 1. Gate-1-Entscheidung 1.8 Nr. 3 nur zur Hälfte umgesetzt — das Dropdown klappt beim Tab-Wechsel doch zu

**`prostratsix/templates/notifications/partials/bell.html:9`**

Der Dropdown-Guard hängt nur am Polling-Trigger, nicht an den beiden anderen:

```
every 30s [document.visibilityState === 'visible' && !document.querySelector('#notification-bell .dropdown-menu.show')],
visibilitychange[document.visibilityState === 'visible'] from:document,     <-- kein Guard
notifications:refresh from:body                                             <-- kein Guard
```

Plan 1.8 Nr. 3 ist verbindlich formuliert: *„Nicht swappen, solange das Dropdown offen
ist."* Umgesetzt ist das nur für `every 30s`. Ausgerechnet der `visibilitychange`-Trigger,
der laut Plan 1.8 Nr. 2 überhaupt erst dazukam, um beim Zurückkommen in den Tab sofort zu
aktualisieren, feuert ungefiltert — und das ist genau der Moment, in dem ein Nutzer
typischerweise ein offenes Dropdown hat.

**Reproduktion**

1. Als eingeloggter Nutzer die Glocke im Offcanvas-Menü aufklappen, Liste steht offen.
2. In einen anderen Browser-Tab (oder eine andere Anwendung) wechseln.
3. Zurück in den ProStratSix-Tab wechseln.
4. → `visibilitychange` feuert, htmx tauscht `<li id="notification-bell">` per `outerHTML`
   aus, das offene `.dropdown-menu.show` verschwindet kommentarlos. Der Nutzer schaut auf
   ein zugeklapptes Menü und muss neu klicken.

Der 30s-Tick ist gegen genau dieses Verhalten abgesichert, der Weg über den Tab-Wechsel
nicht. `notifications:refresh from:body` hat dasselbe Loch, ist aktuell aber unerreichbar
(s. Finding 10).

**Vorschlag**

Denselben Guard an die anderen beiden Trigger hängen. Die Bedingung ist dreimal identisch
und lang — sinnvollerweise vorher einmal in eine Template-Variable ziehen, damit sie nicht
dreimal per Copy-Paste driftet, z. B.:

```django
{% with bell_open="!document.querySelector('#notification-bell .dropdown-menu.show')" %}
```

und dann in allen drei Trigger-Klauseln verwenden — bzw. schlicht ausschreiben:

```
visibilitychange[document.visibilityState === 'visible' && !document.querySelector('#notification-bell .dropdown-menu.show')] from:document,
notifications:refresh[!document.querySelector('#notification-bell .dropdown-menu.show')] from:body
```

Beides ist von htmx 2 gedeckt: der Filter `[...]` steht bei einem Event-Trigger **vor** den
Modifiern (`from:`), genau so wie jetzt schon (`node_modules/htmx.org/dist/htmx.js:2254-2262`).

*(Falls die Entscheidung bewusst revidiert wurde — „beim Tab-Wechsel ist ein Refresh
wichtiger als ein offenes Menü" wäre vertretbar —, gehört das als Notiz in den Plan,
sonst liest der nächste Leser 1.8 Nr. 3 als erfüllt.)*

---

## Mittel

### 2. Jede Bell-Response macht 4 statt 2 Notification-Queries; `bell_context()` ist vollständig redundant, und sein Docstring behauptet das Gegenteil

**`prostratsix/notifications/views.py:20-34`** (+ `prostratsix/notifications/context_processors.py:5-13`, Bestand)

`bell_context()` berechnet exakt dieselben zwei Keys, die der bereits registrierte
Context-Processor `prostratsix.notifications.context_processors.unread_notifications`
(`config/settings/base.py:198`) ohnehin bei **jedem** Template-Render liefert — inklusive
identischem Limit:

| | Context-Processor | `bell_context()` |
|---|---|---|
| `unread_notifications_count` | `qs.unread().count()` | `qs.unread().count()` |
| `recent_notifications` | `list(qs[:5])` | `list(qs[:BELL_ITEM_LIMIT])` (= 5) |

Context-Processors laufen auch für `TemplateView` und für `render()` (`RequestContext.bind_template`),
also auch für `NotificationBellView` und `BellResponseMixin.render_bell`. Beide Quellen
werden folglich pro Request ausgeführt; nur die Werte der View gewinnen anschließend beim
Lookup. Der Docstring in `views.py:25-28` — *„Deliberately computed here instead of read
from the `unread_notifications` context processor, so the partial renders the same way
whether it is polled on its own or embedded in a full page"* — ist **sachlich falsch**: der
Context-Processor deckt beide Fälle bereits ab, auch nach der Mutation in `post()` (er
läuft zur Render-Zeit, sieht also das frisch gesetzte `is_read=True`).

**Reproduktion / Messung** (`CaptureQueriesContext` auf `GET /notifications/bell/`):

```
--- notification queries on ONE bell poll: 4
    SELECT COUNT(*) … FROM "notifications_notification" WHERE recipient_id = … AND is_read = 0
    SELECT … FROM "notifications_notification" WHERE recipient_id = … ORDER BY created_at DESC LIMIT 5
    SELECT COUNT(*) … FROM "notifications_notification" WHERE recipient_id = … AND is_read = 0   <-- doppelt
    SELECT … FROM "notifications_notification" WHERE recipient_id = … ORDER BY created_at DESC LIMIT 5  <-- doppelt
--- total queries: 8
```

Konkrete Folge: jeder eingeloggte Tab feuert 2 Polls/Minute. Bei 200 gleichzeitig offenen
Tabs sind das 400 Requests/Minute × 4 statt × 2 Notification-Queries — 800 vermeidbare
Queries pro Minute, dauerhaft, für Daten, die schon im Kontext liegen. Kein N+1 im
klassischen Sinn (die Anzahl skaliert nicht mit der Notification-Anzahl), aber ein glatter
Faktor 2 auf dem heißesten Pfad der App.

**Vorschlag**

`bell_context()` ersatzlos streichen; `NotificationBellView` wird zum reinen
`TemplateView` mit `template_name = BELL_TEMPLATE` ohne `get_context_data`, und
`BellResponseMixin.render_bell()` zu `render(request, BELL_TEMPLATE)`. `BELL_ITEM_LIMIT`
wandert in `context_processors.py` und ersetzt dort die `5` — damit gibt es das Limit
genau einmal. Die vorhandenen Tests (`response.context["unread_notifications_count"]`,
`response.context["recent_notifications"]`) laufen unverändert weiter, weil der
Context-Processor dieselben Keys setzt.

### 3. Kein Test sichert die Polling-Kette ab — genau der stille Fehlermodus, den der Brief vorhergesagt hat

**`prostratsix/notifications/tests/test_notifications.py:79-175`**

Der Research-Brief warnt explizit (Abschnitt 3, *„Trigger geht beim Self-Swap NICHT
verloren"*): *„vorausgesetzt, der Server rendert das `hx-trigger`-Attribut in jeder
Response identisch mit (Copy-Paste-Fehler: eine spätere Änderung an `bell.html`, die das
Attribut nur ‚beim ersten Laden' setzt, würde die Kette lautlos beenden, ohne Fehler in der
Konsole)."* Das Template macht es heute richtig — es gibt kein `{% if %}` um das `<li>`,
und der Kommentar in `bell.html:4-5` warnt davor. Aber **kein einziger Test prüft es**.

Konkret ungeprüft: Wenn jemand in sechs Monaten das `<li>` in ein
`{% if unread_notifications_count %}` einwickelt (naheliegend: „Glocke ausblenden, wenn
nichts da ist"), bleibt die Suite grün, das Polling stoppt nach dem ersten Swap, und der
Fehler ist im Browser nur mit dem Network-Tab zu sehen — es gibt keine Exception, keine
Konsolenmeldung, kein 500er.

Ebenfalls ungeprüft: Antwortet die View versehentlich mit HTTP 286, bricht htmx das
Polling endgültig ab (Brief, Abschnitt 3).

**Vorschlag**

Ein Test, der die Invariante festnagelt — und zwar für **alle drei** Responses, die im
Bell-Target landen können (Bell-Poll, Mark-Read-htmx, Mark-All-Read-htmx), denn alle drei
werden per `hx-swap="outerHTML"` über `#notification-bell` gelegt:

```python
@pytest.mark.parametrize(
    ("url_name", "method"),
    [("bell", "get"), ...],
)
def test_every_bell_response_keeps_the_polling_attributes(...):
    ...
    body = response.content.decode()
    assert response.status_code == 200          # kein 286 -> htmx würde Polling abbrechen
    assert 'id="notification-bell"' in body
    assert "hx-get=" in body
    assert "hx-trigger=" in body
    assert "every 30s" in body
    assert 'hx-swap="outerHTML"' in body
```

Der Test sollte auch für den Fall **ohne** Notifications laufen (`unread_notifications_count == 0`,
leere Liste) — das ist der Zustand, in dem ein „Glocke ausblenden"-Refactor die Attribute
verlieren würde.

---

## Niedrig

### 4. Bei jedem Swap wachsen Whitespace-Textknoten unbegrenzt in der Navbar

**`prostratsix/templates/notifications/partials/bell.html:1-5`**

Das Partial beginnt mit `{% load i18n %}`, einer Leerzeile, `{% url … as … %}` und einem
zweizeiligen Kommentar. Alle vier rendern zu leerem String, die Zeilenumbrüche bleiben — die
HTTP-Response beginnt also mit rund vier Newlines vor `<li>`.

htmx parst die Response zu einem Fragment und fügt bei `outerHTML` **alle** Kindknoten des
Fragments ein, inklusive Textknoten (`node_modules/htmx.org/dist/htmx.js:1606-1616`:
`insertNodesBefore` prüft `nodeType` erst *nach* dem `insertBefore`, nur um den Settle-Task
zu überspringen). Entfernt wird anschließend ausschließlich `target`, also das alte `<li>`
(`htmx.js:1723-1724`). Der führende Whitespace-Textknoten jeder Response bleibt somit dauerhaft
im `<ul class="navbar-nav">` zurück.

**Konkrete Folge:** 2 Swaps/Minute → nach einer 8-Stunden-Session hängen ~960 leere
Textknoten zwischen den `<li>`-Elementen der Navbar. Visuell und funktional harmlos (in
einem Flex-Container erzeugen reine Whitespace-Textknoten keine anonymen Flex-Items), aber
es ist unbegrenztes DOM-Wachstum in genau der Komponente, für die Commit `2545694` extra
einen Dispose-Handler gegen unbegrenztes Wachstum eingebaut hat. Die Response ist außerdem
pro Poll ein paar Bytes größer als nötig.

**Vorschlag**

Die Präambel des Partials whitespace-frei machen, z. B. `{% load i18n %}{% url 'notifications:list' as notifications_url %}`
direkt an das `<li>` heranziehen (djlint bleibt grün, geprüft), oder den Kommentar hinter
das öffnende `<li>`-Tag verschieben. Alternativ am Server `hx-select="#notification-bell"`
setzen, damit htmx nur das gewünschte Element aus der Response zieht.

### 5. Fehlendes CSRF-Feld sendet den Header als String `"undefined"` statt ihn wegzulassen

**`prostratsix/static/js/project.js:18-22`**

```js
event.detail.headers["X-CSRFToken"] = document.querySelector(
    "[name=csrfmiddlewaretoken]",
)?.value;
```

Findet der Selector nichts, ist der Wert `undefined`. htmx setzt Header nur bei
`value !== null` — `undefined` passiert diese Prüfung und landet als
`X-CSRFToken: undefined` im Request.

**Konkrete Folge:** heute kein Problem — `{% csrf_token %}` steht in `base.html:60`,
`{% block navbar %}` wird nirgends überschrieben, und jedes Template unter
`prostratsix/templates/` außer `base.html` selbst extended `base.html` (geprüft). Der
Header ist also immer korrekt gefüllt. Es ist eine Absicherung für die Zukunft: schleicht
sich irgendwann ein Standalone-Template ohne `base.html` ein, das htmx nutzt, ist der
Fehler ein 403 mit einem Header, der *aussieht*, als wäre er gesetzt — genau die stille
Verwirrung, vor der der Kommentar direkt darüber warnt.

**Vorschlag**

```js
const token = document.querySelector("[name=csrfmiddlewaretoken]")?.value;
if (token) {
    event.detail.headers["X-CSRFToken"] = token;
}
```

### 6. `MarkReadView` und `MarkAllReadView` haben keinen Docstring, obwohl sie jetzt zwei verschiedene Dinge zurückgeben

**`prostratsix/notifications/views.py:73`, `prostratsix/notifications/views.py:89`**

Beide Views geben seit `36605f0` je nach `HX-Request`-Header **entweder** das Bell-Partial
(200) **oder** einen Redirect (302) zurück. `NotificationBellView` und `BellResponseMixin`
haben Docstrings, die genau das erklären — die beiden Views, in denen die Verzweigung
tatsächlich passiert, haben keinen. Wer `MarkReadView` liest, um den Redirect-Pfad zu
verstehen, sieht die htmx-Sonderbehandlung erst in Zeile 3 des Bodys.

**Vorschlag:** je ein Zweizeiler nach dem Muster der anderen neuen Views, der beide
Rückgabewerte und ihre Bedingung nennt.

### 7. Kommentar im Produktions-Dockerfile ist durch den Diff veraltet

**`compose/production/django/Dockerfile:43-44`**

```dockerfile
# Compiles assets/scss into prostratsix/static/css/project.css (minified) and
# copies the Bootstrap JS bundle into prostratsix/static/js/vendors.js.
RUN npm run build
```

`npm run build` kopiert seit `3265d6d` zusätzlich `htmx.js` (`build = series(parallel(styles, vendorScripts, htmxScript))`).
Der Kommentar zählt die Build-Artefakte auf und ist damit jetzt unvollständig.

*Kein Deployment-Bug:* `Dockerfile:96` macht `COPY --from=client-builder … ${APP_HOME}/prostratsix/static
${APP_HOME}/prostratsix/static` — also das ganze Verzeichnis. `htmx.js` landet im
Prod-Image, und `collectstatic`/`ManifestStaticFilesStorage` findet die Datei. Geprüft.

**Vorschlag:** `… and copies the Bootstrap and htmx JS bundles into prostratsix/static/js/.`

### 8. README erwähnt htmx als Build-Artefakt, sagt aber nirgends, wofür es da ist

**`README.md:42`, `README.md:47-58`**

Alle Behauptungen im geänderten Abschnitt wurden gegen `gulpfile.js` und `package.json`
geprüft und stimmen:

| Behauptung | Prüfung |
|---|---|
| `npm install` lädt Bootstrap, htmx und Build-Tooling | ✅ `package.json` `dependencies.htmx.org: ^2.0.10` |
| `npm run build` erzeugt `htmx.js` | ✅ `"build": "gulp build"` → `parallel(styles, vendorScripts, htmxScript)` |
| `npm run dev` baut vorher | ✅ `dev = series(build, serve)` |
| `htmx.js` ist generiert, nicht committed | ✅ `.gitignore:60` |
| `NODE_ENV=production` → minified | ⚠️ gilt nur für CSS (`cssnano`); `htmx.js`/`vendors.js` sind immer die vorminifizierten dist-Dateien. War für `vendors.js` schon vorher so formuliert — keine neue Falschaussage, aber auch keine ganz saubere. |

Was fehlt: die Überschrift heißt weiterhin `### Frontend / Styling (Bootstrap + SCSS theme)`,
und im gesamten README steht kein Satz dazu, **wozu** htmx im Projekt dient. Jemand, der
das Projekt nach dem README aufsetzt, bekommt es zwar korrekt gebaut, weiß aber nicht, dass
`project.js` jetzt die CSRF-Verdrahtung für htmx enthält und dass ein fehlender Gulp-Build
die Notification-Glocke stumm schaltet (kein sichtbarer Fehler, nur ein 404 auf
`/static/js/htmx.js` in der Konsole).

**Vorschlag:** Überschrift auf `### Frontend (Bootstrap + SCSS theme + htmx)` und zwei Sätze
dazu, dass htmx die Navbar-Glocke pollt und dass `project.js` den CSRF-Header (`X-CSRFToken`,
aus dem Hidden-Input, weil `CSRF_COOKIE_HTTPONLY = True`) sowie den Bootstrap-Dispose-Handler
registriert.

### 9. Open Redirect in `MarkReadView` (Bestand) — der Diff schafft dafür eine zweite Oberfläche

**`prostratsix/notifications/views.py:85`** (Bestand), **`prostratsix/templates/notifications/partials/bell.html:29`** (neu)

`redirect(notification.url)` leitet ungeprüft auf ein `URLField` weiter; das neue Dropdown
rendert dieselbe URL zusätzlich als `href`. **Heute nicht ausnutzbar**: `notify()` wird
ausschließlich intern aufgerufen (Signals, `teams`-Services) und es gibt keinen Pfad, über
den ein Nutzer `Notification.url` setzen kann. Das Feld hat aber keinerlei Host-Beschränkung
— sobald irgendwann eine View entsteht, die `notify(url=…)` mit einem vom Nutzer gelieferten
Wert aufruft, ist der Open Redirect da, und `MarkReadView` ist ein POST-Endpoint mit
Login-Zwang, also ein glaubwürdiger Phishing-Absprung.

**Vorschlag** (Entscheidung liegt beim Developer, da Bestand): in `MarkReadView` vor dem
Redirect `django.utils.http.url_has_allowed_host_and_scheme(notification.url, allowed_hosts={request.get_host()})`
prüfen und sonst auf `notifications:list` fallen; bzw. das `href` im Partial analog absichern.
Wenn externe Ziele gewollt sind, gehört das als Kommentar ans `URLField`.

### 10. Weitere Testlücken

**`prostratsix/notifications/tests/test_notifications.py`**

Die 11 neuen Tests prüfen echtes Verhalten (Counts, Template-Auswahl, Redirect-Ziel,
Fremd-Notification → 404), nicht nur „crasht nicht" — das ist gut. Es fehlen aber:

| Fehlender Fall | Warum er zählt |
|---|---|
| `MarkReadView` mit `HX-Request` auf **fremde** Notification | Der 404-Test deckt nur den Nicht-htmx-Pfad ab. `get_object_or_404` steht zwar vor der Verzweigung, aber genau diese Reihenfolge ist ungetestet — ein Refactor, der `is_htmx()` nach vorne zieht, bliebe grün. |
| `MarkAllReadView` markiert **nicht** die Notifications anderer User | `for_user(request.user).unread().update(...)` ist korrekt, aber die Isolation ist nirgends festgeschrieben. Ein verlorenes `.for_user()` bliebe grün. |
| Leerzustand („No notifications") | Der `{% empty %}`-Zweig in `bell.html:44-46` wird von keinem Test gerendert. |
| Voller Seiten-Render mit Glocke (`GET /` als eingeloggter User) | Der Include-Pfad über `offcanvas.html` ist ungetestet; nur der Standalone-Poll wird gerendert. Genau hier liegt die Kopplung an den Context-Processor aus Finding 2. |
| `notifications:refresh from:body` | Der Trigger ist verdrahtet (`bell.html:9`), aber **keine View setzt bisher einen `HX-Trigger`-Response-Header**. Der Pfad ist also weder erreichbar noch getestet — laut Brief 2.7 bewusst für spätere Phasen vorbereitet. Sollte im Plan als „verdrahtet, noch nicht aktiv" vermerkt sein, damit niemand annimmt, es sei erprobt. |
| JS (`project.js`) | Kein JS-Test-Setup im Projekt — akzeptabel, aber die beiden neuen Handler sind der riskanteste Teil des Diffs und ausschließlich manuell verifizierbar. |

---

# Explizit geprüft — und in Ordnung

Diese Punkte standen im Verdacht und halten der Prüfung am Quellcode stand. Belege, damit
sie nicht ein zweites Mal untersucht werden müssen.

### Der Dispose-Handler greift. Kein Memory-Leak.

**`prostratsix/static/js/project.js:31-33`** — der Verdacht war, dass `event.detail.elt` das
`<li>` ist, Bootstrap die Instanz aber am `<a data-bs-toggle="dropdown">` registriert, und
der Handler damit ins Leere greift. Beides stimmt einzeln, die Schlussfolgerung nicht:

1. `htmx.js:1685-1690` — `cleanUpElement()` feuert `htmx:beforeCleanupElement` **und ruft
   sich rekursiv für `element.children` auf**:
   ```js
   function cleanUpElement(element) {
     triggerEvent(element, 'htmx:beforeCleanupElement')
     deInitNode(element)
     forEach(element.children, function(child) { cleanUpElement(child) })
   }
   ```
2. `htmx.js:1723` — `swapOuterHTML()` ruft `cleanUpElement(target)` auf dem alten `<li>` auf,
   *bevor* `target.remove()` läuft. Die Rekursion erreicht also jedes Kind, inklusive des
   `<a data-bs-toggle="dropdown">`.
3. `htmx.js:3102-3108` — `triggerEvent()` setzt `detail.elt = elt` (das jeweilige Element)
   und `htmx.js:3044-3048` — `makeEvent()` erzeugt das Event mit `bubbles: true`. Jedes
   dieser Events blubbert bis `document.body`, wo der Handler hängt.

⇒ Der Handler bekommt **pro Element im entfernten Teilbaum** ein Event, darunter das `<a>`.
`bootstrap.Dropdown.getInstance(<a>)` liefert dort die Instanz (Registry-Key ist das
Element, an dem `data-bs-toggle` sitzt, `node_modules/bootstrap/js/dist/dom/data.js:23`:
`const elementMap = new Map()` — bestätigt: `Map`, kein `WeakMap`), `?.dispose()` räumt
Popper + Registry-Eintrag ab. Für alle anderen Kinder (`<ul>`, `<li>`, `<button>`) gibt
`getInstance()` `null` zurück → No-Op. **Der Fix aus Brief 2.5 / Gate-1 Nr. 1 ist korrekt umgesetzt.**

*(Anmerkung, kein Finding: der Handler deckt nur `Dropdown` ab. Kommen später Tooltips,
Popovers oder Modals in geswapptes Markup, brauchen die je eine eigene Zeile.)*

### `hx-trigger` ist gültiges htmx 2 und die Bedingung ist logisch korrekt

**`bell.html:9`** — `every 30s [<filter>]` ist gedeckt: `htmx.js:2243-2253` parst nach dem
Token `every` das Intervall und ruft dann `maybeGenerateConditional()` für den `[…]`-Filter.
Der Tokenizer (`htmx.js:2103-2132`, `STRINGISH_START = ['"', "'", '/']`) behandelt
`'visible'` und `'#notification-bell .dropdown-menu.show'` korrekt als String-Token, der
Selektor mit `#`, `.` und Leerzeichen wird nicht zerlegt. Whitespace bleibt erhalten. Es gibt
keine CSP im Projekt (geprüft: `grep -rn "CSP" config/settings/` → leer), `htmx.config.allowEval`
ist default `true`, also läuft der `Function()`-Konstruktor in `maybeGenerateConditional`.
Der Bezeichner `document` wird von htmx zu `((event.document) ? (event.document) : (window.document))`
umgeschrieben (`htmx.js:2187-2189`) — `event.document` ist auf einem `CustomEvent`/`Event`
`undefined`, es fällt also auf `window.document` zurück. Die Bedingung wertet zu genau dem
aus, was gemeint war.

**Verhalten, wenn das Element beim ersten Render nicht im DOM ist:** die Bedingung wird erst
zur Tick-Zeit ausgewertet, nicht beim Parsen — kein Problem. **Wenn der Selektor nie matcht**
(Dropdown nie offen): `querySelector` gibt `null`, `!null` → `true`, gepollt wird. Der
Guard ist also *fail-open*, nicht *fail-closed* — richtig herum.

### Das Polling stoppt nicht, wenn der Filter `false` liefert

`htmx.js:2360-2373` — `processPolling()` ruft `handler(elt)` nur, wenn `maybeFilterEvent()`
`false` liefert, **plant sich aber in jedem Fall neu ein**:
```js
if (!maybeFilterEvent(spec, elt, makeEvent('hx:poll:trigger', …))) { handler(elt) }
processPolling(elt, handler, spec)
```
Ein geschlossener Tab bzw. ein offenes Dropdown überspringt also nur den Request; der Timer
läuft weiter. Kein „Polling endet lautlos".

### Der Self-Swap-Vertrag hält

`bell.html` hat **keine** Verzweigung um das `<li id="notification-bell">`. Alle drei
htmx-Attribute (`hx-get`, `hx-trigger`, `hx-swap`) stehen unbedingt am Element und werden
folglich in jeder Response identisch mitgerendert — vom Bell-Poll wie von den beiden
Mark-Read-Views (beide rendern dasselbe Template). Die Polling-Kette bricht nicht ab.
(Abgesichert ist die Invariante allerdings nicht — s. Finding 3.)

### CSRF ist korrekt

- Header-Name ist exakt `X-CSRFToken` → Djangos Default `CSRF_HEADER_NAME = "HTTP_X_CSRFTOKEN"`
  (nicht überschrieben, geprüft). Der im Brief als häufigster Fehler genannte Bindestrich-Name
  wurde vermieden.
- `CSRF_COOKIE_HTTPONLY = True` (`config/settings/base.py:221`) — der Weg über das Hidden-Input
  statt `document.cookie` ist damit zwingend und wurde gewählt.
- **Mehrere `[name=csrfmiddlewaretoken]` im DOM:** auf Seiten mit eigenem `<form>` gibt es
  zusätzliche Tokens. `querySelector` nimmt den **ersten in Dokumentreihenfolge** — das ist
  der aus `base.html:60`, direkt nach `<body>`. Die Werte der einzelnen Inputs sind zwar
  *nicht* stringgleich (Django maskiert den Token pro `{% csrf_token %}`-Aufruf mit frischem
  Salt), aber **jeder einzelne ist gültig**, weil `CsrfViewMiddleware` demaskiert. Kein Bug.

### Autorisierung ist dicht

Kein IDOR gefunden. `NotificationBellView` → `LoginRequiredMixin` + `bell_context(self.request.user)`.
`MarkReadView` → `get_object_or_404(Notification, pk=pk, recipient=request.user)` **vor** der
htmx-Verzweigung. `MarkAllReadView` → `for_user(request.user).unread().update(...)`.
`bell_context()` / Context-Processor → beide `for_user()`. Fremde Notifications sind weder
lesbar noch markierbar; zwei Tests decken das ab.

### Der Nicht-htmx-Pfad funktioniert unverändert

`MarkReadView`/`MarkAllReadView` verzweigen nur bei `HX-Request: true` und liefern sonst die
alten 302er (Test `test_plain_request_still_redirects` für beide). Ein Browser ohne JS
klickt im Dropdown auf `href="{{ notification.url }}"` bzw. `href="{{ notifications_url }}"` —
die `<button>`-Elemente mit `hx-post` tun ohne htmx schlicht nichts, verlieren also keine
Funktionalität, die vorher da war. Die Glocke ersetzt den alten
`notifications:list`-Navbar-Link 1:1 (`offcanvas.html:33`), und der `<a>` zeigt weiterhin
auf `{% url 'notifications:list' %}`.

### Konventionen

Import-Stil (ein Import pro Zeile) eingehalten, `gettext_lazy as _` / `{% translate %}` /
`{% blocktranslate %}` durchgehend, Views bleiben dünn, `from __future__ import annotations`
vorhanden, `ruff check` und `djlint` grün. Der Gulp-Task folgt exakt dem Muster von
`vendorScripts()`. Einzige Kleinigkeit: `bell_context(user)` hat keine Typannotation am
Parameter, während der Rückgabewert annotiert ist (`-> dict`) — inkonsistent, aber
irrelevant, wenn die Funktion nach Finding 2 ohnehin verschwindet.
