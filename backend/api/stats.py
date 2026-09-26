"""Dashboard statistics (scoped to the user's hospital unless admin)."""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, time

from fastapi import APIRouter
from sqlalchemy import ColumnElement, func, select
from starlette.concurrency import run_in_threadpool

from ..db.models import Case, CaseStatus, Prediction, Review, Role
from ..schemas import Stats
from ..services.jobs import queue_length
from .deps import DB, CurrentUser

router = APIRouter(tags=["stats"])


@router.get("/stats", response_model=Stats, summary="Counts, agreement, runtime, turnaround")
async def stats(db: DB, user: CurrentUser) -> Stats:
    scope: list[ColumnElement[bool]] = [Case.deleted_at.is_(None)]
    if user.role != Role.admin:
        scope.append(Case.hospital == user.hospital)

    by_status = dict((await db.execute(select(Case.status, func.count()).where(*scope).group_by(Case.status))).all())
    total = sum(by_status.values())
    today = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    cases_today = (
        await db.execute(select(func.count()).select_from(Case).where(*scope, Case.created_at >= today))
    ).scalar_one()

    rn = func.row_number().over(partition_by=Prediction.case_id, order_by=Prediction.created_at.desc()).label("rn")
    lp = select(
        Prediction.case_id, Prediction.isup_grade, Prediction.runtime_seconds, Prediction.low_confidence_reasons, rn
    ).subquery()
    latest = (
        await db.execute(
            select(lp.c.case_id, lp.c.isup_grade, lp.c.runtime_seconds, lp.c.low_confidence_reasons)
            .join(Case, Case.id == lp.c.case_id)
            .where(lp.c.rn == 1, *scope)
        )
    ).all()
    by_grade = {str(g): 0 for g in range(6)}
    for r in latest:
        by_grade[str(r.isup_grade)] += 1

    rr = func.row_number().over(partition_by=Review.case_id, order_by=Review.created_at.desc()).label("rn")
    lr = select(Review.case_id, Review.decision, Review.final_isup, rr).subquery()
    reviews = {
        r.case_id: r
        for r in (
            await db.execute(
                select(lr.c.case_id, lr.c.decision, lr.c.final_isup)
                .join(Case, Case.id == lr.c.case_id)
                .where(lr.c.rn == 1, *scope)
            )
        ).all()
    }
    ai_grade = {r.case_id: r.isup_grade for r in latest}
    agree = [reviews[c].final_isup == g for c, g in ai_grade.items() if c in reviews]
    decisions: dict[str, int] = {}
    for r in reviews.values():
        key = str(getattr(r.decision, "value", r.decision))
        decisions[key] = decisions.get(key, 0) + 1
    awaiting = [c for c in ai_grade if c not in reviews]
    low_conf = {r.case_id for r in latest if r.low_confidence_reasons}

    done = (
        await db.execute(
            select(Case.created_at, Case.finished_at).where(
                *scope, Case.status == CaseStatus.done, Case.finished_at.is_not(None)
            )
        )
    ).all()
    turnaround = [(f - c).total_seconds() for c, f in done if f and c]
    runtimes = [r.runtime_seconds for r in latest]
    return Stats(
        total_cases=total,
        cases_today=cases_today,
        by_status={s.value: int(by_status.get(s, 0)) for s in CaseStatus},
        by_grade=by_grade,
        awaiting_review=len(awaiting),
        low_confidence_awaiting=len([c for c in awaiting if c in low_conf]),
        agreement_pct=round(100 * sum(agree) / len(agree), 1) if agree else None,
        reviewed=len(reviews),
        decisions=decisions,
        mean_runtime_s=round(statistics.fmean(runtimes), 1) if runtimes else None,
        median_turnaround_s=round(statistics.median(turnaround), 1) if turnaround else None,
        queue_length=await run_in_threadpool(_safe_queue_length),
    )


def _safe_queue_length() -> int:
    try:
        return queue_length()
    except Exception:
        return -1
