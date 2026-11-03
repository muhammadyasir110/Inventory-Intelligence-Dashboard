/* ==========================================================================
   utils.js — Formatting Helpers, Toasts, Small DOM Utilities
   ========================================================================== */

// Organisation display settings (set after login from /api/auth/me).
const OrgPrefs = { currency: 'PKR', timezone: 'Asia/Karachi', locale: 'en-PK' };

function _safeTz() {
  try { new Intl.DateTimeFormat('en', { timeZone: OrgPrefs.timezone }); return OrgPrefs.timezone; }
  catch (_) { return undefined; }
}

const fmt = {
  number: (n) => new Intl.NumberFormat(OrgPrefs.locale, { maximumFractionDigits: 1 }).format(n ?? 0),
  int: (n) => new Intl.NumberFormat(OrgPrefs.locale).format(Math.round(n ?? 0)),
  // Whole currency units, organisation currency (default PKR). Missing -> em dash, never 0.
  currency: (n, digits = 0) => (n === null || n === undefined || Number.isNaN(Number(n))) ? '—'
    : new Intl.NumberFormat(OrgPrefs.locale, { style: 'currency', currency: OrgPrefs.currency,
        minimumFractionDigits: digits, maximumFractionDigits: digits }).format(Number(n)),
  currencyCode: () => OrgPrefs.currency,
  percent: (n) => `${(n ?? 0).toFixed(1)}%`,
  date: (d) => (d ? new Date(d).toLocaleDateString(OrgPrefs.locale, { year: 'numeric', month: 'short', day: 'numeric', timeZone: _safeTz() }) : '—'),
  datetime: (d) => (d ? new Date(d).toLocaleString(OrgPrefs.locale, { dateStyle: 'medium', timeStyle: 'short', timeZone: _safeTz() }) : '—'),
  titleCase: (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : ''),
  cls: (v) => (v === null || v === undefined || v === '') ? '—' : String(v),   // classification label
};

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}

const Toast = (() => {
  function ensureStack() {
    let stack = document.querySelector('.toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.className = 'toast-stack';
      document.body.appendChild(stack);
    }
    return stack;
  }

  function show(message, type = 'info', duration = 4200) {
    const stack = ensureStack();
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    stack.appendChild(el);
    setTimeout(() => {
      el.style.transition = 'opacity 240ms ease, transform 240ms ease';
      el.style.opacity = '0';
      el.style.transform = 'translateX(20px)';
      setTimeout(() => el.remove(), 260);
    }, duration);
  }

  return {
    success: (msg) => show(msg, 'success'),
    error: (msg) => show(msg, 'error'),
    info: (msg) => show(msg, 'info'),
  };
})();

function debounce(fn, wait = 300) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), wait);
  };
}

/** Animates a numeric counter from 0 (or current) to target value. */
function animateCount(el, target, formatter = fmt.int, duration = 700) {
  const start = 0;
  const startTime = performance.now();
  function step(now) {
    const progress = Math.min((now - startTime) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    const value = start + (target - start) * eased;
    el.textContent = formatter(value);
    if (progress < 1) requestAnimationFrame(step);
    else el.textContent = formatter(target);
  }
  requestAnimationFrame(step);
}


// Run an async action with its button disabled (prevents duplicate submissions
// of expensive or state-changing operations); re-enabled on success or error.
async function withBusy(btn, fn) {
  if (btn && btn.dataset.busy === '1') return undefined;
  if (btn) { btn.dataset.busy = '1'; btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }
  try { return await fn(); }
  finally { if (btn) { btn.dataset.busy = ''; btn.disabled = false; btn.removeAttribute('aria-busy'); } }
}
