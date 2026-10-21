/* ==========================================================================
   utils.js — Formatting Helpers, Toasts, Small DOM Utilities
   ========================================================================== */

const fmt = {
  number: (n) => new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 }).format(n ?? 0),
  int: (n) => new Intl.NumberFormat('en-US').format(Math.round(n ?? 0)),
  currency: (n) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(n ?? 0),
  percent: (n) => `${(n ?? 0).toFixed(1)}%`,
  date: (d) => (d ? new Date(d).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' }) : '—'),
  datetime: (d) => (d ? new Date(d).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' }) : '—'),
  titleCase: (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : ''),
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
