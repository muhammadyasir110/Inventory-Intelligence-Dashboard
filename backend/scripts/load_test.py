"""
scripts/load_test.py
---------------------
Simple concurrent load test against a RUNNING instance (standard library only).
Read-only by default: each virtual user signs in once and then repeats the
typical planner requests (dashboard summary, SKU list, forecasting overview,
procurement plan, warehouses). With --heavy, every 10th request is an
optimisation run (it counts against the plan's monthly optimisation limit, so
use a test organisation).

  IIP_LOAD_PASSWORD=... python scripts/load_test.py --base http://127.0.0.1:8000 --user owner \\
        --users 20 --duration 60

Each virtual user signs in separately (several sessions of the same account).
Reports requests, errors, throughput and latency percentiles per endpoint.
Exit code 1 if the error rate exceeds --max-error-rate (default 1%).
Results describe THIS deployment and machine only; they are not a capacity
guarantee for another environment.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict

READS = ["/api/dashboard/latest/summary", "/api/skus?page_size=50", "/api/forecasting/overview",
         "/api/procurement/plan", "/api/warehouses", "/api/auth/me"]


def call(base, method, path, token=None, body=None, timeout=120):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method, headers=headers)
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
            return r.status, payload, time.perf_counter() - t
    except urllib.error.HTTPError as e:
        return e.code, e.read(), time.perf_counter() - t
    except Exception as e:  # connection errors count as failures
        return 0, str(e).encode(), time.perf_counter() - t


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round(q / 100 * (len(xs) - 1)))))
    return xs[k]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--users", type=int, default=10)
    ap.add_argument("--duration", type=int, default=60, help="seconds")
    ap.add_argument("--heavy", action="store_true")
    ap.add_argument("--max-error-rate", type=float, default=0.01)
    a = ap.parse_args(argv)
    pw = os.getenv("IIP_LOAD_PASSWORD")
    if not pw:
        print("Set IIP_LOAD_PASSWORD.")
        return 2
    lat = defaultdict(list)
    codes = defaultdict(lambda: defaultdict(int))
    lock = threading.Lock()
    stop = time.time() + a.duration

    def worker(n):
        s, b, _ = call(a.base, "POST", "/api/auth/login", body={"username": a.user, "password": pw})
        if s != 200:
            with lock:
                codes["login"][s] += 1
            return
        tok = json.loads(b)["token"]
        i = n
        while time.time() < stop:
            if a.heavy and i % 10 == 9:
                path, method, body = "/api/optimization/run", "POST", {"algorithm": "abc", "seed": i}
            else:
                path, method, body = READS[i % len(READS)], "GET", None
            s, _, dt = call(a.base, method, path, tok, body)
            key = path.split("?")[0]
            with lock:
                lat[key].append(dt)
                codes[key][s] += 1
            i += 1
        call(a.base, "POST", "/api/auth/logout", tok)

    print(f"Load test: {a.users} virtual users for {a.duration}s against {a.base}"
          f"{' (with optimisation runs)' if a.heavy else ' (read-only)'}")
    t0 = time.time()
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(a.users)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.time() - t0
    total = sum(sum(c.values()) for c in codes.values())
    errors = sum(n for c in codes.values() for s, n in c.items() if s < 200 or s >= 400)
    alll = [x for v in lat.values() for x in v]
    print(f"\n{'endpoint':40s} {'n':>6s} {'p50 ms':>8s} {'p95 ms':>8s} {'max ms':>8s}  status codes")
    for k in sorted(lat):
        v = lat[k]
        print(f"{k:40s} {len(v):6d} {pct(v,50)*1000:8.0f} {pct(v,95)*1000:8.0f} {max(v)*1000:8.0f}  {dict(codes[k])}")
    if "login" in codes:
        print(f"{'login failures':40s} {dict(codes['login'])}")
    rate = errors / total if total else 1.0
    print(f"\nrequests {total} in {wall:.1f}s = {total / wall:.1f}/s | errors {errors} ({rate:.2%}) | "
          f"latency p50 {pct(alll,50)*1000:.0f} ms, p95 {pct(alll,95)*1000:.0f} ms, "
          f"mean {statistics.mean(alll)*1000:.0f} ms" if alll else "no successful requests")
    ok = rate <= a.max_error_rate
    print("LOAD RESULT:", "PASS" if ok else f"FAIL (error rate above {a.max_error_rate:.0%})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
