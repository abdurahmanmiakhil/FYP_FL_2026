"""PDF case report (reportlab)."""

from __future__ import annotations

import io
import logging
from datetime import UTC, datetime

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..db.models import Case, Prediction, Review
from ..schemas import DISCLAIMER
from .storage import Storage

log = logging.getLogger(__name__)

GRADE_COLOURS = {0: "#15803d", 1: "#15803d", 2: "#b45309", 3: "#b45309", 4: "#b91c1c", 5: "#b91c1c"}
GRADE_BAND = {0: "benign / low", 1: "low", 2: "intermediate", 3: "intermediate", 4: "high", 5: "high"}
DECISION_LABEL = {"confirmed": "Confirmed", "amended": "Amended", "rejected": "Rejected"}


def _fmt(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _img(data: bytes, max_w: float, max_h: float, background: bool = False) -> Image:
    im: PILImage.Image = PILImage.open(io.BytesIO(data))
    if background and im.mode == "RGBA":
        bg = PILImage.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[3])
        im = bg
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "PNG")
    buf.seek(0)
    ratio = min(max_w / im.width, max_h / im.height)
    return Image(buf, width=im.width * ratio, height=im.height * ratio)


def _overlay(storage: Storage, case_id: str, heatmap_png: bytes) -> bytes:
    """Blend the RGBA heatmap over the slide thumbnail (falls back to the heatmap on white)."""
    heat = PILImage.open(io.BytesIO(heatmap_png)).convert("RGBA")
    try:
        base = PILImage.open(io.BytesIO(storage.get_bytes(f"cases/{case_id}/thumbnail.jpg"))).convert("RGBA")
        base = base.resize(heat.size)
    except Exception:
        base = PILImage.new("RGBA", heat.size, (255, 255, 255, 255))
    alpha = heat.split()[3].point(lambda a: int(a * 0.6))
    heat.putalpha(alpha)
    out = io.BytesIO()
    PILImage.alpha_composite(base, heat).convert("RGB").save(out, "PNG")
    return out.getvalue()


