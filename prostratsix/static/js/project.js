const toggle = document.getElementById("offcanvasToggle");
const offcanvas = document.getElementById("OpenMenu");

offcanvas.addEventListener("shown.bs.offcanvas", () => {
    toggle.classList.add("active");
});

offcanvas.addEventListener("hidden.bs.offcanvas", () => {
    toggle.classList.remove("active");
});
