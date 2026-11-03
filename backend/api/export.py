"""
api/export.py
---------------
Enterprise Report Generator for PDF and Excel Formats.
Supports Executive Inventory Reports, Health Reports, Procurement Plans,
Optimization Comparisons, and Risk Reports.
"""

import io
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.orm import Session
from typing import Optional

import pandas as pd
from xml.sax.saxutils import escape
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from database.db import get_db
from models.models import User, InventoryItem
from api.auth import get_current_user
from api.crud import get_batch_or_latest, items_to_dicts, supplier_meta_map, attach_observed_cv
from analytics import analytics_engine as ae
from core.audit import audit
from core.exports import safe_frame, XLSX_OPTIONS


def _org_ctx(user):
    o = user.organization
    tz = (o.timezone if o and o.timezone else "Asia/Karachi")
    try:
        now = datetime.now(ZoneInfo(tz))
    except Exception:
        tz, now = "UTC", datetime.now(timezone.utc)
    return (o.currency if o and o.currency else "PKR"), tz, now


EXPORT_COLUMNS = ["sku", "product_name", "category", "warehouse", "supplier", "quantity_on_hand",
                  "unit_cost", "unit_price", "stock_value", "daily_avg_demand", "lead_time_days",
                  "reorder_point", "safety_stock", "max_stock_level", "eoq", "days_of_supply",
                  "status", "abc_class", "xyz_class", "fsn_class", "ved_class", "expiry_date"]

router = APIRouter(prefix="/api/export", tags=["export"])


def _get_items(db: Session, batch_id: str, org_id: int):
    batch = get_batch_or_latest(db, batch_id, org_id)
    if not batch:
        items_raw = db.query(InventoryItem).filter(InventoryItem.org_id == org_id, InventoryItem.is_active == True).all()
        batch_obj = type('MockBatch', (), {'id': 'latest', 'filename': 'Current Enterprise Inventory'})()
    else:
        items_raw = db.query(InventoryItem).filter(InventoryItem.batch_id == batch.id, InventoryItem.org_id == org_id, InventoryItem.is_active == True).all()
        if not items_raw:
            items_raw = db.query(InventoryItem).filter(InventoryItem.org_id == org_id, InventoryItem.is_active == True).all()
        batch_obj = batch

    items = ae.enrich_items(attach_observed_cv(db, org_id, items_to_dicts(items_raw)))
    return batch_obj, items


@router.get("/{batch_id}/excel")
def export_excel(
    batch_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    batch, items = _get_items(db, batch_id, user.org_id)
    kpis = ae.compute_kpis(items)
    currency, tz, now = _org_ctx(user)
    meta = pd.DataFrame([
        {"field": "organization", "value": user.organization.name if user.organization else ""},
        {"field": "generated_at", "value": now.isoformat(timespec="seconds")},
        {"field": "timezone", "value": tz},
        {"field": "currency", "value": currency},
        {"field": "source_batch", "value": str(getattr(batch, "id", ""))},
        {"field": "units", "value": "quantities in units; demand in units/day; lead time in days; "
                                    "money in " + currency},
    ])

    df_items = pd.DataFrame(items)
    df_kpis = pd.DataFrame([kpis])
    df_low = pd.DataFrame(ae.low_stock_alerts(items))
    df_over = pd.DataFrame(ae.overstock_items(items))
    df_supplier = pd.DataFrame(ae.supplier_performance(items, supplier_meta_map(db, user.org_id)))

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter", engine_kwargs={"options": XLSX_OPTIONS}) as writer:
        safe_frame(meta).to_excel(writer, sheet_name="Report Info", index=False)
        safe_frame(df_kpis).to_excel(writer, sheet_name="Executive KPIs", index=False)
        safe_frame(df_items).to_excel(writer, sheet_name="All Inventory", index=False)
        safe_frame(df_low if not df_low.empty else pd.DataFrame([{"info": "No low stock items"}])).to_excel(
            writer, sheet_name="Low Stock Alerts", index=False)
        safe_frame(df_over if not df_over.empty else pd.DataFrame([{"info": "No overstock items"}])).to_excel(
            writer, sheet_name="Overstock", index=False)
        safe_frame(df_supplier if not df_supplier.empty else pd.DataFrame([{"info": "No supplier data"}])).to_excel(
            writer, sheet_name="Supplier Performance", index=False)

    buffer.seek(0)
    audit(db, user.org_id, "EXPORT_REPORT", "export", getattr(batch, "id", None), user=user,
          details={"format": "xlsx", "rows": len(items)})
    db.commit()
    filename = f"IIP_Report_{now.strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{batch_id}/pdf")
