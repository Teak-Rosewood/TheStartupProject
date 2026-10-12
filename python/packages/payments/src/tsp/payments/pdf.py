"""Render a GST invoice or credit note as a one-page A4 PDF (reportlab: no system libraries)."""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def money(minor: int, currency: str) -> str:
    # "INR" rather than the rupee sign: the built-in PDF fonts do not contain it.
    return f"{currency} {Decimal(minor) / 100:,.2f}"


def render_pdf(inv: dict[str, Any]) -> bytes:
    """``inv`` is the invoice row as a dict (see ``Invoicer.as_dict``)."""
    styles = getSampleStyleSheet()
    small, h = styles["BodyText"], styles["Title"]
    cur = inv["currency"]
    title = "CREDIT NOTE" if inv["kind"] == "credit_note" else "TAX INVOICE"
    s, b = inv["seller"], inv["buyer"]

    def party(p: dict[str, Any]) -> str:
        lines = [f"<b>{p.get('name', '')}</b>", p.get("address", "").replace("\n", "<br/>")]
        if p.get("gstin"):
            lines.append(f"GSTIN: {p['gstin']}")
        if p.get("email"):
            lines.append(p["email"])
        return "<br/>".join(x for x in lines if x)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"{title} {inv['number']}",
        author=s.get("name", ""),
    )
    story: list[Any] = [Paragraph(title, h)]
    meta = [
        ["Number", inv["number"], "Date", inv["issued_at"][:10]],
        ["Place of supply", inv["place_of_supply"], "Reverse charge", "No"],
    ]
    if inv.get("original_number"):
        meta.append(["Against invoice", inv["original_number"], "", ""])
    t = Table(meta, colWidths=[32 * mm, 56 * mm, 32 * mm, 56 * mm])
    t.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story += [t, Spacer(1, 6 * mm)]
    parties = Table(
        [
            [Paragraph("<b>From</b>", small), Paragraph("<b>Bill to</b>", small)],
            [Paragraph(party(s), small), Paragraph(party(b), small)],
        ],
        colWidths=[88 * mm, 88 * mm],
    )
    parties.setStyle(
        TableStyle(
            [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.grey)]
        )
    )
    story += [parties, Spacer(1, 6 * mm)]
    rows = [
        ["Description", "SAC", "Taxable", "CGST", "SGST", "IGST", "Total"],
        [
            Paragraph(inv["description"], small),
            inv["sac"],
            money(inv["taxable"], cur),
            money(inv["cgst"], cur),
            money(inv["sgst"], cur),
            money(inv["igst"], cur),
            money(inv["total"], cur),
        ],
    ]
    items = Table(
        rows,
        colWidths=[52 * mm, 16 * mm, 24 * mm, 20 * mm, 20 * mm, 20 * mm, 24 * mm],
        repeatRows=1,
    )
    items.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story += [items, Spacer(1, 4 * mm)]
    story.append(Paragraph(f"<b>Total: {money(inv['total'], cur)}</b>", styles["Heading3"]))
    if inv["supply_type"] == "export":
        story.append(
            Paragraph(
                "Export of services. Supplied under Letter of Undertaking without payment of IGST."
                if inv["igst"] == 0
                else "Export of services.",
                small,
            )
        )
    story += [
        Spacer(1, 10 * mm),
        Paragraph("This is a computer generated document and needs no signature.", small),
    ]
    doc.build(story)
    return buf.getvalue()
