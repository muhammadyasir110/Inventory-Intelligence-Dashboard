"""
main.py
--------
Inventory Intelligence Platform (IIP) — Enterprise FastAPI entry point.
Grounds inventory management in the MPhil research:
'Explainable Inventory Decision Support through Computational Intelligence'
"""

import os
from core.settings import settings
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from sqlalchemy.orm import Session
from database.db import init_db, get_db
from api.auth import get_current_user
from api import (
    upload, dashboard, recommendations, export, auth, update,
    organizations, skus, warehouses, suppliers, forecasting,
    optimization, scenarios, procurement, decisions, research, eidsf_api
)

APP_VERSION = "2.4.0"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))

app = FastAPI(
    title="Inventory Intelligence Platform API (IIP)",
    description="Enterprise Decision Support, Multi-Item Optimization, and Explainable AI Inventory Platform.",
    version=APP_VERSION,
)

# Bearer tokens are sent in the Authorization header (no cookies), so
# credentials are not needed for CORS; origins come from IIP_CORS_ORIGINS.
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )


@app.on_event("startup")
def on_startup():
    init_db()
    from database.db import SessionLocal
    db = SessionLocal()
    try:
        auth.seed_admin_user(db)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API routers
# ---------------------------------------------------------------------------
app.include_router(auth.router)
app.include_router(organizations.router)
app.include_router(skus.router)
app.include_router(warehouses.router)
app.include_router(suppliers.router)
app.include_router(forecasting.router)
app.include_router(optimization.router)
app.include_router(scenarios.router)
app.include_router(procurement.router)
app.include_router(decisions.router)
app.include_router(upload.router)
app.include_router(dashboard.router)
app.include_router(recommendations.router)
app.include_router(export.router)
app.include_router(update.router)
app.include_router(research.router)
app.include_router(eidsf_api.router)

@app.get("/api/batches", tags=["compatibility"])
def list_batches_compat(
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Backward-compatible alias for /api/dashboard/batches."""
    from api.dashboard import list_batches as _list_batches
    # Re-inject dependencies
    return _list_batches(user=user, db=db)


@app.get("/api/health", tags=["system"])
def health_check():
    return {
        "status": "ok",
        "service": "Inventory Intelligence Platform (IIP)",
        "version": APP_VERSION,
        "environment": settings.env,
        "demo_mode": settings.demo_mode,
        "capabilities": [
            "multi_tenancy",
            "rbac_authentication",
            "stochastic_eoq_safety_stock",
            "multi_item_constrained_optimization",
            "time_series_forecasting",
            "explainable_ai_dss_pipeline",
            "scenario_simulation",
            "procurement_planning",
            "fefo_expiry_tracking",
            "decision_auditing"
        ]
    }


# ---------------------------------------------------------------------------
# Static frontend hosting (single-process dev/demo convenience)
# ---------------------------------------------------------------------------
if os.path.isdir(FRONTEND_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIR, "assets")), name="assets")
    app.mount("/pages", StaticFiles(directory=os.path.join(FRONTEND_DIR, "pages")), name="pages")

    @app.get("/", include_in_schema=False)
    def serve_index():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))