"""
api/upload.py
--------------
7-Step Data Onboarding Wizard & Validation API.
Handles CSV and Excel uploads, live column preview, pre-import validation,
and transactional database persistence.
"""

import hashlib
from research.dataset import PROVENANCE_LABELS
from core.uploads import read_upload_limited, parse_mapping, check_row_limit
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional, List, Dict, Any
import json
import pandas as pd

from database.db import get_db
from models.models import (
    User, UploadBatch, InventoryItem, Category, Supplier, Warehouse,
    AuditLog
)
from api.auth import get_current_user, require_role
from utils.file_parser import validate_and_parse_inventory
from optimization.stock_optimizer import economic_order_quantity

router = APIRouter(prefix="/api/upload", tags=["upload"])


@router.post("/preview")
async def preview_data_upload(
    file: UploadFile = File(...),
    custom_mapping: Optional[str] = Form(None),
    user: User = Depends(get_current_user)
):
    """Step 2 & 4: Uploads file in-memory and returns column mapping + validation report."""
    contents = await read_upload_limited(file)
    mapping_dict = parse_mapping(custom_mapping)

    try:
        df, report = validate_and_parse_inventory(file.filename, contents, mapping_dict)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    check_row_limit(len(df))

    preview_rows = df.head(10).to_dict(orient="records")
    for r in preview_rows:
        for k, v in r.items():
            if str(v) == "NaT" or str(v) == "nan":
                r[k] = None
            elif isinstance(v, datetime):
                r[k] = v.isoformat()

    return {
        "filename": file.filename,
        "report": report,
        "sample_data": preview_rows
    }


@router.post("")
async def upload_file_commit(
    file: UploadFile = File(...),
    custom_mapping: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    provenance: Optional[str] = Form("user_uploaded"),
    user: User = Depends(require_role(["admin", "inventory_manager"])),
    db: Session = Depends(get_db)
):
    """Step 6: Commits validated data into the organization's database."""
    contents = await read_upload_limited(file)
    mapping_dict = parse_mapping(custom_mapping)
    if provenance not in PROVENANCE_LABELS:
        raise HTTPException(status_code=400, detail=f"provenance must be one of {', '.join(PROVENANCE_LABELS)}")

    try:
        df, report = validate_and_parse_inventory(file.filename, contents, mapping_dict)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"File parsing error: {e}")
    check_row_limit(len(df))

    if not report["is_valid"]:
        raise HTTPException(status_code=400, detail="; ".join(report["errors"]))

    # Create UploadBatch
    batch = UploadBatch(
        org_id=user.org_id,
        filename=file.filename,
        row_count=len(df),
        status="processed" if len(report["warnings"]) == 0 else "warning",
        notes=notes,
        provenance=provenance,
        source_sha256=hashlib.sha256(contents).hexdigest(),
        derivations=report.get("derivations") or {},
    )
    db.add(batch)
    db.flush()

    # Pre-fetch existing warehouses, suppliers, categories for fast lookup
    existing_whs = {w.name: w for w in db.query(Warehouse).filter(Warehouse.org_id == user.org_id).all()}
    existing_sups = {s.name: s for s in db.query(Supplier).filter(Supplier.org_id == user.org_id).all()}
    existing_cats = {c.name: c for c in db.query(Category).filter(Category.org_id == user.org_id).all()}

    items_to_add = []
    for _, row in df.iterrows():
        wh_name = str(row["warehouse"]).strip()
        if wh_name not in existing_whs:
            new_wh = Warehouse(org_id=user.org_id, name=wh_name, capacity_units=100000.0)
            db.add(new_wh)
            db.flush()
            existing_whs[wh_name] = new_wh
        wh_obj = existing_whs[wh_name]

        sup_name = str(row["supplier"]).strip()
        if sup_name not in existing_sups:
            new_sup = Supplier(org_id=user.org_id, name=sup_name, on_time_delivery_rate=92.0, quality_score=94.0)
            db.add(new_sup)
            db.flush()
            existing_sups[sup_name] = new_sup
        sup_obj = existing_sups[sup_name]

        cat_name = str(row["category"]).strip()
        if cat_name not in existing_cats:
            new_cat = Category(org_id=user.org_id, name=cat_name)
            db.add(new_cat)
            db.flush()
            existing_cats[cat_name] = new_cat
        cat_obj = existing_cats[cat_name]

        daily_d = float(row.get("daily_avg_demand"))
        cost = float(row.get("unit_cost"))
        eoq = economic_order_quantity(daily_d * 365, cost)

        mfg_date = row.get("manufacturing_date")
        if pd.isna(mfg_date): mfg_date = None
        else: mfg_date = mfg_date.date() if hasattr(mfg_date, "date") else None

        exp_date = row.get("expiry_date")
        if pd.isna(exp_date): exp_date = None
        else: exp_date = exp_date.date() if hasattr(exp_date, "date") else None

        restock_date = row.get("last_restock_date")
        if pd.isna(restock_date): restock_date = None
        else: restock_date = restock_date.date() if hasattr(restock_date, "date") else None

        item = InventoryItem(
            org_id=user.org_id,
            batch_id=batch.id,
            category_id=cat_obj.id,
            supplier_id=sup_obj.id,
            warehouse_id=wh_obj.id,
            sku=str(row["sku"]).strip(),
            product_name=str(row["product_name"]).strip(),
            category=cat_name,
            batch_number=str(row.get("batch_number") or ""),
            quantity_on_hand=float(row.get("quantity_on_hand") or 0.0),
            reorder_point=float(row.get("reorder_point") or 0.0),
            safety_stock=float(row.get("safety_stock") or 0.0),
            max_stock_level=float(row.get("max_stock_level") or 0.0),
            eoq=eoq,
            unit_cost=cost,
            unit_price=float(row.get("unit_price")),
            lead_time_days=float(row.get("lead_time_days")),
            daily_avg_demand=daily_d,
            manufacturing_date=mfg_date,
            expiry_date=exp_date,
            last_restock_date=restock_date,
            is_active=True
        )
        items_to_add.append(item)

    db.add_all(items_to_add)

    audit = AuditLog(
        org_id=user.org_id,
        user_id=user.id,
        username=user.username,
        action="IMPORT_INVENTORY_DATA",
        entity_type="upload_batch",
        entity_id=str(batch.id),
        details={"filename": file.filename, "rows": len(items_to_add), "provenance": provenance,
                 "source_sha256": batch.source_sha256, "derived_fields": sorted(batch.derivations.keys())}
    )
    db.add(audit)
    db.commit()

    return {
        "status": "success",
        "batch_id": batch.id,
        "filename": batch.filename,
        "rows_imported": len(items_to_add),
        "provenance": provenance,
        "source_sha256": batch.source_sha256,
        "validation_report": report
    }