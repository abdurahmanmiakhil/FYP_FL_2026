"""Admin: model card, system health, audit log, data-subject export/delete."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from ..db.models import AuditLog, Case, Patient
from ..schemas import AuditOut, ModelCard, Page
from ..services.audit import Actions, record, verify_chain
from ..services.model_info import model_card
from ..services.storage import case_prefix, get_storage
from .deps import DB, AdminUser, CurrentUser, Meta, request_meta

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/model", response_model=ModelCard, summary="Model card (any signed-in user)")
async def get_model_card(_: CurrentUser) -> ModelCard:
    return await run_in_threadpool(model_card)


@router.get("/audit", response_model=Page[AuditOut], summary="Audit trail (append-only)")
async def audit_log(
    db: DB,
    _: AdminUser,
    action: str | None = None,
    user_id: str | None = None,
    entity_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> Page[AuditOut]:
    q = select(AuditLog)
    if action:
        q = q.where(AuditLog.action == action)
    if user_id:
        q = q.where(AuditLog.user_id == user_id)
    if entity_id:
        q = q.where(AuditLog.entity_id == entity_id)
    if date_from:
        q = q.where(AuditLog.at >= datetime.combine(date_from, time.min))
    if date_to:
        q = q.where(AuditLog.at < datetime.combine(date_to + timedelta(days=1), time.min))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size))).scalars()
    return Page[AuditOut](items=[AuditOut.model_validate(r) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/audit/verify", summary="Recompute the audit hash chain")
async def audit_verify(db: DB, _: AdminUser) -> dict[str, Any]:
    ok, broken = await verify_chain(db)
    n = (await db.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    return {"ok": ok, "entries": n, "first_broken_id": broken}


async def _patient(db: DB, hospital: str, code: str) -> Patient:
    p = (
        await db.execute(select(Patient).where(Patient.hospital == hospital, Patient.pseudonym_code == code))
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient code not found.")
    return p


@router.get("/patients/{hospital}/{code}/export", summary="Data-subject export (all data for one pseudonym)")
async def export_patient(
    hospital: str, code: str, db: DB, admin: AdminUser, meta: Annotated[Meta, Depends(request_meta)]
) -> Response:
    p = await _patient(db, hospital, code)
    cases = (await db.execute(select(Case).where(Case.patient_id == p.id))).unique().scalars().all()
    out: dict[str, Any] = {
        "patient_code": p.pseudonym_code,
        "hospital": p.hospital,
        "created_at": p.created_at.isoformat(),
        "cases": [],
    }
    for c in cases:
        await db.refresh(c, ["predictions", "reviews"])
        out["cases"].append(
            {
                "case_id": c.id,
                "status": c.status.value,
                "uploaded": c.created_at.isoformat(),
                "deleted": c.deleted_at.isoformat() if c.deleted_at else None,
                "slide_sha256": c.slide_sha256,
                "predictions": [
                    {
                        "created": pr.created_at.isoformat(),
                        "model_version": pr.model_version,
                        "isup_grade": pr.isup_grade,
                        "p_cancer": pr.p_cancer,
                        "p_cspca": pr.p_cspca,
                        "p_isup": pr.p_isup,
                        "flags": pr.operating_point_flags,
                        "low_confidence_reasons": pr.low_confidence_reasons,
                    }
                    for pr in c.predictions
                ],
                "reviews": [
                    {
                        "created": r.created_at.isoformat(),
                        "decision": r.decision.value,
                        "final_isup": r.final_isup,
                        "comment": r.comment,
                    }
                    for r in c.reviews
                ],
            }
        )
    await record(
        db,
        Actions.EXPORT,
        "patient",
        p.id,
        user=admin,
        ip=meta.ip,
        user_agent=meta.user_agent,
        details={"format": "json", "cases": len(cases)},
    )
    return Response(
        json.dumps(out, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="patient-{p.id[:8]}.json"'},
    )


@router.delete(
    "/patients/{hospital}/{code}",
    status_code=204,
    summary="Data-subject erasure: permanently delete a patient's slides, results and reviews",
)
async def erase_patient(
    hospital: str, code: str, db: DB, admin: AdminUser, meta: Annotated[Meta, Depends(request_meta)]
) -> None:
    p = await _patient(db, hospital, code)
    cases = (await db.execute(select(Case).where(Case.patient_id == p.id))).unique().scalars().all()
    storage = get_storage()
    from .slides import forget

    for c in cases:
        forget(c.id)
        await run_in_threadpool(storage.delete_prefix, case_prefix(c.id))
    n = len(cases)
    pid = p.id
    await db.delete(p)  # cascades to cases, predictions, reviews
    await db.flush()
    await record(
        db,
        Actions.DELETE,
        "patient",
        pid,
        user=admin,
        ip=meta.ip,
        user_agent=meta.user_agent,
        details={"erasure": True, "cases": n},
    )
