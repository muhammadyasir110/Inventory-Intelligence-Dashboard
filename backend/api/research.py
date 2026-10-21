"""
api/research.py
----------------
Research-dataset validation endpoint. Validates an uploaded file against the
research schema WITHOUT storing it or changing any value, and returns the
full validation report (errors, warnings, unit-confirmation status,
fingerprint). Persisting research datasets into the optimisation pipeline is
deliberately not exposed until the research formulation is confirmed.
"""

import json
import os
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from api.auth import require_role
from core.uploads import read_upload_limited
from models.models import User
from research.dataset import validate, load_mapping, FIELDS, UNIT_OPTIONS, PROVENANCE_LABELS

router = APIRouter(prefix="/api/research", tags=["research"])

DEFAULT_MAPPING = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "research", "config", "research_30sku_mapping.json")


@router.get("/schema")
def research_schema(user: User = Depends(require_role(["admin", "inventory_manager"]))):
    return {"fields": FIELDS, "unit_options": UNIT_OPTIONS, "provenance_labels": PROVENANCE_LABELS,
            "default_mapping": load_mapping(DEFAULT_MAPPING)}


@router.post("/validate")
async def validate_research_dataset(
    file: UploadFile = File(...),
    mapping: Optional[str] = Form(None),
    sheet: Optional[str] = Form(None),
    user: User = Depends(require_role(["admin", "inventory_manager"])),
):
    raw = await read_upload_limited(file, allowed=(".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls"))
    try:
        m = json.loads(mapping) if mapping else DEFAULT_MAPPING
        res = validate(raw, file.filename, m, sheet)
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    rep = res.report()
    rep["preview"] = json.loads(res.data.head(50).to_json(orient="records"))
    return rep
