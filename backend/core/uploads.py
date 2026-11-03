"""
core/uploads.py
----------------
Bounded reading of uploaded files (size limit, extension whitelist) and
safe parsing of the optional JSON column mapping.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from fastapi import HTTPException, UploadFile

from core.settings import settings

ALLOWED_EXTENSIONS = (".csv", ".tsv", ".txt", ".xlsx", ".xls", ".xlsm", ".pdf")
_CHUNK = 1024 * 1024


async def read_upload_limited(file: UploadFile, allowed=ALLOWED_EXTENSIONS) -> bytes:
    name = (file.filename or "").strip()
    ext = os.path.splitext(name.lower())[1]
    if not name or ext not in allowed:
        raise HTTPException(status_code=415,
                            detail=f"Unsupported file type '{ext or name}'. Allowed: {', '.join(allowed)}")
    limit = settings.max_upload_mb * 1024 * 1024
    buf = bytearray()
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > limit:
            raise HTTPException(status_code=413,
                                detail=f"File exceeds the {settings.max_upload_mb} MB upload limit (IIP_MAX_UPLOAD_MB).")
    if not buf:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    return bytes(buf)


def parse_mapping(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    try:
        m = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"custom_mapping is not valid JSON: {e.msg}")
    if not isinstance(m, dict) or not all(isinstance(k, str) and (v is None or isinstance(v, str))
                                          for k, v in m.items()):
        raise HTTPException(status_code=400, detail="custom_mapping must be a JSON object of field -> column name.")
    return m


def check_row_limit(n_rows: int) -> None:
    if n_rows > settings.max_upload_rows:
        raise HTTPException(status_code=413,
                            detail=f"File has {n_rows} rows; the limit is {settings.max_upload_rows} (IIP_MAX_UPLOAD_ROWS).")
