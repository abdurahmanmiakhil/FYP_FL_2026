"""Retention, drift report, scheduler and demo seeding."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import func, select

from backend.db.models import Case, Role, User
from backend.db.session import sync_session
from backend.services.maintenance import drift_report, run_retention
from backend.services.storage import get_storage

from .conftest import login, make_user, other_client, upload

API = "/api/v1"


async def test_retention_purges_soft_deleted_after_30_days(pathologist: AsyncClient, slide: Path) -> None:
    cid = (await upload(pathologist, slide, code="PT-RET")).json()["case_id"]
    admin = make_user(Role.admin)
    async with other_client() as a:
        await login(a, admin)
        await a.delete(f"{API}/cases/{cid}")
    with sync_session() as db:
        key = db.get(Case, cid).slide_file  # type: ignore[union-attr]
    assert get_storage().exists(key)
    assert run_retention()["soft_deleted"] == 0  # still inside the 30-day undo window
    counts = run_retention(datetime.now(UTC) + timedelta(days=31))
    assert counts["soft_deleted"] >= 1
    with sync_session() as db:
        assert db.get(Case, cid) is None
    assert not get_storage().exists(key)


async def test_drift_report(pathologist: AsyncClient, slide: Path) -> None:
    await upload(pathologist, slide, code="PT-DRIFT")
    text, flags = drift_report()
    assert "P(csPCa) distribution" in text and "hue" in text and "PANDA" in text
    assert isinstance(flags, list)


def test_scheduler_runs_each_task_once(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from backend.scripts import scheduler

    calls: list[str] = []
    monkeypatch.setattr(scheduler, "run_retention", lambda now: calls.append("retention"))
    monkeypatch.setattr(scheduler, "store_drift_report", lambda now: calls.append("drift") or "k")
    monday = datetime(2026, 9, 28, 4, 0, tzinfo=UTC)
    scheduler.tick(monday)
    scheduler.tick(monday + timedelta(minutes=5))
    assert calls == ["retention", "drift"]


def test_seed_demo_creates_accounts_and_cases(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    from backend.scripts import seed

    monkeypatch.setenv("DEMO_PASSWORD", "Demo-Password-2026!")
    assert seed.main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "admin@gleasonai.demo" in out and "6 demo case(s) queued" in out
    with sync_session() as db:
        assert (
            db.execute(select(func.count()).select_from(User).where(User.email.like("%@gleasonai.demo"))).scalar_one()
            == 4
        )
        synth = db.execute(select(Case).where(Case.slide_seed_id.is_not(None))).unique().scalars().all()
        assert sum(c.patient.pseudonym_code.startswith("SYNTH-DEMO") for c in synth) == 6
    assert seed.main(["demo"]) == 0  # idempotent: no duplicate cases
    assert "0 demo case(s) queued" in capsys.readouterr().out
