/* ==========================================================================
   errors.js — global error banner (replaces the former inline script so the
   page works under a strict Content-Security-Policy). Messages are inserted
   as text, never as HTML.
   ========================================================================== */
(function () {
  function show(label, text) {
    const banner = document.getElementById('globalErrorBanner');
    const msgEl = document.getElementById('globalErrorMessage');
    if (!banner || !msgEl) return;
    banner.classList.remove('hidden');
    msgEl.textContent = `${label}: ${text}`;
  }
  window.addEventListener('error', (e) => show('Error', `${e.message} (${e.filename || ''}:${e.lineno || ''})`));
  window.addEventListener('unhandledrejection', (e) => show('Unhandled error', String(e.reason && e.reason.message || e.reason)));
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-dismiss]');
    if (btn) document.getElementById(btn.dataset.dismiss)?.classList.add('hidden');
  });
})();
