"""Cases: upload (multipart or resumable chunks), list/filter, detail, outputs, live progress."""

from __future__ import annotations

import asyncio
import csv
import io
import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from ..core import ratelimit
from ..core.config import get_settings
from ..db.models import AuditLog, Case, CaseStatus, Prediction, Role, Upload, User
from ..schemas import (
    AuditOut,
    CaseCreated,
    CaseDetail,
    CaseSummary,
    Page,
    PredictionOut,
    UploadComplete,
    UploadInit,
    UploadState,
)
from ..services import uploads as svc
from ..services.audit import Actions, record
from ..services.cases import CaseFilters, SortKey, build_query, case_detail, prediction_out, summary_from_row
from ..services.jobs import enqueue_prediction, read_progress
from ..services.storage import case_prefix, get_storage
from .deps import DB, AdminUser, CurrentUser, Meta, get_case_for, request_meta, visible_cases
from .slides import forget as forget_slide

router = APIRouter(prefix="/cases", tags=["cases"])
uploads_router = APIRouter(prefix="/uploads", tags=["uploads (resumable)"])


def _require_uploader(user: User) -> None:
    if user.role not in (Role.pathologist, Role.urologist, Role.admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role cannot upload slides.")


def _hospital_for(user: User, requested: str | None) -> str:
    if requested and requested.strip() != user.hospital:
        if user.role != Role.admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only upload for your own hospital.")
        return requested.strip()
    return user.hospital


async def _finish_intake(
    db: DB,
    user: User,
    meta: Meta,
    tmp: Path,
    sha: str,
    size: int,
    suffix: str,
    pseudonym: str,
    hospital: str,
    slide_id: str | None,
) -> CaseCreated:
    case, created = await svc.create_case(db, user, tmp, sha, size, suffix, pseudonym, hospital, slide_id)
    events = f"{get_settings().API_PREFIX}/cases/{case.id}/events"
    if not created:
        return CaseCreated(case_id=case.id, status=case.status, duplicate=True, events_url=events)
    await record(
        db,
        Actions.UPLOAD,
        "case",
        case.id,
        user=user,
        ip=meta.ip,
        user_agent=meta.user_agent,
        details={"bytes": size, "format": suffix.lstrip("."), "sha256": sha[:16]},
    )
    case.status = CaseStatus.queued
    await db.commit()
    case.job_id = await run_in_threadpool(enqueue_prediction, case.id)
    await db.commit()
    await db.refresh(case)
    return CaseCreated(case_id=case.id, status=case.status, events_url=events)


@router.post(
    "", response_model=CaseCreated, status_code=201, summary="Upload a slide (multipart) and start AI analysis"
)
async def upload_case(
    request: Request,
    db: DB,
    user: CurrentUser,
    meta: Annotated[Meta, Depends(request_meta)],
    file: Annotated[UploadFile, File(description=".tif/.tiff/.svs, max 2 GB")],
    pseudonym_code: Annotated[str, Form(min_length=2, max_length=64)],
    hospital: Annotated[str | None, Form(max_length=120)] = None,
    slide_id: Annotated[str | None, Form(max_length=64)] = None,
) -> CaseCreated:
    s = get_settings()
    _require_uploader(user)
    ratelimit.hit(f"upload:{user.id}", s.UPLOAD_RATE_PER_HOUR, 3600)
    suffix = svc.check_suffix(file.filename)
    code = svc.check_pseudonym(pseudonym_code)
    hosp = _hospital_for(user, hospital)

    async def chunks() -> AsyncIterator[bytes]:
        while chunk := await file.read(1 << 20):
            yield chunk

    tmp, sha, size = await svc.stream_to_temp(chunks(), s.MAX_UPLOAD_BYTES)
    return await _finish_intake(db, user, meta, tmp, sha, size, suffix, code, hosp, slide_id)


# ------------------------------------------------------------------ resumable uploads (frontend)


@uploads_router.post("", response_model=UploadState, status_code=201, summary="Start a resumable upload")
async def upload_init(body: UploadInit, db: DB, user: CurrentUser) -> UploadState:
    s = get_settings()
    _require_uploader(user)
    ratelimit.hit(f"upload:{user.id}", s.UPLOAD_RATE_PER_HOUR, 3600)
    suffix = svc.check_suffix(body.filename)
    if body.size > s.MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Slide is larger than 2 GB.")
    up = Upload(user_id=user.id, suffix=suffix, total_bytes=body.size)
    db.add(up)
    await db.commit()
    (svc.incoming_dir() / f"{up.id}.part").touch()
    return UploadState(upload_id=up.id, received_bytes=0, total_bytes=body.size, chunk_bytes=s.UPLOAD_CHUNK_BYTES)


async def _own_upload(db: DB, user: User, upload_id: str) -> Upload:
    up = await db.get(Upload, upload_id)
    if up is None or up.user_id != user.id or up.completed_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found.")
    return up


@uploads_router.get("/{upload_id}", response_model=UploadState, summary="Resume point of an upload")
async def upload_state(upload_id: str, db: DB, user: CurrentUser) -> UploadState:
    up = await _own_upload(db, user, upload_id)
    return UploadState(
        upload_id=up.id,
        received_bytes=up.received_bytes,
        total_bytes=up.total_bytes,
        chunk_bytes=get_settings().UPLOAD_CHUNK_BYTES,
    )


@uploads_router.put(
    "/{upload_id}", response_model=UploadState, summary="Send one chunk (Content-Range: bytes a-b/total)"
)
async def upload_chunk(
    upload_id: str, request: Request, db: DB, user: CurrentUser, content_range: Annotated[str, Header()]
) -> UploadState:
    s = get_settings()
    up = await _own_upload(db, user, upload_id)
    try:
        unit, rng = content_range.split(" ", 1)
        span, total = rng.split("/")
        start, end = (int(x) for x in span.split("-"))
        assert unit == "bytes" and int(total) == up.total_bytes and 0 <= start <= end < up.total_bytes
    except (ValueError, AssertionError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad Content-Range header.") from None
    if start != up.received_bytes:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"message": "Chunk out of order.", "received_bytes": up.received_bytes}
        )
    if end - start + 1 > s.UPLOAD_CHUNK_BYTES * 2:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Chunk too large.")
    body = await request.body()
    if len(body) != end - start + 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Chunk length does not match Content-Range.")
    part = svc.incoming_dir() / f"{up.id}.part"

    def append() -> None:
        with open(part, "r+b" if part.exists() else "wb") as f:
            f.seek(start)
            f.write(body)
            f.truncate(start + len(body))

    await run_in_threadpool(append)
    up.received_bytes = end + 1
    await db.commit()
    return UploadState(
        upload_id=up.id, received_bytes=up.received_bytes, total_bytes=up.total_bytes, chunk_bytes=s.UPLOAD_CHUNK_BYTES
    )


