"""
core/permissions.py
--------------------
Role model (2.6) - the single written definition of who may do what. The
endpoints enforce it with ``require_role`` / ``require_owner`` (api/auth.py);
``tests/test_roles_matrix.py`` checks each row against the live API.

Roles (stored value -> meaning):
  owner                Organisation owner. One per organisation. Everything an
                       administrator can do, plus billing/plan changes, data
                       export, organisation deletion, and ownership transfer.
                       Cannot be changed, reset or deactivated by administrators.
  admin                Administrator: users and invitations (not the owner),
                       organisation profile, master data, all inventory and purchasing work.
  inventory_manager    SKUs, imports, stock adjustments, planning parameters (service
                       level, holding rate, ...), optimisation, draft purchase orders,
                       goods receipts.
  procurement_manager  Purchase orders: draft, approve (not own drafts), order,
                       receive; reads everything.
  warehouse_manager    Stock adjustments and goods receipts; reads everything.
  executive            Analyst / read-only: every read and report, no writes.

Organisation-level reads (dashboards, SKUs, forecasts, reports, exports of
operational data) are open to every active member of the organisation.
Nothing here grants access to another organisation.
"""

ROLE_LABELS = {
    "owner": "Organisation owner",
    "admin": "Administrator",
    "inventory_manager": "Inventory manager",
    "procurement_manager": "Procurement manager",
    "warehouse_manager": "Warehouse manager",
    "executive": "Analyst (read-only)",
}

# action -> roles allowed (owner is implied wherever admin appears)
MATRIX = {
    "read_operational_data":   ["owner", "admin", "inventory_manager", "procurement_manager",
                                "warehouse_manager", "executive"],
    "list_members":            ["owner", "admin", "executive"],
    "manage_members":          ["owner", "admin"],
    "invite_members":          ["owner", "admin"],
    "change_org_profile":      ["owner", "admin"],
    "change_planning_parameters": ["owner", "admin", "inventory_manager"],
    "manage_master_data":      ["owner", "admin"],
    "create_or_edit_skus":     ["owner", "admin", "inventory_manager"],
    "import_inventory":        ["owner", "admin", "inventory_manager"],
    "preview_import":          ["owner", "admin", "inventory_manager", "procurement_manager",
                                "warehouse_manager", "executive"],     # validation only, nothing saved
    "adjust_stock":            ["owner", "admin", "inventory_manager", "warehouse_manager"],
    "run_optimisation":        ["owner", "admin", "inventory_manager"],
    "draft_purchase_orders":   ["owner", "admin", "inventory_manager", "procurement_manager"],
    "approve_purchase_orders": ["owner", "admin", "procurement_manager"],
    "execute_transfers":       ["owner", "admin", "inventory_manager", "warehouse_manager"],
    "receive_goods":           ["owner", "admin", "procurement_manager", "warehouse_manager",
                                "inventory_manager"],
    "run_what_if_simulation":  ["owner", "admin", "inventory_manager", "procurement_manager",
                                "warehouse_manager", "executive"],     # analysis only, nothing changes
    "save_scenario":           ["owner", "admin", "inventory_manager", "executive"],
    "view_billing":            ["owner", "admin"],
    "manage_billing":          ["owner"],
    "export_all_org_data":     ["owner"],
    "request_org_deletion":    ["owner"],
    "transfer_ownership":      ["owner"],
}
