"""
analytics/inventory_math.py
----------------------------
Validated reference implementations of the single-item inventory formulas
used by the platform. Every function states its units and assumptions,
rejects invalid input with ``InvalidInput`` (never substitutes a default),
and is independently checked against hand calculations in
tests/test_inventory_math.py. See docs/MATHEMATICAL_FORMULATION.md.

Notation and units
  D        annual demand                         units / year
  S        ordering (setup) cost per order       currency / order
  H        holding cost per unit per year        currency / (unit * year)
  c        unit purchase cost                    currency / unit
  i        annual holding rate                   fraction of c per year (0.20 = 20 %)
  mu_d     mean demand per day                   units / day
  sigma_d  std. dev. of DAILY demand             units / day
  L        lead time                             days
  sigma_L  std. dev. of lead time                days
  CSL      cycle service level = P(no stockout in a replenishment cycle)
  z        standard-normal quantile, z = Phi^-1(CSL)

Cycle service level (CSL) is NOT the fill rate: the fill rate is the
fraction of demand served from stock and is computed separately
(``fill_rate``) from the expected shortage per cycle.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Optional

_N = NormalDist()

# Periods per day for converting a standard deviation measured over another period.
PERIOD_DAYS = {"day": 1.0, "week": 7.0, "month": 365.0 / 12.0, "year": 365.0}


class InvalidInput(ValueError):
    """Raised when an input violates a formula's domain."""


def _finite(name: str, v) -> float:
    if v is None:
        raise InvalidInput(f"{name} is missing")
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise InvalidInput(f"{name} must be a number, got {v!r}")
    if math.isnan(x) or math.isinf(x):
        raise InvalidInput(f"{name} must be finite, got {v!r}")
    return x


def _nonneg(name: str, v) -> float:
    x = _finite(name, v)
    if x < 0:
        raise InvalidInput(f"{name} must be >= 0, got {x}")
    return x


def _pos(name: str, v) -> float:
    x = _finite(name, v)
    if x <= 0:
        raise InvalidInput(f"{name} must be > 0, got {x}")
    return x


# --------------------------------------------------------------------------- #
#  Costs and EOQ
# --------------------------------------------------------------------------- #
def holding_cost_per_unit_year(unit_cost, annual_holding_rate) -> float:
    """H = i * c  (currency per unit per year). Both inputs must be > 0 for a
    meaningful EOQ; a zero product is reported, not replaced."""
    c = _nonneg("unit_cost", unit_cost)
    i = _nonneg("annual_holding_rate", annual_holding_rate)
    if i > 5:
        raise InvalidInput("annual_holding_rate looks like a percentage; pass 0.20 for 20 %")
    return c * i


def eoq(annual_demand, ordering_cost, holding_cost_per_unit) -> float:
    """Classical deterministic EOQ, Q* = sqrt(2 D S / H).

    Assumptions: constant known demand rate, instantaneous replenishment, no
    shortages, no quantity discounts, no capacity or shelf-life limits. With
    quantity discounts, capacity/budget limits, perishability or stochastic
    demand, Q* is only a starting point (the platform's constrained optimiser
    and the EIDSF model handle those cases).
    D = 0 gives Q* = 0 (nothing to order)."""
    D = _nonneg("annual_demand", annual_demand)
    S = _nonneg("ordering_cost", ordering_cost)
    H = _pos("holding_cost_per_unit", holding_cost_per_unit)
    return math.sqrt(2.0 * D * S / H)


def annual_ordering_cost(annual_demand, order_qty, ordering_cost) -> float:
    D = _nonneg("annual_demand", annual_demand)
    Q = _pos("order_qty", order_qty)
    return D / Q * _nonneg("ordering_cost", ordering_cost)


def annual_holding_cost(order_qty, safety_stock, holding_cost_per_unit) -> float:
    """H * (Q/2 + SS): average cycle stock plus safety stock (no double counting)."""
    Q = _nonneg("order_qty", order_qty)
    SS = _nonneg("safety_stock", safety_stock)
    return _nonneg("holding_cost_per_unit", holding_cost_per_unit) * (Q / 2.0 + SS)


def total_relevant_cost(annual_demand, order_qty, ordering_cost, holding_cost_per_unit,
                        safety_stock=0.0) -> float:
    """Ordering + holding (purchase spend D*c is excluded: it does not depend
    on Q without quantity discounts)."""
    return (annual_ordering_cost(annual_demand, order_qty, ordering_cost)
            + annual_holding_cost(order_qty, safety_stock, holding_cost_per_unit))


# --------------------------------------------------------------------------- #
#  Service level, safety stock, reorder point
# --------------------------------------------------------------------------- #
def z_for_cycle_service_level(csl) -> float:
    """z = Phi^-1(CSL) for 0 < CSL < 1 (e.g. 0.95 -> 1.6449)."""
    p = _finite("cycle_service_level", csl)
    if not (0.0 < p < 1.0):
        raise InvalidInput("cycle_service_level must be strictly between 0 and 1 (e.g. 0.95)")
    return _N.inv_cdf(p)


