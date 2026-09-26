"""Backend test fixtures.

- SQLite database created by running the real Alembic migrations.
- fakeredis instead of Redis; jobs run in-process (JOBS_SYNC) through the real worker task.
- The real prostate_infer pipeline with a fake bundle (random heads) and FakeViT for Phikon.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="gleasonai-test-"))
os.environ.update(
    {
        "ENV": "test",
        "DATABASE_URL": f"sqlite+aiosqlite:///{_TMP}/test.db",
        "STORAGE_DIR": str(_TMP / "storage"),
        "SLIDE_CACHE_DIR": str(_TMP / "slide-cache"),
        "JOBS_SYNC": "true",
        "LOG_LEVEL": "WARNING",
        "SECRET_KEY": "test-secret-key-0123456789abcdef0123456789",
        "MODEL_CACHE_DIR": str(_TMP / "models"),
    }
)

import fakeredis  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from prostate_infer.synthetic import write_synthetic_slide  # noqa: E402
from prostate_infer.testing import use_fake_models, write_fake_bundle  # noqa: E402

from backend.core.redis import set_redis  # noqa: E402
from backend.core.security import CSRF_COOKIE, CSRF_HEADER, hash_password  # noqa: E402
from backend.db.models import Role, User  # noqa: E402
from backend.db.session import sync_session  # noqa: E402

PASSWORD = "Correct-Horse-42!"
BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _migrate() -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def redis() -> Iterator[fakeredis.FakeRedis]:
    r = fakeredis.FakeRedis()
    set_redis(r)
    yield r
    set_redis(None)


@pytest.fixture(scope="session")
def fake_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_fake_bundle(tmp_path_factory.mktemp("bundle"))


@pytest.fixture(autouse=True)
def fake_models(monkeypatch: pytest.MonkeyPatch, fake_bundle: Path) -> None:
    use_fake_models(monkeypatch, fake_bundle)


_seeds = iter(range(100, 1_000_000))


@pytest.fixture
def slide(tmp_path: Path) -> Path:
    """A unique synthetic slide per test (identical files would be deduplicated across tests)."""
    return write_synthetic_slide(tmp_path / "biopsy.tiff", 4480, 2240, seed=next(_seeds))


@pytest.fixture
def slide_b(tmp_path: Path) -> Path:
    return write_synthetic_slide(tmp_path / "biopsy-b.svs", 4480, 2240, seed=next(_seeds))


_counter = iter(range(1, 1_000_000))


def make_user(role: Role, hospital: str = "Radboud", password: str = PASSWORD, **kw: object) -> User:
    n = next(_counter)
    with sync_session() as db:
        u = User(
            email=f"{role.value}{n}@test.example",
            full_name=f"{role.value.title()} {n}",
            role=role,
            hospital=hospital,
            password_hash=hash_password(password),
            **kw,
        )
        db.add(u)
        db.commit()
        return u


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from backend.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def other_client(**cookies: str) -> AsyncClient:
    """A second browser (optionally holding copied cookies)."""
    from backend.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies)


async def login(c: AsyncClient, user: User, password: str = PASSWORD) -> AsyncClient:
    r = await c.post("/api/v1/auth/login", json={"email": user.email, "password": password})
    assert r.status_code == 200, r.text
    c.headers[CSRF_HEADER] = c.cookies[CSRF_COOKIE]
    return c


@pytest.fixture
async def pathologist(client: AsyncClient) -> AsyncClient:
    client.user = make_user(Role.pathologist)  # type: ignore[attr-defined]
    return await login(client, client.user)  # type: ignore[attr-defined]


async def upload(c: AsyncClient, path: Path, code: str = "PT-0001", **form: str):  # type: ignore[no-untyped-def]
    data = path.read_bytes()  # noqa: ASYNC240 - tiny test files
    return await c.post(
        "/api/v1/cases", files={"file": (path.name, data, "image/tiff")}, data={"pseudonym_code": code, **form}
    )
