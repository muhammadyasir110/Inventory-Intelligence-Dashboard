/* ==========================================================================
   app.js — Application Bootstrap, Routing & View Orchestration
   ========================================================================== */

const App = (() => {
  const mount = () => document.getElementById('viewMount');

  const routes = {
    overview: { file: 'pages/overview.html', init: initOverview },
    inventory: { file: 'pages/inventory.html', init: initInventory },
    alerts: { file: 'pages/alerts.html', init: initAlerts },
    overstock: { file: 'pages/overstock.html', init: initOverstock },
    expiry: { file: 'pages/expiry.html', init: initExpiry },
    'safety-stock': { file: 'pages/safety-stock.html', init: initSafetyStock },
    warehouses: { file: 'pages/warehouses.html', init: initWarehouses },
    suppliers: { file: 'pages/suppliers.html', init: initSuppliers },
    recommendations: { file: 'pages/recommendations.html', init: initRecommendations },
    upload: { file: 'pages/upload.html', init: initUpload },
    erp: { file: 'pages/erp.html', init: (el) => bindExportButtons(el) },
    optimization: { file: 'pages/optimization.html', init: initOptimization },
    forecasting: { file: 'pages/forecasting.html', init: initForecasting },
    scenarios: { file: 'pages/scenarios.html', init: initScenarios },
    procurement: { file: 'pages/procurement.html', init: initProcurement },
    decisions: { file: 'pages/decisions.html', init: initDecisions },
    config: { file: 'pages/config.html', init: initConfig },
  };

  let cachedSummary = null;

  function requireElement(root, selector, name) {
    const el = root.querySelector(selector);
    if (!el) {
      throw new Error(`Missing required element ${selector} in ${name} view.`);
    }
    return el;
  }

  async function ensureSummary(force = false) {
    if (cachedSummary && !force) return cachedSummary;
    cachedSummary = await API.getDashboardSummary(AppState.get().currentBatchId);
    updateBatchSelector();
    return cachedSummary;
  }

  async function loadViewTemplate(path) {
    const tplKey = path.replace(/^(pages\/|\/pages\/|\/)/, '').replace(/\.html$/, '');

    // 1. Try fetching dynamically from server if available
    const candidates = [path];
    if (!path.startsWith('/')) candidates.unshift(`/${path}`);

    for (const candidate of candidates) {
      try {
        const response = await fetch(candidate);
        if (response.ok) {
          return await response.text();
        }
      } catch (_) {}
    }

    // 2. Seamless fallback: use embedded templates for file:// and offline browsers
    if (window.EMBEDDED_TEMPLATES && window.EMBEDDED_TEMPLATES[tplKey]) {
      return window.EMBEDDED_TEMPLATES[tplKey];
    }

    throw new Error(`Failed to load view template: ${path}`);
  }

  async function navigate(viewName) {
    if (!routes[viewName]) viewName = 'overview';
    AppState.set({ currentView: viewName });
    highlightNav(viewName);

    // Each navigation renders into a fresh root element. A slower view that is
    // still loading keeps writing into its own (now detached) root instead of
    // the page the user has moved on to.
    const host = mount();
    host.classList.remove('view');
    const html = await loadViewTemplate(routes[viewName].file);
    const el = document.createElement('div');
    el.className = 'view-root';
    el.innerHTML = html;
    host.replaceChildren(el);
    void host.offsetWidth; // restart animation
    host.classList.add('view');

    closeMobileNav();

    try {
      await routes[viewName].init(el);
    } catch (err) {
      if (!el.isConnected) return; // user already navigated elsewhere
      if (err.message && err.message.includes('No inventory data')) {
        renderNoDataState(el);
      } else {
        Toast.error(err.message || 'Something went wrong loading this view.');
      }
    }
  }

  function renderNoDataState(el) {
    el.innerHTML = `
      <div class="empty-state" style="padding-top:80px">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 16V4m0 0 5 5m-5-5-5 5M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>
        <h3>No inventory data yet</h3>
        <p>Upload a CSV or Excel file to automatically generate your dashboard.</p>
        <button class="btn btn-primary" data-nav="upload">Go to Upload</button>
      </div>`;
  }

  function highlightNav(viewName) {
    document.querySelectorAll('.nav-item').forEach((n) => n.classList.toggle('active', n.dataset.view === viewName));
  }

  function closeMobileNav() {
    document.querySelector('.app-shell')?.classList.remove('mobile-nav-open');
  }

  // ---- View initializers --------------------------------------------------

  async function initOverview(el) {
    const summary = await ensureSummary();

    try {
      DashboardRenderer.renderKPIs(requireElement(el, '#kpiGrid', 'overview'), summary.kpis);
    } catch (e) {
      console.error('Failed to render KPIs:', e);
    }

    try {
      Charts.categoryBreakdown('chartCategoryBreakdown', summary.category_breakdown || []);
    } catch (e) {
      console.error('Failed to render category breakdown chart:', e);
    }

    try {
      Charts.statusDistribution('chartStatusDistribution', summary.status_distribution || {});
    } catch (e) {
      console.error('Failed to render status distribution chart:', e);
    }

    try {
      let topSkus = summary.top_skus;
      if (!topSkus || !topSkus.length) {
        try {
          const itemsRes = await API.getItems(AppState.get().currentBatchId, { page_size: 100, sort_by: 'stock_value', order: 'desc' });
          topSkus = itemsRes.items || [];
        } catch (_) {
          topSkus = [];
        }
      }
      Charts.stockValueBySku('chartTopSkus', topSkus);
    } catch (e) {
      console.error('Failed to render top SKUs chart:', e);
    }

    try {
      DashboardRenderer.renderQuickAlerts(requireElement(el, '#quickAlertsList', 'overview'), summary.low_stock_alerts || []);
    } catch (e) {
      console.error('Failed to render quick alerts list:', e);
    }

    el.querySelectorAll('[data-nav-to]').forEach((b) => b.addEventListener('click', () => navigate(b.dataset.navTo)));
    bindExportButtons(el);
  }

  async function initInventory(el) {
    await ensureSummary();
    InventoryTable.reset();
    InventoryTable.init(
      requireElement(el, '#inventoryTableContainer', 'inventory'),
      requireElement(el, '#inventoryToolbar', 'inventory'),
      requireElement(el, '#inventoryPager', 'inventory')
    );
    await InventoryTable.populateFilterOptions(AppState.get().currentBatchId);
    await InventoryTable.refresh(AppState.get().currentBatchId);
    bindExportButtons(el);
  }

  async function initAlerts(el) {
    const summary = await ensureSummary();
    const k = summary.kpis;
    requireElement(el, '#alertsKpiRow', 'alerts').innerHTML = `
      <div class="kpi-card accent-critical"><div class="kpi-top"><div class="kpi-icon">⚠</div></div><div class="kpi-value">${k.critical_count}</div><div class="kpi-label">Critical Items</div></div>
      <div class="kpi-card accent-low"><div class="kpi-top"><div class="kpi-icon">▼</div></div><div class="kpi-value">${k.low_stock_count}</div><div class="kpi-label">Low Stock Items</div></div>
      <div class="kpi-card"><div class="kpi-top"><div class="kpi-icon">%</div></div><div class="kpi-value">${fmt.percent(k.stockout_risk_pct)}</div><div class="kpi-label">Stockout Risk</div></div>`;

    InventoryTable.reset();
    InventoryTable.init(
      requireElement(el, '#alertsTableContainer', 'alerts'),
      el.querySelector('.table-toolbar'),
      requireElement(el, '#alertsPager', 'alerts')
    );
    InventoryTable.setStatusFilter('low');
    await InventoryTable.populateFilterOptions(AppState.get().currentBatchId);
    await InventoryTable.refresh(AppState.get().currentBatchId);
  }

  async function initOverstock(el) {
    const summary = await ensureSummary();
    DashboardRenderer.renderOverstockTable(el.querySelector('#overstockTableBody'), el.querySelector('#overstockEmpty'), summary.overstock_items);
  }

  async function initExpiry(el) {
    const summary = await ensureSummary();
    DashboardRenderer.renderExpiryTable(el.querySelector('#expiryTableBody'), el.querySelector('#expiryEmpty'), summary.expiry_watchlist);
  }

  async function initSafetyStock(el) {
    const summary = await ensureSummary();
    DashboardRenderer.renderSafetyStockTable(el.querySelector('#safetyStockTableBody'), summary.safety_stock_analysis);
  }

  async function initWarehouses(el) {
    const summary = await ensureSummary();
    DashboardRenderer.renderWarehouseUtilization(el.querySelector('#warehouseUtilList'), el.querySelector('#warehouseTableBody'), summary.warehouse_utilization);
    Charts.categoryBreakdown('chartWarehouseValue', summary.warehouse_utilization.map((w) => ({ category: w.warehouse, stock_value: w.stock_value })));
    await loadTransfers(el);
  }

  // Transfer proposals (2.7): shown as proposals; executing one moves stock through the ledger.
  async function loadTransfers(el) {
    const body = el.querySelector('#transferBody');
    if (!body) return;
    const role = (AppState.get().me || {}).role;
    const canExecute = ['owner', 'admin', 'inventory_manager', 'warehouse_manager'].includes(role);
    let rows = [];
    try { rows = await API.getTransfers(); }
    catch (e) { body.innerHTML = `<tr><td colspan="8">${escapeHtml(e.message)}</td></tr>`; return; }
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="8" class="text-muted">No transfer proposals: no warehouse holds a surplus of a SKU that another warehouse is short of.</td></tr>';
      return;
    }
    body.innerHTML = rows.map((t, i) => `
      <tr><td>${escapeHtml(t.sku)}</td><td>${escapeHtml(t.source_warehouse)}</td><td>${escapeHtml(t.destination_warehouse)}</td>
      <td>${fmt.int(t.recommended_quantity)}</td><td>${fmt.currency(t.transfer_value)}</td>
      <td>${escapeHtml(t.urgency)}</td><td style="max-width:320px;font-size:12px">${escapeHtml(t.reasoning)}</td>
      <td>${canExecute ? `<button class="btn btn-secondary btn-sm" data-transfer-idx="${i}">Execute</button>` : ''}</td></tr>`).join('');
    body.onclick = (e) => {
      const b = e.target.closest('[data-transfer-idx]');
      if (!b) return;
      const t = rows[parseInt(b.dataset.transferIdx, 10)];
      const ans = window.prompt(`Move how many units of ${t.sku} from ${t.source_warehouse} to ${t.destination_warehouse}? `
        + `(proposed: ${t.recommended_quantity}). Do this only when the goods are actually moved.`, String(t.recommended_quantity));
      if (ans === null) return;
      const q = parseFloat(ans);
      if (!(q > 0)) { Toast.error('Enter a positive quantity.'); return; }
      guarded(b, async () => {
        const r = await API.executeTransfer(t.source_item_id, t.destination_item_id, q, `proposal ${t.transfer_id}`);
        Toast.success(`Transfer ${r.reference} posted: ${fmt.int(r.quantity)} units of ${r.sku}.`);
        AppState.set({ summary: null });
        await loadTransfers(el);
      });
    };
  }

  async function initSuppliers(el) {
    const summary = await ensureSummary();
    DashboardRenderer.renderSupplierTable(el.querySelector('#supplierTableBody'), summary.supplier_performance);
    Charts.supplierPerformance('chartSupplierPerformance', summary.supplier_performance);
  }

  async function initRecommendations(el) {
    RecommendationsPanel.init(el.querySelector('#recommendationsContainer'), el.querySelector('#recSummaryBadges'), el.querySelector('#recFilterBar'));
    await RecommendationsPanel.refresh(AppState.get().currentBatchId);
  }

  async function initUpload(el) {
    UploadManager.init(el);
    await refreshBatchHistory(el);
  }

  async function refreshBatchHistory(el) {
    try {
      const batches = await API.listBatches();
      DashboardRenderer.renderBatchHistory(el.querySelector('#batchHistoryBody'), batches, AppState.get().currentBatchId, async (batchId) => {
        AppState.set({ currentBatchId: batchId });
        cachedSummary = null;
        await ensureSummary(true);
        navigate('overview');
      });
    } catch (_) { /* upload history is best-effort */ }
  }

  function bindExportButtons(el) {
    // Exports require the bearer token, so they are fetched and saved (a plain
    // link would be rejected with 401 - the 2.4 buttons failed this way).
    const run = (format) => API.downloadExport(AppState.get().currentBatchId || 'latest', format)
      .then(() => Toast.success(`${format.toUpperCase()} report downloaded.`))
      .catch((e) => Toast.error(`Export failed: ${e.message}`));
    el.querySelectorAll('[data-action="export-excel"]').forEach((b) => b.addEventListener('click', () => run('excel')));
    el.querySelectorAll('[data-action="export-pdf"]').forEach((b) => b.addEventListener('click', () => run('pdf')));
    el.querySelectorAll('[data-action="export-csv"]').forEach((b) => b.addEventListener('click', () => run('csv')));
  }

  // ---- Enterprise Feature Initializers ------------------------------------

  function money(v) {
    return fmt.currency(v);
  }

  async function initOptimization(el, opts = {}) {
    const algSel = el.querySelector('#optAlgorithmSelect');
    const seedInp = el.querySelector('#optSeedInput');
    const algorithm = opts.algorithm || algSel?.value || 'abc';
    const seed = opts.seed ?? parseInt(seedInp?.value || '42', 10);
    const eBtn = el.querySelector('#eidsfRunBtn');
    if (eBtn && !eBtn.dataset.bound) {
      eBtn.dataset.bound = '1';
      eBtn.addEventListener('click', async () => {
        const method = el.querySelector('#eidsfMethod')?.value || 'FMCG-ABC';
        const sd = parseInt(seedInp?.value || '42', 10);
        const sum = el.querySelector('#eidsfSummary');
        const body = el.querySelector('#eidsfBody');
        eBtn.disabled = true;
        sum.textContent = `Running ${method} on the current batch (large portfolios take a minute or more)...`;
        try {
          const r = await API.runEidsfPolicy({ method, seed: sd, batch_id: String(AppState.get().currentBatchId || 'latest') });
          const pf = r.portfolio || {};
          const pct = (x, d = 1) => (x === null || x === undefined) ? '—' : `${(100 * x).toFixed(d)}%`;
          const gap = r.certified_gap_pct === null || r.certified_gap_pct === undefined ? '—' : `${r.certified_gap_pct.toFixed(3)}%`;
          const rules = Object.entries(r.rule_counts || {}).map(([k, v]) => `${escapeHtml(k)}: ${v}`).join(', ') || 'none';
          sum.innerHTML = `<strong>${escapeHtml(r.method)}</strong> &middot; ${r.feasible ? 'feasible' : '<span style="color:var(--status-critical)">INFEASIBLE — not a valid policy</span>'}`
            + ` &middot; model cost ${money(r.total_cost)} &middot; lower bound ${money(r.lower_bound)} &middot; certified gap ${gap}`
              + (r.bound_note ? `<br/>${escapeHtml(r.bound_note)}` : '')
            + `<br/>Budget utilisation ${pct(pf.budget_utilisation)} &middot; highest warehouse utilisation ${pct(Math.max(...(pf.warehouse_utilisation || [0])))} (${(pf.warehouse_utilisation || []).length} warehouse pool(s))`
            + ` &middot; EOQ baseline ${money(r.eoq_baseline?.total_cost)} (${r.eoq_baseline?.feasible ? 'feasible' : 'violates constraints'})`
            + `<br/>Rules fired: ${rules}. R1 and R6 need a holdout replay and are not evaluated here.`
            + '<br/><strong>Resources (portfolio level)</strong>: ' + (r.resources || []).map(x =>
                `${escapeHtml(x.resource)} ${pct(x.utilisation)} &ndash; ${escapeHtml(x.status)}`
                + (x.shadow_price !== null && x.shadow_price !== undefined ? ` (shadow price ${x.shadow_price.toFixed(3)}`
                   + (x.status === 'constraining' ? `; 10% more saves about ${money(-x.cost_change_per_10pct_more)} per year, first-order)` : ')') : '')).join('; ')
            + (r.excluded?.length ? `<br/>${r.excluded.length} record(s) excluded: ${r.excluded.slice(0, 5).map(x => `${escapeHtml(x.sku)} (${escapeHtml(x.reason)})`).join(', ')}${r.excluded.length > 5 ? ', …' : ''}.` : '')
            + ((r.input_warnings || []).length ? `<br/>${r.input_warnings.map(escapeHtml).join(' ')}` : '')
            + (r.feasible_region_exists ? '' : '<br/><span style="color:var(--status-critical)">Minimum requirements exceed the budget or capacity: no feasible policy exists.</span>');
          body.innerHTML = (r.skus || []).map(a => `<tr>
            <td>${escapeHtml(a.sku)}</td><td>${a.order_quantity.toFixed(1)}</td><td>${a.safety_stock.toFixed(1)}</td><td>${a.reorder_point.toFixed(1)}</td>
            <td>${money(a.ordering_cost)}</td><td>${money(a.holding_cost)}</td><td>${money(a.shortage_cost)}</td>
            <td>${escapeHtml(a.primary_driver)}</td><td>${(100 * a.model_csl).toFixed(1)}%</td>
            <td style="font-size:11px">${a.rules_fired.map(x => `<strong>${escapeHtml(x.id)}</strong> ${escapeHtml(x.evidence)} &rarr; ${escapeHtml(x.action)}`).join('<br/>') || '—'}</td></tr>`).join('');
          el.querySelector('#eidsfAssumptions').innerHTML = 'Model mapping: ' + (r.assumptions || []).map(escapeHtml).join(' ') + ` Engine ${escapeHtml(r.engine)}; ${r.evaluations} evaluations; seed ${r.seed ?? '—'}.`;
        } catch (e) {
          sum.textContent = 'EIDSF policy failed: ' + (e.message || e);
          Toast.error(e.message || 'EIDSF policy failed');
        } finally {
          eBtn.disabled = false;
        }
      });
    }

    try {
      const data = await API.getOptimizationComparison(AppState.get().currentBatchId || 'latest', algorithm, seed);
      const cur = data.current_policy || {};
      const eoq = data.eoq_policy || {};
      const con = data.constrained_policy || {};
      const solver = data.solver || {};

      el.querySelector('#currentTotalCost').textContent = money(cur.total_inventory_cost);
      el.querySelector('#currentBreakdown').textContent =
        `Holding ${money(cur.holding_cost)} | Ordering ${money(cur.ordering_cost)} | Shortage ${money(cur.shortage_cost)} · Fill rate ${cur.fill_rate ?? '—'}%`;

      el.querySelector('#eoqTotalCost').textContent = money(eoq.total_inventory_cost);
      el.querySelector('#eoqSavings').textContent =
        `Savings ${money(eoq.annual_savings)} (${eoq.savings_percent ?? 0}%) · ` +
        (eoq.respects_constraints ? 'within budget & capacity' : `breaks constraints (needs ${money(eoq.planned_budget_use)})`);

      el.querySelector('#constrainedTotalCost').textContent = money(con.total_inventory_cost);
      el.querySelector('#constrainedSavings').textContent =
        `Net savings ${money(con.annual_savings)} (${con.savings_percent ?? 0}%) · Fill rate ${con.fill_rate ?? '—'}%`;
      const lbl = el.querySelector('#constrainedLabel');
      if (lbl) lbl.textContent = `Constrained policy — ${solver.label || algorithm.toUpperCase()}`;

      Charts.policyComparison('chartPolicyComparison', cur, eoq, con);

      // Service level vs working capital: all values measured by simulation
      const ctxTrade = document.getElementById('chartOptTradeoff');
      if (ctxTrade) {
        Charts.destroy('chartOptTradeoff');
        new Chart(ctxTrade, {
          type: 'bar',
          data: {
            labels: ['Current policy', 'Classical EOQ/SS', `Constrained (${algorithm.toUpperCase()})`],
            datasets: [
              { label: 'Fill rate (%)', data: [cur.fill_rate, eoq.fill_rate, con.fill_rate], backgroundColor: '#22d3b8', borderRadius: 4, yAxisID: 'y' },
              { label: 'Cycle service level (%)', data: [cur.service_level, eoq.service_level, con.service_level].map(v => v == null ? null : Math.round(v * 1000) / 10), backgroundColor: '#a855f7', borderRadius: 4, yAxisID: 'y' },
              { label: 'Avg. inventory value / budget (%)', data: [cur.budget_utilization, eoq.budget_utilization, con.budget_utilization], backgroundColor: '#4f6ef7', borderRadius: 4, yAxisID: 'y' }
            ]
          },
          options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${c.raw}%` } } },
            scales: { x: { grid: { display: false } }, y: { beginAtZero: true, title: { display: true, text: '%' } } }
          }
        });
      }

      // Algorithm benchmark against the exact optimum
      const bBody = el.querySelector('#optBenchmarkBody');
      if (bBody) {
        const rows = data.algorithm_benchmark || [];
        bBody.innerHTML = rows.length ? rows.map(r => `
          <tr>
            <td><strong>${escapeHtml(r.algorithm)}</strong></td>
            <td>${money(r.planning_cost)}</td>
            <td>${r.gap_to_exact_pct == null ? '—' : r.gap_to_exact_pct.toFixed(4) + '%'}</td>
            <td>${r.feasible ? '<span class="badge badge-success">Feasible</span>' : '<span class="badge badge-critical">Violates limits</span>'}</td>
            <td>${r.evaluations ?? '—'}</td>
            <td>${r.runtime_sec == null ? '—' : r.runtime_sec + ' s'}</td>
          </tr>`).join('') : `<tr><td colspan="6" class="text-center text-muted" style="padding:16px">No benchmark available.</td></tr>`;
      }
      const note = el.querySelector('#optMethodNote');
      if (note) {
        const sim = data.simulation_settings || {};
        const imp = data.improvements || {};
        const ci = imp.cost_saving_ci95 || [];
        note.innerHTML = `KPIs measured by Monte Carlo simulation: ${sim.replications} replications × ${sim.days} days ` +
          `(after ${sim.warmup}-day warm-up), ${escapeHtml(sim.dynamics || '')}, seed ${sim.seed}. ` +
          `Annual saving vs current: <strong>${money(imp.cost_saving_annual)}</strong>` +
          (ci.length ? ` (95% CI ${money(ci[0])} – ${money(ci[1])})` : '') +
          `. Solver: ${escapeHtml(solver.label || '')}, gap to exact optimum ${solver.gap_to_exact_pct ?? '—'}%.`;
        const warn = [];
        const cons = data.constraints || {};
        if (cons.feasible_region_exists === false) {
          warn.push(`The budget/capacity cannot cover safety stock alone (minimum ${money(cons.minimum_requirements?.budget)}): no policy can meet the limits at this service level.`);
        } else if ((con.annual_savings ?? 0) < 0) {
          warn.push(`The budget limit (${money(cons.budget_limit)}) binds so tightly that the compliant policy costs more than current practice, which itself uses ${cur.budget_utilization}% of the budget. Review the budget or the service-level target.`);
        }
        if ((solver.gap_to_exact_pct ?? 0) > 1) {
          warn.push(`${escapeHtml(solver.label || 'The metaheuristic')} is ${solver.gap_to_exact_pct}% above the exact optimum at this problem size; use the Exact solver for prescriptions.`);
        }
        if (warn.length) note.innerHTML += `<div style="margin-top:8px;padding:10px 12px;border-radius:6px;border:1px solid var(--status-critical, #ef4444)"><strong>Decision warning:</strong> ${warn.join(' ')}</div>`;
      }

      const itemsBody = el.querySelector('#optItemsTableBody');
      const items = data.items || [];
      if (!items.length) {
        itemsBody.innerHTML = `<tr><td colspan="10" class="text-center text-muted" style="padding:24px">No optimization items available.</td></tr>`;
      } else {
        itemsBody.innerHTML = items.slice(0, 50).map(i => {
          const s = i.expected_annual_saving || 0;
          return `
          <tr>
            <td><span class="sku-tag" data-open-item="${i.id}" style="cursor:pointer">${escapeHtml(i.sku)}</span></td>
            <td><strong>${escapeHtml(i.product_name)}</strong></td>
            <td><span class="badge badge-neutral">${fmt.cls(i.abc_class)}-${fmt.cls(i.xyz_class)}</span></td>
            <td>${i.quantity_on_hand}</td>
            <td>${i.current_rop}</td>
            <td><strong style="color:var(--text-accent)">${i.optimized_rop}</strong></td>
            <td>${i.current_safety_stock}</td>
            <td><strong style="color:var(--signal-teal)">${i.optimized_safety_stock}</strong></td>
            <td>${i.optimized_order_qty}</td>
            <td style="color:${s >= 0 ? 'var(--status-healthy)' : 'var(--status-critical)'};font-weight:600">${s >= 0 ? '+' : '−'}${money(Math.abs(s))}</td>
          </tr>`;
        }).join('');
      }

      const runBtn = el.querySelector('#runOptBtn');
      if (runBtn && !runBtn.dataset.bound) {
        runBtn.dataset.bound = '1';
        runBtn.addEventListener('click', () => withBusy(runBtn, async () => {
          const alg = algSel?.value || 'abc';
          const sd = parseInt(seedInp?.value || '42', 10);
          try {
            Toast.info(`Running ${alg.toUpperCase()} solver and simulation...`);
            const res = await API.runOptimization({ algorithm: alg, seed: sd, batch_id: String(AppState.get().currentBatchId || 'latest') });
            Toast.success(`Run #${res.run_id} saved to the audit trail.`);
            await initOptimization(el, { algorithm: alg, seed: sd });
          } catch (e) {
            Toast.error(e.message || 'Optimization solver failed');
          }
        }));
        algSel?.addEventListener('change', () => initOptimization(el));
      }
    } catch (err) {
      console.error('Failed to init optimization view:', err);
      Toast.error('Optimization view error: ' + err.message);
    }
  }

  async function initForecasting(el) {
    try {
      let horizon = parseInt(el.querySelector('#forecastHorizonSelect')?.value || '30', 10);
      const illustrative = !!el.querySelector('#forecastIllustrativeToggle')?.checked;
      const data = await API.getForecastingOverview(AppState.get().currentBatchId || 'latest', illustrative);
      const metrics = data.overall_metrics || {};
      const fmt2 = (v, suf = '') => (v === null || v === undefined) ? '—' : `${v}${suf}`;

      // MAPE is undefined when demand contains zeros; WAPE is shown instead.
      el.querySelector('#forecastMape').textContent = metrics.average_mape != null
        ? fmt2(metrics.average_mape, '%') : (metrics.average_wape != null ? `WAPE ${metrics.average_wape}%` : '—');
      el.querySelector('#forecastMae').textContent = fmt2(metrics.average_mae);
      el.querySelector('#forecastRmse').textContent = fmt2(metrics.average_rmse);
      el.querySelector('#forecastTotalItems').textContent = `${data.total_items_forecasted || 0} / ${data.total_items || 0}`;

      const sub = el.querySelector('#forecastChartSubtitle');
      const src = metrics.history_sources || {};
      const banner = el.querySelector('#forecastDataBanner');
      if (banner) {
        const missing = (data.insufficient_history || []).length;
        const msgs = [];
        if (metrics.illustrative_only) {
          msgs.push('ILLUSTRATION ONLY: no recorded demand history - the series shown are synthetic and say nothing about forecast accuracy. Do not use them for decisions.');
        } else if (missing) {
          msgs.push(`${missing} SKU(s) have fewer than ${data.min_history_points} recorded daily demand observations and are not forecast. Import demand history on the Data Upload page.`);
        }
        if (data.data_period) msgs.push(`Recorded history used: ${data.data_period.from} to ${data.data_period.to} (units/day).`);
        banner.style.display = msgs.length ? 'block' : 'none';
        banner.textContent = msgs.join(' ');
      }
      if (!el.dataset.illustrativeBound) {
        el.dataset.illustrativeBound = '1';
        el.querySelector('#forecastIllustrativeToggle')?.addEventListener('change', () => initForecasting(el));
      }
      if (!data.total_items_forecasted) {
        Charts.destroy('chartDemandForecast');
        if (sub) sub.textContent = 'No forecast: no SKU has enough recorded demand history.';
      }

      const skuSelect = el.querySelector('#forecastSkuSelect');
      const items = data.sample_forecasts || [];
      if (skuSelect) {
        skuSelect.innerHTML = `<option value="aggregate">All SKUs (Aggregate Network Demand)</option>` +
          items.map(i => `<option value="${i.id}">${escapeHtml(i.sku)} — ${escapeHtml(i.product_name)}</option>`).join('');
      }

      const agg = data.aggregate || { history: [], forecast: [] };
      const itemCache = {};

      async function renderForecastChart() {
        const selVal = skuSelect?.value || 'aggregate';
        let hist = agg.history || [];
        let path = agg.forecast || [];
        let modelLabel = 'Holt or SBA per SKU by demand pattern (sum)';
        let source = metrics.illustrative_only ? 'synthetic (illustrative)' : `recorded: ${src.recorded || 0}, illustrative: ${src.synthetic_illustrative || 0}, insufficient: ${src.insufficient_history || 0}`;
        if (selVal !== 'aggregate') {
          try {
            itemCache[selVal] = itemCache[selVal] || await API.getForecastItem(selVal, illustrative);
            const f = itemCache[selVal];
            hist = (f.history || []).map(h => h.quantity);
            path = f.forecast_path || [];
            source = f.history_source === 'recorded' ? 'recorded history' : 'synthetic (illustrative)';
            if (f.selected_model) modelLabel = `${f.selected_model.model} — ${f.selected_model.pattern} demand`;
          } catch (e) { Toast.error('Item forecast failed: ' + e.message); }
        }
        if (sub) sub.textContent = `${modelLabel}, ${horizon}-day horizon, approximate 95% band · history: ${source}`;
        Charts.demandForecast('chartDemandForecast', hist, path.slice(0, horizon), horizon);
      }

      if (data.total_items_forecasted) await renderForecastChart();
      skuSelect?.addEventListener('change', renderForecastChart);
      el.querySelector('#forecastHorizonSelect')?.addEventListener('change', (e) => {
        horizon = parseInt(e.target.value, 10);
        renderForecastChart();
      });

      const ctxModel = document.getElementById('chartModelComparison');
      const mc = data.model_comparison || {};
      const models = ['Naive (last value)', 'SMA', 'WMA', 'SES', 'Holt', 'Croston', 'SBA'].filter(m => mc[m]);
      const useWape = models.some(m => mc[m].mape == null);
      if (ctxModel) {
        Charts.destroy('chartModelComparison');
        new Chart(ctxModel, {
          type: 'bar',
          data: {
            labels: models.length ? models : ['No data'],
            datasets: [{
              label: `Out-of-sample ${useWape ? 'WAPE' : 'MAPE'} (%) — 7-day holdout`,
              data: models.map(m => useWape ? mc[m].wape : mc[m].mape),
              backgroundColor: ['#cbd5e1', '#94a3b8', '#64748b', '#22d3b8', '#4f6ef7', '#f59e0b', '#ef4444'],
              borderRadius: 4
            }]
          },
          options: {
            responsive: true, maintainAspectRatio: false,
            scales: { x: { grid: { display: false } }, y: { beginAtZero: true, title: { display: true, text: useWape ? 'WAPE (%)' : 'MAPE (%)' } } }
          }
        });
      }

      const watch = data.volatility_watchlist || [];
      Charts.volatilityWatchlist('chartVolatilityWatchlist', watch);
      const watchBody = el.querySelector('#forecastWatchlistBody');
      if (watchBody) {
        watchBody.innerHTML = watch.length ? watch.map(i => `
          <tr>
            <td><span class="sku-tag" data-open-item="${i.id}" style="cursor:pointer">${escapeHtml(i.sku)}</span></td>
            <td><strong>${escapeHtml(i.product_name)}</strong></td>
            <td>${i.daily_avg_demand} / day</td>
            <td><span class="badge ${i.cv > 0.3 ? 'badge-critical' : 'badge-neutral'}">CV: ${i.cv == null ? '—' : i.cv.toFixed(2)}</span></td>
            <td style="color:var(--signal-teal)">&plusmn;${Math.round(1.96 * (i.daily_sd || 0) * 10) / 10} units/day</td>
          </tr>`).join('') : `<tr><td colspan="5" class="text-center text-muted" style="padding:16px">No variability data.</td></tr>`;
      }
    } catch (err) {
      console.error('Failed to init forecasting view:', err);
      Toast.error('Forecasting view error: ' + err.message);
    }
  }

  async function initScenarios(el) {
    const sDemand = el.querySelector('#sliderDemandShock');
    const sLead = el.querySelector('#sliderLeadTimeAdd');
    const sHolding = el.querySelector('#sliderHoldingRate');
    const sBudget = el.querySelector('#sliderBudgetLimit');

    const vDemand = el.querySelector('#demandShockVal');
    const vLead = el.querySelector('#leadTimeAddVal');
    const vHolding = el.querySelector('#holdingRateVal');
    const vBudget = el.querySelector('#budgetLimitVal');
    const signed = (v, f) => `${v >= 0 ? '+' : '−'}${f(Math.abs(v))}`;
    // The holding-rate slider is relative to the organisation's configured rate (2.4 assumed 20 %).
    const cfg = await API.getOrgConfig().catch(() => ({}));
    const baseHolding = Math.round(((cfg.annual_holding_rate || 0.20) * 100) * 10) / 10;
    if (sHolding) sHolding.value = baseHolding;
    if (sBudget && cfg.annual_working_capital_budget) {
      const b = cfg.annual_working_capital_budget;
      sBudget.min = Math.round(b * 0.1); sBudget.max = Math.round(b * 3); sBudget.step = Math.max(1, Math.round(b / 50));
      sBudget.value = b;
    }

    function syncLabels() {
      if (vDemand && sDemand) vDemand.textContent = `${sDemand.value >= 0 ? '+' : ''}${sDemand.value}%`;
      if (vLead && sLead) vLead.textContent = `+${sLead.value} Days`;
      if (vHolding && sHolding) vHolding.textContent = `${sHolding.value}%`;
      if (vBudget && sBudget) vBudget.textContent = fmt.currency(parseInt(sBudget.value, 10));
    }

    [sDemand, sLead, sHolding, sBudget].forEach(s => s?.addEventListener('input', syncLabels));

    function setDelta(id, delta, fmt, badWhenPositive = true) {
      const b = el.querySelector(id);
      if (!b) return;
      b.textContent = signed(delta, fmt);
      b.className = `badge ${(delta > 0) === badWhenPositive && delta !== 0 ? 'badge-critical' : 'badge-success'}`;
    }

    async function runSimulation() {
      try {
        const params = {
          demand_pct_change: parseFloat(sDemand?.value || 0),
          lead_time_days_add: parseFloat(sLead?.value || 0),
          holding_rate_pct_change: ((parseFloat(sHolding?.value || baseHolding) / baseHolding) - 1) * 100,
          budget_limit: parseFloat(sBudget?.value || 1000000),
          batch_id: String(AppState.get().currentBatchId || 'latest')
        };
        const res = await API.simulateScenario(params);
        const base = res.baseline || {};
        const sim = res.scenario || {};
        const nr = res.scenario_no_replan || {};
        const vr = res.value_of_replanning || {};

        el.querySelector('#baseStockoutVal').textContent = `${base.estimated_fill_rate ?? '—'}%`;
        el.querySelector('#simStockoutVal').textContent = `${sim.estimated_fill_rate ?? '—'}%`;
        setDelta('#badgeStockoutDelta', (sim.estimated_fill_rate || 0) - (base.estimated_fill_rate || 0), v => `${v.toFixed(2)} pp`, false);

        el.querySelector('#simCapitalVal').textContent = money(sim.total_inventory_value);
        setDelta('#badgeCapitalDelta', (sim.total_inventory_value || 0) - (base.total_inventory_value || 0), money);

        el.querySelector('#simHoldingVal').textContent = money(sim.holding_cost);
        setDelta('#badgeHoldingDelta', (sim.holding_cost || 0) - (base.holding_cost || 0), money);

        el.querySelector('#simTotalCostVal').textContent = money(sim.total_annual_cost);
        setDelta('#badgeCostDelta', (sim.total_annual_cost || 0) - (base.total_annual_cost || 0), money);

        Charts.scenarioCostImpact('chartScenarioCost', {
          holding_cost: base.holding_cost || 0,
          ordering_cost: base.ordering_cost || 0,
          stockout_cost: base.shortage_cost || 0,
          total_cost: base.total_annual_cost || 0
        }, {
          holding_cost: sim.holding_cost || 0,
          ordering_cost: sim.ordering_cost || 0,
          stockout_cost: sim.shortage_cost || 0,
          total_cost: sim.total_annual_cost || 0
        });

        const ctxRisk = document.getElementById('chartScenarioRisk');
        if (ctxRisk) {
          Charts.destroy('chartScenarioRisk');
          new Chart(ctxRisk, {
            type: 'bar',
            data: {
              labels: ['Baseline (normal)', 'Stress: keep current policy', 'Stress: re-plan with DSS'],
              datasets: [
                { label: `Total annual cost (${fmt.currencyCode()})`, data: [base.total_annual_cost, nr.total_annual_cost, sim.total_annual_cost], backgroundColor: ['#94a3b8', '#ef4444', '#4f6ef7'], borderRadius: 4, yAxisID: 'y' },
                { label: 'Fill rate (%)', type: 'line', data: [base.estimated_fill_rate, nr.estimated_fill_rate, sim.estimated_fill_rate], borderColor: '#22d3b8', backgroundColor: '#22d3b8', yAxisID: 'y1' }
              ]
            },
            options: {
              responsive: true, maintainAspectRatio: false,
              scales: {
                x: { grid: { display: false } },
                y: { beginAtZero: true, title: { display: true, text: `Cost (${fmt.currencyCode()})` } },
                y1: { position: 'right', grid: { display: false }, title: { display: true, text: 'Fill rate (%)' } }
              }
            }
          });
        }
        const vrEl = el.querySelector('#valueOfReplanning');
        if (vrEl) {
          vrEl.textContent = `Re-planning under this stress saves ${money(vr.cost_saving_vs_no_replan)} per year and changes fill rate by ` +
            `${(vr.fill_rate_gain_pp_vs_no_replan ?? 0).toFixed(2)} pp versus keeping the current policy (simulated, ${res.parameters?.replications} replications, seed ${res.parameters?.seed}).`;
        }
      } catch (err) {
        console.error('Scenario run error:', err);
        Toast.error(err.message || 'Simulation error');
      }
    }

    const runBtnS = el.querySelector('#scenarioRunBtn');
    runBtnS?.addEventListener('click', () => withBusy(runBtnS, runSimulation));
    const resetBtn = el.querySelector('#scenarioResetBtn');
    resetBtn?.addEventListener('click', () => withBusy(resetBtn, async () => {
      if (sDemand) sDemand.value = 0;
      if (sLead) sLead.value = 0;
      if (sHolding) sHolding.value = baseHolding;
      if (sBudget) sBudget.value = cfg.annual_working_capital_budget || sBudget.value;
      syncLabels();
      await runSimulation();
    }));

    syncLabels();
    await runSimulation();
  }

  // Procurement: replenishment plan -> draft POs -> approve -> order -> receive.
  // Every action is a server-enforced state transition; buttons are shown only
  // for the actions the server lists in `allowed_actions`.
  async function initProcurement(el) {
    const planBody = el.querySelector('#procurementTableBody');
    const poBody = el.querySelector('#poTableBody');
    const statusFilter = el.querySelector('#poStatusFilter');

    async function loadPlan() {
      try {
        const data = await API.getProcurementPlan();
        const orders = data.recommended_orders || [];
        el.querySelector('#procCriticalOrdersCount').textContent = `${data.critical_orders_count || 0}`;
        el.querySelector('#procTotalOrdersCount').textContent = `${data.total_recommended_orders || 0}`;
        el.querySelector('#procTotalValue').textContent = fmt.currency(data.total_procurement_value || 0);
        el.querySelector('#procSuppliersCount').textContent = `${new Set(orders.map(o => o.supplier_id).filter(Boolean)).size}`;
        planBody.innerHTML = orders.length ? orders.map(o => `
          <tr>
            <td><span class="sku-tag" data-open-item="${o.item_id}" style="cursor:pointer">${escapeHtml(o.sku)}</span></td>
            <td><strong>${escapeHtml(o.product_name)}</strong>${(o.data_warnings || []).length ? `<div class="text-muted" style="font-size:10px">${o.data_warnings.map(escapeHtml).join('; ')}</div>` : ''}</td>
            <td>${escapeHtml(o.supplier || 'Unassigned')}</td>
            <td>${escapeHtml(o.warehouse || '—')}</td>
            <td>${fmt.number(o.quantity_on_hand)}</td>
            <td>${fmt.number(o.reorder_point)}</td>
            <td><strong style="color:var(--text-accent)">${fmt.number(o.recommended_order_quantity)}</strong><div class="text-muted" style="font-size:10px">EOQ ${o.eoq_benchmark || '—'}</div></td>
            <td>${fmt.currency(o.unit_cost, 2)}</td>
            <td><strong>${fmt.currency(o.order_value)}</strong></td>
            <td><span class="badge ${o.urgency === 'CRITICAL' ? 'badge-critical' : 'badge-low'}">${escapeHtml(o.urgency)}</span></td>
          </tr>`).join('')
          : `<tr><td colspan="10" class="text-center text-muted" style="padding:24px">All stock levels are above their reorder points. No replenishment is required.</td></tr>`;
      } catch (err) {
        planBody.innerHTML = `<tr><td colspan="10" class="text-center" style="padding:24px">Could not load the plan: ${escapeHtml(err.message)} <button class="btn btn-secondary btn-sm" data-retry="plan">Retry</button></td></tr>`;
      }
    }

    const ACTION_LABEL = { approve: 'Approve', order: 'Mark ordered', receive: 'Receive all', cancel: 'Cancel' };
    async function loadOrders() {
      try {
        const pos = await API.listPurchaseOrders(statusFilter?.value || '');
        poBody.innerHTML = pos.length ? pos.map(p => `
          <tr>
            <td><strong>${escapeHtml(p.po_number)}</strong></td>
            <td>${escapeHtml(p.supplier_name)}</td>
            <td><span class="badge badge-neutral">${escapeHtml(p.status.replace('_', ' '))}</span></td>
            <td>${p.item_count}</td>
            <td>${fmt.currency(p.total_amount)}</td>
            <td>${escapeHtml(p.created_by || '')}${p.approved_by ? ` / ${escapeHtml(p.approved_by)}` : ''}</td>
            <td>${fmt.date(p.expected_delivery_date)}</td>
            <td style="white-space:nowrap">${(p.allowed_actions || []).map(a =>
              `<button class="btn ${a === 'cancel' ? 'btn-secondary' : 'btn-primary'} btn-sm" data-po-id="${p.id}" data-po-action="${a}" style="padding:2px 8px;font-size:11px;margin-right:4px">${ACTION_LABEL[a] || a}</button>`).join('') || '<span class="text-muted">—</span>'}</td>
          </tr>`).join('')
          : `<tr><td colspan="8" class="text-center text-muted" style="padding:24px">No purchase orders${statusFilter?.value ? ' with this status' : ''} yet.</td></tr>`;
      } catch (err) {
        poBody.innerHTML = `<tr><td colspan="8" class="text-center" style="padding:24px">Could not load purchase orders: ${escapeHtml(err.message)} <button class="btn btn-secondary btn-sm" data-retry="orders">Retry</button></td></tr>`;
      }
    }

    el.addEventListener('click', async (e) => {
      const retry = e.target.closest('[data-retry]');
      if (retry) { retry.dataset.retry === 'plan' ? loadPlan() : loadOrders(); return; }
      const btn = e.target.closest('[data-po-action]');
      if (!btn) return;
      const id = btn.dataset.poId, action = btn.dataset.poAction;
      if (action === 'cancel' && !window.confirm('Cancel this purchase order?')) return;
      btn.disabled = true;
      try {
        await API.poAction(id, action, action === 'receive' ? {} : undefined);
        Toast.success(action === 'receive' ? 'Goods received; stock updated.' : `Purchase order: ${ACTION_LABEL[action] || action} done.`);
        cachedSummary = null;
        await Promise.all([loadOrders(), loadPlan()]);
      } catch (err) {
        Toast.error(err.message);
        btn.disabled = false;
      }
    });

    el.querySelector('#createPoBatchBtn')?.addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        const res = await API.draftsFromPlan();
        const n = (res.created || []).length;
        Toast.success(n ? `${n} draft purchase order(s) created (one per supplier). Review and approve them below.`
                        : 'No draft created: nothing to reorder.');
        if ((res.skipped || []).length) Toast.info(`${res.skipped.length} line(s) skipped (e.g. no supplier assigned).`);
        await loadOrders();
      } catch (err) {
        Toast.error(err.message);
      } finally {
        btn.disabled = false;
      }
    });
    el.querySelector('#refreshProcurementBtn')?.addEventListener('click', () => { loadPlan(); loadOrders(); });
    statusFilter?.addEventListener('change', loadOrders);
    el.querySelector('#procSearchInput')?.addEventListener('input', (e) => {
      const q = e.target.value.toLowerCase();
      planBody.querySelectorAll('tr').forEach(tr => { tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none'; });
    });
    await Promise.all([loadPlan(), loadOrders()]);
  }

  async function initDecisions(el) {
    try {
      const data = await API.getDecisionHistory();
      const history = data.decisions || [];

      let accepted = 0, modified = 0, rejected = 0;
      history.forEach(d => {
        if (d.action_taken === 'accepted') accepted++;
        else if (d.action_taken === 'modified') modified++;
        else if (d.action_taken === 'rejected') rejected++;
      });

      el.querySelector('#decTotalReviewed').textContent = `${history.length}`;
      el.querySelector('#decAcceptedCount').textContent = `${accepted}`;
      el.querySelector('#decModifiedCount').textContent = `${modified}`;
      el.querySelector('#decRejectedCount').textContent = `${rejected}`;

      const tbody = el.querySelector('#decisionsTableBody');
      if (!history.length) {
        tbody.innerHTML = `<tr><td colspan="7" class="text-center text-muted" style="padding:24px">No decisions recorded yet. Accept or reject recommendations from the AI Recommendations page to see governance records here.</td></tr>`;
      } else {
        tbody.innerHTML = history.map(d => {
          let badgeClass = 'badge-success';
          if (d.action_taken === 'modified') badgeClass = 'badge-neutral';
          if (d.action_taken === 'rejected') badgeClass = 'badge-critical';
          return `
            <tr>
              <td class="mono" style="font-size:11px">${fmt.date(d.created_at)}</td>
              <td class="mono">DEC-${d.id}</td>
              <td><strong>${escapeHtml(d.recommendation_title || 'Inventory Reorder Action')}</strong></td>
              <td><span class="badge ${badgeClass}">${(d.action_taken || '').toUpperCase()}</span></td>
              <td>${escapeHtml(d.username || 'admin')}</td>
              <td style="font-size:12px">${escapeHtml(d.manager_comment || 'Direct human approval')}</td>
              <td><span class="badge badge-info">${escapeHtml(d.affected_kpi || 'Stockout Risk')}</span></td>
            </tr>
          `;
        }).join('');
      }

      el.querySelector('#refreshDecisionsBtn')?.addEventListener('click', () => initDecisions(el));
    } catch (err) {
      console.error('Decisions init error:', err);
      Toast.error('Decisions log error: ' + err.message);
    }
  }

  async function initConfig(el) {
    try {
      const config = await API.getOrgConfig();

      const slSlider = el.querySelector('#cfgServiceLevel');
      const slVal = el.querySelector('#cfgServiceLevelVal');
      const hrSlider = el.querySelector('#cfgHoldingRate');
      const hrVal = el.querySelector('#cfgHoldingRateVal');

      if (slSlider && slVal) {
        slSlider.value = config.service_level_target || 0.95;
        slVal.textContent = `${Math.round(slSlider.value * 100)}%`;
        slSlider.oninput = () => { slVal.textContent = `${Math.round(slSlider.value * 100)}%`; };
      }

      if (hrSlider && hrVal) {
        hrSlider.value = config.annual_holding_rate || 0.20;
        hrVal.textContent = `${Math.round(hrSlider.value * 100)}%`;
        hrSlider.oninput = () => { hrVal.textContent = `${Math.round(hrSlider.value * 100)}%`; };
      }

      if (el.querySelector('#cfgSetupCost')) el.querySelector('#cfgSetupCost').value = config.order_setup_cost || 50;
      if (el.querySelector('#cfgShortageMultiplier')) el.querySelector('#cfgShortageMultiplier').value = config.shortage_cost_multiplier || 1.5;
      if (el.querySelector('#cfgBudget')) el.querySelector('#cfgBudget').value = config.annual_working_capital_budget || 1000000;
      if (el.querySelector('#cfgExpiryDays')) el.querySelector('#cfgExpiryDays').value = config.alert_expiry_threshold_days || 90;

      const notes = el.querySelector('#dataQualityNotes');
      try {
        const quality = await API.getDataQuality();
        const drawn = Charts.dataQualityRadar('chartDataQuality', quality);
        const rem = quality.remediations || [];
        if (notes) {
          notes.innerHTML = !quality.total_skus ? 'No inventory data yet.'
            : `Health score ${quality.overall_health_score} / 100 over ${quality.total_skus} SKUs` +
              ` (${quality.skus_with_demand_history || 0} with 14+ days of recorded demand).<br/>` +
              (rem.length ? rem.map((r) => `• <strong>${escapeHtml(r.field)}</strong> (${r.affected_skus} SKUs): ${escapeHtml(r.instruction)}`).join('<br/>')
                          : '• No missing cost, demand, lead-time or supplier data found.');
        }
        if (!drawn && notes && quality.total_skus) notes.innerHTML += '<br/>Chart unavailable.';
      } catch (e) {
        if (notes) notes.textContent = `Data quality could not be loaded: ${e.message}`;
      }

      el.querySelector('#saveConfigBtn')?.addEventListener('click', async (e) => {
        e.preventDefault();
        try {
          const payload = {
            service_level_target: parseFloat(slSlider?.value || 0.95),
            annual_holding_rate: parseFloat(hrSlider?.value || 0.20),
            order_setup_cost: parseFloat(el.querySelector('#cfgSetupCost')?.value || 50),
            shortage_cost_multiplier: parseFloat(el.querySelector('#cfgShortageMultiplier')?.value || 1.5),
            annual_working_capital_budget: parseFloat(el.querySelector('#cfgBudget')?.value || 1000000),
            alert_expiry_threshold_days: parseInt(el.querySelector('#cfgExpiryDays')?.value || 90, 10)
          };
          await API.updateOrgConfig(payload);
          Toast.success('Configuration parameters updated successfully!');
        } catch (err) {
          Toast.error(err.message || 'Failed to save configuration');
        }
      });
      await initAccountSections(el);
    } catch (err) {
      console.error('Config init error:', err);
      Toast.error('Config load error: ' + err.message);
    }
  }

  // Organisation profile, plan/usage/billing, team and invitations, owner tools
  // and own account. The server enforces every permission (core/permissions.py);
  // controls are hidden for other roles only as a convenience.
  const ROLE_LABELS = { owner: 'Owner', admin: 'Administrator', inventory_manager: 'Inventory manager',
    procurement_manager: 'Procurement manager', warehouse_manager: 'Warehouse manager',
    executive: 'Analyst (read-only)' };

  async function guarded(btn, fn) {
    if (btn) btn.disabled = true;
    try { await fn(); } catch (err) { Toast.error(err.message); } finally { if (btn) btn.disabled = false; }
  }

  async function initAccountSections(el) {
    const me = AppState.get().me || await API.getAuthMe().catch(() => ({}));
    const isOwner = me.role === 'owner';
    const isAdmin = isOwner || me.role === 'admin';
    el.querySelectorAll('[data-admin-only]').forEach((n) => n.classList.toggle('role-ok', isAdmin));
    el.querySelectorAll('[data-owner-only]').forEach((n) => n.classList.toggle('role-ok', isOwner));

    let orgName = '';
    try {
      const p = await API.getOrgProfile();
      orgName = p.name || '';
      el.querySelector('#orgName').value = p.name || '';
      el.querySelector('#orgCurrency').value = p.currency || 'PKR';
      el.querySelector('#orgTimezone').value = p.timezone || 'Asia/Karachi';
      el.querySelector('#orgFyStart').value = p.fiscal_year_start_month || 7;
      if (!isAdmin) el.querySelectorAll('#orgProfileCard input, #orgProfileCard select').forEach((i) => { i.disabled = true; });
    } catch (e) { Toast.error(`Profile: ${e.message}`); }
    el.querySelector('#saveOrgProfileBtn')?.addEventListener('click', (ev) => guarded(ev.currentTarget, async () => {
      const p = await API.updateOrgProfile({
        name: el.querySelector('#orgName').value.trim(),
        currency: el.querySelector('#orgCurrency').value,
        timezone: el.querySelector('#orgTimezone').value.trim(),
        fiscal_year_start_month: parseInt(el.querySelector('#orgFyStart').value, 10),
      });
      OrgPrefs.currency = p.currency; OrgPrefs.timezone = p.timezone;
      Toast.success('Organisation profile saved.');
    }));

    // ---- plan, usage, access state, billing history
    try {
      const sub = await API.getSubscription();
      const label = { users: 'Active users', warehouses: 'Warehouses', skus: 'Active SKUs',
                      imports_per_month: 'Imports this month', optimisations_per_month: 'Optimisation runs this month' };
      const used = { users: sub.usage.users, warehouses: sub.usage.warehouses, skus: sub.usage.skus,
                     imports_per_month: sub.usage.imports_this_month, optimisations_per_month: sub.usage.optimisations_this_month };
      el.querySelector('#subUsageBody').innerHTML = Object.keys(label).map((k) =>
        `<tr><td>${label[k]}</td><td>${fmt.int(used[k])}</td><td>${sub.limits[k] == null ? 'Unlimited' : fmt.int(sub.limits[k])}</td></tr>`).join('');
      el.querySelector('#subBilling').textContent =
        `Plan: ${sub.label} · status: ${sub.subscription_status}. ${sub.billing.message}`;
      const acc = sub.access || {};
      el.querySelector('#subAccess').textContent = acc.state === 'read_only'
        ? `Access: read-only. ${acc.message}`
        : (acc.grace_until ? `Access: full until ${acc.grace_until} UTC (payment overdue, grace period).`
                           : 'Access: full (within the plan limits above).');
    } catch (e) { el.querySelector('#subUsageBody').innerHTML = `<tr><td colspan="3">${escapeHtml(e.message)}</td></tr>`; }
    if (isAdmin) {
      try {
        const h = await API.getBillingHistory();
        el.querySelector('#invoiceNote').textContent = h.invoices_note || '';
        el.querySelector('#billingHistoryBody').innerHTML = h.history.length ? h.history.map((r) =>
          `<tr><td>${escapeHtml(r.changed_at.replace('T', ' ').slice(0, 16))}</td><td>${escapeHtml(r.source)}</td>
           <td>${escapeHtml(r.to_plan || '')}</td><td>${escapeHtml(r.to_status || '')}</td><td>${escapeHtml(r.note || '')}</td></tr>`).join('')
          : '<tr><td colspan="5" class="text-muted">No changes recorded yet.</td></tr>';
      } catch (e) { el.querySelector('#billingHistoryBody').innerHTML = `<tr><td colspan="5">${escapeHtml(e.message)}</td></tr>`; }
    }

    // ---- team
    let users = [];
    async function loadUsers() {
      if (!isAdmin) return;
      try {
        users = await API.getUsers();
        el.querySelector('#usersBody').innerHTML = users.map((u) => {
          const locked = u.role === 'owner' && u.username !== me.username;
          const actions = (u.role === 'owner') ? '<span class="text-muted">Changed only by ownership transfer</span>' : `
              <button class="btn btn-secondary btn-sm" data-user-id="${u.id}" data-user-act="${u.is_active ? 'deactivate' : 'activate'}">${u.is_active ? 'Deactivate' : 'Activate'}</button>
              <button class="btn btn-secondary btn-sm" data-user-id="${u.id}" data-user-act="reset">Reset password</button>
              ${u.mfa_enabled && u.username !== me.username ? `<button class="btn btn-secondary btn-sm" data-user-id="${u.id}" data-user-act="mfa">Reset 2FA</button>` : ''}`;
          return `
          <tr>
            <td>${escapeHtml(u.username)}</td><td>${escapeHtml(u.full_name || '')}</td><td>${escapeHtml(ROLE_LABELS[u.role] || u.role)}</td>
            <td>${u.is_active ? 'Active' : 'Deactivated'}</td><td>${fmt.datetime(u.last_login)}</td>
            <td style="white-space:nowrap">${locked ? '<span class="text-muted">Owner</span>' : actions}</td>
          </tr>`;
        }).join('');
        const sel = el.querySelector('#toUser');
        if (sel) {
          sel.innerHTML = users.filter((u) => u.is_active && u.role !== 'owner')
            .map((u) => `<option value="${u.id}">${escapeHtml(u.username)} (${escapeHtml(ROLE_LABELS[u.role] || u.role)})</option>`).join('');
        }
      } catch (e) { Toast.error(`Users: ${e.message}`); }
    }
    el.querySelector('#usersBody')?.addEventListener('click', (e) => {
      const b = e.target.closest('[data-user-act]');
      if (!b) return;
      const id = b.dataset.userId;
      guarded(b, async () => {
        if (b.dataset.userAct === 'mfa') {
          if (!window.confirm('Switch off two-factor login for this user (lost phone)? Confirm their identity first.')) return;
          const r = await API.resetUserMfa(id);
          Toast.success(r.message);
        } else if (b.dataset.userAct === 'reset') {
          const pw = window.prompt('New temporary password for this user (at least 8 characters; 12 in production):');
          if (!pw) return;
          await API.resetUserPassword(id, pw);
          Toast.success('Password reset; the user was signed out everywhere.');
        } else {
          if (b.dataset.userAct === 'deactivate' && !window.confirm('Deactivate this user? They are signed out everywhere.')) return;
          await API.updateUser(id, { is_active: b.dataset.userAct === 'activate' });
          Toast.success('User updated.');
        }
        await loadUsers();
      });
    });
    el.querySelector('#newUserForm')?.addEventListener('submit', (e) => {
      e.preventDefault();
      guarded(e.target.querySelector('[type=submit]'), async () => {
        await API.createUser({ username: el.querySelector('#nuUsername').value.trim(),
                               full_name: el.querySelector('#nuName').value.trim() || null,
                               role: el.querySelector('#nuRole').value,
                               password: el.querySelector('#nuPassword').value });
        e.target.reset();
        Toast.success('User created.');
        await loadUsers();
      });
    });

    // ---- invitations
    async function loadInvites() {
      if (!isAdmin) return;
      try {
        const st = await API.getEmailStatus();
        el.querySelector('#emailStatus').textContent = st.message;
        const rows = await API.getInvitations();
        el.querySelector('#invitesBody').innerHTML = rows.length ? rows.map((r) => `
          <tr><td>${escapeHtml(r.email || '')}</td><td>${escapeHtml(ROLE_LABELS[r.role] || r.role)}</td>
          <td>${escapeHtml(r.invited_by || '')}</td><td>${escapeHtml(r.expires_at.replace('T', ' ').slice(0, 16))}</td>
          <td>${escapeHtml(r.state)}</td>
          <td>${r.state === 'pending' ? `<button class="btn btn-secondary btn-sm" data-invite-revoke="${r.id}">Revoke</button>` : ''}</td></tr>`).join('')
          : '<tr><td colspan="6" class="text-muted">No invitations yet.</td></tr>';
      } catch (e) { Toast.error(`Invitations: ${e.message}`); }
    }
    el.querySelector('#inviteForm')?.addEventListener('submit', (e) => {
      e.preventDefault();
      guarded(e.target.querySelector('[type=submit]'), async () => {
        const r = await API.createInvitation(el.querySelector('#invEmail').value.trim(), el.querySelector('#invRole').value);
        const box = el.querySelector('#inviteLinkBox');
        if (r.accept_url) {
          el.querySelector('#inviteLink').value = r.accept_url;
          box.classList.remove('hidden');
          Toast.info('Invitation created - no e-mail was sent; share the link below.');
        } else {
          box.classList.add('hidden');
          Toast.success('Invitation e-mail handed to the mail server.');
        }
        e.target.reset();
        await loadInvites();
      });
    });
    el.querySelector('#invitesBody')?.addEventListener('click', (e) => {
      const b = e.target.closest('[data-invite-revoke]');
      if (!b || !window.confirm('Revoke this invitation? The link stops working.')) return;
      guarded(b, async () => { await API.revokeInvitation(b.dataset.inviteRevoke); Toast.success('Invitation revoked.'); await loadInvites(); });
    });

    // ---- owner tools
    if (isOwner) {
      el.querySelector('#exportOrgBtn')?.addEventListener('click', (ev) => guarded(ev.currentTarget, async () => {
        await API.exportOrgData();
        Toast.success('Export downloaded.');
      }));
      el.querySelector('#transferForm')?.addEventListener('submit', (e) => {
        e.preventDefault();
        const sel = el.querySelector('#toUser');
        if (!sel.value) { Toast.error('Choose an active member.'); return; }
        if (!window.confirm(`Make ${sel.options[sel.selectedIndex].text} the owner? You will become an administrator.`)) return;
        guarded(e.target.querySelector('[type=submit]'), async () => {
          const r = await API.transferOwnership(parseInt(sel.value, 10), el.querySelector('#toPassword').value);
          Toast.success(r.message);
          setTimeout(() => window.location.reload(), 1200);
        });
      });
      const renderDeletion = (d) => {
        el.querySelector('#delStatus').textContent = d.requested
          ? `Deletion requested on ${d.requested_at.slice(0, 10)}. Data is read-only and will be permanently deleted on or after ${d.purge_not_before.slice(0, 10)} (UTC). You can cancel until then.`
          : `Deleting the organisation makes all data read-only immediately and permanently removes it after ${d.grace_days} days. Export your data first.`;
        el.querySelector('#delRequestFields').classList.toggle('hidden', d.requested);
        el.querySelector('#delCancelBtn').classList.toggle('hidden', !d.requested);
      };
      try { renderDeletion(await API.getDeletionRequest()); } catch (e) { Toast.error(e.message); }
      el.querySelector('#deleteOrgForm')?.addEventListener('submit', (e) => {
        e.preventDefault();
        if (el.querySelector('#delName').value.trim() !== orgName) { Toast.error('The name does not match.'); return; }
        if (!window.confirm('Request permanent deletion of this organisation and all of its data?')) return;
        guarded(el.querySelector('#delRequestBtn'), async () => {
          renderDeletion(await API.requestOrgDeletion(el.querySelector('#delName').value.trim(), el.querySelector('#delPassword').value));
          e.target.reset();
          Toast.success('Deletion requested. The organisation is now read-only.');
          setTimeout(() => window.location.reload(), 1500);
        });
      });
      el.querySelector('#delCancelBtn')?.addEventListener('click', (ev) => guarded(ev.currentTarget, async () => {
        renderDeletion(await API.cancelOrgDeletion());
        Toast.success('Deletion request cancelled.');
        setTimeout(() => window.location.reload(), 1200);
      }));
    }

    // ---- own account
    el.querySelector('#changePwForm')?.addEventListener('submit', (e) => {
      e.preventDefault();
      guarded(e.target.querySelector('[type=submit]'), async () => {
        await API.changePassword(el.querySelector('#cpCurrent').value, el.querySelector('#cpNew').value);
        e.target.reset();
        Toast.success('Password changed. Other sessions were signed out.');
      });
    });
    el.querySelector('#logoutAllBtn')?.addEventListener('click', async () => {
      if (!window.confirm('Sign out on all devices, including this one?')) return;
      try { await API.logoutAll(); } catch (_) { /* session already gone */ }
      Auth.logout();
    });
    // ---- two-factor login
    async function renderMfa() {
      try {
        const st = await API.mfaStatus();
        el.querySelector('#mfaStatusText').textContent = st.enabled
          ? `On since ${st.enabled_at.slice(0, 10)} · ${st.recovery_codes_left} recovery code(s) left.`
          : 'Off. Recommended for owners and administrators.';
        el.querySelector('#mfaStartBtn').classList.toggle('hidden', st.enabled);
        el.querySelector('#mfaDisableForm').classList.toggle('hidden', !st.enabled);
      } catch (e) { el.querySelector('#mfaStatusText').textContent = e.message; }
    }
    el.querySelector('#mfaStartBtn')?.addEventListener('click', (ev) => guarded(ev.currentTarget, async () => {
      const s = await API.mfaSetup();
      el.querySelector('#mfaSecret').textContent = s.secret;
      el.querySelector('#mfaUri').href = s.otpauth_uri;
      el.querySelector('#mfaSetupBox').classList.remove('hidden');
      el.querySelector('#mfaEnableCode').focus();
    }));
    el.querySelector('#mfaEnableForm')?.addEventListener('submit', (e) => {
      e.preventDefault();
      guarded(e.target.querySelector('[type=submit]'), async () => {
        const r = await API.mfaEnable(el.querySelector('#mfaEnableCode').value.trim());
        el.querySelector('#mfaSetupBox').classList.add('hidden');
        el.querySelector('#mfaCodes').textContent = r.recovery_codes.join('\n');
        el.querySelector('#mfaCodesBox').classList.remove('hidden');
        Toast.success('Two-factor login is on.');
        await renderMfa();
      });
    });
    el.querySelector('#mfaDisableForm')?.addEventListener('submit', (e) => {
      e.preventDefault();
      if (!window.confirm('Turn off two-factor login for your account?')) return;
      guarded(e.target.querySelector('[type=submit]'), async () => {
        await API.mfaDisable(el.querySelector('#mfaDisablePw').value, el.querySelector('#mfaDisableCode').value.trim());
        e.target.reset();
        el.querySelector('#mfaCodesBox').classList.add('hidden');
        Toast.success('Two-factor login is off.');
        await renderMfa();
      });
    });
    await renderMfa();

    if (me.email && me.email_verified === false) {
      el.querySelector('#emailVerifyRow').classList.remove('hidden');
      el.querySelector('#emailVerifyText').textContent = `Your e-mail address ${me.email} is not confirmed yet.`;
      el.querySelector('#resendVerifyBtn').addEventListener('click', (ev) => guarded(ev.currentTarget, async () => {
        const r = await API.resendVerification();
        Toast.success(r.message);
      }));
    }
    await loadUsers();
    await loadInvites();
  }

  // ---- Entity Details Modal & Operations Pipeline ---------------------
  let activeTab = 'tabEdit';
  let currentLoadedEntity = null;

  function openDetails(type, id) {
    activeTab = 'tabEdit';
    showTab('tabEdit');

    const modal = document.getElementById('detailModal');
    if (!modal) return;
    modal.classList.remove('hidden');

    document.getElementById('editEntityId').value = id;
    document.getElementById('editEntityType').value = type;
    document.getElementById('editFormError').classList.add('hidden');

    // Setup tab clicks
    document.querySelectorAll('.modal-tab').forEach(tab => {
      tab.classList.toggle('active', tab.dataset.tab === activeTab);
      tab.onclick = () => {
        activeTab = tab.dataset.tab;
        showTab(activeTab);
      };
    });

    if (type === 'item') {
      document.getElementById('modalTitle').textContent = 'Item Detail Control Tower';
      document.getElementById('modalTabMath').classList.remove('hidden');
      document.getElementById('modalTabPipeline').classList.remove('hidden');
      loadItemDetails(id);
    } else if (type === 'warehouse') {
      document.getElementById('modalTitle').textContent = 'Warehouse Detail Control Tower';
      document.getElementById('modalTabMath').classList.remove('hidden');
      document.getElementById('modalTabPipeline').classList.remove('hidden');
      loadWarehouseDetails(id);
    } else if (type === 'supplier') {
      document.getElementById('modalTitle').textContent = 'Supplier Detail Control Tower';
      document.getElementById('modalTabMath').classList.remove('hidden');
      document.getElementById('modalTabPipeline').classList.remove('hidden');
      loadSupplierDetails(id);
    }
  }

  function closeModal() {
    document.getElementById('detailModal')?.classList.add('hidden');
  }

  function showTab(tabId) {
    document.querySelectorAll('.modal-tab-content').forEach(c => {
      c.classList.toggle('hidden', c.id !== tabId);
    });
    document.querySelectorAll('.modal-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.tab === tabId);
    });
  }

  async function loadItemDetails(id) {
    try {
      const [data, sku] = await Promise.all([API.getItem(id), API.getSku(id).catch(() => null)]);
      currentLoadedEntity = data;
      
      const fieldsContainer = document.getElementById('editFormFields');
      fieldsContainer.innerHTML = `
        <div class="form-group"><label>Product Name</label><input type="text" id="editItemName" value="${escapeHtml(data.product_name)}" required /></div>
        <div class="form-group"><label>SKU</label><input type="text" id="editItemSku" value="${escapeHtml(data.sku)}" disabled /></div>
        <div class="form-group"><label>Category</label><input type="text" id="editItemCategory" value="${escapeHtml(data.category)}" required /></div>
        <div class="form-group"><label>Qty on Hand</label><input type="number" id="editItemQty" value="${data.quantity_on_hand}" required /></div>
        <div class="form-group"><label>Reorder Point</label><input type="number" id="editItemRop" value="${data.reorder_point}" required /></div>
        <div class="form-group"><label>Safety Stock</label><input type="number" id="editItemSafety" value="${data.safety_stock}" required /></div>
        <div class="form-group"><label>Max Stock Level</label><input type="number" id="editItemMax" value="${data.max_stock_level}" required /></div>
        <div class="form-group"><label>Unit Cost (${fmt.currencyCode()})</label><input type="number" step="0.01" id="editItemCost" value="${data.unit_cost}" required /></div>
        <div class="form-group"><label>Unit Price (${fmt.currencyCode()})</label><input type="number" step="0.01" id="editItemPrice" value="${data.unit_price}" required /></div>
        <div class="form-group"><label>Daily Avg Demand (Units)</label><input type="number" step="0.1" id="editItemDemand" value="${data.daily_avg_demand}" required /></div>
        <div class="form-group"><label>Lead Time (Days)</label><input type="number" step="0.1" id="editItemLead" value="${data.lead_time_days}" required /></div>
        <div class="form-group"><label>Expiry Date</label><input type="date" id="editItemExpiry" value="${data.expiry_date || ''}" /></div>
      `;

      renderItemMath(data, sku);
      renderItemPipeline(data);
      renderAdjustPanel(fieldsContainer, data);
    } catch (err) {
      Toast.error('Failed to load item details: ' + err.message);
      closeModal();
    }
  }

  // Stock adjustments (cycle count, damage, write-off...) go to the transaction
  // ledger on the server; the list shows movements since this item was imported.
  async function renderAdjustPanel(container, data) {
    const wrap = document.createElement('div');
    wrap.className = 'form-group';
    wrap.style.gridColumn = '1 / -1';
    wrap.innerHTML = `
      <fieldset style="border:1px solid var(--border-subtle);border-radius:6px;padding:10px 12px">
        <legend style="font-size:12px;padding:0 4px">Record stock adjustment (ledgered)</legend>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:end">
          <label style="font-size:11px">Counted quantity<input type="number" min="0" step="any" id="adjCount" style="width:120px" /></label>
          <label style="font-size:11px">Reason<select id="adjReason">
            <option value="cycle_count">Cycle count</option><option value="damage">Damage</option>
            <option value="expiry_write_off">Expiry write-off</option><option value="theft_loss">Theft / loss</option>
            <option value="found_stock">Found stock</option><option value="customer_return">Customer return</option>
            <option value="return_to_supplier">Return to supplier</option><option value="correction">Correction</option></select></label>
          <label style="font-size:11px;flex:1">Notes<input type="text" id="adjNotes" maxlength="1000" /></label>
          <button type="button" class="btn btn-secondary btn-sm" id="adjSubmit">Record</button>
        </div>
        <div id="adjLedger" style="margin-top:8px;font-size:11px;color:var(--text-secondary)">Loading movements…</div>
      </fieldset>`;
    container.appendChild(wrap);
    const ledgerEl = wrap.querySelector('#adjLedger');
    async function loadLedger() {
      try {
        const led = await API.getSkuTransactions(data.id);
        ledgerEl.innerHTML = led.items.length
          ? led.items.slice(0, 8).map((t) => `${fmt.datetime(t.created_at)} · ${escapeHtml(t.type)} ${t.quantity > 0 ? '+' : ''}${fmt.number(t.quantity)} · ${escapeHtml(t.reference || '')}`).join('<br/>')
          : 'No movements recorded since import.';
      } catch (e) { ledgerEl.textContent = `Movements unavailable: ${e.message}`; }
    }
    const btn = wrap.querySelector('#adjSubmit');
    btn.addEventListener('click', () => withBusy(btn, async () => {
      const v = wrap.querySelector('#adjCount').value;
      if (v === '') { Toast.error('Enter the counted quantity.'); return; }
      try {
        const r = await API.adjustStock(data.id, { counted_quantity: parseFloat(v),
          reason: wrap.querySelector('#adjReason').value, notes: wrap.querySelector('#adjNotes').value || null });
        Toast.success(`Stock ${fmt.number(r.on_hand_before)} → ${fmt.number(r.on_hand_after)} recorded.`);
        const q = document.getElementById('editItemQty');
        if (q) q.value = r.on_hand_after;
        cachedSummary = null;
        await loadLedger();
      } catch (e) { Toast.error(e.message); }
    }));
    loadLedger();
  }

  // All numbers below come from the server (GET /api/skus/{id}), which uses the
  // organisation's configured ordering cost, holding rate, service level and the
  // item's own variability. (2.4 recomputed them here with fixed S = 50,
  // i = 20 %, z = 1.65 and CVs of 25 % / 15 %, ignoring the configuration.)
  function renderItemMath(data, sku) {
    const mathContainer = document.getElementById('mathContent');
    const calc = sku && sku.calculations;
    const it = (sku && sku.item) || data;
    const cls = (v) => fmt.cls(v);
    const quality = (sku && sku.data_quality) || [];
    const matrix = `
      <h4 style="margin-top:0">Multi-Criteria Segmentation</h4>
      <div style="display:grid;grid-template-columns:repeat(4, 1fr);gap:12px;margin-bottom:8px;">
        <div class="kpi-card" style="padding:12px;text-align:center"><div class="kpi-value" style="font-size:20px;color:var(--text-accent)">${cls(it.abc_class)}</div><div class="kpi-label" style="font-size:9px">ABC (annual value)</div></div>
        <div class="kpi-card" style="padding:12px;text-align:center"><div class="kpi-value" style="font-size:20px;color:var(--signal-teal)">${cls(it.xyz_class)}</div><div class="kpi-label" style="font-size:9px">XYZ (observed CV)</div></div>
        <div class="kpi-card" style="padding:12px;text-align:center"><div class="kpi-value" style="font-size:20px;color:var(--status-low)">${cls(it.fsn_class)}</div><div class="kpi-label" style="font-size:9px">FSN (turnover)</div></div>
        <div class="kpi-card" style="padding:12px;text-align:center"><div class="kpi-value" style="font-size:20px;color:var(--status-critical)">${cls(it.ved_class)}</div><div class="kpi-label" style="font-size:9px">VED (heuristic proxy)</div></div>
      </div>
      <p class="text-muted" style="font-size:11px;margin:0 0 16px">${escapeHtml(it.xyz_basis || '')}</p>
      ${quality.length ? `<div style="margin-bottom:16px;padding:10px 12px;border-radius:6px;border:1px solid var(--status-warning,#f59e0b);font-size:12px"><strong>Data quality:</strong> ${quality.map(escapeHtml).join(' ')}</div>` : ''}`;
    if (!calc) {
      mathContainer.innerHTML = matrix + `<p class="text-muted">Calculation details are unavailable.</p>`;
      return;
    }
    const ssd = calc.safety_stock_details || {};
    const rd = calc.rop_details || {};
    const S = calc.order_setup_cost, i = calc.annual_holding_rate, H = calc.holding_cost_per_unit;
    const D = Math.round((it.daily_avg_demand || 0) * 365 * 10) / 10;
    const csl = calc.service_level_target;
    mathContainer.innerHTML = matrix + `
      <h4>1. Economic Order Quantity (EOQ)</h4>
      <div class="math-formula-box">
        <div class="equation">Q* = &radic;( 2 D S / H ),&nbsp; H = c &middot; i</div>
        <ul style="padding-left:16px; margin: 8px 0;">
          <li>Annual demand D = ${it.daily_avg_demand} units/day &times; 365 = <strong>${D} units/year</strong></li>
          <li>Ordering cost S = <strong>${fmt.currency(S, 2)}</strong> per order (organisation setting)</li>
          <li>Holding cost H = ${fmt.currency(it.unit_cost, 2)} &times; ${Math.round(i * 1000) / 10}% = <strong>${fmt.currency(H, 2)}</strong> per unit per year</li>
        </ul>
        <div class="substitution">Q* = <strong>${calc.eoq} units</strong>${calc.eoq === 0 ? ' (undefined: missing demand or unit cost)' : ''}</div>
        <p class="text-muted" style="font-size:11px">Classical EOQ assumes constant demand, no shortages, no quantity discounts and no capacity limits.</p>
      </div>
      <h4>2. Safety stock for a ${Math.round(csl * 1000) / 10}% cycle service level</h4>
      <div class="math-formula-box">
        <div class="equation">SS = z &middot; &radic;( L &sigma;<sub>d</sub><sup>2</sup> + d<sup>2</sup> &sigma;<sub>L</sub><sup>2</sup> ),&nbsp; z = &Phi;<sup>-1</sup>(CSL)</div>
        <ul style="padding-left:16px; margin: 8px 0;">
          <li>z = &Phi;<sup>-1</sup>(${csl}) = <strong>${ssd.z_score}</strong> (cycle service level, not the fill rate)</li>
          <li>Lead time L = <strong>${ssd.lead_time_days} days</strong>; daily demand d = <strong>${ssd.daily_avg_demand} units/day</strong></li>
          <li>&sigma;<sub>d</sub> (daily demand) = <strong>${ssd.sigma_D}</strong> units/day; &sigma;<sub>L</sub> = <strong>${ssd.sigma_L}</strong> days</li>
          <li>&sigma; of lead-time demand = <strong>${ssd.sigma_LT}</strong> units</li>
        </ul>
        <div class="substitution">SS = ${ssd.z_score} &times; ${ssd.sigma_LT} = <strong>${ssd.safety_stock} units</strong></div>
      </div>
      <h4>3. Reorder point</h4>
      <div class="math-formula-box">
        <div class="equation">ROP = d &middot; L + SS</div>
        <div class="substitution">${rd.lead_time_demand} + ${rd.safety_stock} = <strong>${rd.rop} units</strong></div>
        <p class="text-muted" style="font-size:11px">Assumes normally distributed lead-time demand and continuous review. Stored policy values for this item: ROP ${it.reorder_point}, SS ${it.safety_stock}.</p>
      </div>`;
  }

  function renderItemPipeline(data) {
    const pipelineContainer = document.getElementById('pipelineStepper');
    const d = Number(data.daily_avg_demand) || 0;
    const L = Number(data.lead_time_days) || 0;
    const rawSd = Number(data.demand_std_dev) || 0;
    const sdD = rawSd <= 0 ? 0.25 * d : (rawSd < 1 ? rawSd * d : rawSd);
    const sdL = Number(data.lead_time_std_dev) > 0 ? Number(data.lead_time_std_dev) : 0.15 * L;
    const sigmaLT = Math.round(Math.sqrt(L * sdD * sdD + d * d * sdL * sdL) * 100) / 100;
    const cv = d > 0 ? Math.round((sdD / d) * 100) / 100 : null;
    const otd = data.supplier_otd ?? data.on_time_delivery_rate;
    const stages = [
      { title: "Enterprise Context", desc: `Scoping product <strong>${escapeHtml(data.product_name)}</strong> (${escapeHtml(data.sku)}) in warehouse <strong>${escapeHtml(data.warehouse)}</strong> sourced from supplier <strong>${escapeHtml(data.supplier)}</strong>.` },
      { title: "Decision Context", desc: `Optimize procurement parameters to prevent stockouts while limiting carrying costs (at the organisation's configured target cycle service level).` },
      { title: "Decision Model", desc: `Segmentation: ABC-XYZ (<strong>${fmt.cls(data.abc_class)}-${fmt.cls(data.xyz_class)}</strong>) | FSN-VED (<strong>${fmt.cls(data.fsn_class)}-${fmt.cls(data.ved_class)}</strong>). XYZ needs recorded demand history; VED is a heuristic proxy.` },
      { title: "Decision Analysis", desc: `Lead-time demand standard deviation &sigma;<sub>LT</sub> = &radic;(L&sigma;<sub>d</sub><sup>2</sup> + d<sup>2</sup>&sigma;<sub>L</sub><sup>2</sup>) = <strong>${sigmaLT} units</strong> (demand CV <strong>${cv ?? 'n/a'}</strong>, &sigma;<sub>L</sub> = ${Math.round(sdL * 100) / 100} days).` },
      { title: "Decision Intelligence", desc: `Prescribed optimal safety stock is <strong>${data.safety_stock} units</strong>. Optimal Reorder point threshold is <strong>${data.reorder_point} units</strong>.` },
      { title: "Decision Explanation", desc: `Current quantity on hand is ${data.quantity_on_hand} units. Status is flagged as "${data.status.toUpperCase()}" relative to safety stock and reorder point limits.` },
      { title: "Decision Recommendation", desc: data.status === 'low' || data.status === 'critical' ? `<strong>Critical Alert:</strong> Purchase order recommended: order ${Math.round(data.max_stock_level - data.quantity_on_hand)} units immediately.` : `Current stock is adequate. Hold on placing further orders to minimize capital block.` },
      { title: "Decision Monitoring", desc: `Supplier on-time delivery: ${otd == null ? 'not recorded' : otd + '%'}; average lead time ${data.lead_time_days} days. Stock falling below safety stock or reorder point raises an alert.` },
      { title: "Organizational Learning", desc: `Manager decisions on recommendations (accept / modify / reject, with comments) are written to the decision audit trail, providing data to evaluate and recalibrate the policy parameters.` }
    ];

    pipelineContainer.innerHTML = stages.map((s, idx) => `
      <div class="pipeline-step active">
        <div class="pipeline-step-title">Stage ${idx + 1}: ${s.title}</div>
        <div class="pipeline-step-desc">${s.desc}</div>
      </div>
    `).join('');
  }

  async function loadWarehouseDetails(id) {
    try {
      const data = await API.getWarehouse(id);
      currentLoadedEntity = data;
      
      const fieldsContainer = document.getElementById('editFormFields');
      fieldsContainer.innerHTML = `
        <div class="form-group"><label>Warehouse Name</label><input type="text" id="editWarehouseName" value="${escapeHtml(data.name)}" required /></div>
        <div class="form-group"><label>Capacity (Units)</label><input type="number" id="editWarehouseCapacity" value="${data.capacity_units}" required /></div>
      `;

      const mathContainer = document.getElementById('mathContent');
      const util = Math.round((data.total_units / data.capacity_units) * 1000) / 10;
      mathContainer.innerHTML = `
        <h4>Warehouse Utilization Analysis</h4>
        <div class="math-formula-box">
          <div class="equation">Utilization = ( Total Units / Capacity ) * 100</div>
          <ul>
            <li>Total Stock Units = <strong>${data.total_units} units</strong></li>
            <li>Storage Capacity Limit = <strong>${data.capacity_units} units</strong></li>
            <li>Headroom Available = <strong>${data.capacity_units - data.total_units} units</strong></li>
          </ul>
          <div class="substitution">
            Calculation: ( ${data.total_units} / ${data.capacity_units} ) * 100 = <strong>${util}% Utilization</strong>
          </div>
        </div>
      `;

      const pipelineContainer = document.getElementById('pipelineStepper');
      pipelineContainer.innerHTML = `
        <div class="pipeline-step active"><div class="pipeline-step-title">Stage 1: Enterprise Context</div><div class="pipeline-step-desc">Storage Node: <strong>${escapeHtml(data.name)}</strong>. Storage profiles.</div></div>
        <div class="pipeline-step active"><div class="pipeline-step-title">Stage 2: Decision Context</div><div class="pipeline-step-desc">Optimize spatial footprint, preventing stock capacity overflow or floor space wastage.</div></div>
        <div class="pipeline-step active"><div class="pipeline-step-title">Stage 3: Decision Model</div><div class="pipeline-step-desc">Capacitated Warehouse Allocation model mapping total SKUs (${data.sku_count} distinct items).</div></div>
        <div class="pipeline-step active"><div class="pipeline-step-title">Stage 4: Decision Analysis</div><div class="pipeline-step-desc">Storage utilization is currently ${util}%, leaving a buffer of ${Math.round(100 - util)}% for peak seasonal deliveries.</div></div>
        <div class="pipeline-step active"><div class="pipeline-step-title">Stage 5: Decision Intelligence</div><div class="pipeline-step-desc">Dynamic floor reallocation suggested for fast-moving items to optimize material handling times.</div></div>
      `;
    } catch (err) {
      Toast.error('Failed to load warehouse details: ' + err.message);
      closeModal();
    }
  }

  async function loadSupplierDetails(id) {
    try {
      const data = await API.getSupplier(id);
      currentLoadedEntity = data;
      
      const fieldsContainer = document.getElementById('editFormFields');
      fieldsContainer.innerHTML = `
        <div class="form-group"><label>Supplier Name</label><input type="text" id="editSupplierName" value="${escapeHtml(data.name)}" required /></div>
        <div class="form-group"><label>Avg Lead Time (Days)</label><input type="number" id="editSupplierLead" value="${data.avg_lead_time_days}" required /></div>
        <div class="form-group"><label>On-Time Delivery Rate (%)</label><input type="number" min="0" max="100" id="editSupplierOtd" value="${data.on_time_delivery_rate}" required /></div>
        <div class="form-group"><label>Quality Score (%)</label><input type="number" min="0" max="100" id="editSupplierQuality" value="${data.quality_score}" required /></div>
      `;

      const mathContainer = document.getElementById('mathContent');
      const score = Math.round((data.on_time_delivery_rate * 0.5 + data.quality_score * 0.5) * 10) / 10;
      const risk = Math.round((100 - score) * 10) / 10;
      mathContainer.innerHTML = `
        <h4>Supplier Risk &amp; Sourcing Formulation</h4>
        <div class="math-formula-box">
          <div class="equation">Performance Score = ( OTD * 0.5 ) + ( Quality * 0.5 )</div>
          <ul>
            <li>On-Time Delivery Rate (OTD) = <strong>${data.on_time_delivery_rate}%</strong></li>
            <li>Quality Acceptability Rate = <strong>${data.quality_score}%</strong></li>
            <li>Supplier Sourcing Risk = 100 - Performance Score = <strong>${risk}% Sourcing Risk</strong></li>
          </ul>
          <div class="substitution">
            Calculation: ( ${data.on_time_delivery_rate} * 0.5 ) + ( ${data.quality_score} * 0.5 ) = <strong>${score}% Performance Score</strong>
          </div>
        </div>
      `;

      const pipelineContainer = document.getElementById('pipelineStepper');
      pipelineContainer.innerHTML = `
        <div class="pipeline-step active"><div class="pipeline-step-title">Stage 1: Enterprise Context</div><div class="pipeline-step-desc">Vendor profile: <strong>${escapeHtml(data.name)}</strong>, providing ${data.sku_count} primary SKUs.</div></div>
        <div class="pipeline-step active"><div class="pipeline-step-title">Stage 2: Decision Context</div><div class="pipeline-step-desc">Sourcing security: monitor and mitigate lead time variances affecting production safety stock buffers.</div></div>
        <div class="pipeline-step active"><div class="pipeline-step-title">Stage 3: Decision Model</div><div class="pipeline-step-desc">Dual-weight Supplier Scorecard Model (OTD weight: 50%, Quality weight: 50%).</div></div>
        <div class="pipeline-step active"><div class="pipeline-step-title">Stage 4: Decision Analysis</div><div class="pipeline-step-desc">Average lead time is ${data.avg_lead_time_days} days. On-Time Delivery standard: ${data.on_time_delivery_rate}%. Sourcing reliability index is ${score}%.</div></div>
        <div class="pipeline-step active"><div class="pipeline-step-title">Stage 5: Decision Intelligence</div><div class="pipeline-step-desc">${score >= 80 ? 'Strategic partner status: maintain order allocation.' : 'Supplier risk alert: recommended to diversify orders to secondary suppliers.'}</div></div>
      `;
    } catch (err) {
      Toast.error('Failed to load supplier details: ' + err.message);
      closeModal();
    }
  }

  async function handleEditFormSubmit(e) {
    e.preventDefault();
    const type = document.getElementById('editEntityType').value;
    const id = document.getElementById('editEntityId').value;
    const errEl = document.getElementById('editFormError');
    errEl.classList.add('hidden');
    errEl.textContent = '';

    try {
      if (type === 'item') {
        const payload = {
          product_name: document.getElementById('editItemName').value,
          category: document.getElementById('editItemCategory').value,
          quantity_on_hand: parseFloat(document.getElementById('editItemQty').value),
          reorder_point: parseFloat(document.getElementById('editItemRop').value),
          safety_stock: parseFloat(document.getElementById('editItemSafety').value),
          max_stock_level: parseFloat(document.getElementById('editItemMax').value),
          unit_cost: parseFloat(document.getElementById('editItemCost').value),
          unit_price: parseFloat(document.getElementById('editItemPrice').value),
          daily_avg_demand: parseFloat(document.getElementById('editItemDemand').value),
          lead_time_days: parseFloat(document.getElementById('editItemLead').value),
          expiry_date: document.getElementById('editItemExpiry').value || null
        };
        await API.updateItem(id, payload);
      } else if (type === 'warehouse') {
        const payload = {
          name: document.getElementById('editWarehouseName').value,
          capacity_units: parseFloat(document.getElementById('editWarehouseCapacity').value)
        };
        await API.updateWarehouse(id, payload);
      } else if (type === 'supplier') {
        const payload = {
          name: document.getElementById('editSupplierName').value,
          avg_lead_time_days: parseFloat(document.getElementById('editSupplierLead').value),
          on_time_delivery_rate: parseFloat(document.getElementById('editSupplierOtd').value),
          quality_score: parseFloat(document.getElementById('editSupplierQuality').value)
        };
        await API.updateSupplier(id, payload);
      }

      Toast.success('Entity updated successfully!');
      closeModal();
      
      cachedSummary = null;
      await ensureSummary(true);
      navigate(AppState.get().currentView);
    } catch (err) {
      errEl.classList.remove('hidden');
      errEl.textContent = err.message || 'Failed to save changes.';
    }
  }

  async function handleDeleteClick() {
    const type = document.getElementById('editEntityType').value;
    const id = document.getElementById('editEntityId').value;
    if (!id) return;
    
    const confirmMsg = `Are you sure you want to delete this ${type}? This action is permanent and cannot be undone.`;
    if (!confirm(confirmMsg)) return;

    try {
      if (type === 'item') {
        await API.deleteItem(id);
      } else if (type === 'warehouse') {
        await API.deleteWarehouse(id);
      } else if (type === 'supplier') {
        await API.deleteSupplier(id);
      }

      Toast.success(`${type.charAt(0).toUpperCase() + type.slice(1)} deleted successfully!`);
      closeModal();
      
      cachedSummary = null;
      await ensureSummary(true);
      navigate(AppState.get().currentView);
    } catch (err) {
      alert('Failed to delete entity: ' + err.message);
    }
  }

  async function updateBatchSelector() {
    const sel = document.getElementById('batchSelect');
    if (!sel) return;
    try {
      const batches = (await API.listBatches()).filter((b) => (b.kind || 'inventory') === 'inventory');
      sel.innerHTML = batches.map((b) => `<option value="${b.id}" title="Provenance: ${escapeHtml(b.provenance || 'unlabelled')}${(b.derived_fields || []).length ? ' | derived fields: ' + escapeHtml(b.derived_fields.join(', ')) : ''}">[${escapeHtml((b.provenance || 'unlabelled').toUpperCase())}] ${escapeHtml(b.filename)} (${fmt.date(b.uploaded_at)})</option>`).join('');
      if (batches.length) {
        sel.value = AppState.get().currentBatchId === 'latest' ? batches[0].id : AppState.get().currentBatchId;
      }
    } catch (_) {}
  }

  function bindGlobalUI() {
    // Event delegation instead of inline onclick handlers (strict CSP).
    document.addEventListener('click', (e) => {
      const item = e.target.closest('[data-open-item]');
      if (item) { openDetails('item', Number(item.dataset.openItem)); return; }
      const nav = e.target.closest('[data-nav]');
      if (nav) { navigate(nav.dataset.nav); }
    });
    document.querySelectorAll('.nav-item[data-view]').forEach((item) => {
      item.addEventListener('click', () => navigate(item.dataset.view));
      item.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(item.dataset.view); }
      });
    });

    document.getElementById('sidebarCollapseBtn')?.addEventListener('click', () => {
      document.querySelector('.app-shell').classList.toggle('sidebar-collapsed');
    });

    document.getElementById('mobileNavToggle')?.addEventListener('click', () => {
      document.querySelector('.app-shell').classList.add('mobile-nav-open');
    });
    document.querySelector('.nav-scrim')?.addEventListener('click', closeMobileNav);

    document.getElementById('navSearchInput')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target.value.trim()) {
        navigate('inventory').then(() => {
          const input = document.querySelector('#inventoryToolbar [data-role="search"]');
          if (input) { input.value = e.target.value; input.dispatchEvent(new Event('input')); }
        });
      }
    });

    document.getElementById('batchSelect')?.addEventListener('change', async (e) => {
      AppState.set({ currentBatchId: e.target.value });
      cachedSummary = null;
      await ensureSummary(true);
      navigate(AppState.get().currentView);
    });

    document.addEventListener('upload:start', () => {
      document.getElementById('uploadProgressWrap')?.classList.remove('hidden');
      document.getElementById('landingUploadProgress')?.classList.remove('hidden');
    });
    document.addEventListener('upload:success', async (e) => {
      document.getElementById('uploadProgressWrap')?.classList.add('hidden');
      document.getElementById('landingUploadProgress')?.classList.add('hidden');
      AppState.set({ currentBatchId: String(e.detail.batch_id), hasData: true });
      cachedSummary = null;
      showDashboard();
      await navigate('overview');
    });
    document.addEventListener('upload:demand', () => {
      cachedSummary = null;
      refreshBatchHistory(document.getElementById('viewMount') || document);
    });
    document.addEventListener('upload:error', () => {
      document.getElementById('uploadProgressWrap')?.classList.add('hidden');
      document.getElementById('landingUploadProgress')?.classList.add('hidden');
    });

    document.getElementById('modalCloseBtn')?.addEventListener('click', closeModal);
    document.getElementById('editCancelBtn')?.addEventListener('click', closeModal);
    document.getElementById('editForm')?.addEventListener('submit', handleEditFormSubmit);
    document.getElementById('editDeleteBtn')?.addEventListener('click', handleDeleteClick);
    document.getElementById('logoutBtn')?.addEventListener('click', () => Auth.logout());
  }

  // Live readiness indicator (replaces the static "System nominal" label of 2.4).
  async function refreshSystemPulse() {
    const t = document.getElementById('systemPulseText');
    if (!t) return;
    try {
      const r = await fetch('/api/ready');
      const j = await r.json();
      t.textContent = r.ok ? 'Server ready' : (j.status === 'migration_required' ? 'Database migration required' : 'Server not ready');
      document.getElementById('systemPulse')?.classList.toggle('degraded', !r.ok);
    } catch (_) {
      t.textContent = 'Server unreachable';
      document.getElementById('systemPulse')?.classList.add('degraded');
    }
  }

  function showDashboard() {
    refreshSystemPulse();
    document.getElementById('landingScreen')?.classList.add('hidden');
    document.getElementById('appShell')?.classList.remove('hidden');
  }

  function showLanding() {
    document.getElementById('appShell')?.classList.add('hidden');
    document.getElementById('landingScreen')?.classList.remove('hidden');
    UploadManager.init(document.getElementById('landingScreen'));
  }

  // Organisation read-only (subscription ended, payment overdue, deletion pending):
  // the server refuses writes (HTTP 402); the banner explains why.
  function showAccessBanner(me) {
    const el = document.getElementById('accessBanner');
    if (!el) return;
    const a = me && me.access;
    if (a && a.state === 'read_only') {
      el.textContent = `Read-only: ${a.message || a.reason}`;
      el.classList.remove('hidden');
    } else if (me && me.email_verified === false && me.email) {
      el.textContent = 'Please confirm your e-mail address (Settings → My account → resend confirmation).';
      el.classList.remove('hidden');
    } else {
      el.classList.add('hidden');
    }
  }

  async function init() {
    ThemeManager.init();
    bindGlobalUI();

    const loader = document.getElementById('pageLoader');

    if (Auth.handleAccountRoute()) { loader?.classList.add('hide'); return; }

    if (!Auth.isAuthenticated()) {
      document.getElementById('appShell')?.classList.add('hidden');
      document.getElementById('landingScreen')?.classList.add('hidden');
      document.getElementById('loginScreen')?.classList.remove('hidden');
      loader?.classList.add('hide');
      return;
    }

    try {
      try {
        const me = await API.getAuthMe();
        OrgPrefs.currency = me.currency || 'PKR';
        OrgPrefs.timezone = me.timezone || 'Asia/Karachi';
        AppState.set({ me });
        showAccessBanner(me);
      } catch (_) { /* 401 handled by the API client */ }
      const batches = await API.listBatches();
      if (batches.length === 0) {
        showLanding();
      } else {
        showDashboard();
        await navigate('overview');
      }
    } catch (err) {
      Toast.error('Could not reach the API. Is the backend running?');
      showLanding();
    } finally {
      loader?.classList.add('hide');
    }
  }

  return { init, navigate, openDetails };
})();

document.addEventListener('DOMContentLoaded', App.init);
