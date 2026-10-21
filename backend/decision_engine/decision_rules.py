"""
decision_engine/decision_rules.py
------------------------------------
A transparent, rule-based decision layer that scores and prioritizes
inventory issues. Kept rule-based (rather than a black-box ML model) so
every recommendation surfaced in the UI is fully explainable -- a
requirement for enterprise trust in inventory automation.

Impact score (0-100) blends financial exposure and urgency so the
recommendation panel can rank the most important actions first.
"""

from datetime import date


def score_stockout_risk(item: dict) -> float:
    """Higher score = more urgent stockout risk."""
    qty = item.get("quantity_on_hand", 0) or 0
    safety = item.get("safety_stock", 0) or 0
    rop = item.get("reorder_point", 0) or 1
    value = item.get("stock_value", 0) or 0

    if qty <= safety:
        urgency = 100
    elif qty <= rop:
        urgency = 70
    else:
        urgency = 0

    financial_weight = min(value / 1000, 1) * 20  # up to +20 for high-value items
    return round(min(urgency + financial_weight, 100), 1)


def score_overstock(item: dict, excess_units: float) -> float:
    value_tied_up = excess_units * (item.get("unit_cost", 0) or 0)
    score = min((value_tied_up / 2000) * 100, 100)
    return round(score, 1)


def score_expiry_risk(item: dict) -> float:
    days = item.get("days_to_expiry")
    if days is None:
        return 0
    if days <= 7:
        return 100
    if days <= 30:
        return 80
    if days <= 90:
        return 50
    return 0


def score_supplier_risk(supplier_row: dict) -> float:
    otd = supplier_row.get("on_time_delivery_rate", 90)
    quality = supplier_row.get("quality_score", 90)
    combined = (otd + quality) / 2
    return round(max(0, 100 - combined), 1)


def priority_label(score: float) -> str:
    if score >= 70:
        return "high"
    if score >= 35:
        return "medium"
    return "low"
