"""
recommendation/recommendation_engine.py
------------------------------------------
Explainable AI (XAI) Recommendation Generator.
Grounds every automated suggestion in an 11-point explainability schema
and maps it through the 9-stage Decision Support Systems (DSS) Pipeline:
1. Enterprise Context
2. Decision Context
3. Decision Model
4. Computational Intelligence
5. Knowledge Extraction
6. Explainability
7. Recommendation
8. Decision Monitoring
9. Organizational Learning
"""

from decision_engine.decision_rules import (
    score_stockout_risk, score_overstock, score_expiry_risk,
    score_supplier_risk, priority_label,
)
from optimization.stock_optimizer import (
    suggested_reorder_quantity, overstock_reduction_target, annual_holding_cost,
    calculate_safety_stock_details, calculate_rop_details, economic_order_quantity
)


def get_pipeline_stages(title: str, context_type: str, details: dict) -> list:
    return [
        {
            "stage": 1,
            "name": "Enterprise Context",
            "desc": f"Multi-echelon inventory network. SKU: {details.get('sku', 'General')} at {details.get('warehouse', 'Main Facility')}."
        },
        {
            "stage": 2,
            "name": "Decision Context",
            "desc": f"Goal: {title}. Optimize working capital and ensure {details.get('service_level', '95%')} customer fulfillment."
        },
        {
            "stage": 3,
            "name": "Decision Model",
            "desc": f"Model: {details.get('model', 'Stochastic Harris-Wilson EOQ & Safety Stock Formulation')}."
        },
        {
            "stage": 4,
            "name": "Computational Intelligence",
            "desc": f"Evaluated empirical demand rate ({details.get('demand', 0)} u/day), lead time ({details.get('lead_time', 0)}d), and holding rate."
        },
        {
            "stage": 5,
            "name": "Knowledge Extraction",
            "desc": f"Extracted optimal replenishment trigger at ROP = {details.get('rop', 0)} units with Safety Stock buffer = {details.get('ss', 0)} units."
        },
        {
            "stage": 6,
            "name": "Explainability",
            "desc": f"Root Cause: {details.get('root_cause', 'Stock variance threshold breached')}."
        },
        {
            "stage": 7,
            "name": "Decision Recommendation",
            "desc": f"Prescribed Action: {details.get('action', 'Replenish stock')}."
        },
        {
            "stage": 8,
            "name": "Decision Monitoring",
            "desc": "Track manager review, execution in ERP, and stockout delta over subsequent 30-day window."
        },
        {
            "stage": 9,
            "name": "Organizational Learning",
            "desc": "Feed outcome data into lead-time variance adjustments and dynamic ABC parameter calibration."
        }
    ]


ENGINE_VERSION = "recommendation_engine/2.2"


def recommendation_key(rec: dict) -> str:
    """Deterministic identity of a recommendation within a batch:
    same category + same item (or supplier) -> same key."""
    import hashlib
    basis = f"{rec.get('category')}|{rec.get('item_id') or ''}|{rec.get('sku') or ''}|" \
            f"{'' if rec.get('item_id') else rec.get('product_name') or ''}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:20]


def _cfg(org_config, name, default):
    if org_config is None:
        return default
    if isinstance(org_config, dict):
        return org_config.get(name, default) or default
    return getattr(org_config, name, default) or default


def _money(v: float) -> str:
    return f"${v:,.2f}"


def evidence_completeness(item: dict) -> float:
    """
    Share of the model inputs behind a recommendation that are OBSERVED in
    the data rather than filled with a default. Shown to managers instead
    of an unvalidated 'confidence' figure.
    """
    checks = [
        float(item.get("daily_avg_demand", 0) or 0) > 0,
        float(item.get("demand_std_dev", 0) or 0) >= 1.0,     # < 1 is the CV placeholder
        float(item.get("lead_time_days", 0) or 0) > 0,
        float(item.get("lead_time_std_dev", 0) or 0) not in (0.0, 1.0),  # 1.0 is the column default
        float(item.get("unit_cost", 0) or 0) > 0,
        float(item.get("unit_price", 0) or 0) > 0,
        item.get("supplier") not in (None, "", "Unassigned"),
        float(item.get("reorder_point", 0) or 0) > 0,
    ]
    return round(sum(checks) / len(checks), 2)


