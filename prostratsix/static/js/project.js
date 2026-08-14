'use strict';

////////////////////////////////
// Offcanvas toggle icon state
////////////////////////////////
const toggle = document.getElementById('offcanvasToggle');
const offcanvas = document.getElementById('OpenMenu');

if (offcanvas && toggle) {
  offcanvas.addEventListener('shown.bs.offcanvas', () => {
    toggle.classList.add('active');
  });

  offcanvas.addEventListener('hidden.bs.offcanvas', () => {
    toggle.classList.remove('active');
  });
}

////////////////////////////////
// Motion preference
////////////////////////////////
const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

////////////////////////////////
// Spring engine — critically damped mass-spring-damper integrated on
// requestAnimationFrame. Takes Apple's designer-friendly damping/response
// pair (see WWDC "Designing Fluid Interfaces") instead of raw physics
// constants. Always retargets from the current live value, never the old
// target, so an interrupted spring never jumps.
////////////////////////////////
class Spring {
  constructor({ from = 0, damping = 1, response = 0.35 } = {}) {
    this.value = from;
    this.target = from;
    this.velocity = 0;
    this._raf = null;
    this._onUpdate = null;
    this.setPhysics(damping, response);
  }

  setPhysics(damping, response) {
    const angularFrequency = (2 * Math.PI) / response;
    this.stiffness = angularFrequency * angularFrequency;
    this.damping = 2 * damping * angularFrequency;
  }

  onUpdate(fn) {
    this._onUpdate = fn;
    return this;
  }

  to(target, { velocity } = {}) {
    this.target = target;
    if (velocity !== undefined) this.velocity = velocity;
    this._run();
  }

  jumpTo(value) {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
    this.value = value;
    this.target = value;
    this.velocity = 0;
    this._onUpdate?.(this.value);
  }

  _run() {
    if (this._raf) return;
    let last = performance.now();

    const step = (now) => {
      const dt = Math.min((now - last) / 1000, 1 / 30);
      last = now;

      const displacement = this.value - this.target;
      const acceleration = -this.stiffness * displacement - this.damping * this.velocity;

      this.velocity += acceleration * dt;
      this.value += this.velocity * dt;
      this._onUpdate?.(this.value);

      const atRest = Math.abs(this.velocity) < 0.001 && Math.abs(displacement) < 0.001;
      if (atRest) {
        this.value = this.target;
        this._onUpdate?.(this.value);
        this._raf = null;
        return;
      }

      this._raf = requestAnimationFrame(step);
    };

    this._raf = requestAnimationFrame(step);
  }
}

////////////////////////////////
// Press / lift feedback for real interactive targets (buttons, links styled
// as buttons, pagination, clickable cards). Feedback starts on pointerdown,
// not on release, and only ever touches transform — never layout properties.
////////////////////////////////
function springify(el, { pressScale = 0.97, liftY = 0 } = {}) {
  if (prefersReducedMotion.matches) return;

  const scaleSpring = new Spring({ from: 1, damping: 1, response: 0.32 });
  const liftSpring = liftY ? new Spring({ from: 0, damping: 1, response: 0.32 }) : null;

  let scale = 1;
  let y = 0;
  let hovering = false;

  const apply = () => {
    el.style.transform = `translateY(${y}px) scale(${scale})`;
  };

  scaleSpring.onUpdate((v) => {
    scale = v;
    apply();
  });
  liftSpring?.onUpdate((v) => {
    y = v;
    apply();
  });

  el.addEventListener('pointerdown', () => {
    scaleSpring.to(pressScale);
  });

  const release = () => {
    scaleSpring.to(1);
  };
  el.addEventListener('pointerup', release);
  el.addEventListener('pointercancel', release);

  if (liftSpring) {
    el.addEventListener('pointerenter', (e) => {
      if (e.pointerType !== 'mouse') return;
      hovering = true;
      liftSpring.to(liftY);
    });
    el.addEventListener('pointerleave', () => {
      hovering = false;
      liftSpring.to(0);
      scaleSpring.to(1);
    });
  } else {
    el.addEventListener('pointerleave', release);
  }
}

document.querySelectorAll('.btn, .page-link, .settings-nav__item, .site-tabs .nav-link').forEach((el) => {
  springify(el, { pressScale: 0.96 });
});

document.querySelectorAll('a.card.card--hover').forEach((el) => {
  springify(el, { pressScale: 0.985, liftY: -4 });
});

////////////////////////////////
// Breach reveal — the site's one signature load moment. A tactical scan-line
// sweeps once across the page's callsign label + primary heading, which
// settle in right behind it. Everything else on the page stays still: one
// orchestrated moment, not scattered effects (see frontend-design's "spend
// your boldness in one place"). Detected generically off the markup patterns
// every template already shares, so no per-page wiring is needed.
////////////////////////////////
function breachReveal() {
  if (prefersReducedMotion.matches) return;

  const heading = document.querySelector(
    'main#main h1, main#main .h1, main#main .hero__title, .auth__inner .auth__title'
  );
  if (!heading) return;

  // Only a callsign/eyebrow that actually belongs to this heading — not the
  // first one anywhere in the document (e.g. a card further down the page).
  const eyebrow = heading.parentElement?.querySelector('.callsign, .eyebrow') ?? null;

  const line = document.createElement('span');
  line.className = 'breach-reveal__line';
  line.setAttribute('aria-hidden', 'true');
  heading.insertAdjacentElement('beforebegin', line);

  const sweep = new Spring({ from: 0, damping: 1, response: 0.35 });
  sweep.onUpdate((v) => {
    line.style.transform = `scaleX(${v})`;
  });
  sweep.to(1);

  window.setTimeout(() => {
    const fade = new Spring({ from: 1, damping: 1, response: 0.3 });
    fade.onUpdate((v) => {
      line.style.opacity = String(v);
    });
    fade.to(0);
    window.setTimeout(() => line.remove(), 400);
  }, 260);

  [eyebrow, heading].filter(Boolean).forEach((el, i) => {
    el.style.opacity = '0';
    const settle = new Spring({ from: 16, damping: 1, response: 0.45 });
    settle.onUpdate((v) => {
      el.style.transform = `translateY(${v}px)`;
      el.style.opacity = String(1 - v / 16);
    });
    window.setTimeout(() => settle.to(0), 80 + i * 100);
  });
}

breachReveal();
