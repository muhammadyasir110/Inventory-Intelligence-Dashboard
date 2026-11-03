"""
analytics/forecasting_engine.py
---------------------------------
Statistical time-series demand forecasting and accuracy evaluation engine.
Supports Simple Moving Average (SMA), Weighted Moving Average (WMA),
Single Exponential Smoothing (SES), and Double Exponential Smoothing (Holt's Linear).
Computes MAE, RMSE, MAPE, and generates 7, 30, and 90-day projections with confidence intervals.
"""

import math
from datetime import date, timedelta
from typing import List, Dict, Any, Optional


def calculate_forecast_errors(actual: List[float], forecasted: List[float],
                              naive_scale: float = None) -> Dict[str, Any]:
    """
    Out-of-sample error metrics (None when undefined - never a fake 0):
    - MAE  = (1/n) sum |A_t - F_t|                     (units)
    - RMSE = sqrt((1/n) sum (A_t - F_t)^2)              (units)
    - MAPE = (100/n) sum |(A_t - F_t)/A_t|              (%), ONLY if every A_t > 0;
             with any zero actual MAPE is undefined (intermittent demand).
    - WAPE = 100 * sum|A_t - F_t| / sum A_t             (%), defined if sum A_t > 0;
             the recommended percentage metric for low/intermittent demand.
    - MASE = MAE / naive_scale, where naive_scale is the in-sample MAE of the
             one-step naive forecast (Hyndman & Koehler, 2006); < 1 beats naive.
    """
    if not actual or not forecasted or len(actual) != len(forecasted):
        return {"mae": None, "rmse": None, "mape": None, "wape": None, "mase": None,
                "mape_note": "no observations"}
    n = len(actual)
    abs_errors = [abs(a - f) for a, f in zip(actual, forecasted)]
    mae = sum(abs_errors) / n
    rmse = math.sqrt(sum((a - f) ** 2 for a, f in zip(actual, forecasted)) / n)
    if all(a > 0 for a in actual):
        mape, note = sum(abs((a - f) / a) for a, f in zip(actual, forecasted)) / n * 100.0, None
    else:
        mape, note = None, "undefined: zero demand in the evaluation window (use WAPE/MASE)"
    tot = sum(actual)
    wape = (sum(abs_errors) / tot * 100.0) if tot > 0 else None
    mase = (mae / naive_scale) if naive_scale and naive_scale > 0 else None
    out = {"mae": round(mae, 2), "rmse": round(rmse, 2),
           "mape": None if mape is None else round(mape, 2),
           "wape": None if wape is None else round(wape, 2),
           "mase": None if mase is None else round(mase, 3)}
    if note:
        out["mape_note"] = note
    return out


def naive_scale(train: List[float]) -> float:
    """In-sample MAE of the one-step naive forecast (MASE denominator)."""
    if len(train) < 2:
        return None
    return sum(abs(train[i] - train[i - 1]) for i in range(1, len(train))) / (len(train) - 1)


def build_daily_series(observations, last_n_days: int = None) -> Dict[str, Any]:
    """Turn recorded (date, quantity) observations into a daily calendar series.

    * observations on the same date (e.g. several sales channels) are SUMMED;
    * the calendar runs from the first to the last recorded date; dates with no
      record are filled with 0 under the documented assumption that "no record
      = no demand that day" (the number of filled days is reported so the
      assumption is visible; many filled days lower the reliability rating);
    * `last_n_days` keeps the most recent part of the calendar.
    Returns {"dates": [iso...], "values": [...], "observed_days", "filled_days",
    "duplicate_rows_merged"} - chronological, no future dates added.
    """
    from collections import OrderedDict
    by_date = OrderedDict()
    n_rows = 0
    for d, q in sorted(observations, key=lambda x: x[0]):
        n_rows += 1
        by_date[d] = by_date.get(d, 0.0) + float(q)
    if not by_date:
        return {"dates": [], "values": [], "observed_days": 0, "filled_days": 0, "duplicate_rows_merged": 0}
    first, last = next(iter(by_date)), next(reversed(by_date))
    dates, values, filled = [], [], 0
    day = first
    while day <= last:
        if day in by_date:
            values.append(by_date[day])
        else:
            values.append(0.0)
            filled += 1
        dates.append(day)
        day = day + timedelta(days=1)
    if last_n_days is not None and len(values) > last_n_days:
        cut = len(values) - last_n_days
        filled = sum(1 for d in dates[cut:] if d not in by_date)
        dates, values = dates[cut:], values[cut:]
    observed = sum(1 for d in dates if d in by_date)
    return {"dates": [d.isoformat() for d in dates], "values": values, "observed_days": observed,
            "filled_days": filled, "duplicate_rows_merged": n_rows - len(by_date)}