@uploads_router.post(
    "/{upload_id}/complete",
    response_model=CaseCreated,
    status_code=201,
    summary="Finish an upload: validate, create the case and start AI analysis",
)
async def upload_complete(
    upload_id: str, body: UploadComplete, db: DB, user: CurrentUser, meta: Annotated[Meta, Depends(request_meta)]
) -> CaseCreated:
    up = await _own_upload(db, user, upload_id)
    if up.received_bytes != up.total_bytes:
        raise HTTPException(status.HTTP_409_CONFLICT, "Upload is not complete yet.")
    code = svc.check_pseudonym(body.pseudonym_code)
    hosp = _hospital_for(user, body.hospital)
    part = svc.incoming_dir() / f"{up.id}.part"
    sha = await run_in_threadpool(svc.sha256_file, part)
    up.completed_at = datetime.now(UTC)
    await db.commit()
    return await _finish_intake(db, user, meta, part, sha, up.total_bytes, up.suffix, code, hosp, body.slide_id)


@uploads_router.delete("/{upload_id}", status_code=204, summary="Abort an upload")
async def upload_abort(upload_id: str, db: DB, user: CurrentUser) -> None:
    up = await _own_upload(db, user, upload_id)
    (svc.incoming_dir() / f"{up.id}.part").unlink(missing_ok=True)
    await db.delete(up)
    await db.commit()


# ------------------------------------------------------------------ list / detail


def _filters(
    status_: CaseStatus | None = Query(None, alias="status"),
    grade: int | None = Query(None, ge=0, le=5),
    date_from: date | None = None,
    date_to: date | None = None,
    reviewer_id: str | None = None,
    needs_review: bool | None = None,
    low_confidence: bool | None = None,
    q: str | None = Query(None, max_length=64),
    hospital: str | None = None,
) -> CaseFilters:
    return CaseFilters(status_, grade, date_from, date_to, reviewer_id, needs_review, low_confidence, q, hospital)


