# MPhil Research Mapping Document
## Thesis: "Explainable Inventory Decision Support through Computational Intelligence"

This document establishes the rigorous mapping between the academic theoretical contributions of the MPhil research and the software artifacts implemented in the **Inventory Intelligence Platform (IIP)**.

---

## 1. Research Pipeline to Code Mapping
The core contribution of the thesis is a **9-Stage Computational Intelligence Decision Support Pipeline**. The table below details where each stage is implemented:

| Stage | Theoretical Purpose | Implemented Engine & Endpoints |
|-------|-------------------|--------------------------------|
| **1. Enterprise Context** | Multi-echelon topology, tenant boundary, supplier contracts | `models/models.py` (`Organization`, `Warehouse`, `Supplier`), `/api/warehouses`, `/api/suppliers` |
| **2. Decision Context** | Goal definition: fill rate target (95%), holding rate (20%), budget constraint | `models/models.py` (`OrgConfig`), `/api/org/config` |
| **3. Decision Model** | Dynamic multi-criteria segmentation (ABC-XYZ-FSN-VED) | `analytics/analytics_engine.py` (`compute_classifications`), `stock_optimizer.py` |
| **4. Decision Analysis** | Stochastic variance modeling (joint lead time & demand variance) | `optimization/stock_optimizer.py` (`calculate_safety_stock_details`), `analytics/forecasting_engine.py` |
| **5. Decision Intelligence** | Constrained multi-item lot sizing solved by Artificial Bee Colony (ABC), PSO, GA, benchmarked against the exact Lagrangian/KKT optimum; policies evaluated by Monte Carlo simulation | `optimization/metaheuristics.py`, `optimization/stock_optimizer.py`, `analytics/inventory_simulation.py`, `/api/optimization/comparison`, `/api/optimization/run` |
| **6. Decision Explanation** | 11-Point Explainability Schema with alternative actions and an evidence-completeness indicator | `recommendation/recommendation_engine.py` (`generate_recommendations`, `evidence_completeness`), `/api/recommendations` |
| **7. Decision Recommendation** | Ranked, prioritized operational prescriptions | `frontend/assets/js/recommendations.js`, `frontend/pages/recommendations.html` |
| **8. Decision Monitoring** | Fill-rate tracking, stockout alert feeds, buffer breach detection | `models/models.py` (`Alert`), `api/dashboard.py`, `frontend/pages/alerts.html` |
| **9. Organizational Learning** | Human-in-the-Loop review audit trail, parameter calibration | `models/models.py` (`DecisionRecord`, `AuditLog`), `api/decisions.py`, `frontend/pages/decisions.html` |

---

## 2. 11-Point Explainability Taxonomy
To address the "black-box" credibility gap of AI in supply chains, every recommendation generated in IIP provides complete auditable rationale:

```json
{
  "business_problem": "Stock level falling below stochastic safety threshold",
  "empirical_evidence": "Current stock = 12 units, ROP = 25 units, Daily Demand = 3.2 units/day",
  "input_data_summary": "Lead Time = 7 days, Unit Cost = $45.00, Supplier OTD = 94%",
  "formula_reference": "Stochastic ROP: (d * L) + Z * sqrt(L * sigma_D^2 + D^2 * sigma_L^2)",
  "affected_kpi": "Stockout Risk Percentage (Service Level)",
  "constraints_checked": "Working capital budget limit: $1,000,000; Warehouse capacity: 150,000 units",
  "optimization_outcome": "Recommended Order Quantity (EOQ): 80 units; Safety Stock: 15 units",
  "risk_assessment": "Failure to reorder results in estimated stockout in 3.7 days",
  "expected_impact": "Prevents potential $1,800 stockout penalty; maintains 95% customer service level",
  "alternative_actions": "Inter-warehouse transfer from Karachi Hub (surplus: 45 units)",
  "suggested_action": "Issue purchase order for 80 units to Nexus Distributors"
}
```

---

## 3. Computational Intelligence Algorithms Implemented
1. **Holt's Double Linear Exponential Smoothing:**
   - Level Update: $L_t = \alpha Y_t + (1 - \alpha)(L_{t-1} + T_{t-1})$
   - Trend Update: $T_t = \beta(L_t - L_{t-1}) + (1 - \beta)T_{t-1}$
   - Forward Forecast ($m$ steps): $F_{t+m} = L_t + m \cdot T_t$
2. **Stochastic Safety Stock under Joint Volatility:**
   - $\sigma_{LT} = \sqrt{L \cdot \sigma_D^2 + D^2 \cdot \sigma_L^2}$
   - $SS = Z \cdot \sigma_{LT}$
3. **Budget- and Capacity-Constrained Multi-Item Lot Sizing:**
   - Exact solution: $Q_i = \sqrt{2SD_i / (c_i(h+\lambda) + 2\mu)}$ with KKT multipliers found by bisection.
   - Metaheuristics: Artificial Bee Colony (thesis algorithm), PSO and GA with a shared closed-form repair operator, equal evaluation budgets and fixed seeds.
4. **Monte Carlo policy evaluation:** daily lost-sales simulation with stochastic demand and lead times under common random numbers; fill rate, cycle service level, stockouts and costs reported with 95% CIs.

## 4. Reproducibility
`backend/experiments/run_benchmark.py` regenerates every computational result
(30 seeds, Friedman / Holm-corrected Wilcoxon tests, effect sizes, scalability,
convergence, simulated policy performance). See `docs/CHANGES_v2.1.md` §3 for how
to interpret and report these results.
