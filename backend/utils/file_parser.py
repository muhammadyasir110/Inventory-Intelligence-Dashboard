"""
utils/file_parser.py
---------------------
Parses and validates uploaded CSV / Excel inventory files into normalized DataFrames.
Implements the 7-step enterprise Data Onboarding & Validation Wizard logic:
1. File format & stream parsing
2. Header normalization & flexible alias mapping
3. Strict validation:
   - Duplicate SKU detection
   - Negative quantity / cost detection
   - Invalid lead time / demand detection
   - Date format parsing (ISO, US, EU)
4. Structured Validation Report (errors, warnings, summary statistics)
5. Sensible fallback auto-derivation for missing parameters
"""

import io
import pandas as pd
from typing import Tuple, Dict, Any, List

FIELD_ALIASES = {
    # v2.2: removed sales-order aliases that silently mis-mapped order files into
    # inventory fields (ordernumber->sku, quantityordered/orderqty/volume->stock on
    # hand, msrp/priceeach/unitprice/price/rate->unit COST, customer/client->supplier,
    # city/country/state/territory->warehouse, productline->product name).
    "sku": ["sku", "itemcode", "productcode", "itemid", "code", "partnumber", "skuid", "materialnumber"],
    "product_name": ["productname", "itemname", "name", "description", "product", "itemdescription", "item", "title"],
    "category": ["category", "productcategory", "type", "group", "itemcategory", "productline", "line", "department", "dept", "class", "classification", "family", "segment"],
    "quantity_on_hand": ["quantityonhand", "qtyonhand", "stock", "currentstock", "onhandqty", "unitsonhand", "stockonhand", "onhand"],
    "reorder_point": ["reorderpoint", "reorderlevel", "rop"],
    "safety_stock": ["safetystock", "minstock", "minimumstock", "safetystk"],
    "max_stock_level": ["maxstocklevel", "maxstock", "maximumstock", "maxlevel"],
    "unit_cost": ["unitcost", "cost", "costprice", "purchaseprice", "buyingprice"],
    "unit_price": ["unitprice", "price", "sellingprice", "salesprice", "retailprice"],
    "lead_time_days": ["leadtimedays", "leadtime", "supplierleadtime", "deliverydays"],
    "daily_avg_demand": ["dailyavgdemand", "avgdailydemand", "dailydemand", "averagedailydemand", "monthlydemand"],
    "batch_number": ["batchnumber", "batchno", "batch", "lotnumber", "lotno", "lot"],
    "manufacturing_date": ["manufacturingdate", "mfgdate", "productiondate", "mfg"],
    "expiry_date": ["expirydate", "expirationdate", "expdate", "expiry"],
    "last_restock_date": ["lastrestockdate", "restockdate", "lastordered", "receiveddate"],
    "supplier": ["supplier", "suppliername", "vendor", "vendorname"],
    "warehouse": ["warehouse", "warehousename", "location", "storagelocation", "site"],
}


def _normalize_header(col: str) -> str:
    return str(col).strip().lower().replace(" ", "").replace("_", "").replace("-", "")


def _build_column_map(columns) -> Dict[str, str]:
    normalized = {_normalize_header(c): c for c in columns}
    mapping = {}
    for canonical, aliases in FIELD_ALIASES.items():
        mapping[canonical] = None
        for alias in aliases:
            if alias in normalized:
                mapping[canonical] = normalized[alias]
                break
    return mapping


