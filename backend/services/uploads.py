"""Slide intake: stream to disk with a size cap, hash, validate, dedupe, create the case."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import anyio
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..core.config import get_settings
from ..db.models import Case, CaseStatus, Patient, User
from .antivirus import ScannerUnavailable, VirusFound, scan_file
from .storage import case_prefix, get_storage

ALLOWED = {".tif", ".tiff", ".svs"}
TIFF_MAGIC = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")  # classic + BigTIFF (SVS is TIFF)
PSEUDONYM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")
SEED_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def incoming_dir() -> Path:
    d = get_settings().STORAGE_DIR / ".incoming"
    d.mkdir(parents=True, exist_ok=True)
    return d


def check_suffix(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only .tif, .tiff and .svs slides are accepted.")
    return suffix


def check_pseudonym(code: str) -> str:
    code = code.strip()
    if not PSEUDONYM_RE.fullmatch(code):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Patient pseudonym code must be 2-64 letters, digits, '.', '_' or '-' (no names or ID numbers).",
        )
    if re.fullmatch(r"\d{13}|\d{5}-\d{7}-\d", code):  # looks like a national ID (e.g. Pakistani CNIC)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Do not use national ID numbers.")
    return code


async def stream_to_temp(chunks: AsyncIterator[bytes], max_bytes: int) -> tuple[Path, str, int]:
    """Write an async byte stream to a temp file; returns (path, sha256, size)."""
    tmp = incoming_dir() / f"{uuid.uuid4()}.part"
    h, size = hashlib.sha256(), 0
    try:
        async with await anyio.open_file(tmp, "wb") as f:  # thread-backed: never blocks the event loop
            async for chunk in chunks:
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Slide is larger than 2 GB.")
                h.update(chunk)
                await f.write(chunk)
    except BaseException:
        await anyio.Path(tmp).unlink(missing_ok=True)
        raise
    return tmp, h.hexdigest(), size


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def validate_file(path: Path, suffix: str) -> None:
    """Magic bytes -> antivirus -> OpenSlide (readable, pyramidal). Raises HTTP 422/400."""
    with open(path, "rb") as f:
        head = f.read(4)
    if head not in TIFF_MAGIC:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "File content is not a TIFF/SVS image.")
    try:
        scan_file(path)
    except VirusFound as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"File rejected by antivirus ({e}).") from e
    except ScannerUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Antivirus scanner unavailable; try later.") from e

    from prostate_infer.qc import SlideValidationError, validate_slide_file

    named = path.with_suffix(suffix)  # OpenSlide picks the reader by content, but keep the right suffix
    path.rename(named)
    try:
        validate_slide_file(named).close()
    except SlideValidationError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Invalid slide: {e}") from e
    finally:
        named.rename(path)


async def create_case(
    db: AsyncSession,
    user: User,
    tmp: Path,
    sha: str,
    size: int,
    suffix: str,
    pseudonym_code: str,
    hospital: str,
    slide_id: str | None,
) -> tuple[Case, bool]:
    """Validate the stored temp file and create (or dedupe to) a case. Returns (case, created)."""
    try:
        await run_in_threadpool(validate_file, tmp, suffix)
        existing = (
            (
                await db.execute(
                    select(Case).where(Case.hospital == hospital, Case.slide_sha256 == sha, Case.deleted_at.is_(None))
                )
            )
            .unique()
            .scalar_one_or_none()
        )
        if existing:
            return existing, False
        if slide_id is not None and not SEED_ID_RE.fullmatch(slide_id):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "slide_id: letters, digits, '_' or '-' only.")
        patient = (
            await db.execute(
                select(Patient).where(Patient.hospital == hospital, Patient.pseudonym_code == pseudonym_code)
            )
        ).scalar_one_or_none()
        if patient is None:
            patient = Patient(pseudonym_code=pseudonym_code, hospital=hospital)
            db.add(patient)
            await db.flush()
        case = Case(
            patient_id=patient.id,
            hospital=hospital,
            slide_file="",
            slide_sha256=sha,
            slide_bytes=size,
            slide_format=suffix.lstrip("."),
            slide_seed_id=slide_id or sha[:32],
            uploaded_by=user.id,
            status=CaseStatus.uploaded,
        )
        db.add(case)
        await db.flush()
        key = f"{case_prefix(case.id)}/slide{suffix}"
        await run_in_threadpool(get_storage().put_file, key, tmp, "image/tiff")
        case.slide_file = key
        await db.commit()
        return case, True
    finally:
        await anyio.Path(tmp).unlink(missing_ok=True)
