"""
utils/demand_parser.py
-----------------------
Parsing and validation of DEMAND HISTORY files (one row per SKU per day).

Required columns (case/spacing-insensitive aliases accepted):
  sku       product code matching an existing active SKU of the organisation
  date      calendar date of the demand: ISO 2026-01-31 or day-first 31/01/2026,
            31-01-2026, 31.01.2026, 31-Jan-2026 (month-first US dates are rejected)
  quantity  units demanded (or sold) on that date, >= 0
Optional:
  channel   e.g. retail / wholesale (default "direct")

Validation (row numbers are spreadsheet rows, header = row 1):
  * missing/unparseable date, non-numeric or negative quantity, empty SKU -> row error
  * SKU not found in the organisation -> row error
  * the same (sku, date, channel) twice in the file -> row error (ambiguous)
  * dates in the future -> row error (would leak future information)
A file with ANY row error is rejected as a whole (nothing is written).
Existing records for the same (item, date, channel) are reported and, on
commit, replaced only if the caller passes on_conflict="replace"
(default "skip").
"""

from __future__ import annotations

import io
import re
from datetime import date
from typing import Dict, List, Tuple

import pandas as pd

ALIASES = {
    "sku": {"sku", "skucode", "itemcode", "productcode", "materialcode", "code"},
    "date": {"date", "perioddate", "day", "salesdate", "orderdate", "transactiondate"},
    "quantity": {"quantity", "qty", "demand", "unitssold", "sales", "salesqty", "quantitydemanded", "units"},
    "channel": {"channel", "saleschannel"},
}
MAX_ROW_ERRORS = 200


def _norm(c: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(c).lower())


def read_table(filename: str, content: bytes) -> pd.DataFrame:
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(io.BytesIO(content), dtype=str)
    if lower.endswith((".tsv", ".tab")):
        return pd.read_csv(io.BytesIO(content), sep="\t", dtype=str)
    return pd.read_csv(io.BytesIO(content), sep=None, engine="python", dtype=str)


# Accepted date formats, tried in order per cell: ISO 8601 first, then the
# day-first forms common in Pakistan. Month-first (US) dates are NOT accepted,
# so "03/04/2026" always means 3 April 2026 (never guessed per file).
DAY_FIRST_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%b-%Y", "%d %b %Y")


def parse_dates(col: pd.Series) -> pd.Series:
    """Explicit, warning-free date parsing (NaT where no accepted format matches)."""
    raw = col.astype("string").str.strip()
    out = pd.to_datetime(raw, errors="coerce", format="ISO8601")
    for fmt in DAY_FIRST_FORMATS:
        todo = out.isna() & raw.notna()
        if not todo.any():
            break
        out[todo] = pd.to_datetime(raw[todo], errors="coerce", format=fmt)
    return out


def parse_demand_file(filename: str, content: bytes, sku_to_item: Dict[str, int],
                      mapping: Dict[str, str] | None = None) -> Tuple[List[dict], dict]:
    try:
        df = read_table(filename, content)
    except Exception as e:  # malformed file
        raise ValueError(f"Could not read '{filename}': {e}")
    if df.empty:
        raise ValueError("The file contains no data rows.")
    cols = {}
    for c in df.columns:
        n = _norm(c)
        for field, names in ALIASES.items():
            if n in names and field not in cols:
                cols[field] = c
    for k, v in (mapping or {}).items():
        if v:
            if v not in df.columns:
                raise ValueError(f"Mapped column '{v}' for '{k}' is not in the file.")
            cols[k] = v
    missing = [f for f in ("sku", "date", "quantity") if f not in cols]
    if missing:
        return [], {"is_valid": False, "total_rows": len(df), "errors": [
            f"Missing required column(s): {', '.join(missing)}. Expected columns: sku, date, quantity "
            "(optional: channel)."], "row_errors": [], "column_mapping": cols,
            "detected_columns": list(df.columns)}

    today = date.today()
    row_errors: List[dict] = []
    records: List[dict] = []
    seen = set()

    def err(row, column, value, msg):
        if len(row_errors) < MAX_ROW_ERRORS:
            row_errors.append({"row": row, "column": column, "value": None if value is None else str(value)[:80],
                               "error": msg})

    dates = parse_dates(df[cols["date"]])
    for idx, raw in df.iterrows():
        row = int(idx) + 2
        sku = str(raw[cols["sku"]] if pd.notna(raw[cols["sku"]]) else "").strip()
        qty_raw = raw[cols["quantity"]]
        ch = str(raw[cols["channel"]]).strip() if "channel" in cols and pd.notna(raw[cols["channel"]]) else "direct"
        d = dates[idx]
        bad = False
        if not sku:
            err(row, cols["sku"], sku, "empty SKU"); bad = True
        elif sku not in sku_to_item:
            err(row, cols["sku"], sku, "SKU not found in your organisation (import the inventory first)"); bad = True
        if pd.isna(d):
            err(row, cols["date"], raw[cols["date"]], "invalid date"); bad = True
        elif d.date() > today:
            err(row, cols["date"], raw[cols["date"]], "date is in the future"); bad = True
        try:
            q = float(str(qty_raw).replace(",", "")) if pd.notna(qty_raw) else None
        except ValueError:
            q = None
        if q is None or q != q:
            err(row, cols["quantity"], qty_raw, "quantity is not a number"); bad = True
        elif q < 0:
            err(row, cols["quantity"], qty_raw, "negative quantity"); bad = True
        if bad:
            continue
        key = (sku, d.date(), ch[:50])
        if key in seen:
            err(row, cols["sku"], sku, f"duplicate row for {sku} on {d.date()} ({ch})")
            continue
        seen.add(key)
        records.append({"sku": sku, "item_id": sku_to_item[sku], "period_date": d.date(),
                        "quantity": q, "channel": ch[:50], "row": row})
    report = {
        "is_valid": not row_errors,
        "total_rows": len(df),
        "valid_rows": len(records) if not row_errors else 0,
        "errors": [f"{len(row_errors)} row(s) failed validation; nothing will be imported."] if row_errors else [],
        "row_errors": row_errors,
        "column_mapping": cols,
        "detected_columns": list(df.columns),
        "skus": len({r["sku"] for r in records}),
        "date_range": ({"from": min(r["period_date"] for r in records).isoformat(),
                        "to": max(r["period_date"] for r in records).isoformat()} if records else None),
    }
    return records, report
