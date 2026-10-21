/* ==========================================================================
   recommendations.js — Explainable AI Recommendation Panel
   ========================================================================== */

const RecommendationsPanel = (() => {
  let container, summaryEl, filterBar;
  let activePriority = 'all';
  let activeCategory = 'all';
  let shown = {};          // rec_key -> recommendation as displayed
  let shownBatchId = null;

  const categoryIcons = {
    reorder: '↻', reduce: '↓', expiry: '⏱', supplier: '⚠',
  };

  function init(containerEl, summaryElement, filterBarEl) {
    container = containerEl;
    summaryEl = summaryElement;
    filterBar = filterBarEl;
    bindFilters();
  }

  function bindFilters() {
    if (!filterBar) return;
    filterBar.querySelectorAll('[data-priority]').forEach((btn) => {
      btn.addEventListener('click', () => {
        filterBar.querySelectorAll('[data-priority]').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        activePriority = btn.dataset.priority;
        refresh();
      });
    });
    const catSel = filterBar.querySelector('[data-role="rec-category"]');
    if (catSel) {
      catSel.addEventListener('change', (e) => { activeCategory = e.target.value; refresh(); });
    }
  }

  function renderSkeleton() {
    container.innerHTML = Array.from({ length: 4 }).map(() => `
      <div class="rec-card"><div class="rec-priority-stripe medium"></div>
        <div class="rec-body">
          <div class="skeleton" style="height:14px;width:40%;margin-bottom:10px"></div>
          <div class="skeleton" style="height:12px;width:90%;margin-bottom:6px"></div>
          <div class="skeleton" style="height:12px;width:70%"></div>
        </div>
      </div>`).join('');
  }

  async function refresh(batchId = AppState.get().currentBatchId) {
    renderSkeleton();
    try {
      const params = {};
      if (activePriority !== 'all') params.priority = activePriority;
      if (activeCategory !== 'all') params.category = activeCategory;
      const data = await API.getRecommendations(batchId, params);
      render(data);
    } catch (err) {
      container.innerHTML = `<div class="empty-state"><h3>Couldn't load recommendations</h3><p>${escapeHtml(err.message)}</p></div>`;
    }
  }

  function render(data) {
    if (summaryEl) {
      summaryEl.innerHTML = `
        <span class="badge badge-high">${data.summary.high} High</span>
        <span class="badge badge-medium">${data.summary.medium} Medium</span>
        <span class="badge badge-low-priority">${data.summary.low} Low</span>`;
    }

    if (!data.recommendations.length) {
      container.innerHTML = `<div class="empty-state"><h3>All clear</h3><p>No actionable recommendations match the current filters — inventory is in good shape.</p></div>`;
      return;
    }

    shown = {};
    data.recommendations.forEach((r) => { shown[r.rec_key] = r; });
    shownBatchId = data.batch_id;
    container.innerHTML = data.recommendations.map((r, idx) => `
      <div class="rec-card" id="recCard_${r.rec_key}">
        <div class="rec-priority-stripe ${r.priority}"></div>
        <div class="rec-body">
          <div class="rec-title-row">
            <h4>${categoryIcons[r.category] || '•'} ${escapeHtml(r.title)}</h4>
            <div style="display:flex;gap:8px;align-items:center">
              <span class="badge badge-${r.priority === 'high' ? 'high' : r.priority === 'medium' ? 'medium' : 'low-priority'}">${r.priority} priority</span>
              ${r.status && r.status !== 'pending' ? `<span class="badge badge-${r.status === 'accepted' ? 'success' : 'critical'}">${r.status.toUpperCase()}</span>` : ''}
            </div>
          </div>
          <p class="rec-reasoning">${escapeHtml(r.reasoning)}</p>
          <div class="rec-action">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2 3 14h7l-1 8 10-12h-7z"/></svg>
            <span><strong>Suggested Action:</strong> ${escapeHtml(r.suggested_action)}</span>
          </div>

          <!-- 11-Point Explainability Accordion -->
          <details class="rec-explain-drawer" style="margin-top:12px;background:var(--bg-surface);border:1px solid var(--border-subtle);border-radius:6px;padding:8px 12px;font-size:12px">
            <summary style="cursor:pointer;font-weight:600;color:var(--signal-indigo);outline:none">
              🔍 11-Point Explainability Breakdown (XAI)
            </summary>
            <div style="margin-top:10px;display:grid;grid-template-columns:1fr 1fr;gap:10px;color:var(--text-secondary)">
              <div><strong>Business Problem:</strong> ${escapeHtml(r.business_problem || 'Stock level misalignment')}</div>
              <div><strong>Empirical Evidence:</strong> ${escapeHtml(r.empirical_evidence || 'Reorder threshold breached')}</div>
              <div><strong>Formula Applied:</strong> <code class="mono">${escapeHtml(r.formula_reference || 'EOQ & Stochastic ROP')}</code></div>
              <div><strong>Affected KPI:</strong> <span class="badge badge-neutral">${escapeHtml(r.affected_kpi || 'Service Level')}</span></div>
              <div><strong>Optimization Outcome:</strong> ${escapeHtml(r.optimization_outcome || 'Optimal batch sizing')}</div>
              <div><strong>Risk Assessment:</strong> ${escapeHtml(r.risk_assessment || 'Service disruption if unaddressed')}</div>
              <div style="grid-column: span 2"><strong>Counterfactual Alternatives:</strong> ${escapeHtml(r.alternative_actions || 'Inter-warehouse transfer or supplier buffer adjustment')}</div>
            </div>
          </details>

          <div class="rec-meta" style="margin-top:14px;display:flex;align-items:center;justify-content:space-between">
            <div style="display:flex;gap:12px;align-items:center">
              ${r.sku ? `<span class="mono">${escapeHtml(r.sku)}</span>` : ''}
              <span>Impact score: <span class="impact-score">${r.impact_score}</span>/100</span>
              <span title="Dataset: ${escapeHtml((r.dataset && r.dataset.filename) || '')} [${escapeHtml((r.dataset && r.dataset.provenance) || 'unlabelled')}]">Data: <strong>${escapeHtml((r.dataset && r.dataset.provenance) || 'unlabelled')}</strong></span>
              <span title="Share of this recommendation's model inputs that are observed in the data rather than defaulted">Evidence completeness: <strong>${(r.evidence_completeness ?? r.confidence_score) == null ? 'n/a' : Math.round((r.evidence_completeness ?? r.confidence_score) * 100) + '%'}</strong></span>
            </div>
            <div class="rec-buttons" style="display:flex;gap:8px">
              <button class="btn btn-secondary btn-sm" onclick="RecommendationsPanel.handleAction('${r.rec_key}', 'rejected')" style="color:var(--status-critical);padding:4px 10px;font-size:11px">Reject</button>
              <button class="btn btn-secondary btn-sm" onclick="RecommendationsPanel.handleAction('${r.rec_key}', 'modified')" style="padding:4px 10px;font-size:11px">Modify</button>
              <button class="btn btn-primary btn-sm" onclick="RecommendationsPanel.handleAction('${r.rec_key}', 'accepted')" style="padding:4px 12px;font-size:11px">Accept</button>
            </div>
          </div>
        </div>
      </div>`).join('');
  }

  async function handleAction(recKey, action) {
    const rec = shown[recKey];
    if (!rec) { Toast.error('Recommendation no longer on screen; refresh.'); return; }
    let comment = '';
    let modifications = null;
    if (action === 'modified') {
      const q = window.prompt('Modified order quantity (units) — or describe the change:', '');
      if (q === null || q.trim() === '') return;
      modifications = isNaN(Number(q)) ? { description: q.trim() } : { order_quantity: Number(q) };
      comment = window.prompt('Why did you modify this recommendation? (required)', '') || '';
      if (!comment.trim()) { Toast.error('A comment is required to modify.'); return; }
    } else if (action === 'rejected') {
      comment = window.prompt('Why are you rejecting this recommendation? (required)', '') || '';
      if (!comment.trim()) { Toast.error('A comment is required to reject.'); return; }
    } else {
      comment = window.prompt('Optional comment for the decision log:', '') || '';
    }
    try {
      const res = await API.reviewDecision({
        rec_key: recKey,
        batch_id: String(shownBatchId ?? 'latest'),
        content_sha256: rec.content_sha256,
        action: action,
        manager_comment: comment.trim() || null,
        modifications: modifications,
      });
      Toast.success(`Decision #${res.decision_id} recorded with a snapshot of this recommendation.`);
      const card = document.getElementById(`recCard_${recKey}`);
      if (card) {
        card.style.opacity = '0.5';
        card.querySelector('.rec-buttons').innerHTML = `<span class="badge badge-${action === 'accepted' ? 'success' : (action === 'modified' ? 'neutral' : 'critical')}">${action.toUpperCase()}</span>`;
      }
    } catch (err) {
      Toast.error(err.message || 'Failed to record decision');
    }
  }

  return { init, refresh, handleAction };
})();