def build_report(
    case: Case, pred: Prediction | None, reviews: list[Review], storage: Storage, generated_by: str
) -> bytes:
    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=16, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12, spaceBefore=8, spaceAfter=4)
    body = ParagraphStyle("b", parent=styles["BodyText"], fontSize=9, leading=12)
    small = ParagraphStyle("s", parent=body, fontSize=7.5, leading=9.5, textColor=colors.HexColor("#475569"))
    warn = ParagraphStyle(
        "w",
        parent=body,
        textColor=colors.HexColor("#92400e"),
        backColor=colors.HexColor("#fef3c7"),
        borderPadding=5,
        alignment=TA_CENTER,
        fontName="Helvetica-Bold",
    )

    def footer(canvas, doc) -> None:  # type: ignore[no-untyped-def]
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(15 * mm, 10 * mm, f"GleasonAI case {case.id} - {DISCLAIMER}")
        canvas.drawRightString(195 * mm, 10 * mm, f"page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        title=f"GleasonAI case report {case.patient.pseudonym_code}",
        author="GleasonAI",
    )
    latest = reviews[0] if reviews else None
    status = "REVIEWED" if latest else "PROVISIONAL - not yet reviewed by a clinician"
    story = [
        Paragraph("GleasonAI - prostate biopsy AI review report", h1),
        Paragraph(DISCLAIMER, warn),
        Spacer(1, 6),
        Table(
            [
                ["Patient code", case.patient.pseudonym_code, "Hospital", case.hospital],
                ["Case id", case.id, "Uploaded", _fmt(case.created_at)],
                ["Slide sha256", case.slide_sha256[:32] + "...", "Result status", status],
            ],
            colWidths=[28 * mm, 62 * mm, 26 * mm, 64 * mm],
            style=TableStyle(
                [
                    ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
                    ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8),
                    ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 8),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
                    ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f1f5f9")),
                ]
            ),
        ),
    ]
    if pred is None:
        story += [Spacer(1, 10), Paragraph(f"No AI result available (status: {case.status.value}).", body)]
    else:
        g = pred.isup_grade
        flags = pred.operating_point_flags
        th = pred.thresholds
        story += [
            Paragraph("AI result", h2),
            Table(
                [
                    [
                        Paragraph(f'<font size="22" color="{GRADE_COLOURS[g]}"><b>ISUP {g}</b></font>', body),
                        Paragraph(
                            f"<b>Gleason pattern:</b> {pred.gleason_hint}<br/><b>Risk band:</b> {GRADE_BAND[g]}<br/>"
                            f"<b>P(cancer, ISUP &ge; 1):</b> {pred.p_cancer * 100:.1f}%<br/>"
                            f"<b>P(clinically significant, ISUP &ge; 2):</b> {pred.p_cspca * 100:.1f}%",
                            body,
                        ),
                    ]
                ],
                colWidths=[45 * mm, 135 * mm],
            ),
            Spacer(1, 4),
            Table(
                [
                    ["ISUP grade", *[str(k) for k in range(6)]],
                    ["Probability", *[f"{p * 100:.1f}%" for p in pred.p_isup]],
                ],
                style=TableStyle(
                    [
                        ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
                        ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8),
                        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
                        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                    ]
                ),
            ),
            Paragraph(
                "The grade uses ordinal thresholds tuned on validation data (as in the thesis), so it can differ "
                "from the single most probable class.",
                small,
            ),
            Spacer(1, 4),
            Paragraph(
                "<b>Operating points</b> (FedAvg, fixed on validation): "
                f"csPCa Youden ({th.get('cspca_youden', 0):.3f}) "
                f"{'positive' if flags.get('cspca_youden') else 'negative'}; csPCa 90% sensitivity "
                f"({th.get('cspca_sens90', 0):.3f}) {'positive' if flags.get('cspca_sens90') else 'negative'}; "
                f"csPCa 95% sensitivity ({th.get('cspca_sens95', 0):.3f}) "
                f"{'positive' if flags.get('cspca_sens95') else 'negative'}; cancer Youden "
                f"({th.get('cancer_youden', 0):.3f}) {'positive' if flags.get('cancer_youden') else 'negative'}.",
                body,
            ),
        ]
        if pred.low_confidence_reasons:
            story += [
                Spacer(1, 4),
                Paragraph("<b>Low confidence - mandatory review:</b> " + " ".join(pred.low_confidence_reasons), warn),
            ]
        if pred.qc.get("warnings"):
            story += [Spacer(1, 4), Paragraph("<b>Slide quality warnings:</b> " + " ".join(pred.qc["warnings"]), small)]
        try:
            heat = _img(_overlay(storage, case.id, storage.get_bytes(pred.heatmap_path)), 180 * mm, 70 * mm)
            story += [
                Paragraph("Attention heatmap over the slide (red/yellow = most influence on the result)", h2),
                heat,
            ]
        except Exception:
            log.warning("report: heatmap missing", extra={"case_id": case.id})
            story.append(Paragraph("Heatmap unavailable.", small))
        tiles = []
        for t in sorted(pred.top_tiles, key=lambda t: t["rank"]):
            try:
                tiles.append(_img(storage.get_bytes(t["key"]), 42 * mm, 42 * mm))
            except Exception:
                log.warning("report: tile image missing", extra={"case_id": case.id, "rank": t["rank"]})
        if tiles:
            rows = [tiles[i : i + 4] for i in range(0, len(tiles), 4)]
            story += [
                KeepTogether(
                    [
                        Paragraph("Top attention tiles (level 0, 224 x 224 px)", h2),
                        Table(rows, style=TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")])),
                    ]
                )
            ]
        story += [
            Spacer(1, 4),
            Paragraph(
                f"Tiles analysed: {pred.n_tiles} (of {pred.n_tiles_total} tissue tiles) | "
                f"runtime {pred.runtime_seconds:.1f} s "
                f"on {pred.device} | model version {pred.model_version} | preprocessing {pred.preprocessing_version} | "
                f"result {_fmt(pred.created_at)}",
                small,
            ),
        ]

    story.append(Paragraph("Clinician review", h2))
    if not reviews:
        story.append(Paragraph("Not reviewed yet. This result is provisional.", body))
    else:
        data = [["Date", "Reviewer", "Decision", "Final ISUP", "Comment"]]
        for r in reviews:
            data.append(
                [
                    _fmt(r.created_at),
                    Paragraph(r.reviewer.full_name if r.reviewer else "-", small),
                    DECISION_LABEL[r.decision.value],
                    "-" if r.final_isup is None else str(r.final_isup),
                    Paragraph((r.comment or "").replace("<", "&lt;"), small),
                ]
            )
        story.append(
            Table(
                data,
                colWidths=[30 * mm, 32 * mm, 20 * mm, 18 * mm, 80 * mm],
                repeatRows=1,
                style=TableStyle(
                    [
                        ("FONT", (0, 0), (-1, -1), "Helvetica", 7.5),
                        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
                        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]
                ),
            )
        )
    story += [
        Spacer(1, 10),
        Paragraph(
            f"Generated {_fmt(datetime.now(UTC))} by {generated_by}. Research prototype from the NUML BS thesis "
            "'Federated Deep Learning for Prostate Cancer Gleason Grading via Histopathology Images and "
            "Telehealth Integration'. Not a medical device; clinical use requires prospective validation "
            "and regulatory clearance.",
            small,
        ),
    ]
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
