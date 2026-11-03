"""
scripts/smoke_test.py
----------------------
Post-deployment smoke test against a RUNNING instance (staging or production).
Uses only the standard library. It creates no data unless --write is given.

  python scripts/smoke_test.py --base https://staging.example.pk --user owner
  python scripts/smoke_test.py --base http://127.0.0.1:8000 --user owner --write --sample ../sample_data/sample_inventory.csv

The password is read from the IIP_SMOKE_PASSWORD environment variable or
prompted (never pass it on the command line). Exit code 0 = all checks passed.

Read-only checks: health, readiness (schema at head), security headers, login
page without demo credentials (staging/production), login, /me, dashboard
batches, SKU list, procurement plan, purchase orders, forecasting overview,
subscription, billing status and e-mail status (owner/admin users), metrics
(only if IIP_SMOKE_METRICS_TOKEN is set), logout (token revoked).
--write adds: upload preview of the sample file (nothing saved) and, only with
--commit-import, an actual import labelled provenance=demonstration.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

RESULTS = []


def call(base, method, path, token=None, body=None, raw=None, ctype=None, timeout=60):
    headers = {"X-Request-ID": f"smoke-{uuid.uuid4().hex[:12]}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if raw is not None:
        data, headers["Content-Type"] = raw, ctype
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method, headers=headers)
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
            return r.status, dict(r.headers), payload, time.perf_counter() - t
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read(), time.perf_counter() - t


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")


def multipart(field, filename, content):
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: text/csv\r\n\r\n").encode() + content + f"\r\n--{b}--\r\n".encode()
    return body, f"multipart/form-data; boundary={b}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--commit-import", action="store_true")
    ap.add_argument("--sample")
    a = ap.parse_args()
    pw = os.getenv("IIP_SMOKE_PASSWORD") or getpass.getpass(f"Password for {a.user}: ")
    print(f"Smoke test against {a.base}")

    s, h, b, dt = call(a.base, "GET", "/api/health")
    check("health", s == 200, f"{s} {dt*1000:.0f} ms")
    s, h, b, dt = call(a.base, "GET", "/api/ready")
    check("ready (schema at head)", s == 200, b.decode()[:120])
    hl = {k.lower(): v for k, v in h.items()}
    check("security headers", all(k in hl for k in ("x-content-type-options", "x-frame-options",
                                                    "content-security-policy", "x-request-id")))
    if a.base.startswith("https://"):
        check("HSTS", "strict-transport-security" in hl)
    s, h, b, dt = call(a.base, "GET", "/")
    health = json.loads(call(a.base, "GET", "/api/health")[2] or b"{}")
    if health.get("environment") in ("staging", "production"):
        check("login page has no demo credentials", s == 200 and b"admin123" not in b, f"{s}")
    s, h, b, dt = call(a.base, "POST", "/api/auth/login", body={"username": a.user, "password": pw})
    check("login", s == 200, f"{s}")
    if s != 200:
        return 1
    token = json.loads(b)["token"]
    role = json.loads(b).get("role")
    for name, path in (("me", "/api/auth/me"), ("batches", "/api/dashboard/batches"),
                       ("skus", "/api/skus?page_size=5"), ("procurement plan", "/api/procurement/plan"),
                       ("purchase orders", "/api/procurement/orders"),
                       ("forecasting overview", "/api/forecasting/overview"),
                       ("subscription", "/api/org/subscription")):
        s, h, b, dt = call(a.base, "GET", path, token)
        check(name, s == 200, f"{s} {dt*1000:.0f} ms")
    if role in ("owner", "admin"):
        for name, path in (("billing status", "/api/billing/status"), ("e-mail status", "/api/auth/email-status")):
            s, h, b, dt = call(a.base, "GET", path, token)
            check(name, s == 200, f"{s} {b[:100].decode(errors='replace')}")
    mt = os.getenv("IIP_SMOKE_METRICS_TOKEN")
    if mt:
        req = urllib.request.Request(a.base.rstrip("/") + "/api/metrics", headers={"Authorization": f"Bearer {mt}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                ok = r.status == 200 and b"iip_http_requests_total" in r.read()
        except urllib.error.HTTPError as e:
            ok = False
            print("   metrics:", e.code)
        check("metrics (token)", ok)
    if a.write and a.sample:
        with open(a.sample, "rb") as fh:
            content = fh.read()
        body, ctype = multipart("file", os.path.basename(a.sample), content)
        s, h, b, dt = call(a.base, "POST", "/api/upload/preview", token, raw=body, ctype=ctype)
        check("upload preview (nothing saved)", s == 200 and json.loads(b)["report"]["is_valid"], f"{s}")
        if a.commit_import:
            b2 = uuid.uuid4().hex
            body = (f"--{b2}\r\nContent-Disposition: form-data; name=\"provenance\"\r\n\r\ndemonstration\r\n"
                    f"--{b2}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"smoke.csv\"\r\n"
                    f"Content-Type: text/csv\r\n\r\n").encode() + content + f"\r\n--{b2}--\r\n".encode()
            s, h, b, dt = call(a.base, "POST", "/api/upload", token, raw=body,
                               ctype=f"multipart/form-data; boundary={b2}")
            check("import (provenance=demonstration)", s == 200, f"{s} {b[:120]!r}")
            s, h, b, dt = call(a.base, "GET", "/api/dashboard/latest/summary", token)
            check("dashboard summary", s == 200, f"{s} {dt*1000:.0f} ms")
    s, h, b, dt = call(a.base, "POST", "/api/auth/logout", token)
    s2, *_ = call(a.base, "GET", "/api/auth/me", token)
    check("logout revokes token", s == 200 and s2 == 401, f"{s}/{s2}")
    failed = [r for r in RESULTS if not r[1]]
    print("SMOKE RESULT:", "PASS" if not failed else f"FAIL ({len(failed)})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
