"""
core/metrics.py
----------------
Minimal in-process metrics in the Prometheus text format (no dependency).

Exposed at GET /api/metrics, protected by a bearer token (IIP_METRICS_TOKEN);
the endpoint returns 404 when no token is configured. Values are PER PROCESS:
with several workers or instances, scrape each one (or aggregate in the
monitoring system). No tenant data, user names or identifiers are recorded -
labels are limited to route templates, methods, status classes and outcomes.

Series
  iip_http_requests_total{method,route,status}        counter
  iip_http_request_duration_seconds{method,route}     histogram
  iip_optimisation_seconds{kind}                      histogram (optimisation / EIDSF runs)
  iip_optimisation_failures_total{kind}               counter
  iip_billing_webhooks_total{provider,outcome}        counter (incl. signature_invalid)
  iip_emails_total{status}                            counter
  iip_process_start_time_seconds                      gauge
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict

BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)


def _key(labels: dict) -> tuple:
    return tuple(sorted((k, str(v)) for k, v in labels.items()))


def _fmt(labels: tuple) -> str:
    if not labels:
        return ""
    inner = ",".join('%s="%s"' % (k, v.replace("\\", "\\\\").replace('"', '\\"')) for k, v in labels)
    return "{" + inner + "}"


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.counters: dict = defaultdict(lambda: defaultdict(float))
        self.hists: dict = defaultdict(dict)
        self.started = time.time()

    def inc(self, name: str, value: float = 1.0, **labels) -> None:
        with self._lock:
            self.counters[name][_key(labels)] += value

    def observe(self, name: str, seconds: float, **labels) -> None:
        k = _key(labels)
        with self._lock:
            h = self.hists[name].setdefault(k, {"buckets": [0] * len(BUCKETS), "sum": 0.0, "count": 0})
            for i, b in enumerate(BUCKETS):
                if seconds <= b:
                    h["buckets"][i] += 1
            h["sum"] += seconds
            h["count"] += 1

    def value(self, name: str, **labels) -> float:
        return self.counters.get(name, {}).get(_key(labels), 0.0)

    def render(self) -> str:
        out = ["# TYPE iip_process_start_time_seconds gauge",
               f"iip_process_start_time_seconds {self.started:.0f}"]
        with self._lock:
            for name, series in sorted(self.counters.items()):
                out.append(f"# TYPE {name} counter")
                for k, v in sorted(series.items()):
                    out.append(f"{name}{_fmt(k)} {v:g}")
            for name, series in sorted(self.hists.items()):
                out.append(f"# TYPE {name} histogram")
                for k, h in sorted(series.items()):
                    for b, c in zip(BUCKETS, h["buckets"]):
                        out.append(f"{name}_bucket{_fmt(k + (('le', str(b)),))} {c}")
                    out.append(f"{name}_bucket{_fmt(k + (('le', '+Inf'),))} {h['count']}")
                    out.append(f"{name}_sum{_fmt(k)} {h['sum']:.6f}")
                    out.append(f"{name}_count{_fmt(k)} {h['count']}")
        return "\n".join(out) + "\n"


metrics = Metrics()
