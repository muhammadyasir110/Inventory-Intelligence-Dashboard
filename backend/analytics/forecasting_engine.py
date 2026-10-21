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
from typing import List, Dict, Any, Tuple


def calculate_forecast_errors(actual: List[float], forecasted: List[float]) -> Dict[str, float]:
    """
    Computes statistical evaluation metrics:
    - MAE: Mean Absolute Error = (1/n) * sum(|A_t - F_t|)
    - RMSE: Root Mean Squared Error = sqrt((1/n) * sum((A_t - F_t)^2))
    - MAPE: Mean Absolute Percentage Error = (1/n) * sum(|(A_t - F_t) / A_t|) * 100%
    """
    if not actual or not forecasted or len(actual) != len(forecasted) or len(actual) == 0:
        return {"mae": 0.0, "rmse": 0.0, "mape": 0.0}

    n = len(actual)
    abs_errors = [abs(a - f) for a, f in zip(actual, forecasted)]
    sq_errors = [(a - f) ** 2 for a, f in zip(actual, forecasted)]
    pct_errors = [abs((a - f) / a) * 100 for a, f in zip(actual, forecasted) if a > 0]

    mae = sum(abs_errors) / n
    rmse = math.sqrt(sum(sq_errors) / n)
    mape = (sum(pct_errors) / len(pct_errors)) if pct_errors else 0.0

    return {
        "mae": round(mae, 2),
        "rmse": round(rmse, 2),
        "mape": round(mape, 2)
    }


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
    """Generates deterministic historical demand time series for demonstration and testing."""
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
    m = calculate_forecast_errors(test, preds)
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
        "SMA": [sma] * holdout,
        "WMA": [wma] * holdout,
        "SES": [ses] * holdout,
        "Holt": [max(0.0, level + (h + 1) * trend) for h in range(holdout)],
    }
    return {k: calculate_forecast_errors(test, v) for k, v in preds.items()}
