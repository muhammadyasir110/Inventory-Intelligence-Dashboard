"""
scripts/release_gate.py
------------------------
Release quality gate. Exits non-zero if ANY required check fails or cannot be
run. Run from backend/ with the development requirements installed:

    python scripts/release_gate.py            # full gate (several minutes)
    python scripts/release_gate.py --quick    # skip the slow research-engine tests

Checks
  1. version identifiers agree (main.py, CITATION.cff, README, CHANGELOG)
  2. no secrets committed (private keys, hard-coded production passwords/URLs)
  3. lint: ruff (pyproject.toml)
  4. migrations match the ORM models (alembic check) on a scratch database
  5. frontend build check (templates, CSP-safe markup, JS syntax)
  6. full automated test suite (pytest), incl. security, tenancy, maths, e2e
     (PostgreSQL migration tests run when IIP_TEST_PG_URL is set)
  7. research claims re-verified against stored results (verify_claims)
  8. dependency vulnerability scan (pip-audit; needs network access)
Use --allow-offline to record (not fail) check 8 when the package index is
unreachable; the summary then says the scan did NOT run.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BACKEND)
PY = sys.executable
results: list[tuple[str, str, str]] = []


def record(name, ok, detail=""):
    results.append((name, "PASS" if ok is True else ("FAIL" if ok is False else ok), detail))


def run(cmd, cwd=BACKEND, env=None, timeout=3600):
    t = time.time()
    r = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True,
                       text=True, timeout=timeout)
    return r, time.time() - t


def check_versions():
    main = open(os.path.join(BACKEND, "main.py")).read()
    v = re.search(r'APP_VERSION = "([^"]+)"', main).group(1)
    probs = []
    cff = open(os.path.join(ROOT, "CITATION.cff")).read()
    if f"version: {v}" not in cff:
        probs.append("CITATION.cff")
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    if v not in readme.split("\n", 3)[0] + readme[:600]:
        probs.append("README.md title/badge")
    ch = os.path.join(ROOT, "CHANGELOG.md")
    if not os.path.exists(ch) or f"## [{v}]" not in open(ch, encoding="utf-8").read():
        probs.append("CHANGELOG.md entry")
    record("1 version identifiers", not probs, f"{v}" + (f"; mismatch: {', '.join(probs)}" if probs else ""))


SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"postgres(ql)?(\+psycopg2)?://[^:\s]+:(?!CHANGE_ME|password|pw@|iip_test_pw)[^@\s]{6,}@(?!127\.0\.0\.1|localhost)"), "database URL with password"),
    (re.compile(r"(?i)(api[_-]?key|secret[_-]?key|aws_secret_access_key)\s*[:=]\s*['\"][A-Za-z0-9/+]{20,}['\"]"), "API key"),
    (re.compile(r"sk_live_[0-9a-zA-Z]{10,}"), "payment provider live key"),
]


def check_secrets():
    r, _ = run(["git", "ls-files"], cwd=ROOT)
    files = r.stdout.split() if r.returncode == 0 else []
    if not files:
        for dp, dn, fn in os.walk(ROOT):
            dn[:] = [d for d in dn if d not in (".git", "venv", "__pycache__", "node_modules")]
            files += [os.path.relpath(os.path.join(dp, f), ROOT) for f in fn]
    hits = []
    for f in files:
        if f.endswith((".png", ".pdf", ".zip", ".db", ".xlsx", ".min.js")) or "/results/" in f:
            continue
        try:
            text = open(os.path.join(ROOT, f), encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for rx, label in SECRET_PATTERNS:
            if rx.search(text):
                hits.append(f"{f}: {label}")
    if os.path.exists(os.path.join(BACKEND, ".env")) and "backend/.env" in files:
        hits.append("backend/.env is tracked")
    record("2 secret scan", not hits, "; ".join(hits[:5]) or f"{len(files)} files scanned")


def check_lint():
    r, _ = run([PY, "-m", "ruff", "check", "--output-format", "concise", "."])
    record("3 lint (ruff)", r.returncode == 0, (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr) else "")


def check_migrations():
    tmp = tempfile.mkdtemp(prefix="iip_gate_")
    env = {"DATABASE_URL": f"sqlite:///{os.path.join(tmp, 'gate.db')}"}
    r1, _ = run([PY, "-m", "alembic", "upgrade", "head"], env=env)
    r2, _ = run([PY, "-m", "alembic", "check"], env=env)
    ok = r1.returncode == 0 and r2.returncode == 0
    lines = [ln for ln in (r2.stdout + r2.stderr).strip().splitlines() if "INFO" not in ln]
    record("4 migrations match models", ok, lines[-1] if lines else "no differences")


def check_frontend():
    r, _ = run([PY, os.path.join(ROOT, "frontend", "build.py"), "--check"], cwd=ROOT)
    record("5 frontend build check", r.returncode == 0, r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:])


def check_tests(quick):
    cmd = [PY, "-m", "pytest", "-q", "-p", "no:warnings", "tests"]
    if quick:
        cmd += ["-m", "not slow"]
    r, dt = run(cmd, timeout=7200)
    tail = [ln for ln in r.stdout.strip().splitlines() if re.search(r"\d+ (passed|failed)", ln)]
    record("6 test suite" + (" (quick)" if quick else ""), r.returncode == 0,
           (tail[-1] if tail else r.stdout[-300:]) + f" [{dt:.0f}s]"
           + ("" if os.getenv("IIP_TEST_PG_URL") else "; PostgreSQL migration test skipped (IIP_TEST_PG_URL unset)"))
    if r.returncode != 0:
        print(r.stdout[-4000:])


def check_claims():
    """Every numerical claim of the DSS manuscript re-computed from the stored
    research results (the research engine is unchanged in 2.5)."""
    script = os.path.join(BACKEND, "experiments", "verification", "verify_claims.py")
    base = os.path.join(BACKEND, "experiments", "results", "eidsf")
    runs = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))) if os.path.isdir(base) else []
    if not (os.path.exists(script) and runs):
        record("7 research claims", False, "verification script or stored results missing")
        return
    tmp = tempfile.mkdtemp(prefix="iip_claims_")
    r, _ = run([PY, script, os.path.join(base, runs[-1])], cwd=tmp)
    out = r.stdout + r.stderr
    m = re.search(r"verified\s+(\d+)", out)
    ok = r.returncode == 0 and m is not None and "CONTRADICTED" not in out
    record("7 research claims", ok, f"{m.group(1) if m else 0} claims verified against results/{runs[-1]}"
           + ("" if ok else "; " + out.strip().splitlines()[-1]))


def check_audit(allow_offline):
    r, _ = run([PY, "-m", "pip_audit", "-r", "requirements.txt", "--progress-spinner", "off"], timeout=900)
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0:
        record("8 dependency audit", True, out.splitlines()[-1] if out else "no known vulnerabilities")
    elif re.search(r"(ConnectionError|Max retries|Temporary failure|Name or service|timed out)", out):
        record("8 dependency audit", "NOT RUN" if allow_offline else False, "package index unreachable")
    else:
        record("8 dependency audit", False, " | ".join(out.splitlines()[-6:]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--allow-offline", action="store_true")
    a = ap.parse_args()
    for fn in (check_versions, check_secrets, check_lint, check_migrations, check_frontend):
        fn()
    check_tests(a.quick)
    check_claims()
    check_audit(a.allow_offline)
    print("\nRELEASE GATE")
    for name, status, detail in results:
        print(f"  [{status:7}] {name}: {detail}")
    failed = [r for r in results if r[1] == "FAIL"]
    print("\nRESULT:", "PASS" if not failed else f"FAIL ({len(failed)} check(s))")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
