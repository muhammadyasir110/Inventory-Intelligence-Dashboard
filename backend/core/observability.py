"""
core/observability.py
----------------------
Structured logging, request correlation IDs, security headers and centralised
error handling.

* Every response carries ``X-Request-ID`` (a client-supplied value is accepted
  only if it is a short token of safe characters; otherwise a new one is made).
* Access log: one line per request with method, route path, status, duration
  and request id. Query strings, headers and bodies are NOT logged (they may
  carry tokens or business data).
* Unhandled exceptions return ``{"detail": "Internal server error",
  "request_id": ...}`` with status 500 - never a stack trace. The traceback is
  logged server-side with the request id.
* Error monitoring: set IIP_ERROR_HOOK to a dotted path ``module:function``;
  it is called as ``fn(exc, request_id, path)`` for unhandled exceptions
  (e.g. a thin wrapper around Sentry's ``capture_exception``). No third-party
  service is contacted unless you configure one.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import re
import sys
import time
import uuid
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.settings import settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
_RID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
access_log = logging.getLogger("iip.access")
error_log = logging.getLogger("iip.error")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", request_id_var.get()),
        }
        for k in ("method", "path", "status", "duration_ms", "org_id"):
            if hasattr(record, k):
                out[k] = getattr(record, k)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return True


def configure_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_iip_configured", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"))
    handler.addFilter(_RequestIdFilter())
    root.addHandler(handler)
    root.setLevel(os.getenv("IIP_LOG_LEVEL", "INFO").upper())
    root._iip_configured = True  # type: ignore[attr-defined]


def _error_hook():
    spec = os.getenv("IIP_ERROR_HOOK")
    if not spec or ":" not in spec:
        return None
    mod, fn = spec.split(":", 1)
    try:
        return getattr(importlib.import_module(mod), fn)
    except Exception:  # pragma: no cover - misconfiguration is logged once
        error_log.exception("IIP_ERROR_HOOK %s could not be loaded", spec)
        return None


_HOOK = None

# Content-Security-Policy for the bundled vanilla-JS frontend: everything is
# served from the same origin; inline styles are used by the templates.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' "
       "https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; "
       "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'self'; form-action 'self'; object-src 'none'")


def install(app: FastAPI) -> None:
    global _HOOK
    configure_logging()
    _HOOK = _error_hook()

    @app.middleware("http")
    async def _request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id", "")
        if not _RID_RE.match(rid):
            rid = uuid.uuid4().hex
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception as exc:  # unhandled -> generic 500
            error_log.exception("unhandled error on %s %s", request.method, request.url.path)
            if _HOOK:
                try:
                    _HOOK(exc, rid, request.url.path)
                except Exception:  # pragma: no cover
                    error_log.exception("error hook failed")
            response = JSONResponse({"detail": "Internal server error", "request_id": rid},
                                    status_code=500)
        finally:
            request_id_var.reset(token)
        dur = round((time.perf_counter() - start) * 1000, 1)
        response.headers["X-Request-ID"] = rid
        _security_headers(request, response)
        try:
            from core.metrics import metrics
            route = request.scope.get("route")
            tmpl = getattr(route, "path", None) or ("static" if not request.url.path.startswith("/api/")
                                                     else "unmatched")
            metrics.inc("iip_http_requests_total", method=request.method, route=tmpl, status=status)
            metrics.observe("iip_http_request_duration_seconds", dur / 1000.0,
                            method=request.method, route=tmpl)
        except Exception:  # metrics must never break a request
            pass
        access_log.info("%s %s %s %.1fms", request.method, request.url.path, status, dur,
                        extra={"request_id": rid, "method": request.method,
                               "path": request.url.path, "status": status, "duration_ms": dur})
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # Pydantic error list without echoing submitted values (may hold secrets).
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")}
                  for e in exc.errors()]
        return JSONResponse({"detail": errors, "request_id": request_id_var.get()},
                            status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        body = {"detail": exc.detail, "request_id": request_id_var.get()}
        return JSONResponse(body, status_code=exc.status_code, headers=getattr(exc, "headers", None))


def _security_headers(request: Request, response) -> None:
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if not request.url.path.startswith(("/docs", "/redoc")):
        h.setdefault("Content-Security-Policy", CSP)
    if request.url.path.startswith("/api/"):
        h.setdefault("Cache-Control", "no-store")
    if settings.is_production:
        # HTTPS is terminated at the edge (see docs/DEPLOYMENT.md); HSTS tells
        # browsers to keep using it.
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
