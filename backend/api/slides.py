"""DeepZoom slide viewer endpoints (OpenSeadragon). The slide never leaves the server: the
browser only receives JPEG viewer tiles."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from ..db.models import User
from ..services import slides as dz
from ..services.storage import get_storage
from .deps import DB, CurrentUser, get_case_for

router = APIRouter(prefix="/slides", tags=["slide viewer"])

# (user id, case id) -> (slide key, local path, expiry): skips the case lookup on every tile request
_access: dict[tuple[str, str], tuple[str, Path, float]] = {}
ACCESS_TTL = 60.0


def forget(case_id: str) -> None:
    """Drop cached access for a case (after delete/erasure)."""
    for k in [k for k in _access if k[1] == case_id]:
        _access.pop(k, None)
    dz.cache.evict(case_id)


async def _slide(db: DB, user: User, case_id: str) -> tuple[str, Path]:
    hit = _access.get((user.id, case_id))
    if hit and hit[2] > time.monotonic():
        return hit[0], hit[1]
    case = await get_case_for(db, user, case_id)
    path = await run_in_threadpool(get_storage().local_path, case.slide_file)
    if len(_access) > 5000:
        _access.clear()
    _access[(user.id, case_id)] = (case.id, path, time.monotonic() + ACCESS_TTL)
    return case.id, path


@router.get("/{case_id}.dzi", summary="DeepZoom descriptor", response_class=Response)
async def get_dzi(case_id: str, db: DB, user: CurrentUser) -> Response:
    key, path = await _slide(db, user, case_id)
    xml = await run_in_threadpool(dz.dzi_xml, key, path)
    return Response(xml, media_type="application/xml", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{case_id}_files/{level}/{col}_{row}.jpeg", summary="DeepZoom tile (JPEG q=85)", response_class=Response)
async def get_tile(case_id: str, level: int, col: int, row: int, db: DB, user: CurrentUser) -> Response:
    key, path = await _slide(db, user, case_id)
    try:
        data = await run_in_threadpool(dz.tile_jpeg, key, path, level, col, row)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tile out of range.") from None
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
