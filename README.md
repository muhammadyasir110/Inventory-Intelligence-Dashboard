# Inventory Intelligence Platform (IIP) 2.7.0
> Explainable inventory decision support for FMCG distributors and wholesalers (Pakistan-first, internationally extensible).
> Research basis: MPhil thesis *"Artificial Bee Colony Optimization for FMCG Supply Chains"* and the
> Explainable Inventory Decision Support Framework (EIDSF).

![Version](https://img.shields.io/badge/version-2.7.0-4f6ef7)
![Status](https://img.shields.io/badge/status-pilot--ready%20prototype-orange)
![Stack](https://img.shields.io/badge/backend-FastAPI%20%2B%20SQLAlchemy%20%2B%20Alembic-009688)
![DB](https://img.shields.io/badge/database-PostgreSQL%20(prod)%20%7C%20SQLite%20(dev)-336791)

## 1. Status (read first)

**2.7.0 is a SaaS candidate: multi-tenant, with organisation owners and roles, invitations, self-service
password reset and e-mail verification (SMTP), server-side plan limits and subscription states, a
provider-neutral billing core, and (2.7) two-factor login, intermittent-demand forecasting and transfer execution. It is ready for staging and a controlled pilot; it is not commercially
launchable yet** (no live payment provider, no cloud deployment or image build verified here, no
penetration test, no legal documents). Status: [`docs/RELEASE_READINESS_v2.7.md`](docs/RELEASE_READINESS_v2.7.md);
limitations: [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md); billing: [`docs/BILLING.md`](docs/BILLING.md);
operations: [`docs/OPERATIONS.md`](docs/OPERATIONS.md); commercial terms to settle:
[`docs/COMMERCIAL_READINESS.md`](docs/COMMERCIAL_READINESS.md). In short:

* verified here: clean install, Alembic migrations on SQLite and PostgreSQL 16 (clean + upgrade of a real
  2.4 database without data loss), backup/restore round-trip on PostgreSQL, cross-tenant isolation of every
  API route, inventory formulas against hand calculations, the full automated suite, a browser test of every page
  under a strict Content-Security-Policy, and a dependency vulnerability scan; in 2.5.1 additionally a
  staging rehearsal on PostgreSQL 16 (non-root, `IIP_ENV=staging`, migrate, bootstrap, smoke test, backup ->
  simulated loss -> restore), a stock ledger for adjustments/edits/receipts, and capacity-checked transfer
  proposals (audit matrix: [`docs/audit/V2.5.1_AUDIT_MATRIX.md`](docs/audit/V2.5.1_AUDIT_MATRIX.md)); in 2.6.0
  the role matrix, invitations/reset/verification (incl. an SMTP conversation with a local server), billing
  webhooks and access rules with a test adapter, organisation export/purge isolation, reconciliation, and an
  upgrade of the populated 2.5.1 staging database (audit matrix:
  [`docs/audit/V2.6_AUDIT_MATRIX.md`](docs/audit/V2.6_AUDIT_MATRIX.md));
* not verified here: a deployment on your chosen cloud (the Dockerfile could not be built in the
  development sandbox), an external penetration test, real-company data, a user study, a live payment
  provider, delivery of e-mail to real inboxes.

All bundled experiment results are **synthetic**; the research engine (`backend/eidsf`) and the manuscript
results are unchanged from 2.4.0 (archived: DOI [10.5281/zenodo.23529016](https://doi.org/10.5281/zenodo.23529016)).

## 2. What it does

| Area | Capability (2.5) |
|---|---|
| Data | CSV/Excel import of inventory snapshots and **daily demand history** with server-side validation, row-level errors and **preview before commit**; atomic imports; provenance + SHA-256 of every file |
| Inventory maths | EOQ, safety stock (constant or random lead time), reorder point, cycle service level vs fill rate, ABC (Pareto), XYZ (**observed** CV only), FSN, VED (labelled heuristic) - see [`docs/MATHEMATICAL_FORMULATION.md`](docs/MATHEMATICAL_FORMULATION.md) |
| Forecasting | SMA, WMA, SES, Holt; out-of-sample holdout and rolling-origin evaluation vs a naive baseline; MAE/RMSE, MAPE only when defined, WAPE and MASE; **no synthetic substitution** (insufficient history is reported) |
| Optimisation | Budget/capacity-constrained multi-item lot sizing (ABC, PSO, GA, exact KKT) and the **EIDSF explainable policy** (FMCG-ABC on the joint (Q, SS) model with a certified Lagrangian lower bound, cost drivers, rules and resource status) |
| Procurement | Replenishment plan, draft POs per supplier, approval with separation of duties, ordering, (partial) **goods receipt that updates stock** and the transaction ledger |
| SaaS | Organisations, roles, platform vs organisation administrators, plans with **backend-enforced** limits, usage, audit log, PKR / time zone / fiscal year per organisation |
| Security | PBKDF2 passwords, revocable sessions, lockout and throttling, strict CSP and security headers, request IDs, generic errors, formula-safe exports, tenant isolation tests on every route |
| Operations | Alembic migrations, `/api/health` + `/api/ready`, JSON logs, error-monitoring hook, tested backup/restore CLI, Dockerfile, Render blueprint, release gate |

## 3. Quick start (local, development)

Requirements: Python 3.12 or 3.13.

**Windows:** double-click `START_DASHBOARD.bat` (creates `backend\venv`, installs requirements, starts
http://127.0.0.1:8000). **macOS / Linux:**

```bash
cd backend
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
```

Development mode creates a demo organisation and the demo user `admin` / `admin123` (shown as *demo mode*).
Change it with `python scripts/manage_users.py set-password admin`. Staging/production never create default
users: see [`docs/INSTALL.md`](docs/INSTALL.md) and [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

```bash
pip install -r requirements-dev.txt
python -m pytest -q tests                      # full suite (~7 min; research-engine tests dominate)
python scripts/release_gate.py --quick         # release checks
```

## 5. Reproduce the research article (EIDSF)

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

## 4. Documentation

| Topic | Document |
|---|---|
| Installation, configuration, environment variables, troubleshooting | [`docs/INSTALL.md`](docs/INSTALL.md) |
| Architecture | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Database, migrations, backup and recovery | [`docs/DATABASE.md`](docs/DATABASE.md) |
| Deployment, HTTPS, rollback, release process | [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) |
| API reference | [`docs/API.md`](docs/API.md) |
| User guide | [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) |
| Administrator guide (organisation and platform) | [`docs/ADMIN_GUIDE.md`](docs/ADMIN_GUIDE.md) |
| Mathematical methods | [`docs/MATHEMATICAL_FORMULATION.md`](docs/MATHEMATICAL_FORMULATION.md) |
| EIDSF / ABC methodology and research validation protocol | [`docs/RESEARCH_MAPPING.md`](docs/RESEARCH_MAPPING.md) |
| Research dataset validation | [`docs/DATA_VALIDATION.md`](docs/DATA_VALIDATION.md) |
| Security checklist | [`docs/SECURITY.md`](docs/SECURITY.md) |
| Known limitations | [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md) |
| Billing and subscriptions (provider-neutral core) | [`docs/BILLING.md`](docs/BILLING.md) |
| Operations: monitoring, backups, RPO/RTO, incidents | [`docs/OPERATIONS.md`](docs/OPERATIONS.md) |
| Commercial readiness: plans, data ownership, retention, legal review | [`docs/COMMERCIAL_READINESS.md`](docs/COMMERCIAL_READINESS.md) |
| Audit matrices | [`2.6`](docs/audit/V2.6_AUDIT_MATRIX.md), [`2.5.1`](docs/audit/V2.5.1_AUDIT_MATRIX.md), [`2.5`](docs/audit/V2.5_AUDIT_REPORT.md) |
| Release readiness | [`2.7`](docs/RELEASE_READINESS_v2.7.md) (current), [`2.6`](docs/RELEASE_READINESS_v2.6.md), [`2.5`](docs/RELEASE_READINESS_v2.5.md) |
| Changelog | [`CHANGELOG.md`](CHANGELOG.md) |

Earlier release notes: [`docs/CHANGES_v2.1.md`](docs/CHANGES_v2.1.md), [`docs/CHANGES_v2.2.md`](docs/CHANGES_v2.2.md),
[`docs/audit/CHANGES_eidsf.md`](docs/audit/CHANGES_eidsf.md); historical readiness: [`docs/RELEASE_READINESS_v2.2.md`](docs/RELEASE_READINESS_v2.2.md).
The v2.0 `System_Description_Report.pdf` (outdated, listed demo credentials) was removed in 2.6.0; it remains in the git history. The documents in `docs/` describe the current system.

## 6. Licence and citation

MIT licence (`LICENSE`). Cite with `CITATION.cff`.
