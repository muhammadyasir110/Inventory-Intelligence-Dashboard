"""
research/dataset.py
--------------------
Strict ingestion and validation of the research dataset.

Principles (MPhil data-integrity rules):
  * The source file is never modified; a byte-identical copy is archived.
  * No value is imputed, clipped, rounded, rescaled or deleted. Cells that
    cannot be parsed as numbers are reported as errors and left empty.
  * The only transformation is TYPE PARSING (text cell -> number), recorded
    in `rules_applied`. Column renaming to canonical names is recorded too.
  * Units are taken only from the mapping file. A unit set to null is
    "unconfirmed": validation still runs, but `units_confirmed` is false and
    downstream unit-dependent calculations must refuse to run.
  * Every artifact carries a provenance label: research | synthetic |
    demonstration | user_uploaded.

Canonical fields
  sku_id, segment, annual_demand (D, units/year), demand_std (sigma, units per
  `demand_std_period`), unit_cost (c), ordering_cost (s, per order),
  holding_cost (h, see `holding_cost_basis`), shortage_cost (pi, see
  `shortage_cost_basis`), lead_time (L, in `lead_time` units), unit_volume (v).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

SCHEMA_VERSION = "research-dataset/1.0"
PROVENANCE_LABELS = ("research", "synthetic", "demonstration", "user_uploaded")

# field -> (required, kind, rule)
FIELDS: Dict[str, Dict[str, Any]] = {
    "sku_id":        {"required": True,  "kind": "id"},
    "segment":       {"required": False, "kind": "text"},
    "annual_demand": {"required": True,  "kind": "num", "min_exclusive": 0.0},
    "demand_std":    {"required": True,  "kind": "num", "min_inclusive": 0.0},
    "unit_cost":     {"required": True,  "kind": "num", "min_exclusive": 0.0},
    "ordering_cost": {"required": True,  "kind": "num", "min_exclusive": 0.0},
    "holding_cost":  {"required": True,  "kind": "num", "min_exclusive": 0.0},
    "shortage_cost": {"required": True,  "kind": "num", "min_inclusive": 0.0},
    "lead_time":     {"required": True,  "kind": "num", "min_exclusive": 0.0},
    "unit_volume":   {"required": True,  "kind": "num", "min_exclusive": 0.0},
}

UNIT_OPTIONS = {
    "currency": None,                                   # free text, e.g. "PKR"
    "annual_demand": ("units_per_year",),
    "demand_std_period": ("year", "month", "week", "day"),
    "holding_cost_basis": ("per_unit_per_year", "fraction_of_unit_cost_per_year"),
    "shortage_cost_basis": ("per_unit_short", "per_unit_short_per_year", "per_stockout_occasion"),
    "lead_time": ("days", "weeks", "months", "years"),
    "unit_volume": None,                                # free text, e.g. "m3"
}


@dataclass
class Issue:
    level: str          # error | warning | info
    rule: str
    message: str
    rows: List[int] = field(default_factory=list)      # 1-based data row numbers
    column: Optional[str] = None

    def as_dict(self):
        return {"level": self.level, "rule": self.rule, "message": self.message,
                "column": self.column, "rows": self.rows[:50],
                "n_rows": len(self.rows)}


@dataclass
class ValidationResult:
    issues: List[Issue]
    data: pd.DataFrame                  # canonical columns, parsed values (NaN where unparseable)
    mapping: Dict[str, Any]
    source_name: str
    source_sha256: str
    rules_applied: List[str]

    @property
    def errors(self):
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self):
        return [i for i in self.issues if i.level == "warning"]

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def units_confirmed(self) -> bool:
        u = self.mapping.get("units", {})
        return all(u.get(k) not in (None, "") for k in UNIT_OPTIONS)

    def report(self) -> Dict[str, Any]:
        u = self.mapping.get("units", {})
        return {
            "schema_version": SCHEMA_VERSION,
            "source_file": self.source_name,
            "source_sha256": self.source_sha256,
            "dataset_label": self.mapping.get("dataset_label"),
            "provenance": self.mapping.get("provenance"),
            "rows": int(len(self.data)),
            "expected_rows": self.mapping.get("expected_rows"),
            "is_valid": self.is_valid,
            "units_confirmed": self.units_confirmed,
            "unconfirmed_units": [k for k in UNIT_OPTIONS if u.get(k) in (None, "")],
            "n_errors": len(self.errors),
            "n_warnings": len(self.warnings),
            "issues": [i.as_dict() for i in self.issues],
            "rules_applied": self.rules_applied,
            "column_mapping": self.mapping.get("columns"),
            "units": u,
        }


# --------------------------------------------------------------------------- #
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_mapping(path_or_dict) -> Dict[str, Any]:
    if isinstance(path_or_dict, dict):
        m = path_or_dict
    else:
        with open(path_or_dict, encoding="utf-8") as fh:
            m = json.load(fh)
    m = json.loads(json.dumps(m))  # deep copy
    m.setdefault("columns", {})
    m.setdefault("units", {})
    if m.get("provenance") not in PROVENANCE_LABELS:
        raise ValueError(f"mapping.provenance must be one of {PROVENANCE_LABELS}")
    for k, opts in UNIT_OPTIONS.items():
        v = m["units"].get(k)
        if v not in (None, "") and opts is not None and v not in opts:
            raise ValueError(f"units.{k}={v!r} is not one of {opts}")
    for f in FIELDS:
        if FIELDS[f]["required"] and not m["columns"].get(f):
            raise ValueError(f"mapping.columns.{f} is required")
    return m


def read_table(name: str, raw: bytes, sheet: Optional[str] = None) -> pd.DataFrame:
    """Reads every cell as TEXT so nothing is converted before validation."""
    low = name.lower()
    if low.endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(io.BytesIO(raw), sheet_name=sheet or 0, dtype=str, keep_default_na=False)
    if low.endswith((".csv", ".txt", ".tsv")):
        text = raw.decode("utf-8-sig")
        sep = "\t" if low.endswith(".tsv") else ","
        return pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False)
    raise ValueError("Research dataset must be .csv, .tsv, .txt, .xlsx, .xlsm or .xls")


def validate(raw: bytes, source_name: str, mapping, sheet: Optional[str] = None) -> ValidationResult:
    m = load_mapping(mapping)
    issues: List[Issue] = []
    rules = ["read all cells as text (no automatic type inference)"]
    df = read_table(source_name, raw, sheet)
    cols = m["columns"]

    # Columns
    missing = [f"{f} -> '{c}'" for f, c in cols.items() if c and c not in df.columns]
    if missing:
        issues.append(Issue("error", "missing_column", "Mapped column(s) not found in file: " + ", ".join(missing)))
        return ValidationResult(issues, pd.DataFrame(), m, source_name, sha256_bytes(raw), rules)
    extra = [c for c in df.columns if c not in cols.values()]
    if extra:
        issues.append(Issue("info", "unmapped_columns", "Columns present but not used: " + ", ".join(map(str, extra))))

    out = pd.DataFrame(index=df.index)
    rows = lambda mask: [int(i) + 1 for i in df.index[mask]]  # noqa: E731

    for f, spec in FIELDS.items():
        c = cols.get(f)
        if not c:
            continue
        s = df[c].astype(str)
        blank = s.str.strip() == ""
        if blank.any():
            issues.append(Issue("error" if spec["required"] else "warning", "missing_value",
                                f"{blank.sum()} empty cell(s)", rows(blank), c))
        if s.str.strip().ne(s).any() and spec["kind"] in ("id", "text"):
            issues.append(Issue("warning", "surrounding_whitespace",
                                "Leading/trailing spaces present (kept as-is)", rows(s.str.strip().ne(s)), c))
        if spec["kind"] == "num":
            num = pd.to_numeric(s.str.strip().where(~blank), errors="coerce")
            bad = num.isna() & ~blank
            if bad.any():
                issues.append(Issue("error", "not_a_number", f"{bad.sum()} non-numeric cell(s): "
                                    + ", ".join(repr(x) for x in s[bad].head(5)), rows(bad), c))
            if "min_exclusive" in spec:
                le = num <= spec["min_exclusive"]
                if le.any():
                    issues.append(Issue("error", "non_positive", f"{le.sum()} value(s) <= {spec['min_exclusive']:g}", rows(le), c))
            if "min_inclusive" in spec:
                lt = num < spec["min_inclusive"]
                if lt.any():
                    issues.append(Issue("error", "negative", f"{lt.sum()} negative value(s)", rows(lt), c))
            out[f] = num
        else:
            out[f] = s
    rules.append("parsed numeric columns with pandas.to_numeric; unparseable cells reported as errors and left empty")
    rules.append("renamed columns to canonical names via mapping (values unchanged)")

    # Duplicates
    if "sku_id" in out:
        dup = out["sku_id"].duplicated(keep=False) & (out["sku_id"].str.strip() != "")
        if dup.any():
            issues.append(Issue("error", "duplicate_sku", "Duplicate SKU_ID values: "
                                + ", ".join(sorted(set(out.loc[dup, "sku_id"]))[:10]), rows(dup), cols["sku_id"]))

    # Plausibility warnings (never block, never alter)
    u = m["units"]
    if {"demand_std", "annual_demand"} <= set(out):
        zero = out["demand_std"] == 0
        if zero.any():
            issues.append(Issue("warning", "zero_std", "Demand standard deviation is 0 (deterministic demand)",
                                rows(zero), cols["demand_std"]))
        per = {"year": 1, "month": 12, "week": 52, "day": 365}.get(u.get("demand_std_period"))
        if per:
            cv = out["demand_std"] / (out["annual_demand"] / per)
            hi = cv > 1.0
            if hi.any():
                issues.append(Issue("warning", "high_cv", "Coefficient of variation > 1 per stated period "
                                    "(normal-demand approximation questionable)", rows(hi), cols["demand_std"]))
    if {"holding_cost", "unit_cost"} <= set(out) and u.get("holding_cost_basis") == "per_unit_per_year":
        r = out["holding_cost"] / out["unit_cost"]
        odd = (r > 1.0) | (r < 0.01)
        if odd.any():
            issues.append(Issue("warning", "holding_ratio", "Annual holding cost outside 1%-100% of unit cost",
                                rows(odd), cols["holding_cost"]))
    if {"holding_cost"} <= set(out) and u.get("holding_cost_basis") == "fraction_of_unit_cost_per_year":
        odd = (out["holding_cost"] > 1.0)
        if odd.any():
            issues.append(Issue("warning", "holding_rate_gt_1", "Holding rate > 100% per year", rows(odd), cols["holding_cost"]))
    if "lead_time" in out:
        days = {"days": 1, "weeks": 7, "months": 30.4375, "years": 365.25}.get(u.get("lead_time"))
        if days:
            long = out["lead_time"] * days > 365
            if long.any():
                issues.append(Issue("warning", "long_lead_time", "Lead time longer than one year", rows(long), cols["lead_time"]))
    if {"shortage_cost", "unit_cost"} <= set(out):
        z = out["shortage_cost"] == 0
        if z.any():
            issues.append(Issue("warning", "zero_shortage_cost", "Shortage cost is 0 (stockouts are free in the model)",
                                rows(z), cols["shortage_cost"]))
    exp = m.get("expected_rows")
    if exp and len(out) != exp:
        issues.append(Issue("warning", "row_count", f"File has {len(out)} rows; mapping expects {exp}"))
    unconf = [k for k in UNIT_OPTIONS if u.get(k) in (None, "")]
    if unconf:
        issues.append(Issue("warning", "units_unconfirmed",
                            "Units not confirmed in the mapping: " + ", ".join(unconf)
                            + ". Unit-dependent calculations are blocked until they are set."))
    return ValidationResult(issues, out, m, source_name, sha256_bytes(raw), rules)


def write_artifact(result: ValidationResult, raw: bytes, out_root: str,
                   software_version: str = "2.4.0") -> str:
    """Writes source copy + processed data + reports + metadata. Returns the folder."""
    folder = os.path.join(out_root, f"{result.mapping['provenance']}_{result.source_sha256[:12]}")
    os.makedirs(folder, exist_ok=True)
    ext = os.path.splitext(result.source_name)[1].lower() or ".bin"
    src = os.path.join(folder, "source" + ext)
    with open(src, "wb") as f:
        f.write(raw)
    processed_csv = result.data.to_csv(index=False).encode("utf-8")
    with open(os.path.join(folder, "processed.csv"), "wb") as f:
        f.write(processed_csv)
    rep = result.report()
    with open(os.path.join(folder, "validation_report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    with open(os.path.join(folder, "validation_report.md"), "w", encoding="utf-8") as f:
        f.write(report_markdown(rep))
    meta = {
        "schema_version": SCHEMA_VERSION,
        "dataset_label": result.mapping.get("dataset_label"),
        "provenance": result.mapping.get("provenance"),
        "source_file": result.source_name,
        "source_sha256": result.source_sha256,
        "processed_sha256": sha256_bytes(processed_csv),
        "rows": int(len(result.data)),
        "is_valid": result.is_valid,
        "units_confirmed": result.units_confirmed,
        "units": result.mapping.get("units"),
        "column_mapping": result.mapping.get("columns"),
        "rules_applied": result.rules_applied,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "software": {"iip": software_version, "python": platform.python_version(),
                     "pandas": pd.__version__},
    }
    with open(os.path.join(folder, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return folder


def report_markdown(rep: Dict[str, Any]) -> str:
    lines = [f"# Validation report — {rep.get('dataset_label') or rep['source_file']}", "",
             f"- Source file: `{rep['source_file']}`",
             f"- SHA-256: `{rep['source_sha256']}`",
             f"- Provenance: **{rep['provenance']}**",
             f"- Rows: {rep['rows']} (expected {rep.get('expected_rows')})",
             f"- Valid: **{rep['is_valid']}** — {rep['n_errors']} error(s), {rep['n_warnings']} warning(s)",
             f"- Units confirmed: **{rep['units_confirmed']}**"
             + (f" (unconfirmed: {', '.join(rep['unconfirmed_units'])})" if rep['unconfirmed_units'] else ""),
             "", "## Issues", "", "| Level | Rule | Column | Rows | Message |", "|---|---|---|---|---|"]
    for i in rep["issues"]:
        rws = ", ".join(map(str, i["rows"][:10])) + (" …" if i["n_rows"] > 10 else "")
        lines.append(f"| {i['level']} | {i['rule']} | {i['column'] or ''} | {rws} | {i['message']} |")
    if not rep["issues"]:
        lines.append("| — | — | — | — | no issues |")
    lines += ["", "## Rules applied", ""] + [f"- {r}" for r in rep["rules_applied"]]
    lines += ["", "## Units (from mapping)", ""] + [f"- {k}: {v}" for k, v in rep["units"].items()]
    return "\n".join(lines) + "\n"
