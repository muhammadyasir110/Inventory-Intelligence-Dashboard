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

  async function request(path, options = {}) {
    const token = localStorage.getItem('iid_token');
    if (token) {
      options.headers = options.headers || {};
      options.headers['Authorization'] = `Bearer ${token}`;
    }
    const res = await fetch(`${BASE}${path}`, options);
    handleUnauthorized(res, path);
    if (!res.ok) {
      let detail = `Request failed (${res.status})`;
      try {
        const body = await res.json();
        detail = body.detail || detail;
      } catch (_) { /* non-JSON error body */ }
      throw new Error(detail);
    }
    return res.json();
  }

  return {
    health: () => request('/api/health'),

    uploadFile: async (file, provenance = 'user_uploaded') => {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('provenance', provenance);
      const token = localStorage.getItem('iid_token');
      const headers = {};
      if (token) headers['Authorization'] = `Bearer ${token}`;
      const res = await fetch(`${BASE}/api/upload`, { method: 'POST', body: formData, headers });
      handleUnauthorized(res, '/api/upload');
      if (!res.ok) {
        let detail = 'Upload failed';
        try { detail = (await res.json()).detail || detail; } catch (_) {}
        throw new Error(detail);
      }
      return res.json();
    },

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

    login: (username, password) => request('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    }),

    logout: () => request('/api/auth/logout', { method: 'POST' }),

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
    
    getForecastingOverview: (batchId = 'latest') => request(`/api/forecasting/overview?batch_id=${batchId}`),
    getForecastItem: (itemId) => request(`/api/forecasting/item/${itemId}`),
    
    simulateScenario: (params) => request('/api/scenarios/simulate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params) }),
    getScenarioHistory: () => request('/api/scenarios/history'),
    
    getProcurementPlan: (batchId = 'latest') => request(`/api/procurement/plan?batch_id=${batchId}`),
    createPurchaseOrder: (data) => request('/api/procurement/orders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }),
    
    getDecisionHistory: () => request('/api/decisions/history'),
    reviewDecision: (data) => request('/api/decisions/review', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }),
    
    getOrgConfig: () => request('/api/org/config'),
    updateOrgConfig: (data) => request('/api/org/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }),
    getDataQuality: () => request('/api/org/data-quality'),
    
    getAuthMe: () => request('/api/auth/me'),
    getUsers: () => request('/api/auth/users'),
    createUser: (data) => request('/api/auth/users', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }),

    listWarehouses: () => request('/api/warehouses'),
    listSuppliers: () => request('/api/suppliers'),
  };
})();
