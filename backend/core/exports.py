"""
core/exports.py
----------------
Spreadsheet-safe exports.

CSV/Excel formula injection: a cell whose text starts with = + - @ (or a tab
/ carriage return) can be executed as a formula when the file is opened in
Excel/LibreOffice. Every TEXT cell that starts with one of those characters
is prefixed with a single quote ('), following OWASP guidance. Numeric cells
are left as numbers. The Excel writer is additionally configured not to
convert strings to formulas or URLs.
"""

from __future__ import annotations

import numbers

import pandas as pd

_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
XLSX_OPTIONS = {"strings_to_formulas": False, "strings_to_urls": False}


def safe_cell(v):
    if isinstance(v, str) and v.startswith(_TRIGGERS):
        return "'" + v
    return v


def safe_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == object:
            out[c] = out[c].map(lambda v: v if isinstance(v, numbers.Number) else safe_cell(v))
    out.columns = [safe_cell(str(c)) for c in out.columns]
    return out
