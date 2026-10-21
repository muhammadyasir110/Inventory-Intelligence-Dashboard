/* ==========================================================================
   table.js — Inventory Data Table
   Server-driven search / filter / sort / pagination against
   GET /api/dashboard/{batch}/items
   ========================================================================== */

const InventoryTable = (() => {
  let container, toolbar, pagerEl;
  let localState = {
    search: '', category: 'all', status: 'all', warehouse: 'all', supplier: 'all',
    sort_by: 'product_name', order: 'asc', page: 1, page_size: 10,
  };

  const columns = [
    { key: 'sku', label: 'SKU', cls: 'cell-sku' },
    { key: 'product_name', label: 'Product' },
    { key: 'category', label: 'Category' },
    { key: 'quantity_on_hand', label: 'Qty', fmt: (v) => fmt.int(v) },
    { key: 'reorder_point', label: 'Reorder Pt', fmt: (v) => fmt.int(v) },
    { key: 'stock_value', label: 'Stock Value', fmt: (v) => fmt.currency(v) },
    { key: 'warehouse', label: 'Warehouse' },
    { key: 'supplier', label: 'Supplier' },
    { key: 'status', label: 'Status', isStatus: true },
  ];

  function init(containerEl, toolbarEl, pagerElement) {
    container = containerEl;
    toolbar = toolbarEl;
    pagerEl = pagerElement;
    bindToolbar();
  }

  function bindToolbar() {
    if (!toolbar) return;
    const searchInput = toolbar.querySelector('[data-role="search"]');
    if (searchInput) {
      searchInput.addEventListener('input', debounce((e) => {
        localState.search = e.target.value;
        localState.page = 1;
        refresh();
      }, 350));
    }
    ['category', 'status', 'warehouse', 'supplier'].forEach((key) => {
      const sel = toolbar.querySelector(`[data-role="${key}"]`);
      if (sel) {
        sel.addEventListener('change', (e) => {
          localState[key] = e.target.value;
          localState.page = 1;
          refresh();
        });
      }
    });
  }

  async function populateFilterOptions(batchId) {
    if (!toolbar) return;
    try {
      const filters = await API.getFilters(batchId);
      fillSelect(toolbar.querySelector('[data-role="category"]'), filters.categories);
      fillSelect(toolbar.querySelector('[data-role="warehouse"]'), filters.warehouses);
      fillSelect(toolbar.querySelector('[data-role="supplier"]'), filters.suppliers);
    } catch (_) { /* filters are optional UX sugar */ }
  }

  function fillSelect(sel, values) {
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = '<option value="all">All</option>' + values.map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join('');
    sel.value = values.includes(current) ? current : 'all';
  }

  function renderSkeleton() {
    container.innerHTML = `
      <div class="table-wrap"><table class="data-table"><tbody>
        ${Array.from({ length: 6 }).map(() => `<tr>${columns.map(() => '<td><div class="skeleton" style="height:14px;width:80%"></div></td>').join('')}</tr>`).join('')}
      </tbody></table></div>`;
  }

  async function refresh(batchId = AppState.get().currentBatchId) {
    renderSkeleton();
    try {
      const params = { ...localState };
      const data = await API.getItems(batchId, params);
      render(data);
    } catch (err) {
      container.innerHTML = `<div class="empty-state"><h3>Couldn't load inventory</h3><p>${escapeHtml(err.message)}</p></div>`;
    }
  }

  function render(data) {
    if (!data.items.length) {
      container.innerHTML = `<div class="empty-state"><h3>No matching items</h3><p>Try adjusting your search or filters.</p></div>`;
      if (pagerEl) pagerEl.innerHTML = '';
      return;
    }

    const thead = `<thead><tr>${columns.map((c) => {
      const sorted = localState.sort_by === c.key;
      const arrow = sorted ? (localState.order === 'asc' ? '▲' : '▼') : '▲';
      return `<th data-key="${c.key}" class="${sorted ? 'sorted' : ''}">${c.label}<span class="sort-arrow">${arrow}</span></th>`;
    }).join('')}</tr></thead>`;

    const rows = data.items.map((item) => {
      const cells = columns.map((c) => {
        if (c.isStatus) return `<td><span class="badge badge-${item.status}">${item.status}</span></td>`;
        const raw = item[c.key];
        const val = c.fmt ? c.fmt(raw) : escapeHtml(raw ?? '—');
        return `<td class="${c.cls || ''}">${val}</td>`;
      }).join('');
      return `<tr class="touchable-row" data-id="${item.id}">${cells}</tr>`;
    }).join('');

    container.innerHTML = `<div class="table-wrap"><table class="data-table">${thead}<tbody>${rows}</tbody></table></div>`;

    container.querySelectorAll('tbody tr.touchable-row').forEach((tr) => {
      tr.addEventListener('click', (e) => {
        // Prevent modal if user clicked something interactive in the row
        if (e.target.closest('a, button, input, select')) return;
        const id = tr.dataset.id;
        App.openDetails('item', id);
      });
    });

    container.querySelectorAll('thead th').forEach((th) => {
      th.addEventListener('click', () => {
        const key = th.dataset.key;
        if (localState.sort_by === key) {
          localState.order = localState.order === 'asc' ? 'desc' : 'asc';
        } else {
          localState.sort_by = key;
          localState.order = 'asc';
        }
        refresh();
      });
    });

    renderPager(data);
  }

  function renderPager(data) {
    if (!pagerEl) return;
    const start = (data.page - 1) * data.page_size + 1;
    const end = Math.min(data.page * data.page_size, data.total);
    pagerEl.innerHTML = `
      <span class="info">Showing ${start}–${end} of ${data.total}</span>
      <div class="controls">
        <button class="btn btn-secondary btn-sm" ${data.page <= 1 ? 'disabled' : ''} data-nav="prev">← Prev</button>
        <button class="btn btn-secondary btn-sm" ${data.page >= data.total_pages ? 'disabled' : ''} data-nav="next">Next →</button>
      </div>`;
    const prev = pagerEl.querySelector('[data-nav="prev"]');
    const next = pagerEl.querySelector('[data-nav="next"]');
    if (prev) prev.addEventListener('click', () => { localState.page -= 1; refresh(); });
    if (next) next.addEventListener('click', () => { localState.page += 1; refresh(); });
  }

  function reset() {
    localState = { search: '', category: 'all', status: 'all', warehouse: 'all', supplier: 'all', sort_by: 'product_name', order: 'asc', page: 1, page_size: 10 };
  }

  function setStatusFilter(status) {
    localState.status = status;
    localState.page = 1;
    const sel = toolbar && toolbar.querySelector('[data-role="status"]');
    if (sel) sel.value = status;
  }

  return { init, refresh, populateFilterOptions, reset, setStatusFilter };
})();
