 # Inventory Intelligence Platform (IIP) 2.4
> **Enterprise Decision Support, Multi-Item Optimization & Explainable AI Inventory Platform**  
> Embodiment of MPhil Research: *"Explainable Inventory Decision Support through Computational Intelligence"*

![Version](https://img.shields.io/badge/version-2.4.0-4f6ef7)
![Tests](https://img.shields.io/badge/tests-see%20RELEASE__READINESS-lightgrey)
![Architecture](https://img.shields.io/badge/architecture-Multi--Tenant%20%7C%20RBAC-blue)
![Stack](https://img.shields.io/badge/backend-FastAPI%20%2B%20SQLAlchemy-009688)

> **v2.2** fixes critical security and data-integrity defects found in a full audit
> ([`docs/audit/BASELINE_AUDIT.md`](docs/audit/BASELINE_AUDIT.md), [`docs/CHANGES_v2.2.md`](docs/CHANGES_v2.2.md)).
> **v2.1 was a research-integrity release.** All KPIs are now computed (optimisation +
> seeded Monte Carlo simulation), the Artificial Bee Colony algorithm actually runs inside
> the system, and a reproducible experiment script produces the paper's result tables.
> See [`docs/CHANGES_v2.1.md`](docs/CHANGES_v2.1.md) for every defect fixed and for
> what can and cannot be claimed in the paper.

---

## 1. Platform Capabilities

1. **Executive Control Tower:**
   - Multi-tenant tenant scoping (`org_id` multi-tenant database isolation)
   - Real-time stock health categorization: Healthy, Low, Critical, Overstock, Expiring (FEFO)
   - Multi-criteria segmentation: **ABC** (Cost Value), **XYZ** (Demand Volatility), **FSN** (Movement Speed), **VED** (Criticality)
2. **Optimization Center:**
   - Budget- and capacity-constrained multi-item lot sizing solved by **Artificial Bee Colony (ABC)**, PSO, GA, or the **exact Lagrangian/KKT** solver (selectable, seeded)
   - Solver benchmark: gap of every method to the exact optimum, feasibility, evaluations, runtime
   - 3-policy comparison (current vs classical EOQ/SS vs constrained) with cost, fill rate, cycle service level and stockouts **measured by Monte Carlo simulation** under common random numbers, with 95% CIs
3. **Demand Forecasting Engine:**
   - Statistical modeling: SMA, WMA, Single Exponential Smoothing, and Holt's Linear Double Exponential Smoothing
   - Multi-horizon projections (7, 30, and 90 days) with 95% confidence interval bands
   - Out-of-sample evaluation (7-day holdout): MAE, RMSE, MAPE; uses recorded `demand_history` when available, otherwise flags the series as synthetic/illustrative
4. **What-If Scenario Simulator:**
   - Real-time stress testing against demand shocks (-50% to +100%), lead-time disruptions (0-30 days), interest rates, and budget cuts
   - Baseline vs do-nothing vs re-planned policy, all simulated; "value of re-planning" metric
5. **Procurement & PO Engine:**
   - Automated replenishment sizing matching EOQ and vendor pack sizes
   - Purchase order lifecycle workflow (Draft → Approved → Ordered → Received)
6. **Explainable AI (XAI) & Human-in-the-Loop Governance:**
   - Full 11-point explainability schema for every recommendation, with an evidence-completeness indicator (share of inputs observed vs defaulted)
   - Manager review actions: Accept, Modify, Reject with immutable audit logging for organizational learning
7. **Policy Configuration & Data Readiness Radar:**
   - Interactive parameters tuning: Service Level (80-99%), Holding Rate, Setup Cost, Shortage Cost
   - 6-axis Data Governance & Health Radar Chart

---

## 2. Status of this release (read first)

* **Research-demonstration prototype**, not a production system. See
  [`docs/RELEASE_READINESS_v2.2.md`](docs/RELEASE_READINESS_v2.2.md).
* The optimisation model (P) is **not yet the thesis formulation** for the 30-SKU research dataset
  (per-SKU ordering/holding/shortage costs, volume capacity). Decisions needed are listed in
  [`docs/MATHEMATICAL_FORMULATION.md`](docs/MATHEMATICAL_FORMULATION.md) §8.
* All bundled experiment results are **synthetic** (`backend/experiments/results/synthetic/`).

## 3. Clean installation

Requirements: Python **3.12 or 3.13** (verified on 3.12.3 and 3.13.16), internet for the first install.

**Windows (recommended):** double-click `START_DASHBOARD.bat`. On first run it creates
`backend\venv` and installs `backend\requirements.txt`. If *Smart App Control* blocks the file,
run the same steps manually:

```bat
cd backend
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
venv\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

**macOS / Linux:**
```bash
cd backend
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000.

### Configuration
Copy `backend/.env.example` to `backend/.env` and edit (see [`docs/SECURITY.md`](docs/SECURITY.md)).
Without a `.env`, the app runs in **development/demo mode**: on a *new* database the `admin` user is
created with the documented demo password `admin123`, and `/api/health` reports `demo_mode: true`.

### Users
```bash
cd backend
python scripts/manage_users.py set-password admin        # change the demo password
python scripts/manage_users.py create participant01 --role executive
python scripts/manage_users.py list
```

### Database
SQLite file `backend/database/iid_local.db` by default; PostgreSQL via
`DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/db`. Schema changes are applied
automatically and **add-only** on start-up. Back up the `.db` file before upgrading.

### Tests
```bash
cd backend
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```
Tests run on a temporary database and never touch `iid_local.db`.

### Research dataset validation
```bash
cd backend
python -m research.ingest "path/to/research_dataset.xlsx"
```
See [`docs/DATA_VALIDATION.md`](docs/DATA_VALIDATION.md). Units must be confirmed in
`backend/research/config/research_30sku_mapping.json` before unit-dependent calculations.

### Synthetic computational study
```bash
cd backend
python experiments/run_benchmark.py            # ~10 min; writes results/synthetic/<date>/
python experiments/run_benchmark.py --quick
```

## 3a. Reproduce the research article (EIDSF)

All results in `paper/` are produced by the `backend/eidsf` package from synthetic data (no company data).
Environment used: Python 3.12.3 with the exact versions in `backend/requirements-research.txt`.

```bash
cd backend
python3.12 -m venv .venv-research && . .venv-research/bin/activate
pip install -r requirements-research.txt
python -m eidsf.study --workers 2                                  # E1-E7      (~70 min, 2 cores)
python -m eidsf.supplement experiments/results/eidsf/<date>        # S1-S3      (~60 min)
python -m eidsf.extend     experiments/results/eidsf/<date>        # X1-X6      (~80 min)
python -m eidsf.stats_unpaired experiments/results/eidsf/<date>     # Mann-Whitney/Holm, A12, Kruskal-Wallis (seconds)
python -m eidsf.artifact_v2 experiments/results/eidsf/<date>       # second-iteration artifact with resource status
python -m eidsf.report     experiments/results/eidsf/<date> --out ../paper/generated
cd ../paper && pdflatex main && bibtex main && pdflatex main && pdflatex main
```

Verification (no re-optimisation needed for the first one):

```bash
python experiments/verification/verify_claims.py experiments/results/eidsf/<date>   # 134 manuscript numbers vs result files
PYTHONPATH=. python experiments/verification/repro_check.py experiments/results/eidsf/<date>  # re-runs a subset, bit-for-bit
python experiments/verification/symbolic_hessian.py   # needs sympy; checks the Hessian of Proposition 1 and the lemma steps
cd tests && python -m unittest discover -p "test_*.py"
```

Result files: `raw_runs.csv` (every E-run), `x_raw_runs.csv` (every X-run), `e*_*.csv`, `s*_*.csv`, `x*_*.csv`
(summaries), `artifact_fmcg_abc.json`, `run_config.json` and `x_config.json` (seeds, versions, runtimes).
The folder `experiments/results/eidsf/2026-10-18` contains the outputs reported in the article; files with
suffix `_v1` are the superseded E3 references (see `docs/audit/CHANGES_eidsf.md`).
Stochastic results are reproducible for the same seeds and library versions; other NumPy/SciPy versions may change
individual runs.

## 4. Documentation Suite

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): Architectural blueprint, data models, and optimization formulations.
- [`docs/RESEARCH_MAPPING.md`](docs/RESEARCH_MAPPING.md): Academic thesis mapping, 9-stage DSS pipeline, and 11-point explainability schema.
- [`docs/API.md`](docs/API.md): Comprehensive REST API reference across all 14 routers (57+ endpoints).
- [`docs/schema.sql`](docs/schema.sql): PostgreSQL enterprise production schema DDL.
- [`docs/CHANGES_v2.1.md`](docs/CHANGES_v2.1.md): defects fixed in v2.1.
- [`docs/CHANGES_v2.2.md`](docs/CHANGES_v2.2.md): v2.2 change log, changed tests, breaking API changes, migration.
- [`docs/audit/BASELINE_AUDIT.md`](docs/audit/BASELINE_AUDIT.md): audit and issue register.
- [`docs/MATHEMATICAL_FORMULATION.md`](docs/MATHEMATICAL_FORMULATION.md): formulas, assumptions, KKT applicability, open decisions.
- [`docs/DATA_VALIDATION.md`](docs/DATA_VALIDATION.md): research-dataset ingestion and validation rules.
- [`docs/SECURITY.md`](docs/SECURITY.md): security remediation and dev/prod configuration.
- [`docs/RELEASE_READINESS_v2.2.md`](docs/RELEASE_READINESS_v2.2.md): what is complete and what is not.
