# Research-Brief: Phase 1 — HTMX-Polling für Notifications

Stand: 2026-07-12 · Basis: `feat/team-system` · Bezug: [`../leagues-and-htmx.md`](../leagues-and-htmx.md), Abschnitt "Phase 1"

Geprüfte Versionen (aus `pyproject.toml` / `package.json` / `npm view`):
Django `6.0.6`, Python `3.14`, Bootstrap `5.3.3` (installiert: `5.3.8` laut
`node_modules/bootstrap/package.json`-Dist), `htmx.org` aktuelle 2.x-Version
laut npm registry: **`2.0.10`** (dist-tag `latest`; `next` ist bereits ein
`4.0.0-beta5` — 3.x wird übersprungen, für uns irrelevant).

Wichtiger Fund vorab, der mehrere Antworten unten bestimmt:
**`config/settings/base.py:221` setzt `CSRF_COOKIE_HTTPONLY = True`.**
JavaScript kann den CSRF-Cookie damit grundsätzlich **nicht** lesen. Jedes
Copy-Paste-Snippet, das `document.cookie` + `getCookie('csrftoken')` nutzt
(das klassische, seit Jahren kursierende Django-AJAX-Beispiel), gibt in diesem
Projekt `null` zurück und produziert 403 CSRF-Fehler. Der Plan nutzt bereits
den korrekten Weg (verstecktes Formularfeld), das wird unten bestätigt und
verschärft.

---

## 1. Empfohlener Ansatz

Der Plan trifft die richtigen Grundentscheidungen (kein `django-htmx`, Self-Swap-Polling, `htmx:configRequest` für CSRF, `HX-Trigger`-Header für sofortige Refreshs) — die Recherche bestätigt sie alle gegen die tatsächliche htmx-2.0.10- und Bootstrap-5.3.8-Quelle. Die einzige echte Lücke im Plan ist der von dir selbst vermutete Bootstrap-Dropdown-Bug: **er existiert tatsächlich, ist aber kein UI-Bug, sondern ein Memory-Leak** — Bootstraps Instanz-Registry (`bootstrap/js/dist/dom/data.js`) ist eine normale `Map()`, kein `WeakMap`, hält also jedes je geöffnete Dropdown-Element für immer im Speicher, sobald `outerHTML`-Polling es aus dem DOM entfernt, ohne dass `dispose()` aufgerufen wurde. Das lässt sich mit einer einzigen, generischen `htmx:beforeCleanupElement`-Zeile in `project.js` beheben (Abschnitt 2.5) — der Dropdown-Klick selbst bräuchte gar keinen Fix, weil Bootstrap 5 seine `data-bs-toggle`-Klicks ohnehin per Event-Delegation auf `document` behandelt, nicht pro Element.

## 2. Konkrete Code-Muster

### 2.1 `package.json` — Dependency ergänzen

```json
"dependencies": {
  "@popperjs/core": "^2.11.8",
  "bootstrap": "^5.3.3",
  "htmx.org": "^2.0.10"
}
```

### 2.2 `gulpfile.js` — eigener Copy-Task statt Merge in `vendors.js`

