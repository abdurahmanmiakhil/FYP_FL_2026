"""Scheduled maintenance: data retention, stale-upload cleanup and the weekly model-drift report."""

from __future__ import annotations

import logging
import statistics
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select

from ..core.config import get_settings
from ..db.models import Case, Prediction, Upload
from ..db.session import sync_session
from .audit import Actions, record_sync
from .storage import case_prefix, get_storage
from .uploads import incoming_dir

log = logging.getLogger("maintenance")

SOFT_DELETE_PURGE_DAYS = 30
STALE_UPLOAD_HOURS = 24

# PANDA test set, FedAvg ensemble (computed from calibration/preds__fedavg__s*.csv)
REFERENCE_P_CSPCA = {"mean": 0.4643, "median": 0.2937, "frac_ge_youden": 0.4501, "youden": 0.4809}
DRIFT_P_SHIFT = 0.15  # flag when the mean P(csPCa) moves this much from the reference
DRIFT_OOD_FRACTION = 0.20  # flag when more than this share of slides had stain-colour warnings


def purge_case(db: Any, case: Case, reason: str) -> None:
    get_storage().delete_prefix(case_prefix(case.id))
    record_sync(db, Actions.RETENTION, "case", case.id, details={"reason": reason})
    db.delete(case)


def run_retention(now: datetime | None = None) -> dict[str, int]:
    """Hard-delete cases past RETENTION_DAYS and soft-deleted cases after 30 days."""
    s = get_settings()
    now = now or datetime.now(UTC)
    purged = {"expired": 0, "soft_deleted": 0, "stale_uploads": 0}
    with sync_session() as db:
        conds = [Case.deleted_at < now - timedelta(days=SOFT_DELETE_PURGE_DAYS)]
        if s.RETENTION_DAYS > 0:
            conds.append(Case.created_at < now - timedelta(days=s.RETENTION_DAYS))
        for case in db.execute(select(Case).where(or_(*conds))).unique().scalars().all():
            kind = "soft_deleted" if case.deleted_at is not None else "expired"
            purge_case(db, case, kind)
            purged[kind] += 1
        for up in db.execute(
            select(Upload).where(
                Upload.completed_at.is_(None), Upload.created_at < now - timedelta(hours=STALE_UPLOAD_HOURS)
            )
        ).scalars():
            (incoming_dir() / f"{up.id}.part").unlink(missing_ok=True)
            db.delete(up)
            purged["stale_uploads"] += 1
        db.commit()
    log.info("retention done", extra=purged)
    return purged


def drift_report(now: datetime | None = None, days: int = 7) -> tuple[str, list[str]]:
    """Markdown report: colour statistics and P(csPCa) of the last `days` vs the PANDA reference."""
    from prostate_infer.qc import PANDA_COLOUR

    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    with sync_session() as db:
        preds = db.execute(select(Prediction).where(Prediction.created_at >= since)).scalars().all()
    flags: list[str] = []
    lines = [
        f"# GleasonAI model drift report - week ending {now:%Y-%m-%d}",
        "",
        f"Predictions in the last {days} days: **{len(preds)}**",
        "",
    ]
    if not preds:
        lines.append("No predictions in this period.")
        return "\n".join(lines), flags

    p = [x.p_cspca for x in preds]
    mean_p = statistics.fmean(p)
    frac = sum(v >= REFERENCE_P_CSPCA["youden"] for v in p) / len(p)
    lines += [
        "## P(csPCa) distribution",
        "",
        "| | this period | PANDA test (thesis) |",
        "|---|---|---|",
        f"| mean | {mean_p:.3f} | {REFERENCE_P_CSPCA['mean']:.3f} |",
        f"| median | {statistics.median(p):.3f} | {REFERENCE_P_CSPCA['median']:.3f} |",
        f"| share >= Youden | {frac:.1%} | {REFERENCE_P_CSPCA['frac_ge_youden']:.1%} |",
        "",
    ]
    if abs(mean_p - REFERENCE_P_CSPCA["mean"]) > DRIFT_P_SHIFT:
        flags.append(f"mean P(csPCa) {mean_p:.2f} differs from the PANDA reference {REFERENCE_P_CSPCA['mean']:.2f}")

    lines += [
        "## Stain colour statistics (tissue pixels)",
        "",
        "| statistic | mean | PANDA range (mean +/- 3 SD) |",
        "|---|---|---|",
    ]
    colours: list[dict[str, float]] = [c for x in preds if (c := x.qc.get("colour"))]
    for name, refs in PANDA_COLOUR.items():
        vals = [c[name] for c in colours if name in c]
        lo = min(m - 3 * sd for m, sd in refs)
        hi = max(m + 3 * sd for m, sd in refs)
        mean_v = statistics.fmean(vals) if vals else float("nan")
        lines.append(f"| {name.replace('_', ' ')} | {mean_v:.3f} | {lo:.3f} - {hi:.3f} |")
    ood = sum(any("Stain colour" in w for w in x.qc.get("warnings", [])) for x in preds) / len(preds)
    lines += ["", f"Slides with stain-colour warnings: **{ood:.1%}**", ""]
    if ood > DRIFT_OOD_FRACTION:
        flags.append(f"{ood:.0%} of slides were outside the PANDA stain range")
    low = sum(bool(x.low_confidence_reasons) for x in preds) / len(preds)
    lines += [f"Low-confidence results (mandatory review): **{low:.1%}**", ""]
    lines += ["## Flags", ""] + ([f"- DRIFT: {f}" for f in flags] or ["- none"])
    return "\n".join(lines) + "\n", flags


def store_drift_report(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    text, flags = drift_report(now)
    key = f"reports/drift-{now:%Y-%m-%d}.md"
    get_storage().put_bytes(key, text.encode(), "text/markdown")
    for f in flags:
        log.warning("model drift flagged", extra={"flag": f})
    return key