def convert_sigma_to_daily(sigma, period: str) -> float:
    """Standard deviation of demand measured per `period` -> per day, assuming
    independent, identically distributed daily demand (variance adds):
    sigma_day = sigma_period / sqrt(days_in_period)."""
    if period not in PERIOD_DAYS:
        raise InvalidInput(f"period must be one of {', '.join(PERIOD_DAYS)}")
    return _nonneg("sigma", sigma) / math.sqrt(PERIOD_DAYS[period])


def sigma_lead_time_demand(mean_daily_demand, sigma_daily_demand, lead_time_days,
                           sigma_lead_time_days=0.0) -> float:
    """Std. dev. of demand during the lead time.

    Constant lead time:    sigma_DLT = sigma_d * sqrt(L)
    Random lead time (independent of demand):
                           sigma_DLT = sqrt(L * sigma_d^2 + mu_d^2 * sigma_L^2)
    The random-lead-time form reduces to the constant form when sigma_L = 0.
    Correlated demand and lead time are not covered by this formula."""
    mu = _nonneg("mean_daily_demand", mean_daily_demand)
    sd = _nonneg("sigma_daily_demand", sigma_daily_demand)
    L = _nonneg("lead_time_days", lead_time_days)
    sL = _nonneg("sigma_lead_time_days", sigma_lead_time_days)
    return math.sqrt(L * sd * sd + mu * mu * sL * sL)


def safety_stock(z, mean_daily_demand, sigma_daily_demand, lead_time_days,
                 sigma_lead_time_days=0.0) -> float:
    """SS = z * sigma_DLT (units). Assumes normally distributed lead-time
    demand, continuous review, and a cycle-service-level target. A negative z
    (CSL < 50 %) gives SS = 0 rather than a negative stock target."""
    zz = _finite("z", z)
    s = sigma_lead_time_demand(mean_daily_demand, sigma_daily_demand, lead_time_days,
                               sigma_lead_time_days)
    return max(zz, 0.0) * s


def reorder_point(mean_daily_demand, lead_time_days, safety_stock_units) -> float:
    """ROP = mu_d * L + SS (units)."""
    mu = _nonneg("mean_daily_demand", mean_daily_demand)
    L = _nonneg("lead_time_days", lead_time_days)
    return mu * L + _nonneg("safety_stock", safety_stock_units)


def standard_normal_loss(z) -> float:
    """G(z) = phi(z) - z * (1 - Phi(z)) (expected shortage of a unit normal)."""
    zz = _finite("z", z)
    return _N.pdf(zz) - zz * (1.0 - _N.cdf(zz))


def expected_shortage_per_cycle(z, sigma_dlt) -> float:
    """ESC = sigma_DLT * G(z) (units short per replenishment cycle)."""
    return _nonneg("sigma_dlt", sigma_dlt) * standard_normal_loss(z)


def fill_rate(z, sigma_dlt, order_qty) -> float:
    """Type-2 service (fill rate) = 1 - ESC / Q. Differs from the CSL."""
    Q = _pos("order_qty", order_qty)
    return max(0.0, 1.0 - expected_shortage_per_cycle(z, sigma_dlt) / Q)


# --------------------------------------------------------------------------- #
#  Classification helpers (documented thresholds)
# --------------------------------------------------------------------------- #
ABC_A_SHARE = 0.80   # items whose preceding cumulative value share is < 80 % -> A
ABC_B_SHARE = 0.95   # preceding share in [80 %, 95 %) -> B, otherwise C
XYZ_X_CV = 0.10      # CV <= 0.10 -> X
XYZ_Y_CV = 0.30      # 0.10 < CV <= 0.30 -> Y, otherwise Z  (thresholds unchanged from 2.4)
XYZ_MIN_POINTS = 14  # minimum demand observations for an XYZ class


def abc_classes(values: dict) -> dict:
    """Pareto ABC on annual consumption value {key: value >= 0}.

    Items are sorted by value (descending). An item is A if the cumulative
    share of all items ranked BEFORE it is < 80 %, B if < 95 %, else C. This
    guarantees the single largest item is always A. If the total value is 0
    (no cost or demand data), every item is None (unclassified)."""
    clean = {k: _nonneg(f"value[{k}]", v) for k, v in values.items()}
    total = sum(clean.values())
    if total <= 0:
        return {k: None for k in clean}
    out, cum = {}, 0.0
    for k, v in sorted(clean.items(), key=lambda kv: (-kv[1], str(kv[0]))):
        share_before = cum / total
        out[k] = "A" if share_before < ABC_A_SHARE else ("B" if share_before < ABC_B_SHARE else "C")
        cum += v
    return out


def coefficient_of_variation(series) -> Optional[float]:
    """Sample CV = s / mean of observed demand; None if fewer than
    XYZ_MIN_POINTS observations or mean <= 0."""
    xs = [float(x) for x in series if x is not None]
    if len(xs) < XYZ_MIN_POINTS:
        return None
    m = sum(xs) / len(xs)
    if m <= 0:
        return None
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var) / m


def xyz_class(cv: Optional[float]) -> Optional[str]:
    if cv is None:
        return None
    return "X" if cv <= XYZ_X_CV else ("Y" if cv <= XYZ_Y_CV else "Z")
