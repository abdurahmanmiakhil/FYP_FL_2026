"""Case listing (filters, sort, pagination) and API representations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from sqlalchemy import Select, Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..db.models import Case, CaseStatus, Decision, Patient, Prediction, Review, Role, User
from ..schemas import CaseDetail, CaseSummary, PredictionOut, ReviewOut, TopTileOut

SortKey = Literal["-created_at", "created_at", "-isup_grade", "isup_grade", "-p_cspca", "p_cspca", "patient_code"]


def _latest_pred():  # type: ignore[no-untyped-def]
    rn = func.row_number().over(partition_by=Prediction.case_id, order_by=Prediction.created_at.desc()).label("rn")
    inner = select(
        Prediction.case_id, Prediction.isup_grade, Prediction.p_cspca, Prediction.low_confidence_reasons, rn
    ).subquery()
    return select(inner).where(inner.c.rn == 1).subquery("lp")


def _latest_review():  # type: ignore[no-untyped-def]
    rn = func.row_number().over(partition_by=Review.case_id, order_by=Review.created_at.desc()).label("rn")
    inner = select(Review.case_id, Review.decision, Review.final_isup, Review.reviewer_id, rn).subquery()
    return select(inner).where(inner.c.rn == 1).subquery("lr")


@dataclass
class CaseFilters:
    status: CaseStatus | None = None
    grade: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    reviewer_id: str | None = None
    needs_review: bool | None = None
    low_confidence: bool | None = None
    q: str | None = None
    hospital: str | None = None


def build_query(base: Select[Any], f: CaseFilters, sort: SortKey) -> Select[Any]:
    lp, lr = _latest_pred(), _latest_review()
    reviewer = User.__table__.alias("reviewer")
    uploader = User.__table__.alias("uploader")
    q = (
        base.join(Patient, Patient.id == Case.patient_id)
        .outerjoin(lp, lp.c.case_id == Case.id)
        .outerjoin(lr, lr.c.case_id == Case.id)
        .outerjoin(reviewer, reviewer.c.id == lr.c.reviewer_id)
        .outerjoin(uploader, uploader.c.id == Case.uploaded_by)
        .add_columns(
            Patient.pseudonym_code,
            lp.c.isup_grade,
            lp.c.p_cspca,
            lp.c.low_confidence_reasons,
            lr.c.decision,
            lr.c.final_isup,
            reviewer.c.full_name.label("reviewer_name"),
            uploader.c.full_name.label("uploader_name"),
        )
    )
    if f.status:
        q = q.where(Case.status == f.status)
    if f.grade is not None:
        q = q.where(lp.c.isup_grade == f.grade)
    if f.date_from:
        q = q.where(Case.created_at >= datetime.combine(f.date_from, time.min))
    if f.date_to:
        q = q.where(Case.created_at < datetime.combine(f.date_to + timedelta(days=1), time.min))
    if f.reviewer_id:
        q = q.where(lr.c.reviewer_id == f.reviewer_id)
    if f.needs_review is True:
        q = q.where(Case.status == CaseStatus.done, lr.c.case_id.is_(None))
    elif f.needs_review is False:
        q = q.where(lr.c.case_id.is_not(None))
    if f.low_confidence is not None:
        # JSON list non-empty: compare its serialised form (portable across SQLite and PostgreSQL)
        empty = func.coalesce(cast(lp.c.low_confidence_reasons, Text), "[]") == "[]"
        q = q.where(~empty if f.low_confidence else empty)
    if f.q:
        q = q.where(func.lower(Patient.pseudonym_code).like(f"%{f.q.strip().lower()}%"))
    if f.hospital:
        q = q.where(Case.hospital == f.hospital)
    order = {
        "-created_at": Case.created_at.desc(),
        "created_at": Case.created_at.asc(),
        "-isup_grade": lp.c.isup_grade.desc().nulls_last(),
        "isup_grade": lp.c.isup_grade.asc().nulls_last(),
        "-p_cspca": lp.c.p_cspca.desc().nulls_last(),
        "p_cspca": lp.c.p_cspca.asc().nulls_last(),
        "patient_code": Patient.pseudonym_code.asc(),
    }[sort]
    return q.order_by(order, Case.id)


def summary_from_row(row: Any) -> CaseSummary:
    c: Case = row[0]
    return CaseSummary(
        id=c.id,
        patient_code=row.pseudonym_code,
        hospital=c.hospital,
        status=c.status,
        progress_stage=c.progress_stage,
        progress_done=c.progress_done,
        progress_total=c.progress_total,
        error=c.error,
        created_at=c.created_at,
        finished_at=c.finished_at,
        uploaded_by_name=row.uploader_name,
        isup_grade=row.isup_grade,
        p_cspca=row.p_cspca,
        low_confidence=bool(row.low_confidence_reasons),
        review_decision=Decision(row.decision) if row.decision else None,
        final_isup=row.final_isup,
        reviewer_name=row.reviewer_name,
    )


def api(path: str) -> str:
    return get_settings().API_PREFIX + path


def prediction_out(case: Case, p: Prediction, reviewed: bool) -> PredictionOut:
    out = PredictionOut.model_validate(p)
    out.heatmap_url = api(f"/cases/{case.id}/heatmap.png?p={p.id}")
    out.top_tiles = [
        TopTileOut(
            rank=t["rank"],
            x=t["x"],
            y=t["y"],
            attention=t["attention"],
            url=api(f"/cases/{case.id}/tiles/{t['rank']}.jpg?p={p.id}"),
        )
        for t in sorted(p.top_tiles, key=lambda t: t["rank"])
    ]
    out.status = "reviewed" if reviewed else "provisional"
    return out


def review_out(r: Review) -> ReviewOut:
    o = ReviewOut.model_validate(r)
    if r.reviewer:
        o.reviewer_name, o.reviewer_role = r.reviewer.full_name, r.reviewer.role
    return o


async def case_detail(db: AsyncSession, case: Case) -> CaseDetail:
    await db.refresh(case, ["predictions", "reviews"])
    pred = case.predictions[0] if case.predictions else None
    reviews = case.reviews
    latest = reviews[0] if reviews else None
    return CaseDetail(
        id=case.id,
        patient_code=case.patient.pseudonym_code,
        hospital=case.hospital,
        status=case.status,
        progress_stage=case.progress_stage,
        progress_done=case.progress_done,
        progress_total=case.progress_total,
        error=case.error,
        created_at=case.created_at,
        finished_at=case.finished_at,
        uploaded_by_name=case.uploader.full_name if case.uploader else None,
        isup_grade=pred.isup_grade if pred else None,
        p_cspca=pred.p_cspca if pred else None,
        low_confidence=bool(pred and pred.low_confidence_reasons),
        review_decision=latest.decision if latest else None,
        final_isup=latest.final_isup if latest else None,
        reviewer_name=latest.reviewer.full_name if latest and latest.reviewer else None,
        slide_sha256=case.slide_sha256,
        slide_bytes=case.slide_bytes,
        slide_format=case.slide_format,
        prediction=prediction_out(case, pred, reviewed=latest is not None) if pred else None,
        predictions_count=len(case.predictions),
        reviews=[review_out(r) for r in reviews],
        dzi_url=api(f"/slides/{case.id}.dzi"),
    )


def can_review(user: User) -> bool:
    return user.role in (Role.pathologist, Role.urologist)