def simple_moving_average(series: List[float], window: int = 7) -> List[float]:
    """Calculates simple moving average for historical time series."""
    if len(series) < window:
        return [sum(series) / len(series)] * len(series) if series else []
    result = []
    for i in range(len(series)):
        if i < window - 1:
            sub = series[:i + 1]
            result.append(sum(sub) / len(sub))
        else:
            sub = series[i - window + 1:i + 1]
            result.append(sum(sub) / window)
    return [round(x, 2) for x in result]


def weighted_moving_average(series: List[float], weights: List[float] = None) -> List[float]:
    """Calculates weighted moving average (default 3-period linearly weighted)."""
    if weights is None:
        weights = [1.0, 2.0, 3.0]
    k = len(weights)
    w_sum = sum(weights)
    if len(series) < k:
        return [sum(series) / len(series)] * len(series) if series else []

    result = []
    for i in range(len(series)):
        if i < k - 1:
            result.append(series[i])
        else:
            window = series[i - k + 1:i + 1]
            val = sum(w * x for w, x in zip(weights, window)) / w_sum
            result.append(val)
    return [round(x, 2) for x in result]


def exponential_smoothing(series: List[float], alpha: float = 0.3) -> List[float]:
    """
    Single Exponential Smoothing (SES):
    F_{t+1} = alpha * A_t + (1 - alpha) * F_t
    """
    if not series:
        return []
    result = [series[0]]
    for i in range(1, len(series)):
        f_next = alpha * series[i - 1] + (1 - alpha) * result[-1]
        result.append(f_next)
    return [round(x, 2) for x in result]


