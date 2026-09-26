"""Permissions matrix and hospital row-level access."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from backend.db.models import Role

from .conftest import login, make_user, other_client, upload

API = "/api/v1"


@pytest.fixture
async def case_id(pathologist: AsyncClient, slide: Path) -> str:
    r = await upload(pathologist, slide, code="PT-PERM")
    return str(r.json()["case_id"])


# (role, method, path, expected) - {case} is a case in hospital "Radboud"
MATRIX = [
    (Role.admin, "GET", "/users", 200),
    (Role.pathologist, "GET", "/users", 403),
    (Role.urologist, "GET", "/users", 403),
    (Role.admin, "GET", "/admin/audit", 200),
    (Role.pathologist, "GET", "/admin/audit", 403),
    (Role.pathologist, "GET", "/admin/model", 200),
    (Role.urologist, "GET", "/cases/{case}", 200),
    (Role.admin, "GET", "/cases/{case}", 200),
    (Role.pathologist, "DELETE", "/cases/{case}", 403),
    (Role.urologist, "DELETE", "/cases/{case}", 403),
    (Role.admin, "POST", "/cases/{case}/reviews", 403),
    (Role.admin, "GET", "/cases/{case}/audit", 200),
    (Role.pathologist, "GET", "/cases/{case}/audit", 403),
    (Role.admin, "DELETE", "/cases/{case}", 204),
]


@pytest.mark.parametrize(("role", "method", "path", "expected"), MATRIX)
async def test_permission_matrix(role: Role, method: str, path: str, expected: int, case_id: str) -> None:
    u = make_user(role, hospital="Radboud")
    async with other_client() as c:
        await login(c, u)
        kw = {"json": {"decision": "confirmed"}} if method == "POST" else {}
        r = await c.request(method, API + path.format(case=case_id), **kw)
        assert r.status_code == expected, r.text


async def test_other_hospital_cannot_see_case(case_id: str) -> None:
    u = make_user(Role.pathologist, hospital="Karolinska")
    async with other_client() as c:
        await login(c, u)
        for path in (
            f"/cases/{case_id}",
            f"/cases/{case_id}/heatmap.png",
            f"/cases/{case_id}/report.pdf",
            f"/slides/{case_id}.dzi",
            f"/cases/{case_id}/events",
        ):
            assert (await c.get(API + path)).status_code == 404, path
        assert (await c.post(f"{API}/cases/{case_id}/reviews", json={"decision": "confirmed"})).status_code == 404
        listing = (await c.get(f"{API}/cases", params={"q": "PT-PERM"})).json()
        assert listing["total"] == 0


async def test_cannot_upload_for_other_hospital(pathologist: AsyncClient, slide_b: Path) -> None:
    r = await upload(pathologist, slide_b, code="PT-X", hospital="Karolinska")
    assert r.status_code == 403


async def test_soft_deleted_case_disappears(case_id: str, pathologist: AsyncClient) -> None:
    admin = make_user(Role.admin)
    async with other_client() as c:
        await login(c, admin)
        assert (await c.delete(f"{API}/cases/{case_id}")).status_code == 204
    assert (await pathologist.get(f"{API}/cases/{case_id}")).status_code == 404
