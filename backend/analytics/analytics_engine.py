"""
analytics/analytics_engine.py
------------------------------
Computes all derived analytics for the dashboard from raw InventoryItem
rows: executive KPIs, stock health classification, low-stock/overstock/
expiry lists, safety stock analysis, warehouse utilization, supplier
performance rollups, and advanced multi-dimensional classification (ABC-XYZ, FSN, VED).
"""

from datetime import date, datetime
from typing import List, Dict, Any


def _to_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return None


def classify_status(item: Dict[str, Any]) -> str:
    """Assigns a single color-coded status to an item, priority ordered."""
    qty = item.get("quantity_on_hand", 0) or 0
    rop = item.get("reorder_point", 0) or 0
    safety = item.get("safety_stock", 0) or 0
    max_level = item.get("max_stock_level", 0) or 0
    expiry = _to_date(item.get("expiry_date"))

    if expiry:
        days_to_expiry = (expiry - date.today()).days
        if days_to_expiry <= 30:
            return "expiring"

    if qty <= safety:
        return "critical"
    if qty <= rop:
        return "low"
    if max_level and qty > max_level:
        return "overstock"
    return "normal"


def compute_classifications(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Calculates ABC, XYZ, FSN, and VED segmentations dynamically."""
    if not items:
        return items

    # 1. ABC classification on annual consumption value (annual demand x unit cost).
    #    Rule (analytics/inventory_math.abc_classes): ranked by value, an item is A
    #    if the cumulative share of items ranked before it is < 80 %, B if < 95 %,
    #    else C. With no cost/demand data at all, items are unclassified (None).
    from analytics.inventory_math import abc_classes, xyz_class
    for idx, it in enumerate(items):
        demand = max(float(it.get("daily_avg_demand", 0) or 0), 0.0)
        cost = max(float(it.get("unit_cost", 0) or 0), 0.0)
        it["annual_cost_usage"] = demand * 365 * cost
    abc = abc_classes({i: it["annual_cost_usage"] for i, it in enumerate(items)})
    for i, it in enumerate(items):
        it["abc_class"] = abc[i]

    # 2. XYZ classification on the OBSERVED coefficient of variation of demand.
    #    `demand_cv_observed` is supplied by the caller from recorded demand
    #    history (>= 14 observations, see api/crud.observed_cv_map). Without
    #    recorded history the item is left unclassified - no CV is invented.
    #    (IIP <= 2.4 derived a CV from the first letter of the SKU; removed.)
    for it in items:
        cv = it.get("demand_cv_observed")
        it["cv"] = None if cv is None else round(float(cv), 3)
        it["xyz_class"] = xyz_class(it["cv"])
        it["xyz_basis"] = ("observed demand history" if it["cv"] is not None
                           else "unclassified: insufficient recorded demand history")

    # 3. FSN (Fast, Slow, Non-moving) based on turnover ratio
    for it in items:
        qty = float(it.get("quantity_on_hand", 0) or 0)
        demand = float(it.get("daily_avg_demand", 0) or 0)
        annual_demand = demand * 365
        turnover = annual_demand / qty if qty > 0 else 0.0
        it["turnover_ratio"] = round(turnover, 2)
        if turnover > 8:
            it["fsn_class"] = "F"
        elif turnover > 2:
            it["fsn_class"] = "S"
        else:
            it["fsn_class"] = "N"

    # 4. VED (Vital, Essential, Desirable). True VED is a managerial criticality
    #    judgement; this is a documented HEURISTIC PROXY from stock status,
    #    price and demand, labelled as such in `ved_basis`.
    for it in items:
        it["ved_basis"] = "heuristic proxy (status, unit price > 100 & demand > 5 -> V; low status or price > 30 -> E)"
        status = it.get("status", "normal")
        price = float(it.get("unit_price", 0) or 0)
        demand = float(it.get("daily_avg_demand", 0) or 0)
        
        if status == "critical" or (price > 100 and demand > 5):
            it["ved_class"] = "V"
        elif status == "low" or price > 30:
            it["ved_class"] = "E"
        else:
            it["ved_class"] = "D"

    return items


def enrich_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Adds computed fields (status, stock_value, days_of_supply) to each item."""
    enriched = []
    for it in items:
        it = dict(it)
        it["status"] = classify_status(it)
        qty = float(it.get("quantity_on_hand", 0) or 0)
        cost = float(it.get("unit_cost", 0) or 0)
        price = float(it.get("unit_price", 0) or 0)
        demand = float(it.get("daily_avg_demand", 0) or 0)
        it["stock_value"] = round(qty * cost, 2)
        it["potential_revenue"] = round(qty * price, 2)
        it["days_of_supply"] = round(qty / demand, 1) if demand > 0 else None
        expiry = _to_date(it.get("expiry_date"))
        it["days_to_expiry"] = (expiry - date.today()).days if expiry else None
        enriched.append(it)

    # Apply MPhil dynamic multi-criteria classifications
    enriched = compute_classifications(enriched)
    return enriched


def compute_kpis(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Executive-level KPI summary including MPhil decision statistics."""
    if not items:
        return {
            "total_skus": 0, "total_stock_units": 0, "total_stock_value": 0,
            "low_stock_count": 0, "critical_count": 0, "overstock_count": 0,
            "expiring_count": 0, "healthy_count": 0, "avg_days_of_supply": 0,
            "inventory_turnover_estimate": 0, "stockout_risk_pct": 0,
            "abc_counts": {"A": 0, "B": 0, "C": 0, "unclassified": 0},
            "xyz_counts": {"X": 0, "Y": 0, "Z": 0, "unclassified": 0},
        }

    total_skus = len(items)
    total_units = sum(float(i.get("quantity_on_hand", 0) or 0) for i in items)
    total_value = sum(float(i.get("stock_value", 0) or 0) for i in items)

    status_counts = {"low": 0, "critical": 0, "overstock": 0, "expiring": 0, "normal": 0}
    abc_counts = {"A": 0, "B": 0, "C": 0, "unclassified": 0}
    xyz_counts = {"X": 0, "Y": 0, "Z": 0, "unclassified": 0}
    fsn_counts = {"F": 0, "S": 0, "N": 0, "unclassified": 0}
    ved_counts = {"V": 0, "E": 0, "D": 0, "unclassified": 0}

    def _bump(counts, key):
        counts[key if key in counts else "unclassified"] += 1

    for i in items:
        status_counts[i.get("status", "normal")] = status_counts.get(i.get("status", "normal"), 0) + 1
        _bump(abc_counts, i.get("abc_class"))
        _bump(xyz_counts, i.get("xyz_class"))
        _bump(fsn_counts, i.get("fsn_class"))
        _bump(ved_counts, i.get("ved_class"))

    dos_values = [i["days_of_supply"] for i in items if i.get("days_of_supply") is not None]
    avg_dos = round(sum(dos_values) / len(dos_values), 1) if dos_values else 0

    total_demand = sum(float(i.get("daily_avg_demand", 0) or 0) for i in items) * 365
    turnover = round(total_demand / total_units, 2) if total_units > 0 else 0

    at_risk = status_counts["low"] + status_counts["critical"]
    stockout_risk_pct = round((at_risk / total_skus) * 100, 1) if total_skus else 0

    return {
        "total_skus": total_skus,
        "total_stock_units": round(total_units, 1),
        "total_stock_value": round(total_value, 2),
        "low_stock_count": status_counts["low"],
        "critical_count": status_counts["critical"],
        "overstock_count": status_counts["overstock"],
        "expiring_count": status_counts["expiring"],
        "healthy_count": status_counts["normal"],
        "avg_days_of_supply": avg_dos,
        "inventory_turnover_estimate": turnover,
        "stockout_risk_pct": stockout_risk_pct,
        "abc_counts": abc_counts,
        "xyz_counts": xyz_counts,
        "fsn_counts": fsn_counts,
        "ved_counts": ved_counts
    }


def low_stock_alerts(items):
    return sorted(
        [i for i in items if i["status"] in ("low", "critical")],
        key=lambda x: (x["status"] != "critical", x.get("quantity_on_hand", 0)),
    )


def overstock_items(items):
    return sorted(
        [i for i in items if i["status"] == "overstock"],
        key=lambda x: -(x.get("stock_value", 0) or 0),
    )


def expiry_watchlist(items):
    return sorted(
        [i for i in items if i.get("days_to_expiry") is not None and i["days_to_expiry"] <= 90],
        key=lambda x: x["days_to_expiry"],
    )


def safety_stock_analysis(items):
    """Compares actual stock vs safety stock threshold, flags breaches."""
    results = []
    for i in items:
        safety = float(i.get("safety_stock", 0) or 0)
        qty = float(i.get("quantity_on_hand", 0) or 0)
        buffer_pct = round(((qty - safety) / safety) * 100, 1) if safety > 0 else None
        results.append({
            "id": i["id"],
            "sku": i["sku"], "product_name": i["product_name"],
            "quantity_on_hand": qty, "safety_stock": safety,
            "buffer_pct": buffer_pct,
            "breach": qty < safety,
            "abc_class": i.get("abc_class", "C"),
            "xyz_class": i.get("xyz_class", "Z"),
        })
    return sorted(results, key=lambda x: (not x["breach"], x["buffer_pct"] if x["buffer_pct"] is not None else 9e9))


def warehouse_utilization(items):
    agg: Dict[str, Dict[str, Any]] = {}
    for i in items:
        wh = i.get("warehouse", "Main Warehouse")
        agg.setdefault(wh, {"warehouse": wh, "total_units": 0, "sku_count": 0, "stock_value": 0})
        agg[wh]["total_units"] += float(i.get("quantity_on_hand", 0) or 0)
        agg[wh]["sku_count"] += 1
        agg[wh]["stock_value"] += float(i.get("stock_value", 0) or 0)

    max_units = max((w["total_units"] for w in agg.values()), default=1) or 1
    result = []
    for w in agg.values():
        w["total_units"] = round(w["total_units"], 1)
        w["stock_value"] = round(w["stock_value"], 2)
        w["utilization_pct"] = round((w["total_units"] / max_units) * 100, 1)
        result.append(w)
    return sorted(result, key=lambda x: -x["total_units"])


def supplier_performance(items, supplier_meta: Dict[str, Dict[str, Any]] = None):
    supplier_meta = supplier_meta or {}
    agg: Dict[str, Dict[str, Any]] = {}
    for i in items:
        sup = i.get("supplier", "Unassigned Supplier")
        agg.setdefault(sup, {"supplier": sup, "sku_count": 0, "stock_value": 0, "avg_lead_time": []})
        agg[sup]["sku_count"] += 1
        agg[sup]["stock_value"] += float(i.get("stock_value", 0) or 0)
        agg[sup]["avg_lead_time"].append(float(i.get("lead_time_days", 0) or 0))

    result = []
    for sup, data in agg.items():
        lead_times = data.pop("avg_lead_time")
        data["avg_lead_time_days"] = round(sum(lead_times) / len(lead_times), 1) if lead_times else 0
        data["stock_value"] = round(data["stock_value"], 2)
        meta = supplier_meta.get(sup, {})
        data["on_time_delivery_rate"] = meta.get("on_time_delivery_rate", 90.0)
        data["quality_score"] = meta.get("quality_score", 90.0)
        data["performance_score"] = round(
            (data["on_time_delivery_rate"] * 0.5 + data["quality_score"] * 0.5), 1
        )
        result.append(data)
    return sorted(result, key=lambda x: -x["performance_score"])


def category_breakdown(items):
    agg: Dict[str, Dict[str, Any]] = {}
    for i in items:
        cat = i.get("category", "Uncategorized")
        agg.setdefault(cat, {"category": cat, "sku_count": 0, "stock_value": 0, "total_units": 0})
        agg[cat]["sku_count"] += 1
        agg[cat]["stock_value"] += float(i.get("stock_value", 0) or 0)
        agg[cat]["total_units"] += float(i.get("quantity_on_hand", 0) or 0)
    for c in agg.values():
        c["stock_value"] = round(c["stock_value"], 2)
        c["total_units"] = round(c["total_units"], 1)
    return sorted(agg.values(), key=lambda x: -x["stock_value"])


def top_skus_by_value(items: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    """Returns the top items ranked by total stock value."""
    if not items:
        return []
    return sorted(items, key=lambda x: -(x.get("stock_value", 0) or 0))[:limit]

