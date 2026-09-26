"""Audit trail entries + hash chain, user admin, data-subject export/erasure."""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import update

from backend.db.models import AuditLog, Role
from backend.db.session import sync_session

from .conftest import PASSWORD, login, make_user, other_client, upload

API = "/api/v1"


async def test_every_step_is_audited(pathologist: AsyncClient, slide: Path) -> None:
    r = await upload(pathologist, slide, code="PT-AUDIT")
    cid = r.json()["case_id"]
    await pathologist.get(f"{API}/cases/{cid}")
    await pathologist.post(f"{API}/cases/{cid}/reviews", json={"decision": "confirmed"})
    await pathologist.get(f"{API}/cases/{cid}/report.pdf")
    admin = make_user(Role.admin)
    async with other_client() as a:
        await login(a, admin)
        trail = (await a.get(f"{API}/cases/{cid}/audit")).json()
        actions = {e["action"] for e in trail}
        assert {"upload", "view", "predict", "review", "export"} <= actions
        me = pathologist.user  # type: ignore[attr-defined]
        logins = (await a.get(f"{API}/admin/audit", params={"action": "login", "user_id": me.id})).json()
        assert logins["total"] >= 1
        verify = (await a.get(f"{API}/admin/audit/verify")).json()
        assert verify["ok"] is True and verify["entries"] > 5
    # nothing sensitive in audit details
    with sync_session() as db:
        for row in db.query(AuditLog).all():
            assert "PT-AUDIT" not in str(row.details) and PASSWORD not in str(row.details)


async def test_audit_tampering_is_detected(client: AsyncClient) -> None:
    admin = make_user(Role.admin)
    await login(client, admin)
    with sync_session() as db:  # SQLite has no trigger; PostgreSQL would refuse this UPDATE outright
        first = db.query(AuditLog).order_by(AuditLog.id).first()
        assert first is not None
        first_id, original = first.id, first.action
        db.execute(update(AuditLog).where(AuditLog.id == first_id).values(action="edited"))
        db.commit()
    verify = (await client.get(f"{API}/admin/audit/verify")).json()
    assert verify["ok"] is False and verify["first_broken_id"] == first_id
    with sync_session() as db:  # restore for the other tests
        db.execute(update(AuditLog).where(AuditLog.id == first_id).values(action=original))
        db.commit()
    assert (await client.get(f"{API}/admin/audit/verify")).json()["ok"] is True


async def test_user_admin_crud(client: AsyncClient) -> None:
    admin = make_user(Role.admin)
    await login(client, admin)
    weak = await client.post(
        f"{API}/users",
        json={
            "email": "new1@test.example",
            "full_name": "Dr New",
            "role": "pathologist",
            "hospital": "Radboud",
            "password": "weak",
        },
    )
    assert weak.status_code == 422
    r = await client.post(
        f"{API}/users",
        json={
            "email": "New2@Test.example",
            "full_name": "Dr New",
            "role": "pathologist",
            "hospital": "Radboud",
            "password": "Initial-Pass-2026",
        },
    )
    assert r.status_code == 201 and r.json()["email"] == "new2@test.example" and r.json()["must_change_password"]
    uid = r.json()["id"]
    dup = await client.post(
        f"{API}/users",
        json={
            "email": "new2@test.example",
            "full_name": "Dr New",
            "role": "pathologist",
            "hospital": "Radboud",
            "password": "Initial-Pass-2026",
        },
    )
    assert dup.status_code == 409
    listing = (await client.get(f"{API}/users", params={"q": "new2"})).json()
    assert listing["total"] == 1
    upd = await client.patch(f"{API}/users/{uid}", json={"role": "urologist", "is_active": False})
    assert upd.json()["role"] == "urologist" and upd.json()["is_active"] is False
    async with other_client() as c:
        r = await c.post(f"{API}/auth/login", json={"email": "new2@test.example", "password": "Initial-Pass-2026"})
        assert r.status_code == 401  # deactivated
    await client.patch(f"{API}/users/{uid}", json={"is_active": True})
    assert (
        await client.post(f"{API}/users/{uid}/reset-password", json={"new_password": "Reset-Pass-2026!"})
    ).status_code == 204
    async with other_client() as c:
        r = await c.post(f"{API}/auth/login", json={"email": "new2@test.example", "password": "Reset-Pass-2026!"})
        assert r.status_code == 200
    me = await client.patch(f"{API}/users/{admin.id}", json={"is_active": False})
    assert me.status_code == 400  # cannot lock yourself out


async def test_patient_export_and_erasure(pathologist: AsyncClient, tmp_path: Path) -> None:
    from prostate_infer.synthetic import write_synthetic_slide

    p = write_synthetic_slide(tmp_path / "erase.tiff", 3584, 1792, seed=11)
    cid = (await upload(pathologist, p, code="PT-ERASE")).json()["case_id"]
    admin = make_user(Role.admin)
    async with other_client() as a:
        await login(a, admin)
        exp = await a.get(f"{API}/admin/patients/Radboud/PT-ERASE/export")
        assert exp.status_code == 200 and exp.json()["cases"][0]["case_id"] == cid
        assert (await a.delete(f"{API}/admin/patients/Radboud/PT-ERASE")).status_code == 204
        assert (await a.get(f"{API}/cases/{cid}")).status_code == 404
        assert (await a.get(f"{API}/admin/patients/Radboud/PT-ERASE/export")).status_code == 404
        assert (await a.get(f"{API}/admin/audit/verify")).json()["ok"] is True
