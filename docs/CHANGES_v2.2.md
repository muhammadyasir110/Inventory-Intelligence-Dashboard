# IIP 2.2 — change log (2026-10-18)

Baseline: v2.1 (`digital_solution_v2.1.zip`, SHA-256 `4fd2fc68…`), git commit `755fb3d`.
Every change below is a separate git commit; `git log --stat` shows the files.

| Phase | Commit | Summary |
|---|---|---|
| 1 | `76418e0` | Baseline audit and prioritized issue register (`docs/audit/BASELINE_AUDIT.md`) |
| 8a | `ff15f55` | Security: no auth fallbacks, PBKDF2, DB sessions with expiry, env settings, CORS, role whitelist, tenant scoping, upload limits; isolated test DB |
| 9a | `78d2477` | Requirements with Python 3.12 + 3.13 wheels (+ `pypdf`), `requirements-dev.txt`, `.env.example`, `scripts/manage_users.py`, safer launchers |
| 2 | `a196a3c` | Strict research-dataset ingestion (`backend/research/`), inventory parser derivation reporting, sales-order aliases removed, provenance on batches, add-only migration |
| 3a | `678b1a9` | Mathematical verification tests, simulator off-by-one fix, σ semantics, undefined-gap handling, `docs/MATHEMATICAL_FORMULATION.md` |
| 7a | `22c987a` | Decision provenance (snapshots, keys, Modify), Recommendations page loaded, review URL fixed, provenance in UI, same-origin API base |
| docs | (this release) | `SECURITY.md`, `DATA_VALIDATION.md`, this file, release-readiness report, synthetic study re-run |

## Tests whose assertions changed (with justification)

| Test | v2.1 assertion | v2.2 | Why |
|---|---|---|---|
| `test_auth_tenancy::test_password_hashing` | `hash(p) == hash(p)` | hashes differ, both verify | equality encoded the unsalted-hash defect (issue 4) |
| `test_api_integration::test_health` | version `2.0.0` | `2.2.0` | version bump |
| `test_api_integration` setup | used the real database | uploads the synthetic sample into a temporary DB | tests must not touch research data (S11) |
| `test_research_engine::test_no_hardcoded_policy_kpis` | 2-decimal fill rates differ | full-precision (fill, CI, cost) differ **and** causal check (more variability → lower fill) | the rounded comparison failed by coincidence (99.12 = 99.12); the new test is stronger |
| `test_math_verification::test_cycle_service_level_*` | (new) first version expected CSL ≈ 0.95 | compares to undershoot-corrected theory (0.8255 / 0.9246, ±3 pp) | daily review cannot reach the continuous-review target; the original expectation was the wrong theory, not a code defect (issue 22) |

## Breaking API changes

* All data endpoints now return **401** without a valid token (previously served admin data).
* `POST /api/auth/register-org` returns **403** unless `IIP_ALLOW_REGISTRATION=true`.
* `POST /api/decisions/review` body: `{rec_key, batch_id, content_sha256?, action, manager_comment, modifications}`
  (legacy `recommendation_id` still accepted for stored recommendations). `modified`/`rejected` require a comment;
  unknown keys → 404 instead of a placeholder record.
* `PUT/DELETE /api/update/*` require editor roles and return 404 for other organisations' ids.
* Upload: 413 (size/rows), 415 (extension), 400 (empty file, bad mapping JSON); new form field `provenance`.
* Gap fields can be `null` (undefined) instead of `0.0`.

## Migration (existing v2.1 database)

Automatic and add-only on first start: new table `user_sessions`; new nullable columns
`upload_batches.provenance|source_sha256|derivations`, `recommendations.rec_key|engine_version|snapshot`.
Verified on a copy of the real v2.1 database: no existing row changed except the admin's `last_login`
and password hash (upgraded to PBKDF2 at first login). Back up `backend/database/iid_local.db` before upgrading anyway.
