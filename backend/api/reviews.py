"""Clinician review (confirm / amend / reject) and the PDF case report."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from ..db.models import CaseStatus, Decision, Prediction, Review
from ..schemas import ReviewIn, ReviewOut
from ..services.audit import Actions, record
from ..services.cases import review_out
from ..services.reports import build_report
from ..services.storage import get_storage
from .deps import DB, Clinician, CurrentUser, Meta, get_case_for, request_meta

router = APIRouter(prefix="/cases", tags=["reviews & reports"])


@router.post(
    "/{case_id}/reviews",
    response_model=ReviewOut,
    status_code=201,
    summary="Record a review decision (pathologist/urologist)",
)
async def create_review(
    case_id: str, body: ReviewIn, db: DB, user: Clinician, meta: Annotated[Meta, Depends(request_meta)]
) -> ReviewOut:
    case = await get_case_for(db, user, case_id)
    if case.status != CaseStatus.done:
        raise HTTPException(status.HTTP_409_CONFLICT, "The AI result is not ready yet.")
    pred = (
        await db.execute(
            select(Prediction).where(Prediction.case_id == case.id).order_by(Prediction.created_at.desc()).limit(1)
        )
    ).scalar_one()
    comment = (body.comment or "").strip() or None
    final: int | None
    if body.decision == Decision.confirmed:
        final = pred.isup_grade
    elif body.decision == Decision.amended:
        if body.final_isup is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose the corrected ISUP grade.")
        final = body.final_isup
    else:
        final = body.final_isup
    if body.decision != Decision.confirmed and not comment:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A comment is required when amending or rejecting.")
    review = Review(
        case_id=case.id,
        prediction_id=pred.id,
        reviewer_id=user.id,
        decision=body.decision,
        final_isup=final,
        comment=comment,
    )
    db.add(review)
    await db.flush()
    await record(
        db,
        Actions.REVIEW,
        "case",
        case.id,
        user=user,
        ip=meta.ip,
        user_agent=meta.user_agent,
        details={
            "review_id": review.id,
            "decision": body.decision.value,
            "final_isup": final,
            "ai_isup": pred.isup_grade,
        },
    )
    await db.refresh(review, ["reviewer"])
    return review_out(review)


@router.get("/{case_id}/reviews", response_model=list[ReviewOut], summary="Review history (newest first)")
async def list_reviews(case_id: str, db: DB, user: CurrentUser) -> list[ReviewOut]:
    case = await get_case_for(db, user, case_id)
    await db.refresh(case, ["reviews"])
    return [review_out(r) for r in case.reviews]


@router.get("/{case_id}/report.pdf", summary="PDF case report", response_class=Response)
async def report_pdf(case_id: str, db: DB, user: CurrentUser, meta: Annotated[Meta, Depends(request_meta)]) -> Response:
    case = await get_case_for(db, user, case_id)
    await db.refresh(case, ["predictions", "reviews"])
    pred = case.predictions[0] if case.predictions else None
    pdf = await run_in_threadpool(
        build_report, case, pred, list(case.reviews), get_storage(), f"{user.full_name} ({user.role.value})"
    )
    await record(
        db,
        Actions.EXPORT,
        "case",
        case.id,
        user=user,
        ip=meta.ip,
        user_agent=meta.user_agent,
        details={"format": "pdf"},
    )
    name = f"gleasonai-{case.patient.pseudonym_code}-{case.id[:8]}.pdf"
    return Response(
        pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )
