/* ==========================================================================
   dashboard.js — Renders KPI cards and all analytics views from the
   /api/dashboard/{batch}/summary payload.
   ========================================================================== */

const DashboardRenderer = (() => {
  const icons = {
    box: '<path d="M21 8 12 3 3 8m18 0-9 5m9-5v9l-9 5m0-9L3 8m9 5v9M3 8v9l9 5"/>',
    dollar: '<path d="M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
    alertTriangle: '<path d="m10.29 3.86-8.18 14.14A2 2 0 0 0 3.82 21h16.36a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/><path d="M12 9v4m0 4h.01"/>',
    alertOctagon: '<polygon points="7.86 2 16.14 2 22 7.86 22 16.14 16.14 22 7.86 22 2 16.14 2 7.86 7.86 2"/><path d="M12 8v4m0 4h.01"/>',
    layers: '<path d="m12.83 2.18 8.49 4.24a1 1 0 0 1 0 1.79l-8.49 4.24a2 2 0 0 1-1.66 0L2.68 8.21a1 1 0 0 1 0-1.79l8.49-4.24a2 2 0 0 1 1.66 0Z"/><path d="m2.68 13.21 8.49 4.24a2 2 0 0 0 1.66 0l8.49-4.24"/>',
    clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    refresh: '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5M3 21v-5h5"/>',
    percent: '<line x1="19" y1="5" x2="5" y2="19"/><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/>',
  };

  function svg(name) {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${icons[name]}</svg>`;
  }

  function kpiCard({ icon, accent, label, value, formatter, trendText, trendUp }) {
    return `
      <div class="kpi-card ${accent ? 'accent-' + accent : ''}">
        <div class="kpi-top">
          <div class="kpi-icon">${svg(icon)}</div>
          ${trendText ? `<span class="kpi-trend ${trendUp ? 'up' : 'down'}">${trendText}</span>` : ''}
        </div>
        <div class="kpi-value count-up" data-target="${value}" data-formatter="${formatter}">0</div>
        <div class="kpi-label">${label}</div>
      </div>`;
  }

  function renderKPIs(container, k) {
    container.innerHTML = [
      kpiCard({ icon: 'box', label: 'Total SKUs', value: k.total_skus, formatter: 'int' }),
      kpiCard({ icon: 'dollar', label: 'Total Stock Value', value: k.total_stock_value, formatter: 'currency' }),
      kpiCard({ icon: 'alertTriangle', accent: 'low', label: 'Low Stock Items', value: k.low_stock_count, formatter: 'int' }),
      kpiCard({ icon: 'alertOctagon', accent: 'critical', label: 'Critical Items', value: k.critical_count, formatter: 'int' }),
      kpiCard({ icon: 'layers', accent: 'overstock', label: 'Overstock Items', value: k.overstock_count, formatter: 'int' }),
      kpiCard({ icon: 'clock', label: 'Expiring Soon', value: k.expiring_count, formatter: 'int' }),
      kpiCard({ icon: 'refresh', label: 'Est. Inventory Turnover', value: k.inventory_turnover_estimate, formatter: 'number' }),
      kpiCard({ icon: 'percent', accent: k.stockout_risk_pct > 20 ? 'critical' : 'healthy', label: 'Stockout Risk', value: k.stockout_risk_pct, formatter: 'percent' }),
    ].join('');

    container.querySelectorAll('.count-up').forEach((el) => {
      const target = parseFloat(el.dataset.target);
      const formatterName = el.dataset.formatter;
      animateCount(el, target, fmt[formatterName] || fmt.int);
    });
  }

  function statusBadge(status) {
    return `<span class="badge badge-${status}">${status}</span>`;
  }

  function renderQuickAlerts(container, alerts) {
    if (!container) return;
    const safeAlerts = Array.isArray(alerts) ? alerts : [];
    if (!safeAlerts.length) {
      container.innerHTML = `<div class="empty-state" style="padding:28px 16px;text-align:center;"><h4 style="margin-bottom:6px;font-size:var(--fs-md);color:var(--text-primary)">No urgent alerts</h4><p style="font-size:var(--fs-xs);color:var(--text-tertiary)">All SKUs are currently within safe stock thresholds.</p></div>`;
      return;
    }
    container.innerHTML = safeAlerts.slice(0, 6).map((i) => `
      <div class="util-row touchable-row" data-id="${i.id}" style="display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--border-subtle);cursor:pointer;">
        <div>
          <div style="font-weight:600;font-size:var(--fs-sm)">${escapeHtml(i.product_name || i.sku || 'Item')}</div>
          <div class="mono text-tertiary" style="font-size:var(--fs-xs)">${escapeHtml(i.sku || '—')} · ${escapeHtml(i.warehouse || 'Main Warehouse')}</div>
        </div>
        ${statusBadge(i.status || 'low')}
      </div>`).join('');

    container.querySelectorAll('.touchable-row').forEach((item) => {
      item.addEventListener('click', () => {
        const id = item.dataset.id;
        if (id && id !== 'null' && typeof App !== 'undefined') App.openDetails('item', id);
      });
    });
  }

  function renderOverstockTable(tbody, emptyEl, items) {
    if (!items.length) {
      tbody.innerHTML = '';
      emptyEl.innerHTML = `<div class="empty-state"><h3>No overstock detected</h3><p>All items are within their maximum stock thresholds.</p></div>`;
      return;
    }
    emptyEl.innerHTML = '';
    tbody.innerHTML = items.map((i) => {
      const excess = Math.max(0, i.quantity_on_hand - i.max_stock_level);
      const excessValue = excess * (i.unit_cost || 0);
      return `<tr class="touchable-row" data-id="${i.id}">
        <td class="cell-sku">${escapeHtml(i.sku)}</td>
        <td>${escapeHtml(i.product_name)}</td>
        <td>${escapeHtml(i.category)}</td>
        <td>${fmt.int(i.quantity_on_hand)}</td>
        <td>${fmt.int(i.max_stock_level)}</td>
        <td>${fmt.int(excess)}</td>
        <td>${fmt.currency(excessValue)}</td>
        <td>${escapeHtml(i.warehouse)}</td>
      </tr>`;
    }).join('');

    tbody.querySelectorAll('tr.touchable-row').forEach((tr) => {
      tr.addEventListener('click', () => {
        const id = tr.dataset.id;
        if (id && id !== 'null') App.openDetails('item', id);
      });
    });
  }

  function renderExpiryTable(tbody, emptyEl, items) {
    if (!items.length) {
      tbody.innerHTML = '';
      emptyEl.innerHTML = `<div class="empty-state"><h3>No items expiring soon</h3><p>Nothing is within the 90-day expiry window.</p></div>`;
      return;
    }
    emptyEl.innerHTML = '';
    tbody.innerHTML = items.map((i) => {
      const urgent = i.days_to_expiry <= 30;
      return `<tr class="touchable-row" data-id="${i.id}">
        <td class="cell-sku">${escapeHtml(i.sku)}</td>
        <td>${escapeHtml(i.product_name)}</td>
        <td>${escapeHtml(i.category)}</td>
        <td>${fmt.int(i.quantity_on_hand)}</td>
        <td>${fmt.date(i.expiry_date)}</td>
        <td><span class="badge ${urgent ? 'badge-critical' : 'badge-low'}">${i.days_to_expiry}d</span></td>
        <td>${fmt.currency(i.stock_value)}</td>
        <td>${escapeHtml(i.warehouse)}</td>
      </tr>`;
    }).join('');

    tbody.querySelectorAll('tr.touchable-row').forEach((tr) => {
      tr.addEventListener('click', () => {
        const id = tr.dataset.id;
        if (id && id !== 'null') App.openDetails('item', id);
      });
    });
  }

  function renderSafetyStockTable(tbody, rows) {
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="6"><div class="empty-state"><h3>No data</h3></div></td></tr>`;
      return;
    }
    tbody.innerHTML = rows.map((r) => `
      <tr class="touchable-row" data-id="${r.id}">
        <td class="cell-sku">${escapeHtml(r.sku)}</td>
        <td>${escapeHtml(r.product_name)}</td>
        <td>${fmt.int(r.quantity_on_hand)}</td>
        <td>${fmt.int(r.safety_stock)}</td>
        <td class="${r.breach ? '' : ''}">${r.buffer_pct === null ? '—' : fmt.percent(r.buffer_pct)}</td>
        <td>${r.breach ? statusBadge('critical') : statusBadge('normal')}</td>
      </tr>`).join('');

    tbody.querySelectorAll('tr.touchable-row').forEach((tr) => {
      tr.addEventListener('click', () => {
        const id = tr.dataset.id;
        if (id && id !== 'null') App.openDetails('item', id);
      });
    });
  }

  function renderWarehouseUtilization(listEl, tableBody, warehouses) {
    listEl.innerHTML = warehouses.map((w) => `
      <div class="util-row">
        <div class="util-label"><span class="name">${escapeHtml(w.warehouse)}</span><span class="value">${fmt.int(w.total_units)} units</span></div>
        <div class="util-track"><div class="util-fill" style="width:${w.utilization_pct}%"></div></div>
      </div>`).join('');

    tableBody.innerHTML = warehouses.map((w) => `
      <tr class="touchable-row" data-id="${w.id}">
        <td>${escapeHtml(w.warehouse)}</td>
        <td>${fmt.int(w.sku_count)}</td>
        <td>${fmt.int(w.total_units)}</td>
        <td>${fmt.currency(w.stock_value)}</td>
        <td>${fmt.percent(w.utilization_pct)}</td>
      </tr>`).join('');

    tableBody.querySelectorAll('tr.touchable-row').forEach((tr) => {
      tr.addEventListener('click', () => {
        const id = tr.dataset.id;
        if (id && id !== 'null') App.openDetails('warehouse', id);
      });
    });
  }

  function renderSupplierTable(tbody, suppliers) {
    tbody.innerHTML = suppliers.map((s) => `
      <tr class="touchable-row" data-id="${s.id}">
        <td>${escapeHtml(s.supplier)}</td>
        <td>${fmt.int(s.sku_count)}</td>
        <td>${fmt.currency(s.stock_value)}</td>
        <td>${fmt.number(s.avg_lead_time_days)} days</td>
        <td>${fmt.percent(s.on_time_delivery_rate)}</td>
        <td>${fmt.percent(s.quality_score)}</td>
        <td><strong>${fmt.number(s.performance_score)}</strong></td>
      </tr>`).join('');

    tbody.querySelectorAll('tr.touchable-row').forEach((tr) => {
      tr.addEventListener('click', () => {
        const id = tr.dataset.id;
        if (id && id !== 'null') App.openDetails('supplier', id);
      });
    });
  }

  function renderBatchHistory(tbody, batches, currentBatchId, onSelect) {
    if (!batches.length) {
      tbody.innerHTML = `<tr><td colspan="6"><div class="empty-state"><h3>No uploads yet</h3><p>Upload your first inventory file to get started.</p></div></td></tr>`;
      return;
    }
    tbody.innerHTML = batches.map((b) => `
      <tr>
        <td>${escapeHtml(b.filename)}${(b.derived_fields || []).length ? `<div style="font-size:11px;color:var(--text-tertiary)">derived: ${escapeHtml(b.derived_fields.join(', '))}</div>` : ''}</td>
        <td><span class="badge badge-neutral">${escapeHtml(b.provenance || 'unlabelled')}</span></td>
        <td>${fmt.int(b.row_count)}</td>
        <td>${fmt.datetime(b.uploaded_at)}</td>
        <td><span class="badge badge-normal">${escapeHtml(b.status)}</span></td>
        <td><button class="btn btn-secondary btn-sm" data-select-batch="${b.id}">${String(currentBatchId) === String(b.id) ? 'Viewing' : 'View'}</button></td>
      </tr>`).join('');

    tbody.querySelectorAll('[data-select-batch]').forEach((btn) => {
      btn.addEventListener('click', () => onSelect(btn.dataset.selectBatch));
    });
  }

  return {
    renderKPIs, renderQuickAlerts, renderOverstockTable, renderExpiryTable,
    renderSafetyStockTable, renderWarehouseUtilization, renderSupplierTable, renderBatchHistory,
  };
})();
