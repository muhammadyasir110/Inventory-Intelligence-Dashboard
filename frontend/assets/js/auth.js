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

  async function handleLogin(e) {
    e.preventDefault();
    const errorEl = document.getElementById('loginError');
    errorEl.classList.add('hidden');
    errorEl.textContent = '';

    const usernameInput = document.getElementById('loginUsername');
    const passwordInput = document.getElementById('loginPassword');

    try {
      const data = await API.login(usernameInput.value, passwordInput.value);
      localStorage.setItem(tokenKey, data.token);
      localStorage.setItem(userKey, data.username);
      localStorage.setItem(roleKey, data.role);

      // Hide login, show dashboard
      document.getElementById('loginScreen').classList.add('hidden');
      Toast.success(`Welcome back, ${data.username}!`);
      
      // Reload App shell
      window.location.reload();
    } catch (err) {
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
  }

  return {
    isAuthenticated,
    getToken,
    getUsername,
    getRole,
    checkSession,
    bindEvents,
    logout: handleLogout
  };
})();

// Initialize Auth listeners
document.addEventListener('DOMContentLoaded', () => {
  Auth.bindEvents();
});
