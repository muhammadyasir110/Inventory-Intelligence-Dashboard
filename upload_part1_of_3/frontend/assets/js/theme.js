/* ==========================================================================
   theme.js — Dark / Light Mode Toggle
   Note: Uses an in-memory variable rather than localStorage, since browser
   storage APIs are unavailable in this environment. Defaults to dark
   ("Control Tower" mode) on every fresh load.
   ========================================================================== */

const ThemeManager = (() => {
  let current = 'dark';

  function apply(theme) {
    current = theme;
    document.documentElement.setAttribute('data-theme', theme);
  }

  function toggle() {
    apply(current === 'dark' ? 'light' : 'dark');
  }

  function init() {
    apply(current);
    const btn = document.getElementById('themeToggleBtn');
    if (btn) btn.addEventListener('click', toggle);
  }

  return { init, toggle, apply, get: () => current };
})();
