"""RQ task: run the thesis ensemble on one case and store every output with its model version."""

from __future__ import annotations

import io
import json
import logging
import time
from datetime import UTC, datetime

from ..core.config import get_settings
from ..core.metrics import GPU_MEMORY, JOB_DURATION, JOBS
from ..db.models import Case, CaseStatus, Prediction, new_id
from ..db.session import sync_session
from ..services.audit import Actions, record_sync
from ..services.jobs import publish_progress
from ..services.storage import case_prefix, get_storage

log = logging.getLogger("worker")

PERMANENT_MESSAGES = {
    "NoTissueError": "No tissue found in slide",
    "SlideValidationError": "Slide could not be read",
    "AssetIntegrityError": "Model files failed the integrity check - contact the administrator",
    "JobTimeoutException": "Processing timed out",
}


class _Progress:
    """Pushes progress to Redis (live SSE) on every call and to the DB at most once a second."""

    def __init__(self, case_id: str):
        self.case_id = case_id
        self._last_db = 0.0

    def __call__(self, stage: str, done: int, total: int) -> None:
        publish_progress(self.case_id, status="processing", stage=stage, done=done, total=total)
        now = time.monotonic()
        if now - self._last_db > 1.0 or done == total:
            self._last_db = now
            with sync_session() as db:
                c = db.get(Case, self.case_id)
                if c is not None:
                    c.progress_stage, c.progress_done, c.progress_total = stage, done, total
                    db.commit()


def _fail(case_id: str, message: str, error_type: str) -> None:
    with sync_session() as db:
        c = db.get(Case, case_id)
        if c is None:
            return
        c.status, c.error, c.finished_at = CaseStatus.failed, message, datetime.now(UTC)
        record_sync(db, Actions.PREDICT_FAILED, "case", case_id, details={"error": error_type})
        db.commit()
    publish_progress(case_id, status="failed", stage="failed", done=0, total=0, error=message)


def process_case(case_id: str) -> str:
    """RQ entry point: times the job and counts outcomes for Prometheus."""
    t0 = time.monotonic()
    outcome = "error"
    try:
        outcome = _process_case(case_id)
        return outcome
    finally:
        JOBS.labels(outcome).inc()
        JOB_DURATION.labels(outcome).observe(time.monotonic() - t0)
        _gpu_memory()


def _gpu_memory() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            GPU_MEMORY.set(torch.cuda.memory_allocated())
    except Exception:  # metrics must never fail a job
        log.debug("gpu memory metric unavailable", exc_info=True)


def _process_case(case_id: str) -> str:
    from prostate_infer.predict import make_heatmap, predict_slide, top_tiles

    s = get_settings()
    storage = get_storage()
    with sync_session() as db:
        case = db.get(Case, case_id)
        if case is None or case.deleted_at is not None:
            return "skipped"
        case.status, case.error = CaseStatus.processing, None
        case.started_at = case.started_at or datetime.now(UTC)
        case.attempts += 1
        attempt, slide_key, seed_id = case.attempts, case.slide_file, case.slide_seed_id
        db.commit()
    publish_progress(case_id, status="processing", stage="reading slide", done=0, total=1)

    try:
        path = storage.local_path(slide_key)
        pred = predict_slide(path, image_id=seed_id, progress=_Progress(case_id))
        publish_progress(case_id, status="processing", stage="saving results", done=0, total=1)

        import openslide

        pid = new_id()
        base = f"{case_prefix(case_id)}/pred-{pid}"
        with openslide.OpenSlide(str(path)) as sl:
            buf = io.BytesIO()
            make_heatmap(pred, sl).save(buf, "PNG", optimize=True)
            storage.put_bytes(f"{base}/heatmap.png", buf.getvalue(), "image/png")
            thumb = io.BytesIO()
            sl.get_thumbnail((1024, 1024)).convert("RGB").save(thumb, "JPEG", quality=85)
            storage.put_bytes(f"{case_prefix(case_id)}/thumbnail.jpg", thumb.getvalue(), "image/jpeg")
            tiles = []
            for t in top_tiles(pred, sl, k=8):
                b = io.BytesIO()
                t.image.save(b, "JPEG", quality=90)
                key = f"{base}/tile-{t.rank}.jpg"
                storage.put_bytes(key, b.getvalue(), "image/jpeg")
                tiles.append({"key": key, "x": t.x, "y": t.y, "attention": t.attention, "rank": t.rank})
        storage.put_bytes(f"{base}/result.json", pred.model_dump_json().encode(), "application/json")
    except Exception as e:  # classify: permanent -> fail now; transient -> let RQ retry
        name = type(e).__name__
        if name in PERMANENT_MESSAGES:
            msg = PERMANENT_MESSAGES[name]
            if name == "SlideValidationError":
                msg = f"{msg}: {e}"
            log.warning("prediction failed", extra={"case_id": case_id, "error_type": name})
            _fail(case_id, msg, name)
            return "failed"
        log.exception("prediction error", extra={"case_id": case_id, "attempt": attempt})
        if attempt > s.JOB_RETRIES:
            _fail(case_id, "Processing failed after several attempts - contact the administrator", name)
        else:
            with sync_session() as db:
                c = db.get(Case, case_id)
                if c is not None:
                    c.status, c.error = CaseStatus.queued, "Temporary error - retrying"
                    db.commit()
            publish_progress(case_id, status="queued", stage="retrying", done=0, total=0)
        raise

    with sync_session() as db:
        c = db.get(Case, case_id)
        if c is None or c.deleted_at is not None:  # deleted while running: discard outputs
            storage.delete_prefix(base)
            return "discarded"
        db.add(
            Prediction(
                id=pid,
                case_id=case_id,
                model_version=pred.model_version,
                preprocessing_version=pred.preprocessing_version,
                p_cancer=pred.p_cancer,
                p_cspca=pred.p_cspca,
                p_isup=pred.p_isup,
                isup_grade=pred.isup_grade,
                gleason_hint=pred.gleason_hint,
                operating_point_flags=pred.operating_point_flags.model_dump(),
                thresholds=pred.thresholds,
                n_tiles=pred.n_tiles,
                n_tiles_total=pred.n_tiles_total,
                seed_std_p_cspca=pred.seed_std_p_cspca,
                low_confidence_reasons=pred.low_confidence_reasons,
                qc=json.loads(pred.qc.model_dump_json()),
                slide_width=pred.slide_width,
                slide_height=pred.slide_height,
                runtime_seconds=pred.runtime_seconds,
                device=pred.device,
                result_path=f"{base}/result.json",
                heatmap_path=f"{base}/heatmap.png",
                top_tiles=tiles,
            )
        )
        c.status, c.error, c.finished_at = CaseStatus.done, None, datetime.now(UTC)
        c.progress_stage, c.progress_done, c.progress_total = "done", 1, 1
        record_sync(
            db,
            Actions.PREDICT,
            "case",
            case_id,
            details={
                "prediction_id": pid,
                "model_version": pred.model_version[:16],
                "isup_grade": pred.isup_grade,
                "runtime_s": pred.runtime_seconds,
                "low_confidence": bool(pred.low_confidence_reasons),
            },
        )
        db.commit()
    publish_progress(case_id, status="done", stage="done", done=1, total=1)
    log.info("prediction stored", extra={"case_id": case_id, "runtime_s": pred.runtime_seconds})
    return "done"
