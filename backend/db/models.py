"""SQLAlchemy 2.0 models. Patients are pseudonym codes only - no names, no national IDs."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Role(enum.StrEnum):
    admin = "admin"
    pathologist = "pathologist"
    urologist = "urologist"


class CaseStatus(enum.StrEnum):
    uploaded = "uploaded"
    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"


class Decision(enum.StrEnum):
    confirmed = "confirmed"
    amended = "amended"
    rejected = "rejected"


TS = DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[Role] = mapped_column(Enum(Role, name="role"))
    hospital: Mapped[str] = mapped_column(String(120), index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    totp_secret_enc: Mapped[str | None] = mapped_column(String(255), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    password_changed_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class Session(Base):
    """One login session. Access tokens carry its id; refresh tokens rotate within it."""

    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    refresh_jti: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = (UniqueConstraint("hospital", "pseudonym_code", name="uq_patient_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    pseudonym_code: Mapped[str] = mapped_column(String(64), index=True)
    hospital: Mapped[str] = mapped_column(String(120), index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        Index("ix_cases_hospital_status", "hospital", "status"),
        Index("ix_cases_sha", "hospital", "slide_sha256"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    hospital: Mapped[str] = mapped_column(String(120))
    slide_file: Mapped[str] = mapped_column(String(255))  # storage key (random UUID name)
    slide_sha256: Mapped[str] = mapped_column(String(64))
    slide_bytes: Mapped[int] = mapped_column(BigInteger)
    slide_format: Mapped[str] = mapped_column(String(8))
    slide_seed_id: Mapped[str] = mapped_column(String(64))  # seeds tile cap/bag sampling (PANDA image_id)
    uploaded_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[CaseStatus] = mapped_column(Enum(CaseStatus, name="case_status"), default=CaseStatus.uploaded)
    progress_stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(TS, nullable=True, index=True)
    deleted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    patient: Mapped[Patient] = relationship(lazy="joined")
    uploader: Mapped[User | None] = relationship(lazy="joined", foreign_keys=[uploaded_by])
    predictions: Mapped[list[Prediction]] = relationship(
        back_populates="case", order_by="Prediction.created_at.desc()", cascade="all, delete-orphan"
    )
    reviews: Mapped[list[Review]] = relationship(
        back_populates="case", order_by="Review.created_at.desc()", cascade="all, delete-orphan"
    )


class Prediction(Base):
    """One model run. A case can be re-run (e.g. new model version); the newest is current."""

    __tablename__ = "predictions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    model_version: Mapped[str] = mapped_column(String(64), index=True)
    preprocessing_version: Mapped[str] = mapped_column(String(200))
    p_cancer: Mapped[float] = mapped_column(Float)
    p_cspca: Mapped[float] = mapped_column(Float)
    p_isup: Mapped[list[Any]] = mapped_column(JSON)
    isup_grade: Mapped[int] = mapped_column(Integer, index=True)
    gleason_hint: Mapped[str] = mapped_column(String(40))
    operating_point_flags: Mapped[dict[str, Any]] = mapped_column(JSON)
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSON)
    n_tiles: Mapped[int] = mapped_column(Integer)
    n_tiles_total: Mapped[int] = mapped_column(Integer)
    seed_std_p_cspca: Mapped[float] = mapped_column(Float)
    low_confidence_reasons: Mapped[list[Any]] = mapped_column(JSON)
    qc: Mapped[dict[str, Any]] = mapped_column(JSON)
    slide_width: Mapped[int] = mapped_column(Integer)
    slide_height: Mapped[int] = mapped_column(Integer)
    runtime_seconds: Mapped[float] = mapped_column(Float)
    device: Mapped[str] = mapped_column(String(20))
    result_path: Mapped[str] = mapped_column(String(255))  # full SlidePrediction JSON (attention, per-seed)
    heatmap_path: Mapped[str] = mapped_column(String(255))
    top_tiles: Mapped[list[Any]] = mapped_column(JSON)  # [{key, x, y, attention, rank}]
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="predictions")

    @property
    def low_confidence(self) -> bool:
        return bool(self.low_confidence_reasons)


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    prediction_id: Mapped[str | None] = mapped_column(ForeignKey("predictions.id", ondelete="SET NULL"), nullable=True)
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decision: Mapped[Decision] = mapped_column(Enum(Decision, name="decision"))
    final_isup: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="reviews")
    reviewer: Mapped[User | None] = relationship(lazy="joined")


class Upload(Base):
    """A resumable chunked upload in progress (frontend); becomes a Case when completed."""

    __tablename__ = "uploads"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    suffix: Mapped[str] = mapped_column(String(8))
    total_bytes: Mapped[int] = mapped_column(BigInteger)
    received_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class AuditLog(Base):
    """Append-only (enforced by a PostgreSQL trigger) and hash-chained (tamper-evident)."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    user_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    action: Mapped[str] = mapped_column(String(40), index=True)
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
