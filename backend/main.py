"""
main.py
--------
Inventory Intelligence Platform (IIP) - FastAPI entry point.

Research basis: 'Artificial Bee Colony Optimization for FMCG Supply Chains'
and the Explainable Inventory Decision Support Framework (EIDSF).

Endpoints of note
  GET /api/health   liveness (process is up; no database access)
  GET /api/ready    readiness (database reachable and schema at migration head)
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.settings import settings
from core import observability
from database.db import init_db, get_db, SessionLocal, current_revision, head_revision
from api.auth import get_current_user
from api import (
    upload, dashboard, recommendations, export, auth, update,
    organizations, skus, warehouses, suppliers, forecasting,
    optimization, scenarios, procurement, decisions, research, eidsf_api, platform,
    account, billing, mfa,
)

APP_VERSION = "2.7.0"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))
MAX_JSON_BODY = 2 * 1024 * 1024  # non-upload request bodies

log = logging.getLogger("iip.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    if not settings.is_production:
        db = SessionLocal()
        try:
            auth.seed_admin_user(db)
        finally:
            db.close()
    log.info("IIP %s started (env=%s, demo_mode=%s)", APP_VERSION, settings.env, settings.demo_mode)
    yield
    log.info("IIP %s shutting down", APP_VERSION)


app = FastAPI(
    title="Inventory Intelligence Platform API (IIP)",
    description="Explainable inventory decision support for FMCG distributors "
                "(research prototype; see docs/KNOWN_LIMITATIONS.md).",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

@app.middleware("http")
async def _body_limit(request: Request, call_next):
    """Reject oversized non-upload bodies early (uploads have their own limit)."""
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and not request.url.path.startswith("/api/upload"):
        if int(cl) > MAX_JSON_BODY:
            return JSONResponse({"detail": "Request body too large."}, status_code=413)
    return await call_next(request)


# Installed after _body_limit so it wraps it (request id + security headers on every response).
observability.install(app)

# Bearer tokens are sent in the Authorization header (no cookies), so CSRF does
# not apply and credentials are not needed for CORS; origins: IIP_CORS_ORIGINS.
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After", "Content-Disposition"],
    )


# ---------------------------------------------------------------------------
# API routers
# ---------------------------------------------------------------------------
for _r in (auth, account, mfa, billing, platform, organizations, skus, warehouses, suppliers, forecasting, optimization,
           scenarios, procurement, decisions, upload, dashboard, recommendations, export,
           update, research, eidsf_api):
    app.include_router(_r.router)


@app.get("/api/batches", tags=["compatibility"])
def list_batches_compat(
    user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Backward-compatible alias for /api/dashboard/batches."""
    from api.dashboard import list_batches as _list_batches
    return _list_batches(user=user, db=db)


@app.get("/api/health", tags=["system"])
def health_check():
    """Liveness: the process is running. No database access, no tenant data."""
    return {
        "status": "ok",
        "service": "Inventory Intelligence Platform (IIP)",
        "version": APP_VERSION,
        "environment": settings.env,
        "demo_mode": settings.demo_mode,
    }


@app.get("/api/metrics", tags=["system"], include_in_schema=False)
def metrics_endpoint(request: Request):
    """Prometheus metrics (per process). Requires `Authorization: Bearer $IIP_METRICS_TOKEN`;
    404 when IIP_METRICS_TOKEN is not set."""
    import hmac
    from fastapi.responses import PlainTextResponse
    from core.metrics import metrics
    expected = os.getenv("IIP_METRICS_TOKEN") or ""
    if not expected:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    got = request.headers.get("authorization", "")
    if not hmac.compare_digest(got.encode(), f"Bearer {expected}".encode()):
        return JSONResponse({"detail": "Authentication required."}, status_code=401)
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")


@app.get("/api/ready", tags=["system"])
def readiness():
    """Readiness: database reachable and schema at the migration head."""
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        rev, head = current_revision(), head_revision()
    except Exception:
        log.exception("readiness check failed")
        return JSONResponse({"status": "unavailable", "database": "unreachable"}, status_code=503)
    ok = rev == head
    return JSONResponse({"status": "ready" if ok else "migration_required",
                         "database": "ok", "schema_revision": rev, "schema_head": head},
                        status_code=200 if ok else 503)


# ---------------------------------------------------------------------------
# Static frontend hosting (same origin as the API)
# ---------------------------------------------------------------------------
if os.path.isdir(FRONTEND_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIR, "assets")), name="assets")
    app.mount("/pages", StaticFiles(directory=os.path.join(FRONTEND_DIR, "pages")), name="pages")

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    def serve_index():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
