/* ==========================================================================
   auth.js — Client-Side Authentication Guard & Session Management
   ========================================================================== */

const Auth = (() => {
  const tokenKey = 'iid_token';
  const userKey = 'iid_username';
  const roleKey = 'iid_role';

  function isAuthenticated() {
    return !!localStorage.getItem(tokenKey);
  }

  function getToken() {
    return localStorage.getItem(tokenKey);
  }

  function getUsername() {
    return localStorage.getItem(userKey) || 'Guest';
  }

  function getRole() {
    return localStorage.getItem(roleKey) || 'viewer';
  }

  let pendingMfaToken = null;   // second login step (two-factor login on)

  async function handleLogin(e) {
    e.preventDefault();
    const errorEl = document.getElementById('loginError');
    errorEl.classList.add('hidden');
    errorEl.textContent = '';

    const usernameInput = document.getElementById('loginUsername');
    const passwordInput = document.getElementById('loginPassword');

    try {
      let data;
      const mfaBox = document.getElementById('mfaStep');
      if (pendingMfaToken) {
        data = await API.mfaLogin(pendingMfaToken, document.getElementById('loginMfaCode').value.trim());
      } else {
        data = await API.login(usernameInput.value, passwordInput.value);
        if (data.mfa_required) {
          pendingMfaToken = data.mfa_token;
          mfaBox?.classList.remove('hidden');
          usernameInput.disabled = true; passwordInput.disabled = true;
          document.getElementById('loginMfaCode')?.focus();
          return;
        }
      }
      pendingMfaToken = null;
      localStorage.setItem(tokenKey, data.token);
      localStorage.setItem(userKey, data.username);
      localStorage.setItem(roleKey, data.role);

      // Hide login, show dashboard
      document.getElementById('loginScreen').classList.add('hidden');
      Toast.success(`Welcome back, ${data.username}!`);
      
      // Reload App shell
      window.location.reload();
    } catch (err) {
      if (pendingMfaToken && err.status === 401) {   // second step expired: start again
        pendingMfaToken = null;
        document.getElementById('mfaStep')?.classList.add('hidden');
        usernameInput.disabled = false; passwordInput.disabled = false;
      }
      errorEl.classList.remove('hidden');
      errorEl.textContent = err.message || 'Login failed. Please check credentials.';
    }
  }

  async function handleLogout() {
    try {
      await API.logout();
    } catch (_) {}
    localStorage.removeItem(tokenKey);
    localStorage.removeItem(userKey);
    localStorage.removeItem(roleKey);
    window.location.reload();
  }

  function checkSession() {
    if (!isAuthenticated()) {
      // Hide everything, show login
      document.getElementById('appShell')?.classList.add('hidden');
      document.getElementById('landingScreen')?.classList.add('hidden');
      document.getElementById('loginScreen')?.classList.remove('hidden');
      return false;
    }
    return true;
  }

  function bindEvents() {
    document.getElementById('loginForm')?.addEventListener('submit', handleLogin);
    document.getElementById('logoutBtn')?.addEventListener('click', handleLogout);
    document.getElementById('forgotPwBtn')?.addEventListener('click', () => renderForgot());
    showDemoHint();
  }

  // Demo credentials are shown ONLY when the server says it runs the
  // development demo (never in staging/production).
  async function showDemoHint() {
    const el = document.getElementById('demoHint');
    if (!el) return;
    try {
      const h = await API.health();
      if (h.demo_mode) {
        el.textContent = 'Development demo: username admin, password admin123 (never available in production).';
        el.classList.remove('hidden');
      }
    } catch (_) { /* no hint */ }
  }

  // ---- public account screens (#/invite, #/reset-password, #/verify-email) ----
  function node(tag, attrs = {}, children = []) {
    const n = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => {
      if (k === 'text') n.textContent = v; else if (v !== false && v != null) n.setAttribute(k, v === true ? '' : v);
    });
    (Array.isArray(children) ? children : [children]).forEach((c) => c && n.appendChild(c));
    return n;
  }

  function field(id, label, type = 'text', extra = {}) {
    return node('div', { class: 'form-group', style: 'margin-bottom:12px' }, [
      node('label', { for: id, text: label, style: 'display:block;margin-bottom:6px;font-size:12px;color:var(--text-secondary)' }),
      node('input', { id, type, class: 'input', style: 'width:100%', required: true, ...extra }),
    ]);
  }

  function screen(title, intro, children) {
    ['appShell', 'landingScreen', 'loginScreen'].forEach((id) => document.getElementById(id)?.classList.add('hidden'));
    document.getElementById('pageLoader')?.classList.add('hide');
    const card = document.getElementById('accountCard');
    card.replaceChildren(
      node('h2', { text: title, style: 'font-family:var(--ff-display);font-weight:600;margin-bottom:8px' }),
      node('p', { text: intro, style: 'color:var(--text-secondary);margin-bottom:16px;font-size:13px' }),
      ...children,
      node('div', { id: 'accountMsg', role: 'status', style: 'margin-top:12px;font-size:13px' }),
      node('div', { style: 'margin-top:16px;text-align:center' },
        node('a', { href: '#', id: 'backToLogin', text: 'Back to sign in', class: 'btn btn-ghost btn-sm' })),
    );
    document.getElementById('accountScreen').classList.remove('hidden');
    document.getElementById('backToLogin').addEventListener('click', (e) => {
      e.preventDefault(); history.replaceState(null, '', window.location.pathname); window.location.reload();
    });
  }

  function say(text, ok) {
    const m = document.getElementById('accountMsg');
    m.textContent = text;
    m.style.color = ok ? 'var(--status-success, #16a34a)' : 'var(--status-critical)';
  }

  async function busy(btn, fn) {
    btn.disabled = true;
    try { await fn(); } finally { btn.disabled = false; }
  }

  function renderForgot() {
    const btn = node('button', { type: 'submit', class: 'btn btn-primary', text: 'Send reset link', style: 'width:100%' });
    const form = node('form', { id: 'forgotForm' }, [field('fpEmail', 'E-mail address of your account', 'email',
      { autocomplete: 'email', maxlength: '255' }), btn]);
    screen('Reset your password', 'Enter the e-mail address of your account. If e-mail delivery is configured, '
      + 'you will receive a single-use link. Otherwise ask your organisation administrator.', [form]);
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      busy(btn, async () => {
        try {
          const r = await API.requestPasswordReset(document.getElementById('fpEmail').value.trim());
          say(r.message, true);
          form.querySelector('input').disabled = true; btn.remove();
        } catch (err) { say(err.message, false); }
      });
    });
  }

  function renderReset(token) {
    const btn = node('button', { type: 'submit', class: 'btn btn-primary', text: 'Set new password', style: 'width:100%' });
    const form = node('form', {}, [field('rpNew', 'New password (at least 12 characters in production)', 'password',
      { autocomplete: 'new-password', minlength: '8' }), field('rpRepeat', 'Repeat new password', 'password',
      { autocomplete: 'new-password' }), btn]);
    screen('Choose a new password', 'All your sessions will be signed out.', [form]);
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const a = document.getElementById('rpNew').value, b = document.getElementById('rpRepeat').value;
      if (a !== b) { say('The two passwords do not match.', false); return; }
      busy(btn, async () => {
        try { const r = await API.confirmPasswordReset(token, a); say(r.message, true); btn.remove(); }
        catch (err) { say(err.message, false); }
      });
    });
  }

  async function renderInvite(token) {
    screen('Join your team', 'Checking your invitation…', []);
    let info;
    try { info = await API.lookupInvitation(token); }
    catch (err) { say(err.message, false); return; }
    const btn = node('button', { type: 'submit', class: 'btn btn-primary', text: 'Create my account', style: 'width:100%' });
    const form = node('form', {}, [
      field('iaUser', 'Choose a username', 'text', { minlength: '3', maxlength: '100', autocomplete: 'username' }),
      field('iaName', 'Full name (optional)', 'text', { maxlength: '255', required: false }),
      field('iaPw', 'Password', 'password', { autocomplete: 'new-password', minlength: '8' }), btn]);
    screen('Join your team', `${info.organization} invited ${info.email} as ${info.role_label}. `
      + `The invitation expires on ${new Date(info.expires_at + 'Z').toLocaleString()}.`, [form]);
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      busy(btn, async () => {
        try {
          const r = await API.acceptInvitation(token, document.getElementById('iaUser').value.trim(),
            document.getElementById('iaPw').value, document.getElementById('iaName').value.trim() || null);
          localStorage.setItem(tokenKey, r.token); localStorage.setItem(userKey, r.username);
          localStorage.setItem(roleKey, r.role);
          history.replaceState(null, '', window.location.pathname);
          window.location.reload();
        } catch (err) { say(err.message, false); }
      });
    });
  }

  async function renderVerify(token) {
    screen('Confirm your e-mail address', 'Confirming…', []);
    try { const r = await API.verifyEmail(token); say(r.message + ' You can now sign in.', true); }
    catch (err) { say(err.message, false); }
  }

  // Returns true when the URL is a public account link (the app shell is not started).
  function handleAccountRoute() {
    const m = /^#\/(invite|reset-password|verify-email)\?token=([A-Za-z0-9_\-]{10,256})$/.exec(window.location.hash || '');
    if (!m) return false;
    const [, kind, token] = m;
    if (kind === 'invite') renderInvite(token);
    else if (kind === 'reset-password') renderReset(token);
    else renderVerify(token);
    return true;
  }

  return {
    isAuthenticated,
    getToken,
    getUsername,
    getRole,
    checkSession,
    bindEvents,
    handleAccountRoute,
    logout: handleLogout
  };
})();

// Initialize Auth listeners
document.addEventListener('DOMContentLoaded', () => {
  Auth.bindEvents();
});