@router.get("", response_model=Page[CaseSummary], summary="List cases (filters, sort, pagination)")
async def list_cases(
    db: DB,
    user: CurrentUser,
    f: Annotated[CaseFilters, Depends(_filters)],
    sort: SortKey = "-created_at",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> Page[CaseSummary]:
    q = build_query(visible_cases(user), f, sort)
    total = (await db.execute(select(func.count()).select_from(q.order_by(None).subquery()))).scalar_one()
    rows = (await db.execute(q.offset((page - 1) * page_size).limit(page_size))).all()
    return Page[CaseSummary](items=[summary_from_row(r) for r in rows], total=total, page=page, page_size=page_size)


def _csv_safe(v: object) -> object:
    """Neutralise spreadsheet formulas (CSV injection): prefix cells starting with = + - @ tab CR."""
    return f"'{v}" if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


@router.get("/export.csv", summary="Export the filtered case list as CSV", response_class=Response)
async def export_csv(
    db: DB,
    user: CurrentUser,
    f: Annotated[CaseFilters, Depends(_filters)],
    meta: Annotated[Meta, Depends(request_meta)],
    sort: SortKey = "-created_at",
) -> Response:
    rows = (await db.execute(build_query(visible_cases(user), f, sort).limit(10000))).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "case_id",
            "patient_code",
            "hospital",
            "status",
            "uploaded_utc",
            "isup_grade",
            "p_cspca",
            "low_confidence",
            "review_decision",
            "final_isup",
            "reviewer",
        ]
    )
    for r in rows:
        s = summary_from_row(r)
        w.writerow(
            [
                _csv_safe(v)
                for v in [
                    s.id,
                    s.patient_code,
                    s.hospital,
                    s.status.value,
                    s.created_at.isoformat(),
                    s.isup_grade,
                    "" if s.p_cspca is None else f"{s.p_cspca:.4f}",
                    s.low_confidence,
                    s.review_decision.value if s.review_decision else "",
                    s.final_isup,
                    s.reviewer_name or "",
                ]
            ]
        )
    await record(
        db,
        Actions.EXPORT,
        "case_list",
        None,
        user=user,
        ip=meta.ip,
        user_agent=meta.user_agent,
        details={"format": "csv", "rows": len(rows)},
    )
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="gleasonai-cases.csv"'},
    )


@router.get("/{case_id}", response_model=CaseDetail, summary="Case with latest prediction and review history")
async def get_case(case_id: str, db: DB, user: CurrentUser, meta: Annotated[Meta, Depends(request_meta)]) -> CaseDetail:
    case = await get_case_for(db, user, case_id)
    await record(db, Actions.VIEW, "case", case.id, user=user, ip=meta.ip, user_agent=meta.user_agent)
    return await case_detail(db, case)


@router.delete("/{case_id}", status_code=204, summary="Soft-delete a case (admin)")
async def delete_case(case_id: str, db: DB, admin: AdminUser, meta: Annotated[Meta, Depends(request_meta)]) -> None:
    case = await get_case_for(db, admin, case_id)
    case.deleted_at, case.deleted_by = datetime.now(UTC), admin.id
    forget_slide(case.id)
    await record(
        db, Actions.DELETE, "case", case.id, user=admin, ip=meta.ip, user_agent=meta.user_agent, details={"soft": True}
    )


@router.post("/{case_id}/retry", response_model=CaseCreated, summary="Re-run AI analysis for a failed case")
async def retry_case(
    case_id: str, db: DB, user: CurrentUser, meta: Annotated[Meta, Depends(request_meta)]
) -> CaseCreated:
    _require_uploader(user)
    case = await get_case_for(db, user, case_id)
    if case.status != CaseStatus.failed:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only failed cases can be retried.")
    case.status, case.error, case.attempts = CaseStatus.queued, None, 0
    await record(
        db, Actions.UPLOAD, "case", case.id, user=user, ip=meta.ip, user_agent=meta.user_agent, details={"retry": True}
    )
    case.job_id = await run_in_threadpool(enqueue_prediction, case.id)
    await db.commit()
    return CaseCreated(
        case_id=case.id, status=case.status, events_url=f"{get_settings().API_PREFIX}/cases/{case.id}/events"
    )


# ------------------------------------------------------------------ outputs


async def _prediction(db: DB, case: Case, pred_id: str | None) -> Prediction:
    q = select(Prediction).where(Prediction.case_id == case.id)
    q = q.where(Prediction.id == pred_id) if pred_id else q.order_by(Prediction.created_at.desc()).limit(1)
    p = (await db.execute(q)).scalar_one_or_none()
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No prediction for this case yet.")
    return p


