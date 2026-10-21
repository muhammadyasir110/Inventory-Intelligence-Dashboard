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
    erp: { file: 'pages/erp.html', init: () => {} },
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

    const el = mount();
    el.classList.remove('view');
    const html = await loadViewTemplate(routes[viewName].file);
    el.innerHTML = html;
    void el.offsetWidth; // restart animation
    el.classList.add('view');

    closeMobileNav();

    try {
      await routes[viewName].init(el);
    } catch (err) {
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
        <button class="btn btn-primary" onclick="App.navigate('upload')">Go to Upload</button>
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
    el.querySelectorAll('[data-action="export-excel"]').forEach((b) => b.addEventListener('click', () => {
      window.location.href = API.exportExcelUrl(AppState.get().currentBatchId);
    }));
    el.querySelectorAll('[data-action="export-pdf"]').forEach((b) => b.addEventListener('click', () => {
      window.location.href = API.exportPdfUrl(AppState.get().currentBatchId);
    }));
  }

  // ---- Enterprise Feature Initializers ------------------------------------

  function money(v) {
    return (v === null || v === undefined || isNaN(v)) ? '—' : `$${Math.round(v).toLocaleString()}`;
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
            + (r.excluded?.length ? `<br/>${r.excluded.length} record(s) excluded (non-positive demand or unit cost).` : '')
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
            <td><span class="sku-tag" onclick="App.openDetails('item', ${i.id})" style="cursor:pointer">${escapeHtml(i.sku)}</span></td>
            <td><strong>${escapeHtml(i.product_name)}</strong></td>
            <td><span class="badge badge-neutral">${i.abc_class || '-'}-${i.xyz_class || '-'}</span></td>
            <td>${i.quantity_on_hand}</td>
            <td>${i.current_rop}</td>
            <td><strong style="color:var(--signal-indigo)">${i.optimized_rop}</strong></td>
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
        runBtn.addEventListener('click', async () => {
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
        });
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
      const data = await API.getForecastingOverview(AppState.get().currentBatchId || 'latest');
      const metrics = data.overall_metrics || {};
      const fmt = (v, suf = '') => (v === null || v === undefined) ? '—' : `${v}${suf}`;

      el.querySelector('#forecastMape').textContent = fmt(metrics.average_mape, '%');
      el.querySelector('#forecastMae').textContent = fmt(metrics.average_mae);
      el.querySelector('#forecastRmse').textContent = fmt(metrics.average_rmse);
      el.querySelector('#forecastTotalItems').textContent = `${data.total_items_forecasted || 0}`;

      const sub = el.querySelector('#forecastChartSubtitle');
      const src = metrics.history_sources || {};
      const banner = el.querySelector('#forecastDataBanner');
      if (banner) {
        banner.style.display = metrics.illustrative_only ? 'block' : 'none';
        banner.textContent = metrics.illustrative_only
          ? 'No recorded demand history found for these SKUs. Forecasts and accuracy figures are computed on an ILLUSTRATIVE synthetic series and are not evidence of forecast accuracy. Load records into demand_history to evaluate real accuracy.'
          : '';
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
        let source = metrics.illustrative_only ? 'synthetic (illustrative)' : `recorded: ${src.recorded || 0}, synthetic: ${src.synthetic_illustrative || 0}`;
        if (selVal !== 'aggregate') {
          try {
            itemCache[selVal] = itemCache[selVal] || await API.getForecastItem(selVal);
            const f = itemCache[selVal];
            hist = (f.history || []).map(h => h.quantity);
            path = f.forecast_path || [];
            source = f.history_source === 'recorded' ? 'recorded history' : 'synthetic (illustrative)';
          } catch (e) { Toast.error('Item forecast failed: ' + e.message); }
        }
        if (sub) sub.textContent = `Holt linear smoothing, ${horizon}-day horizon, 95% prediction band · history: ${source}`;
        Charts.demandForecast('chartDemandForecast', hist, path.slice(0, horizon), horizon);
      }

      await renderForecastChart();
      skuSelect?.addEventListener('change', renderForecastChart);
      el.querySelector('#forecastHorizonSelect')?.addEventListener('change', (e) => {
        horizon = parseInt(e.target.value, 10);
        renderForecastChart();
      });

      const ctxModel = document.getElementById('chartModelComparison');
      const mc = data.model_comparison || {};
      const models = ['SMA', 'WMA', 'SES', 'Holt'].filter(m => mc[m]);
      if (ctxModel) {
        Charts.destroy('chartModelComparison');
        new Chart(ctxModel, {
          type: 'bar',
          data: {
            labels: models.length ? models : ['No data'],
            datasets: [{
              label: 'Out-of-sample MAPE (%) — 7-day holdout',
              data: models.map(m => mc[m].mape),
              backgroundColor: ['#94a3b8', '#64748b', '#22d3b8', '#4f6ef7'],
              borderRadius: 4
            }]
          },
          options: {
            responsive: true, maintainAspectRatio: false,
            scales: { x: { grid: { display: false } }, y: { beginAtZero: true, title: { display: true, text: 'MAPE (%)' } } }
          }
        });
      }

      const watch = data.volatility_watchlist || [];
      Charts.volatilityWatchlist('chartVolatilityWatchlist', watch);
      const watchBody = el.querySelector('#forecastWatchlistBody');
      if (watchBody) {
        watchBody.innerHTML = watch.length ? watch.map(i => `
          <tr>
            <td><span class="sku-tag" onclick="App.openDetails('item', ${i.id})" style="cursor:pointer">${escapeHtml(i.sku)}</span></td>
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
    const money = (v) => (v === null || v === undefined || isNaN(v)) ? '—' : `$${Math.round(v).toLocaleString()}`;
    const signed = (v, f) => `${v >= 0 ? '+' : '−'}${f(Math.abs(v))}`;

    function syncLabels() {
      if (vDemand && sDemand) vDemand.textContent = `${sDemand.value >= 0 ? '+' : ''}${sDemand.value}%`;
      if (vLead && sLead) vLead.textContent = `+${sLead.value} Days`;
      if (vHolding && sHolding) vHolding.textContent = `${sHolding.value}%`;
      if (vBudget && sBudget) vBudget.textContent = `$${parseInt(sBudget.value, 10).toLocaleString()}`;
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
          holding_rate_pct_change: ((parseFloat(sHolding?.value || 20) / 20) - 1) * 100,
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
                { label: 'Total annual cost ($)', data: [base.total_annual_cost, nr.total_annual_cost, sim.total_annual_cost], backgroundColor: ['#94a3b8', '#ef4444', '#4f6ef7'], borderRadius: 4, yAxisID: 'y' },
                { label: 'Fill rate (%)', type: 'line', data: [base.estimated_fill_rate, nr.estimated_fill_rate, sim.estimated_fill_rate], borderColor: '#22d3b8', backgroundColor: '#22d3b8', yAxisID: 'y1' }
              ]
            },
            options: {
              responsive: true, maintainAspectRatio: false,
              scales: {
                x: { grid: { display: false } },
                y: { beginAtZero: true, title: { display: true, text: 'Cost ($)' } },
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

    el.querySelector('#scenarioRunBtn')?.addEventListener('click', runSimulation);
    el.querySelector('#scenarioResetBtn')?.addEventListener('click', () => {
      if (sDemand) sDemand.value = 0;
      if (sLead) sLead.value = 0;
      if (sHolding) sHolding.value = 20;
      if (sBudget) sBudget.value = 1000000;
      syncLabels();
      runSimulation();
    });

    syncLabels();
    await runSimulation();
  }

  async function initProcurement(el) {
    try {
      const data = await API.getProcurementPlan(AppState.get().currentBatchId || 'latest');
      const orders = data.recommended_orders || [];

      el.querySelector('#procCriticalOrdersCount').textContent = `${data.critical_orders_count || 0}`;
      el.querySelector('#procTotalOrdersCount').textContent = `${data.total_recommended_orders || orders.length}`;
      el.querySelector('#procTotalValue').textContent = `$${Math.round(data.total_procurement_value || 0).toLocaleString()}`;
      
      const suppliers = new Set(orders.map(o => o.supplier)).size;
      el.querySelector('#procSuppliersCount').textContent = `${suppliers}`;

      const tbody = el.querySelector('#procurementTableBody');
      if (!orders.length) {
        tbody.innerHTML = `<tr><td colspan="11" class="text-center text-muted" style="padding:24px">All stock levels are above reorder thresholds. No replenishment orders currently required.</td></tr>`;
      } else {
        tbody.innerHTML = orders.map((o, idx) => `
          <tr>
            <td><span class="sku-tag" onclick="App.openDetails('item', ${o.item_id})" style="cursor:pointer">${escapeHtml(o.sku)}</span></td>
            <td><strong>${escapeHtml(o.product_name)}</strong></td>
            <td>${escapeHtml(o.supplier)}</td>
            <td>${escapeHtml(o.warehouse)}</td>
            <td>${o.current_stock}</td>
            <td>${o.reorder_point}</td>
            <td><strong style="color:var(--signal-indigo)">${o.recommended_order_qty}</strong></td>
            <td>$${o.unit_cost.toFixed(2)}</td>
            <td><strong>$${Math.round(o.total_cost).toLocaleString()}</strong></td>
            <td><span class="badge ${o.urgency === 'critical' ? 'badge-critical' : 'badge-low'}">${o.urgency.toUpperCase()}</span></td>
            <td>
              <button class="btn btn-secondary btn-sm" onclick="App.createSinglePo(${idx})" style="padding:2px 8px;font-size:11px">Issue PO</button>
            </td>
          </tr>
        `).join('');
      }

      window._currentProcurementOrders = orders;

      el.querySelector('#createPoBatchBtn')?.addEventListener('click', async () => {
        try {
          if (!orders.length) {
            Toast.info('No pending procurement lines to order.');
            return;
          }
          Toast.info('Submitting purchase orders to ERP ledger...');
          const res = await API.createPurchaseOrder({
            supplier_id: orders[0]?.supplier_id || 1,
            warehouse_id: orders[0]?.warehouse_id || 1,
            items: orders.slice(0, 10).map(o => ({
              item_id: o.item_id,
              quantity_ordered: o.recommended_order_qty,
              unit_cost: o.unit_cost
            }))
          });
          Toast.success(`Purchase Order ${res.po_number || ''} generated successfully!`);
        } catch (e) {
          Toast.error(e.message || 'PO creation failed');
        }
      });

      el.querySelector('#procSearchInput')?.addEventListener('input', (e) => {
        const q = e.target.value.toLowerCase();
        tbody.querySelectorAll('tr').forEach(tr => {
          tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none';
        });
      });
    } catch (err) {
      console.error('Procurement init error:', err);
      Toast.error('Procurement error: ' + err.message);
    }
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

      try {
        const quality = await API.getDataQuality();
        Charts.dataQualityRadar('chartDataQuality', quality);
      } catch (_) {
        Charts.dataQualityRadar('chartDataQuality');
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
    } catch (err) {
      console.error('Config init error:', err);
      Toast.error('Config load error: ' + err.message);
    }
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
      const data = await API.getItem(id);
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
        <div class="form-group"><label>Unit Cost ($)</label><input type="number" step="0.01" id="editItemCost" value="${data.unit_cost}" required /></div>
        <div class="form-group"><label>Unit Price ($)</label><input type="number" step="0.01" id="editItemPrice" value="${data.unit_price}" required /></div>
        <div class="form-group"><label>Daily Avg Demand (Units)</label><input type="number" step="0.1" id="editItemDemand" value="${data.daily_avg_demand}" required /></div>
        <div class="form-group"><label>Lead Time (Days)</label><input type="number" step="0.1" id="editItemLead" value="${data.lead_time_days}" required /></div>
        <div class="form-group"><label>Expiry Date</label><input type="date" id="editItemExpiry" value="${data.expiry_date || ''}" /></div>
      `;

      renderItemMath(data);
      renderItemPipeline(data);
    } catch (err) {
      Toast.error('Failed to load item details: ' + err.message);
      closeModal();
    }
  }

  function renderItemMath(data) {
    const mathContainer = document.getElementById('mathContent');
    const D_ann = Math.round(data.daily_avg_demand * 365);
    const S = 50.0;
    const H = Math.round(data.unit_cost * 0.20 * 100) / 100 || 2.0;
    const calculated_eoq = Math.round(Math.sqrt((2 * D_ann * S) / H) * 10) / 10;
    
    const sigma_D = Math.round(data.daily_avg_demand * 0.25 * 1000) / 1000;
    const sigma_LT = Math.round(data.lead_time_days * 0.15 * 1000) / 1000;
    const var_demand_part = data.lead_time_days * (sigma_D ** 2);
    const var_lead_time_part = (data.daily_avg_demand ** 2) * (sigma_LT ** 2);
    const sigma_LT_demand = Math.round(Math.sqrt(var_demand_part + var_lead_time_part) * 1000) / 1000;
    const calculated_ss = Math.round(1.65 * sigma_LT_demand * 10) / 10;
    
    const lead_demand = Math.round(data.daily_avg_demand * data.lead_time_days * 10) / 10;
    const calculated_rop = Math.round((lead_demand + calculated_ss) * 10) / 10;

    mathContainer.innerHTML = `
      <h4 style="margin-top:0">Multi-Criteria Decision Segmentation Matrix</h4>
      <div style="display:grid;grid-template-columns:repeat(4, 1fr);gap:12px;margin-bottom:20px;">
        <div class="kpi-card" style="padding:12px;text-align:center"><div class="kpi-value" style="font-size:20px;color:var(--signal-indigo)">${data.abc_class || 'C'}</div><div class="kpi-label" style="font-size:9px">ABC Class (Value)</div></div>
        <div class="kpi-card" style="padding:12px;text-align:center"><div class="kpi-value" style="font-size:20px;color:var(--signal-teal)">${data.xyz_class || 'Z'}</div><div class="kpi-label" style="font-size:9px">XYZ Class (CV)</div></div>
        <div class="kpi-card" style="padding:12px;text-align:center"><div class="kpi-value" style="font-size:20px;color:var(--status-low)">${data.fsn_class || 'S'}</div><div class="kpi-label" style="font-size:9px">FSN Class (Turnover)</div></div>
        <div class="kpi-card" style="padding:12px;text-align:center"><div class="kpi-value" style="font-size:20px;color:var(--status-critical)">${data.ved_class || 'D'}</div><div class="kpi-label" style="font-size:9px">VED Class (Safety)</div></div>
      </div>

      <h4>1. Economic Order Quantity (EOQ) Model</h4>
      <div class="math-formula-box">
        <div class="equation">EOQ = &radic;( 2 * D * S ) / H</div>
        <div><strong>Parameters substituted:</strong></div>
        <ul style="padding-left:16px; margin: 8px 0;">
          <li>Annual Demand (D) = daily avg demand * 365 = ${data.daily_avg_demand} * 365 = <strong>${D_ann} units/year</strong></li>
          <li>Setup/Order Cost (S) = <strong>$${S.toFixed(2)}</strong></li>
          <li>Annual Holding Cost (H) = unit cost * holding rate = $${data.unit_cost} * 20% = <strong>$${H.toFixed(2)} / unit / year</strong></li>
        </ul>
        <div class="substitution">
          Calculation: &radic;( 2 * ${D_ann} * ${S} ) / ${H} = &radic;(${2 * D_ann * S}) / ${H} = <strong>${calculated_eoq} units</strong>
        </div>
      </div>

      <h4>2. Safety Stock (SS) under Uncertainty</h4>
      <div class="math-formula-box">
        <div class="equation">SS = Z * &sigma;<sub>L</sub> = Z * &radic;( L * &sigma;<sub>D</sub><sup>2</sup> + D<sup>2</sup> * &sigma;<sub>LT</sub><sup>2</sup> )</div>
        <div><strong>Parameters substituted:</strong></div>
        <ul style="padding-left:16px; margin: 8px 0;">
          <li>Service Factor (Z) = <strong>1.65</strong> (95% standard service fill-rate)</li>
          <li>Lead Time (L) = <strong>${data.lead_time_days} days</strong></li>
          <li>Daily Demand (D) = <strong>${data.daily_avg_demand} units/day</strong></li>
          <li>Demand Std Dev (&sigma;<sub>D</sub>) = 25% of Daily Demand = <strong>${sigma_D}</strong></li>
          <li>Lead Time Std Dev (&sigma;<sub>LT</sub>) = 15% of Lead Time = <strong>${sigma_LT}</strong></li>
          <li>Combined Lead Time Demand Volatility (&sigma;<sub>L</sub>) = <strong>${sigma_LT_demand}</strong></li>
        </ul>
        <div class="substitution">
          Calculation: 1.65 * &radic;( ${data.lead_time_days} * ${sigma_D}<sup>2</sup> + ${data.daily_avg_demand}<sup>2</sup> * ${sigma_LT}<sup>2</sup> ) = 1.65 * ${sigma_LT_demand} = <strong>${calculated_ss} units</strong>
        </div>
      </div>

      <h4>3. Stochastic Reorder Point (ROP)</h4>
      <div class="math-formula-box">
        <div class="equation">ROP = ( d * L ) + SS</div>
        <div><strong>Parameters substituted:</strong></div>
        <ul style="padding-left:16px; margin: 8px 0;">
          <li>Lead Time Demand (d * L) = ${data.daily_avg_demand} * ${data.lead_time_days} = <strong>${lead_demand} units</strong></li>
          <li>Safety Stock buffer (SS) = <strong>${calculated_ss} units</strong></li>
        </ul>
        <div class="substitution">
          Calculation: ${lead_demand} + ${calculated_ss} = <strong>${calculated_rop} units</strong>
        </div>
      </div>
    `;
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
      { title: "Decision Model", desc: `Computational Intelligence Segmentation Matrix: ABC-XYZ (<strong>${data.abc_class || 'C'}-${data.xyz_class || 'Z'}</strong>) | FSN-VED (<strong>${data.fsn_class || 'S'}-${data.ved_class || 'D'}</strong>).` },
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
      const batches = await API.listBatches();
      sel.innerHTML = batches.map((b) => `<option value="${b.id}" title="Provenance: ${escapeHtml(b.provenance || 'unlabelled')}${(b.derived_fields || []).length ? ' | derived fields: ' + escapeHtml(b.derived_fields.join(', ')) : ''}">[${escapeHtml((b.provenance || 'unlabelled').toUpperCase())}] ${escapeHtml(b.filename)} (${fmt.date(b.uploaded_at)})</option>`).join('');
      if (batches.length) {
        sel.value = AppState.get().currentBatchId === 'latest' ? batches[0].id : AppState.get().currentBatchId;
      }
    } catch (_) {}
  }

  function bindGlobalUI() {
    document.querySelectorAll('.nav-item[data-view]').forEach((item) => {
      item.addEventListener('click', () => navigate(item.dataset.view));
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

  function showDashboard() {
    document.getElementById('landingScreen')?.classList.add('hidden');
    document.getElementById('appShell')?.classList.remove('hidden');
  }

  function showLanding() {
    document.getElementById('appShell')?.classList.add('hidden');
    document.getElementById('landingScreen')?.classList.remove('hidden');
    UploadManager.init(document.getElementById('landingScreen'));
  }

  async function init() {
    ThemeManager.init();
    bindGlobalUI();

    const loader = document.getElementById('pageLoader');

    if (!Auth.isAuthenticated()) {
      document.getElementById('appShell')?.classList.add('hidden');
      document.getElementById('landingScreen')?.classList.add('hidden');
      document.getElementById('loginScreen')?.classList.remove('hidden');
      loader?.classList.add('hide');
      return;
    }

    try {
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

  async function createSinglePo(idx) {
    try {
      const orders = window._currentProcurementOrders || [];
      const o = orders[idx];
      if (!o) return;
      Toast.info(`Creating PO for ${o.product_name}...`);
      const res = await API.createPurchaseOrder({
        supplier_id: o.supplier_id || 1,
        warehouse_id: o.warehouse_id || 1,
        items: [{
          item_id: o.item_id,
          quantity_ordered: o.recommended_order_qty,
          unit_cost: o.unit_cost
        }]
      });
      Toast.success(`Purchase Order ${res.po_number || ''} created!`);
    } catch (e) {
      Toast.error(e.message || 'Failed to create PO');
    }
  }

  return { init, navigate, openDetails, createSinglePo };
})();

document.addEventListener('DOMContentLoaded', App.init);
