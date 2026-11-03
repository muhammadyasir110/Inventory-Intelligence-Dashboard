"""
core/ratelimit.py
------------------
Request throttling and bounded concurrency for expensive operations.

* ``RateLimiter``: sliding-window counter kept in process memory. It protects
  a single application process. When several processes or instances serve
  traffic, each enforces its own window, so the effective limit is
  ``limit x instances``; put a shared limiter (reverse proxy / Redis) in front
  for strict global limits (documented in docs/SECURITY.md).
* Account lockout after repeated failed logins is stored in the database
  (users.failed_login_count / locked_until), so it holds across instances.
* ``heavy_slot``: a semaphore that bounds simultaneous optimisation /
  simulation / import computations per process; when all slots are busy the
  request is rejected with 503 + Retry-After instead of queueing unboundedly.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager

from fastapi import HTTPException, Request

from core.settings import settings


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple, deque] = {}
        self._lock = threading.Lock()

    def hit(self, scope: str, key: str, limit: int, window_s: float = 60.0) -> None:
        now = time.monotonic()
        k = (scope, key)
        with self._lock:
            dq = self._hits.setdefault(k, deque())
            while dq and now - dq[0] > window_s:
                dq.popleft()
            if len(dq) >= limit:
                retry = max(1, int(window_s - (now - dq[0])) + 1)
                raise HTTPException(status_code=429,
                                    detail="Too many requests. Please wait and try again.",
                                    headers={"Retry-After": str(retry)})
            dq.append(now)
            if len(self._hits) > 50_000:  # bound memory: drop idle keys
                for kk in [kk for kk, v in self._hits.items() if not v or now - v[-1] > window_s]:
                    self._hits.pop(kk, None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def limit_login(request: Request) -> None:
    limiter.hit("login", client_ip(request), settings.rate_login_per_min)


def limit_heavy(org_id: int) -> None:
    limiter.hit("heavy", str(org_id), settings.rate_heavy_per_min)


_heavy_sem = threading.BoundedSemaphore(settings.max_concurrent_heavy)


@contextmanager
def heavy_slot(kind: str = "computation"):
    """Bound concurrent expensive computations in this process; runtime and
    failures are recorded in core.metrics (iip_optimisation_*)."""
    from core.metrics import metrics
    if not _heavy_sem.acquire(timeout=5):
        metrics.inc("iip_optimisation_failures_total", kind=kind, reason="busy")
        raise HTTPException(status_code=503,
                            detail="The optimisation engine is busy. Please retry shortly.",
                            headers={"Retry-After": "10"})
    start = time.perf_counter()
    try:
        yield
    except Exception:
        metrics.inc("iip_optimisation_failures_total", kind=kind, reason="error")
        raise
    finally:
        metrics.observe("iip_optimisation_seconds", time.perf_counter() - start, kind=kind)
        _heavy_sem.release()
