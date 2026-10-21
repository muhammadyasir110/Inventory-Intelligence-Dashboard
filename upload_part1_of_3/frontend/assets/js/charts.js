/* ==========================================================================
   charts.js — Chart.js Visualizations
   Centralizes chart creation/updates so charts can be safely re-rendered
   whenever a new file is uploaded or filters change.
   ========================================================================== */

const Charts = (() => {
  const instances = {};

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function destroy(id) {
    const ctx = document.getElementById(id);
    if (ctx) {
      try {
        const existing = Chart.getChart(ctx);
        if (existing) {
          existing.destroy();
        }
      } catch (_) {}
    }
    if (instances[id]) {
      try {
        instances[id].destroy();
      } catch (_) {}
      delete instances[id];
    }
  }

  function baseFont() {
    return { family: "Inter, sans-serif", size: 11 };
  }

  function gridColor() {
    return cssVar('--border-subtle') || 'rgba(148,163,199,0.12)';
  }

  function textColor() {
    return cssVar('--text-secondary') || '#94a3b8';
  }

  function statusDistribution(canvasId, data) {
    destroy(canvasId);
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;
    const d = data || {};
    const labels = ['Healthy', 'Low Stock', 'Critical', 'Overstock', 'Expiring'];
    const values = [Number(d.normal) || 0, Number(d.low) || 0, Number(d.critical) || 0, Number(d.overstock) || 0, Number(d.expiring) || 0];
    const colors = [
      cssVar('--status-healthy') || '#10b981',
      cssVar('--status-low') || '#f59e0b',
      cssVar('--status-critical') || '#ef4444',
      cssVar('--status-overstock') || '#a855f7',
      cssVar('--status-expiring') || '#fb7185',
    ];
    instances[canvasId] = new Chart(ctx, {
      type: 'doughnut',
      data: { labels, datasets: [{ data: values, backgroundColor: colors, borderWidth: 0, hoverOffset: 6 }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        cutout: '68%',
        plugins: {
          legend: { position: 'bottom', labels: { color: textColor(), font: baseFont(), padding: 14, usePointStyle: true, pointStyle: 'circle' } },
          tooltip: { callbacks: { label: (c) => ` ${c.label}: ${c.raw} SKUs` } },
        },
      },
    });
  }

  function categoryBreakdown(canvasId, categories) {
    destroy(canvasId);
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;
    const safeCats = Array.isArray(categories) ? categories : [];
    const top = safeCats.slice(0, 8);
    
    // Check if we have stock values or if we should display unit volumes / counts
    const hasValues = top.some((c) => (Number(c.stock_value) || 0) > 0);
    const hasUnits = top.some((c) => (Number(c.total_units) || 0) > 0);
    const metricLabel = hasValues ? 'Stock Value ($)' : (hasUnits ? 'Stock Units (Qty)' : 'SKU Count');

    const labels = top.length ? top.map((c) => c.category || 'Uncategorized') : ['No Category Data'];
    const data = top.length ? top.map((c) => {
      if (hasValues) return Number(c.stock_value) || 0;
      if (hasUnits) return Number(c.total_units) || 0;
      return Number(c.sku_count) || 0;
    }) : [0];

    instances[canvasId] = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: metricLabel,
          data,
          backgroundColor: cssVar('--signal-indigo') || '#4f6ef7',
          borderRadius: 6,
          maxBarThickness: 28,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        indexAxis: 'y',
        plugins: { 
          legend: { display: false }, 
          tooltip: { 
            callbacks: { 
              label: (c) => hasValues ? ` $${Number(c.raw).toLocaleString()}` : ` ${Number(c.raw).toLocaleString()} units` 
            } 
          } 
        },
        scales: {
          x: { beginAtZero: true, grid: { color: gridColor() }, ticks: { color: textColor(), font: baseFont() } },
          y: { grid: { display: false }, ticks: { color: textColor(), font: baseFont() } },
        },
      },
    });
  }

  function supplierPerformance(canvasId, suppliers) {
    destroy(canvasId);
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;
    const safeSups = Array.isArray(suppliers) ? suppliers : [];
    const top = safeSups.slice(0, 6);
    const labels = top.length ? top.map((s) => s.supplier || 'Supplier') : ['No Data'];

    instances[canvasId] = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          { label: 'On-Time Delivery %', data: top.length ? top.map((s) => Number(s.on_time_delivery_rate) || 0) : [0], backgroundColor: cssVar('--signal-teal') || '#22d3b8', borderRadius: 6, maxBarThickness: 18 },
          { label: 'Quality Score %', data: top.length ? top.map((s) => Number(s.quality_score) || 0) : [0], backgroundColor: cssVar('--signal-indigo') || '#4f6ef7', borderRadius: 6, maxBarThickness: 18 },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom', labels: { color: textColor(), font: baseFont(), usePointStyle: true, pointStyle: 'circle' } } },
        scales: {
          x: { grid: { display: false }, ticks: { color: textColor(), font: baseFont(), maxRotation: 20, minRotation: 0 } },
          y: { beginAtZero: true, max: 100, grid: { color: gridColor() }, ticks: { color: textColor(), font: baseFont() } },
        },
      },
    });
  }

  function stockValueBySku(canvasId, items) {
    destroy(canvasId);
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;
    const safeItems = Array.isArray(items) ? items : [];

    const hasValues = safeItems.some((i) => (Number(i.stock_value) || 0) > 0);
    const hasUnits = safeItems.some((i) => (Number(i.quantity_on_hand) || 0) > 0);
    const metricKey = hasValues ? 'stock_value' : (hasUnits ? 'quantity_on_hand' : 'quantity_on_hand');
    const metricLabel = hasValues ? 'Stock Value ($)' : 'Stock Units on Hand';

    const top = [...safeItems].sort((a, b) => (Number(b[metricKey]) || 0) - (Number(a[metricKey]) || 0)).slice(0, 8);
    const colorMap = {
      normal: cssVar('--status-healthy') || '#10b981',
      low: cssVar('--status-low') || '#f59e0b',
      critical: cssVar('--status-critical') || '#ef4444',
      overstock: cssVar('--status-overstock') || '#a855f7',
      expiring: cssVar('--status-expiring') || '#fb7185'
    };
    const labels = top.map((i) => {
      const name = String(i.product_name || i.sku || 'Item');
      return name.length > 18 ? name.slice(0, 18) + '…' : name;
    });
    const values = top.map((i) => Number(i[metricKey] || 0));

    instances[canvasId] = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels.length ? labels : ['No Item Data'],
        datasets: [{
          label: metricLabel,
          data: values.length ? values : [0],
          backgroundColor: top.length ? top.map((i) => colorMap[i.status] || cssVar('--signal-indigo') || '#4f6ef7') : [cssVar('--signal-indigo') || '#4f6ef7'],
          borderRadius: 6,
          maxBarThickness: 28,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { 
          legend: { display: false }, 
          tooltip: { 
            callbacks: { 
              label: (c) => hasValues ? ` $${Number(c.raw).toLocaleString()}` : ` ${Number(c.raw).toLocaleString()} units` 
            } 
          } 
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: textColor(), font: baseFont(), maxRotation: 30 } },
          y: { beginAtZero: true, grid: { color: gridColor() }, ticks: { color: textColor(), font: baseFont() } },
        },
      },
    });
  }

  function policyComparison(canvasId, current, eoq, constrained) {
    destroy(canvasId);
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    const labels = ['Holding Cost ($)', 'Ordering Cost ($)', 'Shortage Cost ($)', 'Total Annual Cost ($)'];
    instances[canvasId] = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            label: 'Current Policy',
            data: [current?.holding_cost || 0, current?.ordering_cost || 0, current?.shortage_cost || 0, current?.total_inventory_cost || 0],
            backgroundColor: '#94a3b8',
            borderRadius: 4
          },
          {
            label: 'Classical EOQ/SS',
            data: [eoq?.holding_cost || 0, eoq?.ordering_cost || 0, eoq?.shortage_cost || 0, eoq?.total_inventory_cost || 0],
            backgroundColor: cssVar('--signal-teal') || '#22d3b8',
            borderRadius: 4
          },
          {
            label: 'Constrained Optimised Policy',
            data: [constrained?.holding_cost || 0, constrained?.ordering_cost || 0, constrained?.shortage_cost || 0, constrained?.total_inventory_cost || 0],
            backgroundColor: cssVar('--signal-indigo') || '#4f6ef7',
            borderRadius: 4
          }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: textColor(), font: baseFont() } },
          tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: $${Number(c.raw).toLocaleString()}` } }
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: textColor(), font: baseFont() } },
          y: { grid: { color: gridColor() }, ticks: { color: textColor(), font: baseFont() } }
        }
      }
    });
  }

  function demandForecast(canvasId, historyData = [], forecastData = [], horizon = 30) {
    destroy(canvasId);
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    const histLabels = historyData.map((_, i) => `T-${historyData.length - i}`);
    const foreLabels = forecastData.map((_, i) => `Day +${i + 1}`);
    const labels = [...histLabels, ...foreLabels];

    const histPadded = [...historyData, ...new Array(forecastData.length).fill(null)];
    const forePadded = [...new Array(historyData.length).fill(null), ...forecastData.map(d => d.predicted || d)];
    const upperPadded = [...new Array(historyData.length).fill(null), ...forecastData.map(d => (d.upper !== undefined ? d.upper : (d * 1.25)))];
    const lowerPadded = [...new Array(historyData.length).fill(null), ...forecastData.map(d => (d.lower !== undefined ? d.lower : (d * 0.75)))];

    instances[canvasId] = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: 'Historical Demand',
            data: histPadded,
            borderColor: '#94a3b8',
            backgroundColor: 'rgba(148,163,184,0.1)',
            borderWidth: 2,
            tension: 0.2,
            fill: false
          },
          {
            label: `Holt Projected (${horizon}d)`,
            data: forePadded,
            borderColor: cssVar('--signal-indigo') || '#4f6ef7',
            backgroundColor: 'rgba(79,110,247,0.1)',
            borderWidth: 2.5,
            tension: 0.25,
            fill: false
          },
          {
            label: '95% Upper Bound',
            data: upperPadded,
            borderColor: 'rgba(34,211,184,0.4)',
            borderDash: [4, 4],
            borderWidth: 1,
            pointRadius: 0,
            fill: false
          },
          {
            label: '95% Lower Bound',
            data: lowerPadded,
            borderColor: 'rgba(34,211,184,0.4)',
            borderDash: [4, 4],
            borderWidth: 1,
            pointRadius: 0,
            fill: false
          }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: textColor(), font: baseFont() } }
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: textColor(), font: baseFont() } },
          y: { grid: { color: gridColor() }, ticks: { color: textColor(), font: baseFont() } }
        }
      }
    });
  }

  function scenarioCostImpact(canvasId, baseline, simulated) {
    destroy(canvasId);
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    const labels = ['Holding Cost', 'Ordering Cost', 'Stockout Penalty', 'Total Cost'];
    instances[canvasId] = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            label: 'Baseline Policy',
            data: [
              baseline.holding_cost || 0,
              baseline.ordering_cost || 0,
              baseline.stockout_cost || 0,
              baseline.total_cost || 0
            ],
            backgroundColor: '#94a3b8',
            borderRadius: 4
          },
          {
            label: 'Simulated Stress Scenario',
            data: [
              simulated.holding_cost || 0,
              simulated.ordering_cost || 0,
              simulated.stockout_cost || 0,
              simulated.total_cost || 0
            ],
            backgroundColor: cssVar('--status-critical') || '#ef4444',
            borderRadius: 4
          }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: textColor(), font: baseFont() } },
          tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: $${Number(c.raw).toLocaleString()}` } }
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: textColor(), font: baseFont() } },
          y: { grid: { color: gridColor() }, ticks: { color: textColor(), font: baseFont() } }
        }
      }
    });
  }

  function dataQualityRadar(canvasId, scores) {
    destroy(canvasId);
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    const s = scores || {
      sku_integrity: 100,
      cost_data: 95,
      demand_history: 90,
      lead_time: 85,
      expiry_coverage: 88,
      storage_profile: 92
    };

    instances[canvasId] = new Chart(ctx, {
      type: 'radar',
      data: {
        labels: [
          'SKU Integrity',
          'Cost/Price Data',
          'Demand Time-Series',
          'Lead Time Reliability',
          'Expiry Tracking',
          'Storage Profiles'
        ],
        datasets: [{
          label: 'Data Readiness Score (%)',
          data: [
            s.sku_integrity || 98,
            s.cost_data || 95,
            s.demand_history || 90,
            s.lead_time || 85,
            s.expiry_coverage || 88,
            s.storage_profile || 92
          ],
          backgroundColor: 'rgba(79, 110, 247, 0.25)',
          borderColor: cssVar('--signal-indigo') || '#4f6ef7',
          pointBackgroundColor: cssVar('--signal-teal') || '#22d3b8',
          borderWidth: 2
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: {
          r: {
            angleLines: { color: gridColor() },
            grid: { color: gridColor() },
            pointLabels: { color: textColor(), font: baseFont() },
            ticks: { display: false, min: 0, max: 100 }
          }
        },
        plugins: {
          legend: { display: false }
        }
      }
    });
  }

  function volatilityWatchlist(canvasId, items = []) {
    destroy(canvasId);
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    const topItems = items.slice(0, 8);
    const labels = topItems.map(i => {
      const name = i.product_name || i.sku || 'SKU';
      return name.length > 14 ? name.slice(0, 14) + '…' : name;
    });
    const cvValues = topItems.map(i => (i.cv === undefined || i.cv === null) ? null : Math.round(i.cv * 1000) / 10);
    const bufferUnits = topItems.map(i => Math.round(1.96 * (i.daily_sd || 0) * 10) / 10);

    instances[canvasId] = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels.length ? labels : ['No Data'],
        datasets: [
          {
            label: 'Volatility (CV %)',
            data: cvValues.length ? cvValues : [0],
            backgroundColor: 'rgba(239, 68, 68, 0.75)',
            borderColor: cssVar('--status-critical') || '#ef4444',
            borderWidth: 1,
            borderRadius: 4,
            yAxisID: 'y'
          },
          {
            label: 'Buffer Expansion (Units)',
            data: bufferUnits.length ? bufferUnits : [0],
            backgroundColor: 'rgba(34, 211, 184, 0.75)',
            borderColor: cssVar('--signal-teal') || '#22d3b8',
            borderWidth: 1,
            borderRadius: 4,
            yAxisID: 'y1'
          }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: textColor(), font: baseFont() } },
          tooltip: {
            callbacks: {
              label: (c) => ` ${c.dataset.label}: ${c.raw}`
            }
          }
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: textColor(), font: baseFont(), maxRotation: 25 } },
          y: {
            type: 'linear', display: true, position: 'left',
            grid: { color: gridColor() },
            ticks: { color: textColor(), font: baseFont(), callback: v => v + '%' },
            title: { display: true, text: 'Volatility CV %', color: textColor(), font: baseFont() }
          },
          y1: {
            type: 'linear', display: true, position: 'right',
            grid: { drawOnChartArea: false },
            ticks: { color: textColor(), font: baseFont() },
            title: { display: true, text: 'Safety Buffer Units', color: textColor(), font: baseFont() }
          }
        }
      }
    });
  }

  return {
    statusDistribution,
    categoryBreakdown,
    supplierPerformance,
    stockValueBySku,
    policyComparison,
    demandForecast,
    scenarioCostImpact,
    dataQualityRadar,
    volatilityWatchlist,
    destroy
  };
})();
