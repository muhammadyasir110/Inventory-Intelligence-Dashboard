"""
api/forecasting.py
-------------------
Demand forecasting API (SMA, WMA, SES, Holt) with out-of-sample accuracy.

Data policy (v2.5): forecasts are produced ONLY from recorded demand history
(table demand_history, imported via POST /api/upload/demand-history) with at
least MIN_RECORDED_POINTS daily observations. Items without enough history
are reported as `insufficient_history` and excluded from totals. A synthetic
illustration is produced only when the caller passes `illustrative=true`, and
every such result is labelled `synthetic_illustrative` (IIP <= 2.4 substituted
it automatically).

Units: demand in units/day; horizons in days; history is the most recent
recorded observations ordered by date (no future data is used for fitting).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
import math

from database.db import get_db
from models.models import User, InventoryItem, DemandHistory
from api.auth import get_current_user
from analytics.forecasting_engine import (
    build_daily_series,
    simple_moving_average, weighted_moving_average, exponential_smoothing,
    holt_linear_smoothing, generate_item_demand_series, holdout_model_comparison, rolling_origin_evaluation,
    select_model, forecast_path, holdout_selected
)

from api.crud import active_items_for_batch

router = APIRouter(prefix="/api/forecasting", tags=["forecasting"])

MIN_RECORDED_POINTS = 14


LOW_RELIABILITY_POINTS = 28   # fewer recorded points -> explicit low-reliability warning
MAX_FILLED_SHARE = 0.2        # more zero-filled calendar days than this -> low-reliability warning


def _calendar_info(cal: dict) -> dict:
    return {"observed_days": cal["observed_days"], "zero_filled_days": cal["filled_days"],
            "same_date_rows_merged": cal["duplicate_rows_merged"],
            "assumption": "dates without a record are treated as zero demand; same-date rows (channels) are summed"}


def _history(db: Session, it, days: int, illustrative: bool = False):
    """(history, source, calendar): recorded daily history if the item has at
    least MIN_RECORDED_POINTS recorded days in the window; otherwise
    ([], "insufficient_history", None), or a labelled synthetic illustration
    when `illustrative` is requested."""
    # Daily calendar: channels summed per date, gaps filled with 0 (reported).
    # (<= 2.5.0 used raw rows, so two channels counted as two days and gaps were ignored.)
    rows = (db.query(DemandHistory.period_date, DemandHistory.quantity_demanded)
              .filter(DemandHistory.item_id == it.id, DemandHistory.org_id == it.org_id)
              .order_by(DemandHistory.period_date.desc()).limit(max(days, 1) * 20).all())
    cal = build_daily_series([(d, q) for d, q in rows], last_n_days=days)
    if cal["observed_days"] >= MIN_RECORDED_POINTS:
        hist = [{"date": d, "quantity": v} for d, v in zip(cal["dates"], cal["values"])]
        return hist, "recorded", _calendar_info(cal)
    if illustrative:
        daily_d = float(it.daily_avg_demand or 0.0)
        return generate_item_demand_series(daily_d, it.demand_std_dev, days=days), "synthetic_illustrative", None
    return [], "insufficient_history", None


def _reliability(source: str, n: int, hold: dict, calendar: dict = None) -> dict:
    warnings = []
    if calendar and n and calendar["zero_filled_days"] / n > MAX_FILLED_SHARE:
        warnings.append(f"{calendar['zero_filled_days']} of {n} days had no record and were treated as zero demand.")
    if source == "synthetic_illustrative":
        warnings.append("Synthetic illustration - not based on your demand data. Do not use for decisions.")
    elif n < LOW_RELIABILITY_POINTS:
        warnings.append(f"Low reliability: only {n} recorded observations (at least {LOW_RELIABILITY_POINTS} recommended).")
    mase = (hold or {}).get("mase")
    if mase is not None and mase >= 1.0:
        warnings.append("The model does not beat the naive forecast on the holdout (MASE >= 1).")
    level = "illustrative" if source == "synthetic_illustrative" else ("low" if warnings else "normal")
    return {"level": level, "warnings": warnings}


@router.get("/overview")
def get_forecasting_overview(
    batch_id: str = Query("latest"),
    horizon: int = Query(90, ge=7, le=180),
    illustrative: bool = Query(False, description="include labelled synthetic illustrations "
                                                  "for items without recorded history"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    items, _batch = active_items_for_batch(db, user.org_id, batch_id)
    empty_metrics = {"average_mape": None, "average_mae": None, "average_rmse": None,
                     "average_wape": None, "history_sources": {}, "illustrative_only": False}
    if not items:
        return {"total_items_forecasted": 0, "overall_metrics": empty_metrics,
                "network_forecast_30d_units": 0.0, "network_forecast_30d_value": 0.0,
                "top_demand_items": [], "sample_forecasts": [], "aggregate": {},
                "model_comparison": {}, "insufficient_history": [], "units": "units/day"}

    HIST = 37
    total_units_30d = 0.0
    total_val_30d = 0.0
    hold_metrics = []
    model_acc = {}
    item_summaries = []
    sources = {"recorded": 0, "synthetic_illustrative": 0, "insufficient_history": 0}
    agg = [0.0] * HIST
    insufficient = []
    periods = []
    patterns = {}

    for it in items:
        daily_d = float(it.daily_avg_demand or 0.0)
        cost = float(it.unit_cost or 0.0)
        history, source, cal = _history(db, it, HIST, illustrative)
        sources[source] += 1
        if source == "insufficient_history":
            insufficient.append({"id": it.id, "sku": it.sku, "product_name": it.product_name})
            continue
        if source == "recorded":
            periods += [history[0]["date"], history[-1]["date"]]
        series = [h["quantity"] for h in history]
        for k, v in enumerate(series[-HIST:]):
            agg[HIST - len(series[-HIST:]) + k] += v

        sel = select_model(series)
        path = forecast_path(series, horizon=90)
        f_7 = sum(p["predicted"] for p in path[:7])
        f_30 = sum(p["predicted"] for p in path[:30])
        f_90 = sum(p["predicted"] for p in path[:90])
        hold = holdout_selected(series, holdout=7)
        patterns[sel["pattern"]] = patterns.get(sel["pattern"], 0) + 1
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
            "mape": hold["mape"], "wape": hold.get("wape"), "mase": hold.get("mase"),
            "cv": round(sd / mean, 3) if mean > 0 else None,
            "daily_sd": round(sd, 2), "history_source": source, "history_points": len(series),
            "reliability": _reliability(source, len(series), hold, cal),
            "calendar": cal,
            "model": sel["model"],
            "demand_pattern": sel["pattern"], "adi": sel["adi"], "cv2": sel["cv2"],
        })

    def avg(key, rows):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    top = sorted(item_summaries, key=lambda x: -x["forecast_30d_value"])
    recorded_only = [s_ for s_ in item_summaries if s_["history_source"] == "recorded"]
    return {
        "total_items_forecasted": len(item_summaries),
        "total_items": len(items),
        "units": "units/day (forecast totals in units; values in organisation currency)",
        "horizon_days": horizon,
        "data_period": {"from": min(periods), "to": max(periods)} if periods else None,
        "insufficient_history": insufficient,
        "min_history_points": MIN_RECORDED_POINTS,
        "overall_metrics": {
            "model_name": "Per SKU by demand pattern: Holt (smooth/erratic) or SBA (intermittent/lumpy)",
            "demand_patterns": patterns,
            "average_mape": avg("mape", hold_metrics),
            "average_mae": avg("mae", hold_metrics),
            "average_rmse": avg("rmse", hold_metrics),
            "average_wape": avg("wape", hold_metrics),
            "average_mase": avg("mase", hold_metrics),
            "evaluation": "out-of-sample 7-day holdout (MAPE omitted where demand has zeros)",
            "history_sources": sources,
            "illustrative_only": sources["recorded"] == 0 and sources["synthetic_illustrative"] > 0,
            "items_with_recorded_history": len(recorded_only),
        },
        "network_forecast_30d_units": round(total_units_30d, 1),
        "network_forecast_30d_value": round(total_val_30d, 2),
        "top_demand_items": top[:10],
        "sample_forecasts": top[:50],
        "volatility_watchlist": sorted([i for i in item_summaries if i["cv"] is not None],
                                       key=lambda x: -x["cv"])[:10],
        "aggregate": {"history": [round(v, 1) for v in agg], "forecast": forecast_path(agg, horizon=horizon)},
        "model_comparison": {m: {"mape": avg("mape", v), "mae": avg("mae", v), "rmse": avg("rmse", v),
                                 "wape": avg("wape", v), "mase": avg("mase", v)}
                             for m, v in model_acc.items()},
    }


@router.get("/item/{item_id}")
def get_item_forecast(
    item_id: int,
    days_history: int = Query(30, ge=14, le=365),
    illustrative: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    it = db.query(InventoryItem).filter(InventoryItem.id == item_id, InventoryItem.org_id == user.org_id).first()
    if not it:
        raise HTTPException(status_code=404, detail="SKU not found.")

    daily_d = float(it.daily_avg_demand or 0.0)
    history_records, source, cal = _history(db, it, days_history, illustrative)
    item_info = {"id": it.id, "sku": it.sku, "product_name": it.product_name,
                 "category": it.category, "unit_cost": it.unit_cost, "daily_avg_demand": daily_d}
    if source == "insufficient_history":
        n = db.query(DemandHistory).filter(DemandHistory.item_id == it.id,
                                           DemandHistory.org_id == user.org_id).count()
        return {"item": item_info, "history_source": source, "history": [],
                "status": "insufficient_history", "recorded_points": n,
                "min_history_points": MIN_RECORDED_POINTS,
                "message": (f"{n} recorded demand observations; at least {MIN_RECORDED_POINTS} are needed. "
                            "Import demand history (Data Upload -> Demand history) to enable forecasting.")}
    series = [r["quantity"] for r in history_records]

    sma_series = simple_moving_average(series, window=7)
    wma_series = weighted_moving_average(series, weights=[1.0, 2.0, 3.0])
    ses_series = exponential_smoothing(series, alpha=0.3)
    holt_res = holt_linear_smoothing(series, alpha=0.3, beta=0.1, horizons=[7, 30, 90])

    hold = holdout_selected(series, holdout=7)
    sel = select_model(series)
    path = forecast_path(series, horizon=90)
    return {
        "item": item_info,
        "status": "ok",
        "selected_model": sel,
        "units": "units/day",
        "horizons_days": [7, 30, 90],
        "data_period": {"from": history_records[0]["date"], "to": history_records[-1]["date"]},
        "reliability": _reliability(source, len(series), hold, cal),
        "calendar": cal,
        "rolling_origin": rolling_origin_evaluation(series, initial=min(21, max(len(series) - 7, 7))),
        "history": history_records,
        "models": {
            "simple_moving_average": sma_series,
            "weighted_moving_average": wma_series,
            "single_exponential_smoothing": ses_series,
            "holt_fitted": holt_res["fitted"]
        },
        "projections": ({
            "forecast_7d": holt_res["forecasts"]["7"],
            "forecast_30d": holt_res["forecasts"]["30"],
            "forecast_90d": holt_res["forecasts"]["90"],
            "confidence_intervals": holt_res["intervals"]
        } if sel["key"] == "Holt" else {
            "forecast_7d": round(sum(p["predicted"] for p in path[:7]), 1),
            "forecast_30d": round(sum(p["predicted"] for p in path[:30]), 1),
            "forecast_90d": round(sum(p["predicted"] for p in path[:90]), 1),
            "confidence_intervals": None,
            "note": "SBA forecasts an average demand rate; totals are rate x days. Intervals are not "
                    "given for intermittent demand (it is not normally distributed)."
        }),
        "history_source": source,
        "forecast_path": path,
        "model_comparison": holdout_model_comparison(series),
        "accuracy_metrics": hold,
        "in_sample_fit_metrics": holt_res["metrics"]
    }