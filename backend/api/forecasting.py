"""
api/forecasting.py
-------------------
Demand Forecasting API.
Generates statistical time-series projections (SMA, WMA, Exponential Smoothing)
with error metric scorecards (MAE, RMSE, MAPE) and confidence intervals.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional, List
from sqlalchemy.orm import Session
from datetime import date
import math

from database.db import get_db
from models.models import User, InventoryItem, DemandHistory, Forecast
from api.auth import get_current_user
from analytics.forecasting_engine import (
    simple_moving_average, weighted_moving_average, exponential_smoothing,
    holt_linear_smoothing, generate_item_demand_series, holdout_evaluation,
    holt_path, holdout_model_comparison
)

from api.crud import active_items_for_batch

router = APIRouter(prefix="/api/forecasting", tags=["forecasting"])

MIN_RECORDED_POINTS = 14


def _history(db: Session, it, days: int):
    """
    Recorded demand history if the item has at least MIN_RECORDED_POINTS
    observations in demand_history; otherwise an ILLUSTRATIVE synthetic
    series (clearly labelled -- its accuracy metrics are not evidence).
    """
    rows = (db.query(DemandHistory)
              .filter(DemandHistory.item_id == it.id)
              .order_by(DemandHistory.period_date.desc()).limit(days).all())
    if len(rows) >= MIN_RECORDED_POINTS:
        rows = list(reversed(rows))
        return ([{"date": r.period_date.isoformat(), "quantity": float(r.quantity_demanded)} for r in rows],
                "recorded")
    daily_d = float(it.daily_avg_demand or 1.0)
    return generate_item_demand_series(daily_d, it.demand_std_dev, days=days), "synthetic_illustrative"


@router.get("/overview")
def get_forecasting_overview(
    batch_id: str = Query("latest"),
    horizon: int = Query(90, ge=7, le=180),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    items, _batch = active_items_for_batch(db, user.org_id, batch_id)
    empty_metrics = {"average_mape": None, "average_mae": None, "average_rmse": None}
    if not items:
        return {"total_items_forecasted": 0, "overall_metrics": empty_metrics,
                "network_forecast_30d_units": 0.0, "network_forecast_30d_value": 0.0,
                "top_demand_items": [], "sample_forecasts": [], "aggregate": {},
                "model_comparison": {}}

    HIST = 37
    total_units_30d = 0.0
    total_val_30d = 0.0
    hold_metrics = []
    model_acc = {}
    item_summaries = []
    sources = {"recorded": 0, "synthetic_illustrative": 0}
    agg = [0.0] * HIST

    for it in items:
        daily_d = float(it.daily_avg_demand or 1.0)
        cost = float(it.unit_cost or 0.0)
        history, source = _history(db, it, HIST)
        sources[source] += 1
        series = [h["quantity"] for h in history]
        for k, v in enumerate(series[-HIST:]):
            agg[HIST - len(series[-HIST:]) + k] += v

        path = holt_path(series, horizon=90)
        f_7 = sum(p["predicted"] for p in path[:7])
        f_30 = sum(p["predicted"] for p in path[:30])
        f_90 = sum(p["predicted"] for p in path[:90])
        hold = holdout_evaluation(series, holdout=7)
        if hold["mape"] is not None:
            hold_metrics.append(hold)
        for m, met in holdout_model_comparison(series).items():
            model_acc.setdefault(m, []).append(met)

        mean = sum(series) / len(series) if series else 0.0
        sd = math.sqrt(sum((x - mean) ** 2 for x in series) / max(len(series) - 1, 1)) if series else 0.0
        total_units_30d += f_30
        total_val_30d += f_30 * cost
        item_summaries.append({
            "id": it.id, "sku": it.sku, "product_name": it.product_name,
            "category": it.category, "daily_avg_demand": daily_d,
            "forecast_7d": round(f_7, 1), "forecast_30d": round(f_30, 1),
            "forecast_90d": round(f_90, 1), "forecast_30d_value": round(f_30 * cost, 2),
            "mape": hold["mape"], "cv": round(sd / mean, 3) if mean > 0 else None,
            "daily_sd": round(sd, 2), "history_source": source,
            "model": "Holt Linear Exponential Smoothing",
        })

    def avg(key, rows):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    top = sorted(item_summaries, key=lambda x: -x["forecast_30d_value"])
    return {
        "total_items_forecasted": len(items),
        "overall_metrics": {
            "model_name": "Holt Linear Exponential Smoothing",
            "average_mape": avg("mape", hold_metrics),
            "average_mae": avg("mae", hold_metrics),
            "average_rmse": avg("rmse", hold_metrics),
            "evaluation": "out-of-sample 7-day holdout",
            "history_sources": sources,
            "illustrative_only": sources["recorded"] == 0,
        },
        "network_forecast_30d_units": round(total_units_30d, 1),
        "network_forecast_30d_value": round(total_val_30d, 2),
        "top_demand_items": top[:10],
        "sample_forecasts": top[:50],
        "volatility_watchlist": sorted([i for i in item_summaries if i["cv"] is not None],
                                       key=lambda x: -x["cv"])[:10],
        "aggregate": {"history": [round(v, 1) for v in agg], "forecast": holt_path(agg, horizon=horizon)},
        "model_comparison": {m: {"mape": avg("mape", v), "mae": avg("mae", v), "rmse": avg("rmse", v)}
                             for m, v in model_acc.items()},
    }


@router.get("/item/{item_id}")
def get_item_forecast(
    item_id: int,
    days_history: int = Query(30, ge=14, le=180),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    it = db.query(InventoryItem).filter(InventoryItem.id == item_id, InventoryItem.org_id == user.org_id).first()
    if not it:
        raise HTTPException(status_code=404, detail="SKU not found.")

    daily_d = float(it.daily_avg_demand or 1.0)
    history_records, source = _history(db, it, days_history)
    series = [r["quantity"] for r in history_records]

    sma_series = simple_moving_average(series, window=7)
    wma_series = weighted_moving_average(series, weights=[1.0, 2.0, 3.0])
    ses_series = exponential_smoothing(series, alpha=0.3)
    holt_res = holt_linear_smoothing(series, alpha=0.3, beta=0.1, horizons=[7, 30, 90])

    return {
        "item": {
            "id": it.id,
            "sku": it.sku,
            "product_name": it.product_name,
            "category": it.category,
            "unit_cost": it.unit_cost,
            "daily_avg_demand": daily_d
        },
        "history": history_records,
        "models": {
            "simple_moving_average": sma_series,
            "weighted_moving_average": wma_series,
            "single_exponential_smoothing": ses_series,
            "holt_fitted": holt_res["fitted"]
        },
        "projections": {
            "forecast_7d": holt_res["forecasts"]["7"],
            "forecast_30d": holt_res["forecasts"]["30"],
            "forecast_90d": holt_res["forecasts"]["90"],
            "confidence_intervals": holt_res["intervals"]
        },
        "history_source": source,
        "forecast_path": holt_path(series, horizon=90),
        "model_comparison": holdout_model_comparison(series),
        "accuracy_metrics": holdout_evaluation(series, holdout=7),
        "in_sample_fit_metrics": holt_res["metrics"]
    }