htmx liefert eine einzige UMD-Datei (`node_modules/htmx.org/dist/htmx.min.js`),
genau wie Bootstrap sein `bootstrap.bundle.min.js`. Es gibt in diesem
Gulpfile **keinen** Concat-Mechanismus (kein `gulp-concat` als Dependency) —
`vendorScripts()` kopiert aktuell 1:1 eine Quelldatei nach `vendors.js`. Eine
zweite, eigene Funktion nach demselben Muster ist die kleinste Änderung, zieht
keine neue Gulp-Dependency nach sich und hält "Bootstrap" und "htmx" als zwei
unabhängig cachebare/austauschbare Dateien (falls z. B. später auf CDN-Fallback
o. Ä. umgestellt wird). Vom Plan-Wortlaut ("eigenes Bundle oder mit rein — je
nachdem wie der Task gebaut ist") ist das die zum bestehenden Code passende
Wahl:

```js
// Copy the bundled Bootstrap JS (Popper included) into the static dir so the
// site is fully self-hosted with no CDN dependency.
function vendorScripts() {
  return src(`${paths.nodeModules}/bootstrap/dist/js/bootstrap.bundle.min.js`, {
    encoding: false,
  })
    .pipe(rename('vendors.js'))
    .pipe(dest(paths.js));
}

// htmx ships as a single UMD file (like the Bootstrap bundle above); copy it
// as its own static file so it stays fully self-hosted and independently
// cacheable/upgradable from the Bootstrap bundle.
function htmxScript() {
  return src(`${paths.nodeModules}/htmx.org/dist/htmx.min.js`, {
    encoding: false,
  })
    .pipe(rename('htmx.js'))
    .pipe(dest(paths.js));
}

const build = series(parallel(styles, vendorScripts, htmxScript));

exports.styles = styles;
exports.vendorScripts = vendorScripts;
exports.htmxScript = htmxScript;
exports.build = build;
exports.serve = serve;
exports.default = dev;
```

### 2.3 `prostratsix/templates/base.html` — Einbindung

htmx **vor** `project.js` und mit `defer`, exakt wie im Plan gefordert — weil
alle drei Skripte `defer` tragen, garantiert der Browser Ausführung in
Dokumentreihenfolge, unabhängig vom Ladezeitpunkt. htmx.js bleibt **außerhalb**
von `{% compress js %}`, genau wie `vendors.js` heute schon (Vendor-Bundles
werden im Projekt bewusst nicht durch django-compressor gejagt, s. Abschnitt 3
zu `django-compressor`):

```html
{% block javascript %}
  <!-- Bootstrap bundle (Popper included), self-hosted via Gulp -->
  <script defer src="{% static 'js/vendors.js' %}"></script>
  <!-- htmx, self-hosted via Gulp (see gulpfile.js htmxScript task) -->
  <script defer src="{% static 'js/htmx.js' %}"></script>
  <!-- Your stuff: Third-party javascript libraries go here -->
  <!-- place project specific Javascript in this file -->
  {% compress js %}
    <script defer src="{% static 'js/project.js' %}"></script>
  {% endcompress %}
{% endblock javascript %}
```

Direkt nach `<body class="layout ...">` ein verstecktes CSRF-Feld ergänzen —
notwendig, **weil `CSRF_COOKIE_HTTPONLY = True`** JS den Cookie nicht lesen
lässt (s. Vorbemerkung):

```html
<body class="layout {% block bodyclass %}{% endblock bodyclass %}">
  {% csrf_token %}
  {% block navbar %}
```

### 2.4 CSRF — `prostratsix/static/js/project.js`

`htmx:configRequest` statt `hx-headers`. Beide sind offiziell dokumentiert
(`https://htmx.org/docs/#csrf-prevention` zeigt `hx-headers` auf `<body>`),
aber für dieses Projekt ist `configRequest` die robustere Wahl: `hx-headers`
auf `<body>` würde bedeuten, den Token als Jinja/Django-Variable direkt in ein
JSON-Attribut zu interpolieren (`hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'`)
— funktioniert, vermischt aber Django-Templating mit einem HTML-Attribut, das
djlint/Formatter anfassen. Der JS-Weg hält die Logik an einer Stelle
(`project.js`) und deckt automatisch auch zukünftige `hx-post`/`hx-delete`
Views ab, ohne dass irgendein Template etwas dafür tun muss:

```js
// CSRF: htmx sends no token by default. CSRF_COOKIE_HTTPONLY=True in
// config/settings/base.py means we cannot read the cookie from JS — we must
// pull the token from the hidden input that {% csrf_token %} renders right
// after <body> in base.html.
document.body.addEventListener("htmx:configRequest", (event) => {
    event.detail.headers["X-CSRFToken"] = document.querySelector(
        "[name=csrfmiddlewaretoken]",
    )?.value;
});
```

Der Header-Name **muss** `X-CSRFToken` heißen (nicht `X-CSRF-TOKEN`, wie es
das generische htmx-Doku-Beispiel zeigt) — Django validiert gegen
`CSRF_HEADER_NAME`, das im Projekt nicht überschrieben ist und daher auf dem
Default `HTTP_X_CSRFTOKEN` steht (→ HTTP-Header `X-CSRFToken`). Ein falscher
Headername liefert stillen 403 ohne offensichtlichen Hinweis in der
Browser-Konsole.

### 2.5 Bootstrap-Dropdown-Dispose — dein vermuteter Bug, bestätigt

Quelle: `node_modules/bootstrap/js/dist/dom/data.js` (installierte Version
5.3.8) — die Instanz-Registry ist:

```js
const elementMap = new Map();   // <- kein WeakMap!
```

und `BaseComponent.dispose()` (`node_modules/bootstrap/js/dist/base-component.js:43-49`)
ist der einzige Codepfad, der einen Eintrag wieder aus dieser Map entfernt
(`Data.remove(...)`) sowie (für `Dropdown` konkret,
`node_modules/bootstrap/js/dist/dropdown.js:163-167`) die zugehörige
Popper-Instanz zerstört (`this._popper.destroy()`).

Das heißt konkret: Klickt ein User das Glocken-Dropdown auch nur **einmal**
auf (wodurch Bootstrap per `getOrCreateInstance` eine `Dropdown`+`Popper`-
Instanz anlegt und in `elementMap` einträgt), und wird das `<li id=
"notification-bell">` danach 30-Sekunden-Takt für Stunden per `outerHTML`
ausgetauscht, bleibt **jede** alte `<li>`-Node inklusive ihrer
Dropdown/Popper-Instanz für die Lebensdauer des Tabs im Speicher — echtes,
unbegrenztes Leak bei langen Sessions, genau wie vermutet.

Die Klick-Funktionalität selbst ist **nicht** betroffen: Bootstrap bindet
`data-bs-toggle="dropdown"` nicht pro Element, sondern delegiert einmalig auf
`document` (`node_modules/bootstrap/js/dist/dropdown.js:388`:
`EventHandler.on(document, EVENT_CLICK_DATA_API, SELECTOR_DATA_TOGGLE, ...)`).
Ein frisch eingeswapptes `<a data-bs-toggle="dropdown">` funktioniert daher
sofort, ganz ohne Re-Init — das ist der Grund, warum der Bug bisher nicht als
UI-Fehler auffällt, sondern nur als langsam wachsender Speicherverbrauch.

Fix: htmx feuert genau für diesen Zweck `htmx:beforeCleanupElement`
("triggered before htmx disables an element or removes it from the DOM").
Einmalig global in `project.js`:

```js
// Bootstrap's dropdown/tooltip/popover instances live in a plain Map (not a
// WeakMap) keyed by DOM node — see node_modules/bootstrap/js/dist/dom/data.js.
// If we let htmx rip an element with an active Bootstrap instance out of the
// DOM (as the self-swapping notification bell does every 30s), that instance
// — and its Popper.js listeners — leaks for the lifetime of the tab. Dispose
// explicitly right before htmx removes the element.
document.body.addEventListener("htmx:beforeCleanupElement", (event) => {
    const dropdown = bootstrap.Dropdown.getInstance(event.detail.elt);
    dropdown?.dispose();
});
```

`bootstrap.Dropdown.getInstance()` gibt `null` zurück, wenn nie eine Instanz
angelegt wurde (User hat nie geklickt) — der `?.dispose()` ist dann ein
No-Op, kein Fehler. Der Handler ist generisch genug, dass er auch für
zukünftige Tooltips/Popovers/Offcanvas-Instanzen in eingeswapptem Markup greift,
falls die `bootstrap.*`-Klasse jeweils analog erweitert wird.

### 2.6 `prostratsix/notifications/urls.py` und `views.py`

Wie im Plan — keine Abweichung, hier nur zur Vollständigkeit mit korrektem
Import-Stil des Projekts (ein Import pro Zeile, `ruff` `isort.force-single-line`):

```python
# urls.py
from .views import NotificationBellView

urlpatterns = [
    ...,
    path("bell/", NotificationBellView.as_view(), name="bell"),
]
```

### 2.7 `HX-Trigger`-Response-Header aus einer View

Format-Referenz (`https://htmx.org/headers/hx-trigger/`):

```
HX-Trigger: notifications:refresh
HX-Trigger: notifications:refresh, other:event
HX-Trigger: {"notifications:refresh": null}
HX-Trigger: {"notifications:refresh": null, "showMessage": {"level": "info", "message": "Saved"}}
```

In einer Django-View (z. B. später in einer Leagues-Registrierungsview, die
`notify_many()` aufruft):

```python
def post(self, request, *args, **kwargs):
    ...  # register_team(...) which internally calls notify_many(...)
    response = redirect("leagues:detail", slug=league.slug)
    response["HX-Trigger"] = "notifications:refresh"
    return response
```

Wichtig: `notifications:refresh` bubbelt vom auslösenden Element bis zu
`<body>` hoch (htmx-Doku: "This will trigger ... on the triggering element
and will bubble up to the body"). Der Bell-Trigger im Plan lauscht mit
`from:body` genau darauf — das ist korrekt so übernommen. Voraussetzung:
Der Request, der den Header setzt, muss selbst ein htmx-Request sein (egal
welcher — Formular-Submit per htmx reicht), sonst gibt es kein `body`-Element,
an dem htmx das Event empfangen könnte, weil kein htmx-JS den Response
verarbeitet (ein reiner Server-Redirect ohne htmx-Beteiligung setzt zwar den
Header, aber niemand liest ihn).

### 2.8 Barrierefreiheit ohne Screenreader-Spam

Der naheliegende Reflex — `aria-live="polite"` direkt auf das Badge im
`<li id="notification-bell">` — funktioniert nur unzuverlässig, **weil genau
dieses Element per `outerHTML` alle 30s komplett ausgetauscht wird**.
ARIA-Live-Regions müssen für AT (NVDA/JAWS/VoiceOver) als **derselbe,
stabile DOM-Node** bestehen bleiben; nur *Kindinhalt-Mutationen* dieses Nodes
werden zuverlässig angekündigt. Ein `outerHTML`-Swap entfernt den gesamten
Node und fügt einen neuen ein — das ist keine "Mutation" einer bestehenden
Live-Region, sondern Node-Austausch, den viele Screenreader schlicht
ignorieren (und selbst wenn sie es nicht täten: Ansage bei *jedem* 30s-Tick,
auch ohne Zählerwechsel, ist exakt der Spam, den du vermeiden willst).

Empfehlung: Live-Region aus dem gepollten Subtree herausziehen und per
`hx-swap-oob="innerHTML:#…"` gezielt aktualisieren — dabei bleibt der
Container-Node stabil, nur sein Textinhalt ändert sich (Doku:
`https://htmx.org/attributes/hx-swap-oob/`, Abschnitt "Using alternate swap
strategies"):

```html
<!-- prostratsix/templates/navbar/offcanvas.html, außerhalb von #notification-bell -->
<span id="notification-live-region" class="visually-hidden" aria-live="polite" aria-atomic="true"></span>
```

```html
<!-- prostratsix/templates/notifications/partials/bell.html -->
{% load i18n %}
<li class="nav-item dropdown" id="notification-bell" ...>
  ...
  <span class="badge text-bg-danger rounded-pill" aria-hidden="true">
    {{ unread_notifications_count }}
  </span>
  ...
</li>
{% if notify_screenreader %}
  <span id="notification-live-region" hx-swap-oob="innerHTML:#notification-live-region">
    {% blocktranslate count counter=unread_notifications_count %}
      {{ counter }} new notification
    {% plural %}
      {{ counter }} new notifications
    {% endblocktranslate %}
  </span>
{% endif %}
```

`notify_screenreader` wäre ein Kontextflag, das die View nur bei einem
tatsächlichen Zählerwechsel setzt (z. B. Vergleich mit einem in der Session
gecachten letzten Wert) — nicht bei jedem 30s-Tick. Das ist mehr Aufwand als
im Plan vorgesehen; für den MVP reicht es, das Badge **ohne** `aria-live` zu
lassen (visuell trotzdem sichtbar) und die Ankündigung ganz wegzulassen, bis
jemand sie explizit braucht — besser kein Live-Announcement als eines, das
alle 30s unabhängig vom Inhalt feuert.

## 3. Fallstricke

- **CSRF-Header-Name.** `X-CSRF-TOKEN` (Bindestrich) statt `X-CSRFToken`
  ist der mit Abstand häufigste Copy-Paste-Fehler aus generischen
  htmx-Beispielen (die zeigen bewusst einen generischen Namen). Führt zu 403
  ohne Client-seitigen Fehler — die Request geht raus, der Header ist nur
  falsch benannt.
- **`getCookie('csrftoken')`-Snippets aus alten Django-Tutorials.** Geben in
  diesem Projekt `null` zurück, weil `CSRF_COOKIE_HTTPONLY = True`. Der
  Fehler zeigt sich erst zur Laufzeit (403), nicht beim Schreiben des Codes.
- **`hx-trigger` mit `every 30s [visibilityState...]` läuft im Hintergrund
  weiter, feuert aber nicht.** Der Timer selbst pausiert *nicht* — htmx
  evaluiert den Filter bei jedem Tick neu und verwirft den Request nur, wenn
  er `false` ist. Praktisch bedeutet das: Kommt ein Tab nach 5 Minuten im
  Hintergrund wieder in den Vordergrund, dauert es bis zu 30s, bis der
  nächste (dann durchgelassene) Tick den Bell-Partial nachlädt — das Badge
  ist bis dahin sichtbar veraltet. Bekannter Diskussionspunkt
  (`bigskysoftware/htmx#824`, `#821`): Wer sofortige Aktualisierung beim
  Zurückkommen will, braucht zusätzlich einen `visibilitychange`-Trigger:
  `hx-trigger="every 30s [document.visibilityState === 'visible'], visibilitychange[document.visibilityState === 'visible'] from:document, notifications:refresh from:body"`.
  Für den MVP im Plan (max. 30s Verzögerung) ist das vertretbar, aber es ist
  eine bewusste Lücke, keine, die man erst beim Testen entdecken sollte.
- **Trigger geht beim Self-Swap NICHT verloren** — das ist die gute
  Nachricht, aber sie ist nicht offensichtlich: htmx verarbeitet neu
  eingefügten Content automatisch (`htmx:afterSwap` → interner
  `process()`-Aufruf auf dem neuen Node), das schließt `hx-trigger`,
  `hx-get`, `hx-swap` auf dem frisch eingesetzten `<li>` mit ein. Die Polling-
  Kette reißt nicht ab — vorausgesetzt, der Server rendert das `hx-trigger`-
  Attribut in **jeder** Response identisch mit (Copy-Paste-Fehler: eine
  spätere Änderung an `bell.html`, die das Attribut nur "beim ersten Laden"
  setzt, würde die Kette lautlos beenden, ohne Fehler in der Konsole).
- **`286`-Statuscode stoppt Polling serverseitig.** Nicht im Plan verwendet,
  aber gut zu kennen, falls Notifications später z. B. für gesperrte Accounts
  komplett abgeschaltet werden sollen: Antwortet die View mit HTTP 286 statt
  200, bricht htmx das Polling für dieses Element endgültig ab (kein Retry).
- **Bootstrap-Dropdown-Leak (s. 2.5).** Merkbar nicht an einem UI-Fehler,
  sondern nur am DevTools-Memory-Profiler (wachsender Heap über Stunden,
  Anzahl `Dropdown`-Instanzen im Snapshot steigt monoton). Ohne aktives
  Profiling unsichtbar — genau deshalb hoher Impact, wenn nicht behoben.
- **Offen bleibende Dropdown-Menüs beim Swap.** Feuert der 30s-Tick genau in
  dem Moment, in dem der User das Dropdown-Menü geöffnet hat, wird das
  gesamte `<li>` (inkl. offenem `.dropdown-menu.show`) ersetzt — das Menü
  klappt kommentarlos zu. Kein Crash, aber UX-Ruckler; im MVP hinnehmbar,
  aber erwähnenswert für den Reviewer.
- **`django-compressor` + `COMPRESS_OFFLINE = True` in Production
  (`config/settings/production.py:129`).** Whitenoise bedient Static Files
  ohne Django-Prozess dazwischen — `{% compress %}`-Blöcke müssen zur
  Deploy-Zeit per `manage.py compress` vorkompiliert sein. Landet der neue
  CSRF/Dispose-Code in `project.js` (wie oben vorgeschlagen), ändert sich am
  bestehenden Vorgehen nichts, weil `project.js` schon heute im
  `{% compress js %}`-Block liegt — kein neuer Schritt nötig, aber wichtig,
  dass niemand versehentlich `htmx.js`/`vendors.js` in den `compress`-Block
  zieht: `COMPRESS_OFFLINE` verlangt dann, dass auch deren Inhalt zur
  Build-Zeit bekannt ist, und ein zusätzliches Vendor-Bundle im Compress-Cache
  bringt keinen Vorteil (es ist schon minifiziert von Bootstrap/htmx selbst).
  Der `JSMinFilter` (rjsmin) im Projekt ist ein reiner Text-Minifizierer ohne
  echtes JS-Parsing — Optional Chaining (`?.`) und Arrow Functions in unserem
  `project.js`-Snippet sind unproblematisch, weil `project.js` das heute schon
  nutzt (kein neues Risiko, aber nicht ungeprüft übernehmen, falls später
  exotischere Syntax (z. B. Nullish-Coalescing in Template-Strings mit Regex-
  artigen Zeichen) dazukommt — rjsmin ist bekannt dafür, bei Regex-Literal-vs-
  Divisions-Mehrdeutigkeit gelegentlich zu stolpern).
- **`{% csrf_token %}` außerhalb eines `<form>`.** HTML-technisch unbedenklich
  (ein `<input type="hidden">` braucht kein umschließendes Formular, um im
  DOM zu stehen), aber es lohnt sich, `djlint` (`H017`/`H035` sind im Projekt
  explizit *included*, s. `pyproject.toml`) nach dem Hinzufügen laufen zu
  lassen — falls eine dieser Regeln anschlägt, ist es eine reine
  Formatierungsfrage, kein funktionales Problem.

## 4. Abweichungen vom Plan

1. **`vendors.js` erweitern vs. eigene Datei.** Der Plan lässt beides offen
   ("bzw. als separates htmx.js kopiert — je nachdem, wie der Task aktuell
   gebaut ist"). Empfehlung dieser Recherche: **eigene Datei** (`htmxScript()`
   als zweite Gulp-Funktion, Abschnitt 2.2), weil der bestehende Task keinen
   Concat-Mechanismus hat und das Anlegen einer zweiten, zum bestehenden
   Muster identischen Funktion die kleinste, am wenigsten überraschende
   Änderung ist. Das ist keine Korrektur des Plans, sondern die Auflösung
   einer im Plan bewusst offen gelassenen Frage.
2. **`aria-live` auf dem Badge selbst funktioniert nicht zuverlässig**, weil
   das Badge Teil des sich komplett neu einsetzenden `<li>` ist (Abschnitt
   2.8). Der Plan erwähnt `aria-live` nicht explizit im HTML-Snippet — das
   ist also eher eine Lücke als ein Widerspruch, aber eine, die man vor dem
   Implementieren kennen sollte: naives Draufsetzen von `aria-live="polite"`
   auf den bestehenden Badge-`<span>` im Plan-Snippet sieht harmlos aus,
   ändert aber nichts am eigentlichen Problem und suggeriert fälschlich
   erledigte Barrierefreiheit.
3. **Kein Widerspruch, aber Ergänzung:** Der Plan nennt den Bootstrap-
   Dropdown-Verdacht als offene Frage ("größter Verdacht auf einen
   versteckten Bug"). Diese Recherche bestätigt ihn mit Quellcode-Beleg
   (Abschnitt 2.5) und liefert den Fix als eine einzige, generische
   Event-Handler-Zeile — das sollte 1:1 so in den Implementierungs-Scope von
   Lauf 1 aufgenommen werden (Plan-Abschnitt 1.4 erwähnt es nicht).

## 5. Quellen

- https://htmx.org/docs/ (Polling, Load Polling, CSRF Prevention, Swapping, OOB Swaps, Events & Logging, 1.x→2.x Migration)
- https://htmx.org/attributes/hx-trigger/ (Polling-Syntax, Trigger Filters)
- https://htmx.org/attributes/hx-swap-oob/ (alternate swap strategies, `innerHTML:#id`)
- https://htmx.org/headers/hx-trigger/ (Response-Header-Format, Multiple Triggers)
- https://htmx.org/reference/ (Konfigurationsvariablen)
- https://htmx.org/events/ (`htmx:configRequest`, `htmx:beforeSwap`, `htmx:beforeCleanupElement`, `htmx:afterSwap`)
- https://github.com/bigskysoftware/htmx/issues/824 — "Combine polling and visibilitychange event"
- https://github.com/bigskysoftware/htmx/discussions/821 — "Polling only if browser tab has focus?"
- https://github.com/bigskysoftware/htmx/issues/3286 — "Bootstrap dropdown not working after back-button navigation"
- https://github.com/bigskysoftware/htmx/discussions/2543 — "Start polling on HX Trigger header"
- https://docs.djangoproject.com/en/6.0/ref/csrf/ (CSRF-Settings, `CsrfViewMiddleware`, `CSRF_HEADER_NAME`-Default)
- https://django-compressor.readthedocs.io/en/latest/settings.html (`COMPRESS_OFFLINE`, `COMPRESS_FILTERS`)
- Projektinterne Quellen (Ground Truth, direkt gelesen):
  `node_modules/bootstrap/js/dist/dom/data.js` (Instanz-Registry ist `Map`, nicht `WeakMap`)
  `node_modules/bootstrap/js/dist/base-component.js` (`dispose()`-Implementierung)
  `node_modules/bootstrap/js/dist/dropdown.js` (Popper-Destroy in `dispose()`, `EVENT_CLICK_DATA_API`-Delegation auf `document`)
  `config/settings/base.py:221` (`CSRF_COOKIE_HTTPONLY = True`)
  `config/settings/production.py:120-137` (`COMPRESS_OFFLINE`, `COMPRESS_FILTERS`)
  `gulpfile.js`, `package.json` (bestehender Build-Task)
  `npm view htmx.org version` / `dist-tags` (aktuelle 2.x-Version: `2.0.10`)
