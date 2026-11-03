/* ==========================================================================
   upload.js — File import with preview-before-commit
   A file is first validated on the server (nothing is written). The user
   sees row counts, errors with spreadsheet row numbers, warnings and the
   fields that would be derived by rule, and only then confirms the import.
   Zones: data-kind="inventory" (default) or data-kind="demand".
   ========================================================================== */

const UploadManager = (() => {
  const EXT = {
    inventory: /\.(csv|xlsx|xls|xlsm|tsv|txt|pdf)$/i,
    demand: /\.(csv|xlsx|xls|xlsm|tsv|txt)$/i,
  };

  function init(root = document) {
    root.querySelectorAll('.upload-zone').forEach((zone) => {
      if (zone.dataset.bound === 'true') return;
      zone.dataset.bound = 'true';
      zone.setAttribute('tabindex', '0');
      zone.setAttribute('role', 'button');
      const input = zone.querySelector('input[type="file"]');
      zone.addEventListener('click', () => input && input.click());
      zone.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input && input.click(); } });
      zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('dragover'); });
      zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
      zone.addEventListener('drop', (e) => {
        e.preventDefault();
        zone.classList.remove('dragover');
        if (e.dataTransfer.files.length) preview(zone, e.dataTransfer.files[0]);
      });
      if (input) input.addEventListener('change', (e) => {
        if (e.target.files.length) preview(zone, e.target.files[0]);
        e.target.value = '';
      });
    });
  }

  function panelFor(zone) {
    let panel = zone.parentElement.querySelector(':scope > .upload-preview');
    if (!panel) {
      panel = document.createElement('div');
      panel.className = 'upload-preview';
      panel.setAttribute('role', 'region');
      panel.setAttribute('aria-live', 'polite');
      panel.style.marginTop = '16px';
      zone.insertAdjacentElement('afterend', panel);
    }
    return panel;
  }

  function rowErrorTable(rows) {
    if (!rows || !rows.length) return '';
    return `<div class="table-wrap" tabindex="0" role="region" aria-label="Preview table" style="max-height:220px;overflow:auto;margin-top:8px">
      <table class="data-table"><thead><tr><th>Row</th><th>Column</th><th>Value</th><th>Problem</th></tr></thead><tbody>
      ${rows.slice(0, 50).map((r) => `<tr><td>${r.row}</td><td>${escapeHtml(r.column || '')}</td><td>${escapeHtml(r.value == null ? '' : String(r.value))}</td><td>${escapeHtml(r.error)}${r.severity === 'warning' ? ' (warning)' : ''}</td></tr>`).join('')}
      </tbody></table></div>`;
  }

  async function preview(zone, file) {
    const kind = zone.dataset.kind || 'inventory';
    if (!EXT[kind].test(file.name)) {
      Toast.error(`Unsupported file type for ${kind === 'demand' ? 'demand history' : 'inventory'} import.`);
      return;
    }
    const panel = panelFor(zone);
    panel.innerHTML = `<div class="progress-bar-indeterminate"></div><p style="margin-top:8px;font-size:var(--fs-sm)">Validating ${escapeHtml(file.name)}… nothing is saved yet.</p>`;
    let res;
    try {
      res = kind === 'demand' ? await API.previewDemand(file) : await API.previewUpload(file);
    } catch (err) {
      panel.innerHTML = `<div style="padding:12px;border:1px solid var(--status-critical);border-radius:6px">Could not read the file: ${escapeHtml(err.message)}</div>`;
      return;
    }
    const rep = res.report || {};
    const ok = !!rep.is_valid;
    const derived = Object.keys(rep.derivations || {});
    const lines = [
      `<strong>${escapeHtml(res.filename)}</strong>: ${rep.total_rows ?? 0} row(s) read${ok ? `, ${rep.valid_rows ?? 0} valid` : ''}.`,
    ];
    if (kind === 'inventory' && ok) lines.push(`${rep.distinct_skus} SKUs, ${rep.distinct_warehouses} warehouse(s), ${rep.distinct_suppliers} supplier(s); stock value ${fmt.currency(rep.total_stock_value)}.`);
    if (kind === 'demand' && rep.date_range) lines.push(`${rep.skus} SKU(s), ${rep.date_range.from} to ${rep.date_range.to}; ${rep.conflicts_with_existing || 0} row(s) already recorded.`);
    if (derived.length) lines.push(`Not in your file, would be derived by rule: <em>${derived.map(escapeHtml).join(', ')}</em>.`);
    (rep.notes || []).forEach((n) => lines.push(escapeHtml(n)));
    const errs = (rep.errors || []).map((e) => `<li>${escapeHtml(e)}</li>`).join('');
    const warns = (rep.warnings || []).slice(0, 8).map((w) => `<li>${escapeHtml(w)}</li>`).join('');
    const provenance = document.getElementById('uploadProvenance')?.value || 'user_uploaded';
    panel.innerHTML = `
      <div style="padding:12px 14px;border-radius:6px;border:1px solid ${ok ? 'var(--signal-teal)' : 'var(--status-critical)'}">
        <div style="font-size:var(--fs-sm);line-height:1.6">${lines.join('<br>')}</div>
        ${errs ? `<ul style="margin:8px 0 0 16px;color:var(--status-critical)">${errs}</ul>` : ''}
        ${warns ? `<details style="margin-top:8px"><summary>Warnings (${(rep.warnings || []).length})</summary><ul style="margin:6px 0 0 16px">${warns}</ul></details>` : ''}
        ${rowErrorTable(rep.row_errors)}
        <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          ${ok ? `<button class="btn btn-primary btn-sm" data-upload-confirm>Import ${kind === 'demand' ? 'demand history' : 'inventory'}</button>` : '<span class="text-muted">Fix the problems above and choose the file again. Nothing was imported.</span>'}
          ${ok && kind === 'demand' && rep.conflicts_with_existing ? `<label style="font-size:12px"><input type="checkbox" data-upload-replace /> Replace the ${rep.conflicts_with_existing} existing row(s)</label>` : ''}
          <button class="btn btn-secondary btn-sm" data-upload-cancel>Cancel</button>
        </div>
      </div>`;
    panel.querySelector('[data-upload-cancel]')?.addEventListener('click', () => { panel.innerHTML = ''; });
    panel.querySelector('[data-upload-confirm]')?.addEventListener('click', (e) => commit(zone, panel, file, kind, provenance, e.currentTarget));
  }

  async function commit(zone, panel, file, kind, provenance, btn) {
    btn.disabled = true;
    document.dispatchEvent(new CustomEvent('upload:start', { detail: { filename: file.name, kind } }));
    try {
      if (kind === 'demand') {
        const replace = panel.querySelector('[data-upload-replace]')?.checked;
        const r = await API.importDemand(file, replace ? 'replace' : 'skip', provenance);
        Toast.success(`Demand history imported: ${r.rows_written} row(s) written, ${r.rows_skipped_existing} skipped, ${r.rows_replaced} replaced.`);
        panel.innerHTML = '';
        document.dispatchEvent(new CustomEvent('upload:demand', { detail: r }));
      } else {
        const result = await API.uploadFile(file, provenance);
        Toast.success(`Imported ${result.rows_imported} rows as ${provenance}.`);
        panel.innerHTML = '';
        document.dispatchEvent(new CustomEvent('upload:success', { detail: result }));
      }
    } catch (err) {
      const rows = err.detail && err.detail.row_errors;
      panel.innerHTML = `<div style="padding:12px;border:1px solid var(--status-critical);border-radius:6px">Import failed: ${escapeHtml(err.message)}${rowErrorTable(rows)}</div>`;
      document.dispatchEvent(new CustomEvent('upload:error', { detail: { error: err.message } }));
    }
  }

  return { init, preview };
})();