def export_pdf(
    batch_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    batch, items = _get_items(db, batch_id, user.org_id)
    kpis = ae.compute_kpis(items)
    currency, tz, now = _org_ctx(user)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5 * inch, bottomMargin=0.5 * inch)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleStyle", parent=styles["Heading1"], textColor=colors.HexColor("#1e3a8a"))
    subtitle_style = ParagraphStyle("Subtitle", parent=styles["Normal"], textColor=colors.HexColor("#64748b"))

    elements = [
        Paragraph("Inventory Intelligence Platform — Executive Decision Report", title_style),
        Paragraph(f"Organization: {escape(user.organization.name) if user.organization else 'Enterprise'} | "
                  f"Generated: {now.strftime('%Y-%m-%d %H:%M')} ({tz}) | Currency: {currency} | "
                  f"Source batch: {getattr(batch, 'id', '')}", subtitle_style),
        Spacer(1, 15),
    ]

    kpi_data = [
        ["Metric", "Value"],
        ["Total Active SKUs", kpis["total_skus"]],
        ["Total Stock Units", f"{kpis['total_stock_units']:,.1f}"],
        [f"Total Stock Value ({currency})", f"{kpis.get('total_stock_value', 0):,.2f}"],
        ["Low Stock Items", kpis["low_stock_count"]],
        ["Critical Stockout Risk Items", kpis["critical_count"]],
        ["Overstock Items", kpis["overstock_count"]],
        ["Expiring Soon (<= 90d)", kpis["expiring_count"]],
        ["Average Days of Supply (DSI)", f"{kpis['avg_days_of_supply']} days"],
        ["Estimated Inventory Turnover", f"{kpis['inventory_turnover_estimate']}x"],
        ["Network Stockout Risk", f"{kpis['stockout_risk_pct']}%"],
    ]
    kpi_table = Table(kpi_data, colWidths=[3.2 * inch, 2.8 * inch])
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(Paragraph("Executive Health Summary", styles["Heading2"]))
    elements.append(kpi_table)
    elements.append(Spacer(1, 15))

    low_stock = ae.low_stock_alerts(items)[:20]
    if low_stock:
        elements.append(Paragraph("Urgent Priority Stock Alerts", styles["Heading2"]))
        rows = [["SKU", "Product Name", "Qty on Hand", "Reorder Point", "Status"]]
        for i in low_stock:
            rows.append([escape(str(i["sku"]))[:20], escape(str(i["product_name"]))[:32],
                         f"{i['quantity_on_hand']:.0f}", f"{i['reorder_point']:.0f}", i["status"].upper()])
        t = Table(rows, colWidths=[1.0 * inch, 2.6 * inch, 1.0 * inch, 1.0 * inch, 0.8 * inch])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#b91c1c")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fef2f2")]),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(t)

    elements.append(Spacer(1, 12))
    elements.append(Paragraph("Classifications are computed from the data at generation time; XYZ needs recorded "
                              "demand history. Figures are model outputs, not audited financial statements.",
                              subtitle_style))
    doc.build(elements)
    buffer.seek(0)
    audit(db, user.org_id, "EXPORT_REPORT", "export", getattr(batch, "id", None), user=user,
          details={"format": "pdf", "rows": len(items)})
    db.commit()
    filename = f"IIP_Executive_Report_{now.strftime('%Y%m%d')}.pdf"
    return StreamingResponse(
        buffer, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{batch_id}/csv")
def export_csv(
    batch_id: str,
    status: Optional[str] = Query(None, pattern="^(normal|low|critical|overstock|expiring)$"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Inventory snapshot as CSV (UTF-8 with BOM for Excel), formula-injection safe.
    Header comment lines record organisation, timestamp, timezone, currency and filters."""
    batch, items = _get_items(db, batch_id, user.org_id)
    if status:
        items = [i for i in items if i.get("status") == status]
    currency, tz, now = _org_ctx(user)
    df = pd.DataFrame(items)
    for c in EXPORT_COLUMNS:
        if c not in df.columns:
            df[c] = None
    df = safe_frame(df[EXPORT_COLUMNS])
    header = (f"# organization={user.organization.name if user.organization else ''}; "
              f"generated_at={now.isoformat(timespec='seconds')}; timezone={tz}; currency={currency}; "
              f"batch={getattr(batch, 'id', '')}; filter_status={status or 'all'}; "
              "units: quantity=units, demand=units/day, lead_time=days\n")
    body = header + df.to_csv(index=False)
    audit(db, user.org_id, "EXPORT_REPORT", "export", getattr(batch, "id", None), user=user,
          details={"format": "csv", "rows": len(df), "filter_status": status})
    db.commit()
    return Response(content=("\ufeff" + body).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="IIP_Inventory_{now.strftime("%Y%m%d_%H%M")}.csv"'})
