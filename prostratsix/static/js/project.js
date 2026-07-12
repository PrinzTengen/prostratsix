const toggle = document.getElementById("offcanvasToggle");
const offcanvas = document.getElementById("OpenMenu");

offcanvas.addEventListener("shown.bs.offcanvas", () => {
    toggle.classList.add("active");
});

offcanvas.addEventListener("hidden.bs.offcanvas", () => {
    toggle.classList.remove("active");
});

// htmx sends no CSRF token on its own. CSRF_COOKIE_HTTPONLY is True in
// config/settings/base.py, so we cannot read the cookie from JavaScript — the
// token comes from the hidden input that {% csrf_token %} renders right after
// <body> in base.html. The header must be spelled X-CSRFToken: that is what
// Django's default CSRF_HEADER_NAME maps to, and a wrong name fails silently
// with a 403. Only set the header when the token is actually there — assigning
// an undefined value would send the literal string "undefined" instead.
document.body.addEventListener("htmx:configRequest", (event) => {
    const token = document.querySelector("[name=csrfmiddlewaretoken]")?.value;
    if (token) {
        event.detail.headers["X-CSRFToken"] = token;
    }
});

// Bootstrap keeps its component instances in a plain Map keyed by DOM node (see
// node_modules/bootstrap/js/dist/dom/data.js — a Map, not a WeakMap). When htmx
// swaps an element away, as the notification bell does with itself every 30s,
// any Bootstrap instance attached to it — and the Popper listeners it owns —
// stays in that Map for the lifetime of the tab. Dispose it while the element
// is still around. getInstance() returns null if the user never opened the
// dropdown, so this is a no-op in the common case.
document.body.addEventListener("htmx:beforeCleanupElement", (event) => {
    bootstrap.Dropdown.getInstance(event.detail.elt)?.dispose();
});