@router.get("/{case_id}/prediction", response_model=PredictionOut, summary="Latest AI result")
async def get_prediction(case_id: str, db: DB, user: CurrentUser) -> PredictionOut:
    case = await get_case_for(db, user, case_id)
    p = await _prediction(db, case, None)
    await db.refresh(case, ["reviews"])
    return prediction_out(case, p, reviewed=bool(case.reviews))


@router.get("/{case_id}/prediction/result.json", summary="Full model output (attention per tile, per-seed logits)")
async def get_prediction_json(case_id: str, db: DB, user: CurrentUser, p: str | None = None) -> Response:
    case = await get_case_for(db, user, case_id)
    pred = await _prediction(db, case, p)
    data = await run_in_threadpool(get_storage().get_bytes, pred.result_path)
    return Response(data, media_type="application/json")


PRIVATE_CACHE = {"Cache-Control": "private, max-age=3600"}


@router.get("/{case_id}/heatmap.png", summary="Attention heatmap (RGBA PNG)", response_class=Response)
async def get_heatmap(case_id: str, db: DB, user: CurrentUser, p: str | None = None) -> Response:
    case = await get_case_for(db, user, case_id)
    pred = await _prediction(db, case, p)
    data = await run_in_threadpool(get_storage().get_bytes, pred.heatmap_path)
    return Response(data, media_type="image/png", headers=PRIVATE_CACHE)


@router.get("/{case_id}/thumbnail.jpg", summary="Low-resolution slide thumbnail", response_class=Response)
async def get_thumbnail(case_id: str, db: DB, user: CurrentUser) -> Response:
    case = await get_case_for(db, user, case_id)
    try:
        data = await run_in_threadpool(get_storage().get_bytes, f"{case_prefix(case.id)}/thumbnail.jpg")
    except (FileNotFoundError, OSError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thumbnail not ready yet.") from None
    return Response(data, media_type="image/jpeg", headers=PRIVATE_CACHE)


@router.get("/{case_id}/tiles/{k}.jpg", summary="Top-attention tile k (0-7)", response_class=Response)
async def get_tile(case_id: str, k: int, db: DB, user: CurrentUser, p: str | None = None) -> Response:
    case = await get_case_for(db, user, case_id)
    pred = await _prediction(db, case, p)
    tile = next((t for t in pred.top_tiles if t["rank"] == k), None)
    if tile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tile not found.")
    data = await run_in_threadpool(get_storage().get_bytes, tile["key"])
    return Response(data, media_type="image/jpeg", headers=PRIVATE_CACHE)


@router.get("/{case_id}/events", summary="Live job progress (Server-Sent Events)", response_class=StreamingResponse)
async def case_events(case_id: str, request: Request, db: DB, user: CurrentUser) -> StreamingResponse:
    case = await get_case_for(db, user, case_id)
    cid = case.id

    def snapshot_from_case(c: Case) -> dict:  # type: ignore[type-arg]
        return {
            "status": c.status.value,
            "stage": c.progress_stage or c.status.value,
            "done": c.progress_done,
            "total": c.progress_total,
            "error": c.error,
        }

    initial = snapshot_from_case(case)

    async def gen() -> AsyncIterator[bytes]:
        last, idle = None, 0.0
        yield b"retry: 3000\n\n"
        while True:
            if await request.is_disconnected():
                return
            snap = await run_in_threadpool(read_progress, cid) or initial
            if snap.get("status") in (None, "queued") and initial["status"] in ("done", "failed"):
                snap = initial
            if snap != last:
                yield f"event: progress\ndata: {json.dumps(snap)}\n\n".encode()
                last, idle = snap, 0.0
                if snap.get("status") in ("done", "failed"):
                    return
            else:
                idle += 1
                if idle % 15 == 0:
                    yield b": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.get("/{case_id}/audit", response_model=list[AuditOut], summary="Audit trail of one case (admin)")
async def case_audit(case_id: str, db: DB, admin: AdminUser) -> list[AuditLog]:
    case = await get_case_for(db, admin, case_id)
    rows = await db.execute(
        select(AuditLog)
        .where(AuditLog.entity == "case", AuditLog.entity_id == case.id)
        .order_by(AuditLog.id.desc())
        .limit(500)
    )
    return list(rows.scalars())