def generate_recommendations(items, supplier_perf=None, org_config=None):
    supplier_perf = {s.get("supplier", s.get("name")): s for s in (supplier_perf or [])}
    recs = []
    h_rate = float(_cfg(org_config, "annual_holding_rate", 0.20))
    setup = float(_cfg(org_config, "order_setup_cost", 50.0))
    sl_target = float(_cfg(org_config, "service_level_target", 0.95))
    budget = float(_cfg(org_config, "annual_working_capital_budget", 1000000.0))
    sl_txt = f"{sl_target * 100:.0f}%"

    for item in items:
        status = item.get("status")
        sku = item.get("sku", "SKU")
        name = item.get("product_name", sku)
        wh = item.get("warehouse", "Main Warehouse")
        qty = float(item.get("quantity_on_hand", 0) or 0)
        cost = float(item.get("unit_cost", 0) or 0)
        daily_d = float(item.get("daily_avg_demand", 1) or 1)
        lt = float(item.get("lead_time_days", 7) or 7)
        ss = float(item.get("safety_stock", 0) or (daily_d * lt * 0.3))
        rop = float(item.get("reorder_point", 0) or (daily_d * lt + ss))
        price = float(item.get("unit_price", 0) or 0) or cost
        days_cover = qty / daily_d if daily_d > 0 else float("inf")
        evidence = evidence_completeness(item)

        if status in ("low", "critical"):
            score = score_stockout_risk(item)
            reorder_qty = max(suggested_reorder_quantity(item), 10.0)
            order_val = round(reorder_qty * cost, 2)
            
            p_details = {
                "sku": sku, "warehouse": wh, "demand": daily_d, "lead_time": lt,
                "rop": rop, "ss": ss, "service_level": sl_txt,
                "model": "Stochastic (s, Q) Reorder Point Model",
                "root_cause": f"On-hand inventory ({qty:.0f}) has breached {'Safety Stock limit' if status == 'critical' else 'Reorder Point'} ({ss:.0f} / {rop:.0f} units).",
                "action": f"Create PO for {reorder_qty:.0f} units from {item.get('supplier', 'Supplier')}."
            }

            recs.append({
                "item_id": item.get("id"),
                "sku": sku,
                "product_name": name,
                "title": f"Replenishment Order: {name}",
                "category": "reorder",
                "priority": priority_label(score),
                "impact_score": score,
                "confidence_score": evidence,
                "evidence_completeness": evidence,
                
                "business_problem": f"Imminent stockout risk. Current on-hand stock ({qty:.0f} units) is insufficient to cover lead time demand ({daily_d * lt:.1f} units).",
                "empirical_evidence": f"Stock on Hand = {qty:.0f} units | Reorder Point = {rop:.0f} units | Safety Stock = {ss:.0f} units | Daily Demand = {daily_d:.1f} units/day.",
                "input_data_summary": f"Lead Time = {lt:.0f} days, Unit Cost = {_money(cost)}, Setup Cost = {_money(setup)}, Holding Rate = {h_rate * 100:.0f}%.",
                "formula_reference": "ROP = (d * L) + Z * sqrt(L * sigma_D^2 + D^2 * sigma_L^2)",
                "affected_kpi": "Stockout Rate & On-Time Customer Fulfillment",
                "constraints_checked": f"Order value {_money(order_val)} = {100 * order_val / budget:.2f}% of the annual working-capital budget ({_money(budget)}). Supplier MOQ / pack size: not available in the data, not checked.",
                "optimization_outcome": f"Order {reorder_qty:.0f} units ({_money(order_val)}) to restore stock to the target level (max stock or lead-time demand cover). For cost-optimal batch sizes under budget limits see the Optimization Center.",
                "risk_assessment": f"{'CRITICAL' if status == 'critical' else 'HIGH'} — Stock covers about {days_cover:.1f} days of demand versus a {lt:.0f}-day supplier lead time.",
                "expected_impact": f"Avoids an expected shortfall of about {max(daily_d * lt - qty, 0):.0f} units during the lead time (≈ {_money(max(daily_d * lt - qty, 0) * price)} of sales at unit price); supports the {sl_txt} cycle-service-level target.",
                "alternative_actions": f"1. Expedited freight order ({lt/2:.0f} days lead time with +15% freight fee). 2. Inter-warehouse transfer from regional hub if available.",
                "suggested_action": f"Place purchase order for {reorder_qty:.0f} units ({_money(order_val)}) with {item.get('supplier', 'supplier')}.",
                "reasoning": (
                    f"Current stock ({qty:.0f} units) has fallen below {'critical safety stock' if status == 'critical' else 'reorder point'} "
                    f"({ss:.0f} / {rop:.0f} units). At consumption of {daily_d:.1f} units/day and {lt:.0f} days supplier lead time, "
                    f"an immediate order of {reorder_qty:.0f} units is required to avoid customer stockouts."
                ),
                "status": "pending",
                "pipeline_stages": get_pipeline_stages(f"Replenishment Order: {name}", "reorder", p_details)
            })

        elif status == "overstock":
            excess = overstock_reduction_target(item)
            score = score_overstock(item, excess)
            holding_cost = annual_holding_cost(item)
            excess_val = round(excess * cost, 2)

            p_details = {
                "sku": sku, "warehouse": wh, "demand": daily_d, "lead_time": lt,
                "rop": rop, "ss": ss, "service_level": sl_txt,
                "model": "Max Stock Threshold & Holding Cost Optimization",
                "root_cause": f"Stock on hand ({qty:.0f} units) exceeds maximum threshold ({item.get('max_stock_level', 0):.0f} units) by {excess:.0f} units.",
                "action": f"Liquidate or reallocate {excess:.0f} units to release {_money(excess_val)} in working capital."
            }

            recs.append({
                "item_id": item.get("id"),
                "sku": sku,
                "product_name": name,
                "title": f"Excess Stock Reduction: {name}",
                "category": "reduce",
                "priority": priority_label(score),
                "impact_score": score,
                "confidence_score": evidence,
                "evidence_completeness": evidence,
                
                "business_problem": f"Capital lockup and unnecessary holding costs due to overstocked units ({excess:.0f} units excess).",
                "empirical_evidence": f"Stock on Hand = {qty:.0f} units | Max Stock Level = {item.get('max_stock_level', 0):.0f} units | Excess = {excess:.0f} units.",
                "input_data_summary": f"Unit Cost = {_money(cost)}, Annual Holding Cost Rate = {h_rate * 100:.0f}%, Daily Demand = {daily_d:.1f} units/day.",
                "formula_reference": "Excess Units = max(0, Qty - MaxStockLevel); Holding Cost = Excess * UnitCost * h",
                "affected_kpi": "Inventory Holding Cost & Working Capital Velocity",
                "constraints_checked": "Warehouse Floor Capacity Headroom: Restored upon liquidation",
                "optimization_outcome": f"Reallocating {excess:.0f} excess units saves ~{_money(excess_val * h_rate)}/yr in holding costs.",
                "risk_assessment": "MEDIUM — Prolonged overstock leads to cash lockup, shrinkage, and potential obsolescence.",
                "expected_impact": f"Releases approximately {_money(excess_val)} in trapped working capital.",
                "alternative_actions": "1. Run promotional 10% bundle discount. 2. Reallocate to secondary deficit warehouses. 3. Negotiate supplier return.",
                "suggested_action": f"Pause new purchase orders and initiate clearance or inter-warehouse transfer of ~{excess:.0f} units.",
                "reasoning": (
                    f"Stock level ({qty:.0f} units) exceeds target ceiling ({item.get('max_stock_level', 0):.0f} units) by {excess:.0f} units, "
                    f"costing approximately {_money(excess_val * h_rate)}/year in holding costs."
                ),
                "status": "pending",
                "pipeline_stages": get_pipeline_stages(f"Excess Stock Reduction: {name}", "reduce", p_details)
            })

        if item.get("days_to_expiry") is not None and item["days_to_expiry"] <= 90:
            score = score_expiry_risk(item)
            at_risk_val = round(qty * cost, 2)

            p_details = {
                "sku": sku, "warehouse": wh, "demand": daily_d, "lead_time": lt,
                "rop": rop, "ss": ss, "service_level": sl_txt,
                "model": "Perishable Inventory FEFO Outbound Prioritization",
                "root_cause": f"Batch expires in {item['days_to_expiry']} days with {qty:.0f} units remaining.",
                "action": "Prioritize batch in outbound dispatch (FEFO) and apply 15% markdown if velocity < 1.0."
            }

            recs.append({
                "item_id": item.get("id"),
                "sku": sku,
                "product_name": name,
                "title": f"FEFO Expiry Mitigation: {name}",
                "category": "expiry",
                "priority": priority_label(score),
                "impact_score": score,
                "confidence_score": evidence,
                "evidence_completeness": evidence,
                
                "business_problem": f"Perishable inventory approaching expiration in {item['days_to_expiry']} days.",
                "empirical_evidence": f"Days to Expiry = {item['days_to_expiry']}d | At-Risk Units = {qty:.0f} | Stock Value at Risk = {_money(at_risk_val)}.",
                "input_data_summary": f"Expiry Date = {item.get('expiry_date')}, Daily Demand = {daily_d:.1f} u/day, Estimated Sales before Expiry = {daily_d * item['days_to_expiry']:.0f} units.",
                "formula_reference": "FEFO Priority Rank = DaysToExpiry / DailyDemand",
                "affected_kpi": "Inventory Scrap & Write-off Rate",
                "constraints_checked": "Shelf Life Policy Compliance: Alert Active",
                "optimization_outcome": f"Expected sales before expiry ≈ {min(daily_d * item['days_to_expiry'], qty):.0f} of {qty:.0f} units; up to {_money(max(qty - daily_d * item['days_to_expiry'], 0) * cost)} would be written off without action.",
                "risk_assessment": f"{'CRITICAL' if item['days_to_expiry'] <= 30 else 'HIGH'} — Potential 100% loss of remaining stock value.",
                "expected_impact": f"Recovers up to {_money(at_risk_val)} of inventory value prior to expiration.",
                "alternative_actions": "1. Dispatch under FEFO fulfillment rule. 2. Bundle with fast-moving items. 3. Supplier return under credit memo.",
                "suggested_action": "Prioritize outbound fulfillment via FEFO immediately and monitor daily sell-through rate.",
                "reasoning": (
                    f"This SKU expires in {item['days_to_expiry']} days with {qty:.0f} units on hand ({_money(at_risk_val)} at risk). "
                    f"FEFO outbound queueing must be enforced to prevent inventory write-offs."
                ),
                "status": "pending",
                "pipeline_stages": get_pipeline_stages(f"FEFO Expiry Mitigation: {name}", "expiry", p_details)
            })

    for sup_name, perf in supplier_perf.items():
        score = score_supplier_risk(perf)
        if score >= 25:
            otd = perf.get('on_time_delivery_rate', 90)
            qual = perf.get('quality_score', 90)
            sku_cnt = perf.get('sku_count', 1)

            p_details = {
                "sku": "Multiple", "warehouse": "Network-Wide", "demand": 0, "lead_time": perf.get("avg_lead_time_days", 7),
                "rop": 0, "ss": 0, "service_level": "Supplier Sourcing",
                "model": "Multi-Criteria Supplier Performance & Risk Index",
                "root_cause": f"Supplier score ({otd:.0f}% OTD / {qual:.0f}% Quality) falls below 90% benchmark.",
                "action": "Enforce vendor SLA and identify dual-sourcing partner."
            }

            recs.append({
                "item_id": None,
                "sku": None,
                "product_name": sup_name,
                "title": f"Supplier Risk Mitigation: {sup_name}",
                "category": "supplier",
                "priority": priority_label(score),
                "impact_score": score,
                "confidence_score": None,
                "evidence_completeness": None,
                
                "business_problem": f"Substandard supplier performance affecting {sku_cnt} critical supply lines.",
                "empirical_evidence": f"On-Time Delivery Rate = {otd:.1f}% | Quality Acceptability = {qual:.1f}% | Composite Risk Score = {score:.1f}/100.",
                "input_data_summary": f"Average Lead Time = {perf.get('avg_lead_time_days', 7):.1f} days, Supplied Inventory Value = {_money(float(perf.get('stock_value', perf.get('total_value', 0)) or 0))}.",
                "formula_reference": "Supplier Risk Index = 100 - (0.5 * OTD + 0.5 * Quality)",
                "affected_kpi": "Supplier Reliability & Lead Time Variability",
                "constraints_checked": "Procurement Contract SLA: Threshold Breached (< 90%)",
                "optimization_outcome": "Dual-sourcing is expected to reduce supply-disruption exposure; quantify it in the Scenario Simulator (lead-time stress test).",
                "risk_assessment": "HIGH — Lead time instability creates bullwhip variance in downstream inventory replenishment.",
                "expected_impact": "Lower lead-time variance (sigma_L) reduces the safety stock SS = z*sqrt(L*sigma_d^2 + d^2*sigma_L^2) required for the same service level.",
                "alternative_actions": "1. Negotiate revised Service Level Agreement (SLA). 2. Split demand 60/40 with secondary qualified vendor.",
                "suggested_action": "Conduct vendor performance review and qualify a secondary backup supplier for high-volume SKUs.",
                "reasoning": (
                    f"{sup_name} exhibits an OTD rate of {otd:.0f}% and quality score of {qual:.0f}%, "
                    f"introducing supply volatility across {sku_cnt} SKUs."
                ),
                "status": "pending",
                "pipeline_stages": get_pipeline_stages(f"Supplier Risk Mitigation: {sup_name}", "supplier", p_details)
            })

    for r in recs:
        r["rec_key"] = recommendation_key(r)
        r["engine_version"] = ENGINE_VERSION
    recs.sort(key=lambda r: -r["impact_score"])
    return recs