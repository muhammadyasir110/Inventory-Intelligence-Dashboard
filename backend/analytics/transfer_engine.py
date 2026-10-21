"""
analytics/transfer_engine.py
-----------------------------
Inter-Warehouse Balancing & Inventory Transfer Recommendation Engine.
Analyzes spatial distribution of inventory to resolve localized shortages
using localized surpluses before generating external purchase orders.
"""

from typing import List, Dict, Any


def generate_transfer_recommendations(items: List[Dict[str, Any]], warehouses: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Identifies surplus nodes and deficit nodes for each SKU across warehouses
    and generates concrete, explainable inter-warehouse transfer orders.
    """
    if not items:
        return []

    wh_capacity_map = {w.get("warehouse", w.get("name")): w.get("capacity_units", 100000.0) for w in (warehouses or [])}

    # Group items by SKU
    sku_groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        sku = it.get("sku", "UNKNOWN")
        sku_groups.setdefault(sku, []).append(it)

    transfers = []
    transfer_counter = 1

    for sku, sku_items in sku_groups.items():
        if len(sku_items) < 2:
            # Single warehouse instance, inter-warehouse transfer not applicable
            # (unless simulated across virtual nodes)
            continue

        surplus_nodes = []
        deficit_nodes = []

        for node in sku_items:
            qty = float(node.get("quantity_on_hand", 0) or 0)
            rop = float(node.get("reorder_point", 0) or 0)
            safety = float(node.get("safety_stock", 0) or 0)
            max_lvl = float(node.get("max_stock_level", 0) or (rop * 2))
            status = node.get("status", "normal")

            if qty > max_lvl or status == "overstock":
                excess = qty - max_lvl
                if excess > 0:
                    surplus_nodes.append({"item": node, "excess": excess})
            elif qty <= rop or status in ("low", "critical"):
                needed = max_lvl - qty
                deficit_nodes.append({"item": node, "needed": needed, "urgency": "critical" if qty <= safety else "high"})

        # Match surplus with deficit
        for s in surplus_nodes:
            s_item = s["item"]
            s_excess = s["excess"]
            s_wh = s_item.get("warehouse", "Main Warehouse")

            for d in deficit_nodes:
                d_item = d["item"]
                d_needed = d["needed"]
                d_wh = d_item.get("warehouse", "Secondary Hub")

                if s_wh == d_wh or s_excess <= 0:
                    continue

                transfer_qty = min(s_excess, d_needed)
                if transfer_qty >= 1.0:
                    unit_cost = float(s_item.get("unit_cost", 0) or 0)
                    transfer_val = round(transfer_qty * unit_cost, 2)
                    
                    transfers.append({
                        "transfer_id": f"TRF-{transfer_counter:04d}",
                        "sku": sku,
                        "product_name": s_item.get("product_name", sku),
                        "source_warehouse": s_wh,
                        "destination_warehouse": d_wh,
                        "recommended_quantity": round(transfer_qty, 0),
                        "unit_cost": unit_cost,
                        "transfer_value": transfer_val,
                        "urgency": d["urgency"],
                        "reasoning": (
                            f"Source warehouse '{s_wh}' has {s_excess:.0f} excess units above max stock, "
                            f"while destination warehouse '{d_wh}' is facing a stockout deficit of {d_needed:.0f} units "
                            f"(current stock {d_item.get('quantity_on_hand', 0):.0f} vs ROP {d_item.get('reorder_point', 0):.0f})."
                        ),
                        "expected_saving": f"Avoids emergency external PO cost and liquidates  of idle holding capital.",
                        "status": "pending_approval"
                    })
                    transfer_counter += 1
                    s_excess -= transfer_qty
                    d["needed"] -= transfer_qty

    return sorted(transfers, key=lambda x: (x["urgency"] != "critical", -x["transfer_value"]))