def holt_linear_smoothing(series: List[float], alpha: float = 0.3, beta: float = 0.1, horizons: List[int] = None) -> Dict[str, Any]:
    """
    Double Exponential Smoothing (Holt's Linear Method) for level and trend:
    L_t = alpha * A_t + (1 - alpha) * (L_{t-1} + T_{t-1})
    T_t = beta * (L_t - L_{t-1}) + (1 - beta) * T_{t-1}
    F_{t+m} = L_t + m * T_t
    """
    if horizons is None:
        horizons = [7, 30, 90]

    if not series:
        return {
            "fitted": [],
            "level": 0.0,
            "trend": 0.0,
            "forecasts": {str(h): 0.0 for h in horizons},
            "intervals": {str(h): {"lower": 0.0, "upper": 0.0, "daily_rate": 0.0} for h in horizons},
            "metrics": {"mae": 0.0, "rmse": 0.0, "mape": 0.0}
        }

    if len(series) == 1:
        val = series[0]
        return {
            "fitted": [val],
            "level": val,
            "trend": 0.0,
            "forecasts": {str(h): round(val * h, 1) for h in horizons},
            "intervals": {str(h): {"lower": round(val * h * 0.8, 1), "upper": round(val * h * 1.2, 1), "daily_rate": round(val, 2)} for h in horizons},
            "metrics": {"mae": 0.0, "rmse": 0.0, "mape": 0.0}
        }

    # Initialize level and trend
    level = series[0]
    trend = series[1] - series[0]
    fitted = [level]

    for i in range(1, len(series)):
        prev_level = level
        val = series[i]
        level = alpha * val + (1 - alpha) * (prev_level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend
        fitted.append(level + trend)

    # Compute error metrics on in-sample fitted values
    metrics = calculate_forecast_errors(series, fitted)

    # Standard error of residuals for confidence bounds
    residuals = [a - f for a, f in zip(series, fitted)]
    var_res = sum(r ** 2 for r in residuals) / max(len(residuals) - 1, 1)
    std_res = math.sqrt(var_res)

    # Multi-horizon forecast generation
    forecasts = {}
    intervals = {}
    z_95 = 1.96

    for h in horizons:
        # Sum of projected demand over horizon h
        total_projected = sum(max(level + m * trend, 0.1) for m in range(1, h + 1))
        # Cumulative variance expands with sqrt(h)
        std_horizon = std_res * math.sqrt(h)
        lower_bound = max(0.0, total_projected - z_95 * std_horizon)
        upper_bound = total_projected + z_95 * std_horizon

        forecasts[str(h)] = round(total_projected, 1)
        intervals[str(h)] = {
            "lower": round(lower_bound, 1),
            "upper": round(upper_bound, 1),
            "daily_rate": round(total_projected / h, 2)
        }

    return {
        "fitted": [round(x, 2) for x in fitted],
        "level": round(level, 2),
        "trend": round(trend, 3),
        "forecasts": forecasts,
        "intervals": intervals,
        "metrics": metrics
    }


def generate_item_demand_series(daily_avg_demand: float, demand_std_dev: float = None, days: int = 30) -> List[Dict[str, Any]]:
    """ILLUSTRATIVE ONLY: a deterministic sine-shaped series around the mean.
    It is not demand data. The API uses it only when the caller explicitly
    asks for an illustration (forecasting endpoints, `illustrative=true`) and
    labels every result as synthetic; it is never substituted silently."""
    if demand_std_dev is None:
        demand_std_dev = max(daily_avg_demand * 0.25, 0.1)
    
    today = date.today()
    series = []
    for i in range(days, 0, -1):
        day = today - timedelta(days=i)
        factor = 1.0 + 0.18 * math.sin(i * 0.45)
        qty = max(0.1, round(daily_avg_demand * factor, 1))
        series.append({
            "date": day.isoformat(),
            "quantity": qty
        })
    return series

def holdout_evaluation(series: List[float], holdout: int = 7, alpha: float = 0.3,
                       beta: float = 0.1) -> Dict[str, Any]:
    """
    Out-of-sample accuracy: fit Holt on series[:-holdout], forecast the
    last `holdout` days, and score against the actual values. (In-sample
    residual errors overstate accuracy and should not be reported.)
    """
    if len(series) < holdout + 7:
        return {"mae": None, "rmse": None, "mape": None, "holdout_days": 0,
                "note": "series too short for holdout evaluation"}
    train, test = series[:-holdout], series[-holdout:]
    level, trend = train[0], (train[1] - train[0]) if len(train) > 1 else 0.0
    for y in train[1:]:
        prev = level
        level = alpha * y + (1 - alpha) * (level + trend)
        trend = beta * (level - prev) + (1 - beta) * trend
    preds = [max(0.0, level + (h + 1) * trend) for h in range(holdout)]
    m = calculate_forecast_errors(test, preds, naive_scale(train))
    m["holdout_days"] = holdout
    m["evaluation"] = "out-of-sample holdout"
    return m


def _holt_fit(series: List[float], alpha: float = 0.3, beta: float = 0.1):
    level = series[0]
    trend = (series[1] - series[0]) if len(series) > 1 else 0.0
    fitted = [series[0]]
    for y in series[1:]:
        fitted.append(level + trend)
        prev = level
        level = alpha * y + (1 - alpha) * (level + trend)
        trend = beta * (level - prev) + (1 - beta) * trend
    return level, trend, fitted


def holt_path(series: List[float], horizon: int = 30, alpha: float = 0.3,
              beta: float = 0.1, z: float = 1.96) -> List[Dict[str, float]]:
    """Daily Holt forecast path with approximate 95% prediction bands
    (residual sd x sqrt(h)), truncated at zero."""
    if not series:
        return []
    if len(series) < 3:
        m = sum(series) / len(series)
        return [{"predicted": round(m, 2), "lower": round(m, 2), "upper": round(m, 2)} for _ in range(horizon)]
    level, trend, fitted = _holt_fit(series, alpha, beta)
    res = [a - f for a, f in zip(series[1:], fitted[1:])]
    sd = math.sqrt(sum(r * r for r in res) / max(len(res) - 1, 1))
    out = []
    for h in range(1, horizon + 1):
        p = max(0.0, level + h * trend)
        band = z * sd * math.sqrt(h)
        out.append({"predicted": round(p, 2), "lower": round(max(0.0, p - band), 2),
                    "upper": round(p + band, 2)})
    return out


# ---------------------------------------------------------------------------
# Intermittent demand (2.7): Syntetos-Boylan classification, Croston and SBA
# ---------------------------------------------------------------------------
ADI_CUTOFF = 1.32      # average inter-demand interval (Syntetos, Boylan & Croston, 2005)
CV2_CUTOFF = 0.49      # squared coefficient of variation of non-zero demand sizes


def demand_pattern(series: List[float]) -> Dict[str, Any]:
    """Classify a demand series as smooth / erratic / intermittent / lumpy.

    ADI = n / (number of periods with demand > 0); CV^2 = (sd / mean)^2 of the
    non-zero demand sizes (population sd). Fewer than two non-zero periods:
    'insufficient' (no classification is attempted).
    """
    n = len(series)
    sizes = [y for y in series if y > 0]
    if n == 0 or len(sizes) < 2:
        return {"pattern": "insufficient", "adi": None, "cv2": None, "nonzero_periods": len(sizes),
                "zero_share": round(1 - len(sizes) / n, 3) if n else None}
    adi = n / len(sizes)
    m = sum(sizes) / len(sizes)
    var = sum((y - m) ** 2 for y in sizes) / len(sizes)
    cv2 = var / (m * m) if m > 0 else 0.0
    if adi < ADI_CUTOFF:
        pattern = "smooth" if cv2 < CV2_CUTOFF else "erratic"
    else:
        pattern = "intermittent" if cv2 < CV2_CUTOFF else "lumpy"
    return {"pattern": pattern, "adi": round(adi, 3), "cv2": round(cv2, 3),
            "nonzero_periods": len(sizes), "zero_share": round(1 - len(sizes) / n, 3)}


def croston(series: List[float], alpha: float = 0.1, variant: str = "croston") -> Dict[str, Any]:
    """Croston's method (Croston, 1972) and the Syntetos-Boylan approximation (SBA, 2005).

    Demand sizes z and inter-demand intervals p are smoothed separately and
    updated only in periods with demand:
        y_t > 0:  z <- z + alpha (y_t - z),  p <- p + alpha (q - p),  q <- 1
        y_t = 0:  q <- q + 1
    initialised at the first demand t1 (1-based) with z = y_t1, p = t1.
    Forecast of demand per period: z / p (Croston) or (1 - alpha/2) z / p (SBA,
    which removes Croston's positive bias). Returns the one-step forecasts made
    before each period ('fitted', None before the first demand) and the final rate.
    """
    if variant not in ("croston", "sba"):
        raise ValueError("variant must be 'croston' or 'sba'")
    factor = (1 - alpha / 2) if variant == "sba" else 1.0
    z = p = None
    q = 0
    fitted: List[Optional[float]] = []
    for t, y in enumerate(series, start=1):
        fitted.append(None if z is None else factor * z / p)
        if z is None:
            if y > 0:
                z, p, q = float(y), float(t), 1
            continue
        if y > 0:
            z = z + alpha * (y - z)
            p = p + alpha * (q - p)
            q = 1
        else:
            q += 1
    rate = None if z is None else factor * z / p
    return {"rate": rate, "fitted": fitted, "alpha": alpha, "variant": variant}


def intermittent_path(series: List[float], horizon: int = 30, alpha: float = 0.1,
                      z: float = 1.96) -> List[Dict[str, float]]:
    """Flat SBA forecast path with approximate bands (residual sd of the one-step
    rate forecasts x sqrt(h)), truncated at zero. Intermittent demand is not
    normal; the bands are indicative only and are labelled as such by the API."""
    res = croston(series, alpha=alpha, variant="sba")
    rate = res["rate"] or 0.0
    errs = [y - f for y, f in zip(series, res["fitted"]) if f is not None]
    sd = math.sqrt(sum(e * e for e in errs) / max(len(errs) - 1, 1)) if errs else 0.0
    return [{"predicted": round(rate, 2), "lower": round(max(0.0, rate - z * sd * math.sqrt(h)), 2),
             "upper": round(rate + z * sd * math.sqrt(h), 2)} for h in range(1, horizon + 1)]


def select_model(series: List[float]) -> Dict[str, Any]:
    """Forecasting method by demand pattern: SBA for intermittent and lumpy series
    (Syntetos & Boylan), Holt otherwise. Returned with the reason."""
    pat = demand_pattern(series)
    if pat["pattern"] in ("intermittent", "lumpy"):
        return {"model": "SBA (Syntetos-Boylan approximation of Croston)", "key": "SBA", **pat,
                "reason": f"{pat['pattern']} demand (ADI {pat['adi']} >= {ADI_CUTOFF}): "
                          "level-and-trend smoothing is biased when many days have zero demand."}
    return {"model": "Holt Linear Exponential Smoothing", "key": "Holt", **pat,
            "reason": f"{pat['pattern']} demand: demand occurs on most days."}


def forecast_path(series: List[float], horizon: int = 30) -> List[Dict[str, float]]:
    """Forecast path of the method chosen by select_model."""
    return intermittent_path(series, horizon) if select_model(series)["key"] == "SBA" \
        else holt_path(series, horizon)


def holdout_selected(series: List[float], holdout: int = 7) -> Dict[str, Any]:
    """Out-of-sample holdout accuracy of the method chosen (on the training part
    only, so the choice uses no holdout data)."""
    if len(series) < holdout + 7:
        return {"mae": None, "rmse": None, "mape": None, "holdout_days": 0,
                "note": "series too short for holdout evaluation"}
    train, test = series[:-holdout], series[-holdout:]
    sel = select_model(train)
    if sel["key"] == "SBA":
        rate = croston(train, variant="sba")["rate"] or 0.0
        m = calculate_forecast_errors(test, [rate] * holdout, naive_scale(train))
        m.update(holdout_days=holdout, evaluation="out-of-sample holdout")
    else:
        m = holdout_evaluation(series, holdout)
    m["model"] = sel["key"]
    return m


def holdout_model_comparison(series: List[float], holdout: int = 7) -> Dict[str, Dict[str, float]]:
    """Out-of-sample MAE/RMSE/MAPE of SMA, WMA, SES and Holt on the last `holdout` points."""
    if len(series) < holdout + 7:
        return {}
    train, test = series[:-holdout], series[-holdout:]
    sma = sum(train[-7:]) / 7.0
    w = [1.0, 2.0, 3.0]
    wma = sum(a * b for a, b in zip(w, train[-3:])) / sum(w)
    ses = train[0]
    for y in train[1:]:
        ses = 0.3 * y + 0.7 * ses
    level, trend, _ = _holt_fit(train)
    preds = {
        "Naive (last value)": [train[-1]] * holdout,
        "SMA": [sma] * holdout,
        "WMA": [wma] * holdout,
        "SES": [ses] * holdout,
        "Holt": [max(0.0, level + (h + 1) * trend) for h in range(holdout)],
    }
    for name, variant in (("Croston", "croston"), ("SBA", "sba")):
        r = croston(train, variant=variant)["rate"]
        if r is not None:
            preds[name] = [r] * holdout
    sc = naive_scale(train)
    return {k: calculate_forecast_errors(test, v, sc) for k, v in preds.items()}


def rolling_origin_evaluation(series: List[float], initial: int = 21, horizon: int = 1,
                              alpha: float = 0.3, beta: float = 0.1) -> Dict[str, Any]:
    """Rolling-origin (time-series cross-validation) evaluation of Holt versus
    the naive forecast: for origins t = initial .. n-horizon, fit on
    series[:t] only (no future data) and forecast series[t+horizon-1]."""
    n = len(series)
    if n < initial + horizon:
        return {"origins": 0, "note": f"needs at least {initial + horizon} observations"}
    act, holt, naive = [], [], []
    for t in range(initial, n - horizon + 1):
        train = series[:t]
        level, trend, _ = _holt_fit(train, alpha, beta)
        holt.append(max(0.0, level + horizon * trend))
        naive.append(train[-1])
        act.append(series[t + horizon - 1])
    sc = naive_scale(series[:initial])
    return {"origins": len(act), "horizon": horizon,
            "holt": calculate_forecast_errors(act, holt, sc),
            "naive": calculate_forecast_errors(act, naive, sc)}