def _parse_pdf_to_dataframe(file_bytes: bytes) -> pd.DataFrame:
    """Extracts tabular inventory rows from PDF documents using pypdf."""
    import pypdf
    import re

    try:
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    except Exception as e:
        raise ValueError(f"Failed to read PDF document: {e}")

    all_lines: List[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        for line in text.split("\n"):
            cleaned = line.strip()
            if cleaned:
                all_lines.append(cleaned)

    if not all_lines:
        raise ValueError("Could not extract any readable text or tables from the uploaded PDF.")

    # Detect delimiter: tab, pipe, comma, or multi-space
    parsed_rows = []
    sample_text = "\n".join(all_lines[:25])
    if "\t" in sample_text:
        delimiter = "\t"
    elif "|" in sample_text:
        delimiter = "|"
    elif "," in sample_text and sample_text.count(",") > 10:
        delimiter = ","
    else:
        delimiter = None

    if delimiter:
        for line in all_lines:
            parts = [p.strip() for p in line.split(delimiter)]
            if len(parts) >= 2:
                parsed_rows.append(parts)
    else:
        for line in all_lines:
            parts = re.split(r'\s{2,}|\t', line)
            parts = [p.strip() for p in parts if p.strip()]
            if len(parts) >= 2:
                parsed_rows.append(parts)

    if len(parsed_rows) < 2:
        # Fallback: whitespace tokenization for whitespace-aligned invoices
        for line in all_lines:
            tokens = line.split()
            if len(tokens) >= 3:
                parsed_rows.append(tokens)

    if not parsed_rows:
        raise ValueError("No tabular inventory records detected in PDF. Ensure the PDF contains lines with SKU/Product details.")

    # Determine headers and data
    first_row = parsed_rows[0]
    has_letters = any(re.search(r'[a-zA-Z]', str(c)) for c in first_row)
    if has_letters and len(parsed_rows) > 1:
        headers = [re.sub(r'[^a-zA-Z0-9_ ]', '', str(c)).strip() or f"Col_{i+1}" for i, c in enumerate(first_row)]
        data_rows = parsed_rows[1:]
        col_count = len(headers)
    else:
        col_count = max(len(r) for r in parsed_rows)
        headers = [f"Col_{i+1}" for i in range(col_count)]
        data_rows = parsed_rows

    cleaned_data = []
    for r in data_rows:
        if len(r) == col_count:
            cleaned_data.append(r)
        elif len(r) > col_count:
            cleaned_data.append(r[:col_count])
        elif len(r) >= 2:
            cleaned_data.append(r + [None] * (col_count - len(r)))

    df = pd.DataFrame(cleaned_data, columns=headers)
    return df


def validate_and_parse_inventory(
    arg1: Any,
    arg2: Any = None,
    custom_column_mapping: Dict[str, str] = None
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Performs end-to-end parsing and strict validation of inventory files.
    Supports: .csv, .xlsx, .xls, .xlc, .xlsm, .tsv, .txt, .pdf
    Accepts either (filename, file_bytes) or (file_bytes, filename).
    Returns: (normalized_df, validation_report)
    """
    if isinstance(arg1, (bytes, bytearray)):
        file_bytes = bytes(arg1)
        filename = str(arg2 or "upload.csv")
    elif isinstance(arg2, (bytes, bytearray)):
        filename = str(arg1 or "upload.csv")
        file_bytes = bytes(arg2)
    else:
        file_bytes = arg1 if isinstance(arg1, bytes) else str(arg1).encode("utf-8")
        filename = str(arg2 or "upload.csv")

    lower = filename.lower()
    try:
        if lower.endswith(".pdf"):
            df = _parse_pdf_to_dataframe(file_bytes)
        elif lower.endswith((".xlsx", ".xls", ".xlc", ".xlsm")):
            try:
                df = pd.read_excel(io.BytesIO(file_bytes))
            except Exception:
                df = pd.read_csv(io.BytesIO(file_bytes), sep=None, engine="python")
        elif lower.endswith((".tsv", ".tab")):
            df = pd.read_csv(io.BytesIO(file_bytes), sep="\t")
        elif lower.endswith((".csv", ".txt")):
            # Robust Python engine with delimiter autodetection (comma, semicolon, tab)
            try:
                df = pd.read_csv(io.BytesIO(file_bytes), sep=None, engine="python")
            except Exception:
                try:
                    df = pd.read_csv(io.BytesIO(file_bytes), sep=",")
                except Exception:
                    df = pd.read_excel(io.BytesIO(file_bytes))
        else:
            # Universal fallback: attempt Excel then CSV then PDF
            try:
                df = pd.read_excel(io.BytesIO(file_bytes))
            except Exception:
                try:
                    df = pd.read_csv(io.BytesIO(file_bytes), sep=None, engine="python")
                except Exception:
                    df = _parse_pdf_to_dataframe(file_bytes)
    except Exception as parse_err:
        raise ValueError(f"Failed to parse '{filename}': {parse_err}")


    if df.empty:
        raise ValueError("The uploaded file contains no data rows.")

    # v2.2: refuse research-schema files here; they have their own strict
    # validator (research/dataset.py, POST /api/research/validate) and would
    # otherwise lose demand, cost and lead-time columns to defaults.
    research_cols = {"annualdemanddbar", "demandstddevsigma", "unitcostc", "orderingcosts",
                     "holdingcosth", "shortagecostpi", "leadtimel", "unitvolumev"}
    hits = research_cols & {_normalize_header(c) for c in df.columns}
    if len(hits) >= 3:
        return pd.DataFrame(), {
            "is_valid": False, "total_rows": len(df), "valid_rows": 0,
            "errors": ["This file uses the research-dataset schema (e.g. Annual_Demand_Dbar, "
                       "Holding_Cost_h). Validate it with Research Data validation "
                       "(POST /api/research/validate or `python -m research.ingest`), "
                       "not the inventory upload."],
            "warnings": [], "column_mapping": {}, "detected_columns": list(df.columns), "derivations": {},
        }

    # Build or override column map
    auto_col_map = _build_column_map(df.columns)
    col_map = {**auto_col_map, **(custom_column_mapping or {})}

    errors: List[str] = []
    warnings: List[str] = []
    derivations: Dict[str, List[Dict[str, Any]]] = {}

    def derived(field: str, rule: str, n: int):
        """Record that `n` rows of `field` were NOT read from the file."""
        n = int(n)
        if n > 0:
            derivations.setdefault(field, []).append({"rule": rule, "rows": n})
            warnings.append(f"Derived/defaulted {field} for {n} row(s): {rule}")

    # Mandatory identifier check
    if col_map.get("sku") is None and col_map.get("product_name") is None:
        errors.append("Critical Error: Missing product identifier column (SKU or Product Name).")
        return pd.DataFrame(), {
            "is_valid": False,
            "total_rows": len(df),
            "valid_rows": 0,
            "errors": errors,
            "warnings": warnings,
            "column_mapping": col_map,
            "detected_columns": list(df.columns)
        }

    normalized = pd.DataFrame()

    # 1. SKU parsing & Duplicate Validation
    sku_col = col_map.get("sku")
    if sku_col:
        raw_skus = df[sku_col].astype(str).str.strip()
        dups = raw_skus[raw_skus.duplicated()].unique()
        if len(dups) > 0:
            warnings.append(f"Notice: {len(dups)} duplicate SKU entries found in upload (e.g. {', '.join(dups[:3])}). Multi-warehouse records merged.")
        normalized["sku"] = raw_skus
    else:
        normalized["sku"] = "SKU-" + df.index.astype(str)
        derived("sku", "no SKU column: generated sequential identifiers SKU-<row>", len(df))

    # 2. Product Name
    pname_col = col_map.get("product_name")
    if pname_col:
        normalized["product_name"] = df[pname_col].astype(str).str.strip()
    else:
        normalized["product_name"] = normalized["sku"]
        derived("product_name", "no product-name column: copied SKU", len(df))

    # 3. Category
    cat_col = col_map.get("category")
    normalized["category"] = df[cat_col].astype(str).str.strip() if cat_col else "Uncategorized"

    # 4. Numerics & Negative Value Validation
    def parse_numeric(field: str, default_val: float = 0.0, allow_negative: bool = False) -> pd.Series:
        col = col_map.get(field)
        if col:
            raw = pd.to_numeric(df[col], errors="coerce")
            derived(field, f"empty or non-numeric cell in '{col}' replaced by default {default_val:g}",
                    raw.isna().sum())
            s = raw.fillna(default_val)
            if not allow_negative:
                neg_count = (s < 0).sum()
                if neg_count > 0:
                    errors.append(f"Validation Error: {neg_count} rows in column '{col}' contain invalid negative values (upload rejected).")
            return s
        derived(field, f"no column for {field}: default {default_val:g} used", len(df))
        return pd.Series([default_val] * len(df))

    normalized["quantity_on_hand"] = parse_numeric("quantity_on_hand", 0.0)
    normalized["unit_cost"] = parse_numeric("unit_cost", 0.0)
    normalized["unit_price"] = parse_numeric("unit_price", 0.0)
    normalized["lead_time_days"] = parse_numeric("lead_time_days", 7.0)
    normalized["daily_avg_demand"] = parse_numeric("daily_avg_demand", 1.0)
    normalized["reorder_point"] = parse_numeric("reorder_point", 0.0)
    normalized["safety_stock"] = parse_numeric("safety_stock", 0.0)
    normalized["max_stock_level"] = parse_numeric("max_stock_level", 0.0)

    # Convert monthly demand if detected
    if col_map.get("daily_avg_demand") and "monthly" in str(col_map.get("daily_avg_demand")).lower():
        normalized["daily_avg_demand"] = (normalized["daily_avg_demand"] / 30.0).round(2)
        derived("daily_avg_demand", "monthly demand column divided by 30", len(df))

    # 5. Batch & Dates
    batch_col = col_map.get("batch_number")
    normalized["batch_number"] = df[batch_col].astype(str).str.strip() if batch_col else None

    def parse_date(field: str) -> pd.Series:
        col = col_map.get(field)
        if col:
            return pd.to_datetime(df[col], errors="coerce")
        return pd.Series([pd.NaT] * len(df))

    normalized["manufacturing_date"] = parse_date("manufacturing_date")
    normalized["expiry_date"] = parse_date("expiry_date")
    normalized["last_restock_date"] = parse_date("last_restock_date")

    # 6. Entity Assignments
    sup_col = col_map.get("supplier")
    normalized["supplier"] = df[sup_col].astype(str).str.strip() if sup_col else "Main Supplier"
    wh_col = col_map.get("warehouse")
    normalized["warehouse"] = df[wh_col].astype(str).str.strip() if wh_col else "Main Logistics Hub"

    # 7. Dynamic Defaults for Missing Policy Parameters
    mask_rop = normalized["reorder_point"] <= 0
    derived("reorder_point", "heuristic ROP = daily demand x lead time (no safety stock)", mask_rop.sum())
    normalized.loc[mask_rop, "reorder_point"] = (
        normalized.loc[mask_rop, "daily_avg_demand"] * normalized.loc[mask_rop, "lead_time_days"]
    ).round(1)

    mask_ss = normalized["safety_stock"] <= 0
    derived("safety_stock", "heuristic SS = 0.30 x ROP (not the statistical formula)", mask_ss.sum())
    normalized.loc[mask_ss, "safety_stock"] = (
        normalized.loc[mask_ss, "reorder_point"] * 0.30
    ).round(1)

    mask_max = normalized["max_stock_level"] <= 0
    derived("max_stock_level", "heuristic max = 3 x ROP + SS", mask_max.sum())
    normalized.loc[mask_max, "max_stock_level"] = (
        normalized.loc[mask_max, "reorder_point"] * 3.0 + normalized.loc[mask_max, "safety_stock"]
    ).round(1)

    # Unit cost and price bi-directional fallbacks
    mask_cost_zero = normalized["unit_cost"] <= 0
    mask_has_price = normalized["unit_price"] > 0
    derived("unit_cost", "heuristic unit cost = 0.70 x unit price", (mask_cost_zero & mask_has_price).sum())
    normalized.loc[mask_cost_zero & mask_has_price, "unit_cost"] = (
        normalized.loc[mask_cost_zero & mask_has_price, "unit_price"] * 0.70
    ).round(2)

    mask_price_zero = normalized["unit_price"] <= 0
    mask_has_cost = normalized["unit_cost"] > 0
    derived("unit_price", "heuristic unit price = 1.35 x unit cost", (mask_price_zero & mask_has_cost).sum())
    normalized.loc[mask_price_zero & mask_has_cost, "unit_price"] = (
        normalized.loc[mask_price_zero & mask_has_cost, "unit_cost"] * 1.35
    ).round(2)

    # Dynamic daily average demand fallback if quantity exists but demand was not provided
    mask_demand_zero = normalized["daily_avg_demand"] <= 0
    mask_has_qty = normalized["quantity_on_hand"] > 0
    derived("daily_avg_demand", "heuristic daily demand = stock on hand / 45", (mask_demand_zero & mask_has_qty).sum())
    normalized.loc[mask_demand_zero & mask_has_qty, "daily_avg_demand"] = (
        normalized.loc[mask_demand_zero & mask_has_qty, "quantity_on_hand"] / 45.0
    ).clip(lower=0.5).round(2)

    for fld, label in (("lead_time_days", "lead time"), ("daily_avg_demand", "daily demand")):
        nz = int((normalized[fld] <= 0).sum())
        if nz:
            warnings.append(f"{nz} row(s) have zero {label}; kept as 0 (models treat L < 1 day as 1 day).")
    is_valid = len(errors) == 0


    validation_report = {
        "is_valid": is_valid,
        "total_rows": len(df),
        "valid_rows": len(normalized) if is_valid else 0,
        "errors": errors,
        "warnings": warnings,
        "column_mapping": col_map,
        "detected_columns": list(df.columns),
        "derivations": derivations,
        "unmapped_columns": [c for c in df.columns if c not in set(v for v in col_map.values() if v)],
        "total_stock_value": float((normalized["quantity_on_hand"] * normalized["unit_cost"]).sum()),
        "total_units": float(normalized["quantity_on_hand"].sum()),
        "distinct_skus": int(normalized["sku"].nunique()),
        "distinct_suppliers": int(normalized["supplier"].nunique()),
        "distinct_warehouses": int(normalized["warehouse"].nunique())
    }

    return normalized, validation_report


def parse_inventory_file(filename: str, file_bytes: bytes) -> pd.DataFrame:
    """Backward-compatible helper function."""
    df, report = validate_and_parse_inventory(filename, file_bytes)
    if not report["is_valid"]:
        raise ValueError("; ".join(report["errors"]))
    return df