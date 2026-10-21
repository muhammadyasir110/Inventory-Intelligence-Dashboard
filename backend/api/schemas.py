"""
api/schemas.py
----------------
Pydantic models used for API response documentation/validation.
Kept intentionally lightweight -- most dashboard payloads are dynamic
(shape depends on uploaded data), so we validate the stable parts only.
"""

from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class UploadResponse(BaseModel):
    batch_id: int
    filename: str
    row_count: int
    uploaded_at: datetime
    message: str


class BatchSummary(BaseModel):
    id: int
    filename: str
    row_count: int
    uploaded_at: datetime
    status: str

    class Config:
        from_attributes = True


class KPIResponse(BaseModel):
    total_skus: int
    total_stock_units: float
    total_stock_value: float
    low_stock_count: int
    critical_count: int
    overstock_count: int
    expiring_count: int
    healthy_count: int
    avg_days_of_supply: float
    inventory_turnover_estimate: float
    stockout_risk_pct: float
