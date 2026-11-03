/* ==========================================================================
   api.js — Backend API Client
   All network calls to the FastAPI backend go through this module.
   ========================================================================== */

const API = (() => {
  // Same-origin by default (the FastAPI server also serves this frontend, on any port).
  // Only file:// pages and common static dev-server ports call the local backend on :8000.
  // Override with window.IIP_API_BASE if needed.
  const DEV_BACKEND = 'http://127.0.0.1:8000';
  const STATIC_DEV_PORTS = ['5500', '5501', '3000', '5173', '8080'];
  const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
  const BASE = (typeof window.IIP_API_BASE === 'string') ? window.IIP_API_BASE
    : (window.location.protocol === 'file:' || (isLocal && STATIC_DEV_PORTS.includes(window.location.port)))
      ? DEV_BACKEND
      : '';

  // A 401 means the session is missing, invalid or expired: clear it and
  // return to the login screen (the server never falls back to a default user).
  function handleUnauthorized(res, path) {
    if (res.status === 401 && !String(path).startsWith('/api/auth/login')) {
      localStorage.removeItem('iid_token');
      localStorage.removeItem('iid_username');
      localStorage.removeItem('iid_role');
      if (!window.__iipRedirecting) {
        window.__iipRedirecting = true;
        window.location.reload();
      }
    }
  }

  // Server errors carry `detail` as a string, an object ({message, code, ...}
  // for plan limits / row errors) or a list (validation errors). Turn every
  // shape into a readable message and keep the raw detail on the Error.
  function describe(detail, status) {
    if (!detail) return `Request failed (${status})`;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return detail.map((d) => `${(d.loc || []).filter((x) => x !== 'body').join('.')}: ${d.msg}`).join('; ');
    }
    if (typeof detail === 'object') return detail.message || JSON.stringify(detail);
    return String(detail);
  }

  async function errorFrom(res) {
    let detail = null;
    try { detail = (await res.json()).detail; } catch (_) { /* non-JSON error body */ }
    const err = new Error(res.status === 429 ? (describe(detail, res.status) || 'Too many requests')
                                             : describe(detail, res.status));
    err.status = res.status;
    err.detail = detail;
    return err;
  }

  async function request(path, options = {}) {
    const token = localStorage.getItem('iid_token');
    if (token) {
      options.headers = options.headers || {};
      options.headers['Authorization'] = `Bearer ${token}`;
    }
    let res;
    try {
      res = await fetch(`${BASE}${path}`, options);
    } catch (e) {
      throw new Error('Network error: the server could not be reached. Check your connection and retry.');
    }
    handleUnauthorized(res, path);
    if (!res.ok) throw await errorFrom(res);
    return res.json();
  }

  function json(method, body) {
    return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) };
  }

  function form(fields) {
    const fd = new FormData();
    Object.entries(fields).forEach(([k, v]) => { if (v !== undefined && v !== null) fd.append(k, v); });
    return { method: 'POST', body: fd };
  }

  // Authenticated file download (exports need the bearer token, so a plain
  // link cannot be used). Saves the file with the server-provided name.
  async function download(path, fallbackName) {
    const token = localStorage.getItem('iid_token');
    const res = await fetch(`${BASE}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    handleUnauthorized(res, path);
    if (!res.ok) throw await errorFrom(res);
    const blob = await res.blob();
    const cd = res.headers.get('Content-Disposition') || '';
    const m = /filename="?([^";]+)"?/.exec(cd);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = m ? m[1] : fallbackName;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  return {
    health: () => request('/api/health'),

    uploadFile: (file, provenance = 'user_uploaded') => request('/api/upload', form({ file, provenance })),
    previewUpload: (file) => request('/api/upload/preview', form({ file })),
    previewDemand: (file) => request('/api/upload/demand-history/preview', form({ file })),
    importDemand: (file, onConflict = 'skip', provenance = 'user_uploaded') =>
      request('/api/upload/demand-history', form({ file, on_conflict: onConflict, provenance })),

    listBatches: () => request('/api/batches'),

    getDashboardSummary: (batchId = 'latest') => request(`/api/dashboard/${batchId}/summary`),

    getItems: (batchId = 'latest', params = {}) => {
      const qs = new URLSearchParams(params).toString();
      return request(`/api/dashboard/${batchId}/items?${qs}`);
    },

    getFilters: (batchId = 'latest') => request(`/api/dashboard/${batchId}/filters`),

    getRecommendations: (batchId = 'latest', params = {}) => {
      const qs = new URLSearchParams(params).toString();
      return request(`/api/recommendations/${batchId}?${qs}`);
    },

    exportExcelUrl: (batchId = 'latest') => `${BASE}/api/export/${batchId}/excel`,
    exportPdfUrl: (batchId = 'latest') => `${BASE}/api/export/${batchId}/pdf`,
    downloadExport: (batchId = 'latest', format = 'excel') =>
      download(`/api/export/${batchId}/${format}`, `IIP_Report.${format === 'excel' ? 'xlsx' : format}`),

    login: (username, password) => request('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    }),

    logout: () => request('/api/auth/logout', { method: 'POST' }),

    // 2.7 transfers
    getTransfers: () => request('/api/warehouses/transfers'),
    executeTransfer: (source_item_id, destination_item_id, quantity, notes) =>
      request('/api/warehouses/transfers/execute', json('POST', { source_item_id, destination_item_id, quantity, notes })),

    // 2.7 two-factor login
    mfaLogin: (mfa_token, code) => request('/api/auth/mfa/login', json('POST', { mfa_token, code })),
    mfaStatus: () => request('/api/auth/mfa/status'),
    mfaSetup: () => request('/api/auth/mfa/setup', json('POST', {})),
    mfaEnable: (code) => request('/api/auth/mfa/enable', json('POST', { code })),
    mfaDisable: (password, code) => request('/api/auth/mfa/disable', json('POST', { password, code })),
    resetUserMfa: (id) => request(`/api/auth/users/${id}/reset-mfa`, json('POST', {})),

    // 2.6 account lifecycle (public endpoints use single-use tokens)
    requestPasswordReset: (email) => request('/api/auth/password-reset/request', json('POST', { email })),
    confirmPasswordReset: (token, new_password) =>
      request('/api/auth/password-reset/confirm', json('POST', { token, new_password })),
    lookupInvitation: (token) => request(`/api/auth/invitations/lookup?token=${encodeURIComponent(token)}`),
    acceptInvitation: (token, username, password, full_name) =>
      request('/api/auth/invitations/accept', json('POST', { token, username, password, full_name })),
    verifyEmail: (token) => request('/api/auth/verify-email', json('POST', { token })),
    resendVerification: () => request('/api/auth/verify-email/resend', json('POST', {})),
    getInvitations: () => request('/api/auth/invitations'),
    createInvitation: (email, role) => request('/api/auth/invitations', json('POST', { email, role })),
    revokeInvitation: (id) => request(`/api/auth/invitations/${id}`, { method: 'DELETE' }),
    getEmailStatus: () => request('/api/auth/email-status'),

    // 2.6 billing and organisation lifecycle
    getBillingStatus: () => request('/api/billing/status'),
    getBillingHistory: () => request('/api/billing/history'),
    getPlans: () => request('/api/billing/plans'),
    startCheckout: (plan, interval) => request('/api/billing/checkout', json('POST', { plan, interval })),
    exportOrgData: () => download('/api/org/export', 'organization_export.zip'),
    getDeletionRequest: () => request('/api/org/deletion-request'),
    requestOrgDeletion: (confirm_name, password) =>
      request('/api/org/deletion-request', json('POST', { confirm_name, password })),
    cancelOrgDeletion: () => request('/api/org/deletion-request', { method: 'DELETE' }),
    transferOwnership: (user_id, password) =>
      request('/api/org/transfer-ownership', json('POST', { user_id, password })),

    updateItem: (id, data) => request(`/api/update/item/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    }),

    updateWarehouse: (id, data) => request(`/api/update/warehouse/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    }),

    updateSupplier: (id, data) => request(`/api/update/supplier/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    }),

    getItem: (id) => request(`/api/dashboard/item/${id}`),
    getWarehouse: (id) => request(`/api/dashboard/warehouse/${id}`),
    getSupplier: (id) => request(`/api/dashboard/supplier/${id}`),
    
    deleteItem: (id) => request(`/api/update/item/${id}`, { method: 'DELETE' }),
    deleteWarehouse: (id) => request(`/api/update/warehouse/${id}`, { method: 'DELETE' }),
    deleteSupplier: (id) => request(`/api/update/supplier/${id}`, { method: 'DELETE' }),

    // === Enterprise Platform API Methods ===
    getOptimizationComparison: (batchId = 'latest', algorithm = 'abc', seed = 42) => request(`/api/optimization/comparison?batch_id=${batchId}&algorithm=${encodeURIComponent(algorithm)}&seed=${seed}`),
    runOptimization: (params) => request('/api/optimization/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params) }),
    runEidsfPolicy: (params) => request('/api/eidsf/policy', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params) }),
    
    getForecastingOverview: (batchId = 'latest', illustrative = false) =>
      request(`/api/forecasting/overview?batch_id=${encodeURIComponent(batchId)}&illustrative=${illustrative}`),
    getForecastItem: (itemId, illustrative = false) => request(`/api/forecasting/item/${itemId}?illustrative=${illustrative}`),
    
    simulateScenario: (params) => request('/api/scenarios/simulate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params) }),
    getScenarioHistory: () => request('/api/scenarios/history'),
    
    getProcurementPlan: () => request('/api/procurement/plan'),
    createPurchaseOrder: (data) => request('/api/procurement/orders', json('POST', data)),
    draftsFromPlan: () => request('/api/procurement/drafts-from-plan', json('POST', {})),
    listPurchaseOrders: (status) => request(`/api/procurement/orders${status ? `?status=${status}` : ''}`),
    getPurchaseOrder: (id) => request(`/api/procurement/orders/${id}`),
    poAction: (id, action, body) => request(`/api/procurement/orders/${id}/${action}`, json('POST', body || {})),
    
    getDecisionHistory: () => request('/api/decisions/history'),
    reviewDecision: (data) => request('/api/decisions/review', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }),
    
    getOrgConfig: () => request('/api/org/config'),
    updateOrgConfig: (data) => request('/api/org/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }),
    getDataQuality: () => request('/api/org/data-quality'),
    
    getAuthMe: () => request('/api/auth/me'),
    getUsers: () => request('/api/auth/users'),
    createUser: (data) => request('/api/auth/users', json('POST', data)),
    updateUser: (id, data) => request(`/api/auth/users/${id}`, json('PATCH', data)),
    resetUserPassword: (id, newPassword) => request(`/api/auth/users/${id}/reset-password`, json('POST', { new_password: newPassword })),
    changePassword: (current, next) => request('/api/auth/change-password', json('POST', { current_password: current, new_password: next })),
    logoutAll: () => request('/api/auth/logout-all', json('POST', {})),
    getOrgProfile: () => request('/api/org/profile'),
    updateOrgProfile: (data) => request('/api/org/profile', json('PUT', data)),
    getSubscription: () => request('/api/org/subscription'),
    getSku: (id) => request(`/api/skus/${id}`),
    getSkuTransactions: (id) => request(`/api/skus/${id}/transactions`),
    adjustStock: (id, body) => request(`/api/skus/${id}/adjustments`, json('POST', body)),

    listWarehouses: () => request('/api/warehouses'),
    listSuppliers: () => request('/api/suppliers'),
  };
})();
