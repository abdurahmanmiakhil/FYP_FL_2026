"""Backend settings (pydantic-settings; every secret comes from the environment)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET = "dev-only-insecure-secret-change-me-0123456789abcdef"


class Settings(BaseSettings):
    ENV: Literal["dev", "test", "prod"] = "dev"
    PROJECT_NAME: str = "GleasonAI - Prostate Grading Dashboard"
    API_PREFIX: str = "/api/v1"
    PUBLIC_URL: str = "http://localhost:3000"  # frontend origin (CORS, links in reports)

    # --- security
    SECRET_KEY: SecretStr = SecretStr(DEV_SECRET)
    ACCESS_TOKEN_MINUTES: int = 15
    REFRESH_TOKEN_DAYS: int = 7
    IDLE_TIMEOUT_MINUTES: int = 15
    COOKIE_SECURE: bool = False  # True in prod (HTTPS only)
    COOKIE_DOMAIN: str | None = None
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    LOGIN_RATE_PER_MIN: int = 5
    UPLOAD_RATE_PER_HOUR: int = 30
    LOCKOUT_THRESHOLD: int = 5
    LOCKOUT_MINUTES: int = 15
    PASSWORD_MIN_LENGTH: int = 12

    # --- data
    DATABASE_URL: str = "sqlite+aiosqlite:///./gleasonai.db"
    REDIS_URL: str = "redis://localhost:6379/0"
    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    STORAGE_DIR: Path = Path("./storage")
    SLIDE_CACHE_DIR: Path = Path("./storage/.slide-cache")  # local copies of S3 slides for OpenSlide
    S3_ENDPOINT_URL: str | None = None
    S3_BUCKET: str = "gleasonai"
    S3_ACCESS_KEY: SecretStr = SecretStr("")
    S3_SECRET_KEY: SecretStr = SecretStr("")
    S3_REGION: str = "us-east-1"
    # request server-side encryption per object ("AES256" on AWS S3 / S3 with a KMS). SeaweedFS encrypts
    # at rest with -s3.encryptVolumeData instead, so this stays empty there.
    S3_SSE: str = ""
    MAX_UPLOAD_BYTES: int = 2 * 1024**3
    UPLOAD_CHUNK_BYTES: int = 8 * 1024**2
    RETENTION_DAYS: int = 3650  # cases older than this are deleted by the retention job (0 = never)
    CLAMAV_HOST: str | None = None  # optional antivirus (clamd) - e.g. "clamav"
    CLAMAV_PORT: int = 3310

    # --- jobs
    JOB_TIMEOUT_SECONDS: int = 1800
    JOB_RETRIES: int = 2
    JOBS_SYNC: bool = False  # tests: run jobs in-process
    DEMO_SLIDES_DIR: Path | None = None  # real PANDA slides for `make seed-demo`

    # --- observability
    LOG_LEVEL: str = "INFO"
    SENTRY_DSN: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("DEMO_SLIDES_DIR", "S3_ENDPOINT_URL", "CLAMAV_HOST", "SENTRY_DSN", mode="before")
    @classmethod
    def _empty_is_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("SECRET_KEY")
    @classmethod
    def _secret_strong(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        return v

    def check_production(self) -> None:
        """Refuse to start in prod with development defaults."""
        if self.ENV != "prod":
            return
        problems = []
        if self.SECRET_KEY.get_secret_value() == DEV_SECRET:
            problems.append("SECRET_KEY is the development default")
        if not self.COOKIE_SECURE:
            problems.append("COOKIE_SECURE must be true (HTTPS)")
        if self.DATABASE_URL.startswith("sqlite"):
            problems.append("use PostgreSQL in production")
        if problems:
            raise RuntimeError("unsafe production configuration: " + "; ".join(problems))

    @property
    def sync_database_url(self) -> str:
        """Driver used by the (synchronous) RQ worker and Alembic."""
        return self.DATABASE_URL.replace("postgresql+asyncpg", "postgresql+psycopg").replace(
            "sqlite+aiosqlite", "sqlite"
        )

    @property
    def docs_enabled(self) -> bool:
        return self.ENV != "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
