/* ==========================================================================
   state.js — Lightweight Global State Store
   ========================================================================== */

const AppState = (() => {
  const state = {
    currentBatchId: 'latest',
    currentView: 'overview',
    summary: null,
    filters: { search: '', category: 'all', status: 'all', warehouse: 'all', supplier: 'all' },
    sort: { by: 'product_name', order: 'asc' },
    page: 1,
    pageSize: 10,
    hasData: false,
  };

  const listeners = {};

  function on(event, cb) {
    (listeners[event] = listeners[event] || []).push(cb);
  }

  function emit(event, payload) {
    (listeners[event] || []).forEach((cb) => cb(payload));
  }

  function set(patch) {
    Object.assign(state, patch);
    emit('change', state);
  }

  function get() {
    return state;
  }

  return { get, set, on, emit };
})();
