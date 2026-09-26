"""API request/response models (the OpenAPI contract the frontend client is generated from)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from ..db.models import CaseStatus, Decision, Role

DISCLAIMER = "AI decision support only. Final diagnosis requires a pathologist."

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")


def _email(v: str) -> str:
    """Permissive email check (hospital intranets often use special-use domains such as .local)."""
    v = v.strip().lower()
    if len(v) > 254 or not _EMAIL_RE.fullmatch(v):
        raise ValueError("not a valid email address")
    return v


Email = Annotated[str, AfterValidator(_email)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- auth / users


class LoginIn(BaseModel):
    email: Email
    password: str = Field(min_length=1, max_length=128)
    otp: str | None = Field(default=None, max_length=10, description="TOTP code when two-factor is enabled")


class UserOut(ORM):
    id: str
    email: str
    full_name: str
    role: Role
    hospital: str
    is_active: bool
    totp_enabled: bool
    must_change_password: bool
    created_at: datetime
    last_login_at: datetime | None = None


class SessionOut(BaseModel):
    user: UserOut
    csrf_token: str
    access_expires_in: int
    idle_timeout_seconds: int


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class TotpSetupOut(BaseModel):
    otpauth_uri: str
    secret: str
    qr_svg: str


class TotpCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class UserCreate(BaseModel):
    email: Email
    full_name: str = Field(min_length=2, max_length=120)
    role: Role
    hospital: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=1, max_length=128)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    role: Role | None = None
    hospital: str | None = Field(default=None, min_length=2, max_length=120)
    is_active: bool | None = None


class PasswordResetIn(BaseModel):
    new_password: str = Field(min_length=1, max_length=128)


# ---------------------------------------------------------------- cases


class TopTileOut(BaseModel):
    rank: int
    x: int
    y: int
    attention: float
    url: str = ""


class PredictionOut(ORM):
    id: str
    model_version: str
    preprocessing_version: str
    p_cancer: float
    p_cspca: float
    p_isup: list[float]
    isup_grade: int
    gleason_hint: str
    operating_point_flags: dict[str, bool]
    thresholds: dict[str, float]
    n_tiles: int
    n_tiles_total: int
    seed_std_p_cspca: float
    low_confidence_reasons: list[str]
    qc: dict[str, Any]
    slide_width: int
    slide_height: int
    runtime_seconds: float
    device: str
    created_at: datetime
    heatmap_url: str = ""
    top_tiles: list[TopTileOut] = []
    status: Literal["provisional", "reviewed"] = "provisional"
    disclaimer: str = DISCLAIMER


class ReviewIn(BaseModel):
    decision: Decision
    final_isup: int | None = Field(default=None, ge=0, le=5, description="required when amending")
    comment: str | None = Field(default=None, max_length=4000)


class ReviewOut(ORM):
    id: str
    case_id: str
    prediction_id: str | None
    decision: Decision
    final_isup: int | None
    comment: str | None
    created_at: datetime
    reviewer_name: str | None = None
    reviewer_role: Role | None = None


class CaseSummary(BaseModel):
    id: str
    patient_code: str
    hospital: str
    status: CaseStatus
    progress_stage: str | None
    progress_done: int
    progress_total: int
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    uploaded_by_name: str | None
    isup_grade: int | None = None
    p_cspca: float | None = None
    low_confidence: bool = False
    review_decision: Decision | None = None
    final_isup: int | None = None
    reviewer_name: str | None = None


class CaseDetail(CaseSummary):
    slide_sha256: str
    slide_bytes: int
    slide_format: str
    prediction: PredictionOut | None = None
    predictions_count: int = 0
    reviews: list[ReviewOut] = []
    dzi_url: str


class CaseCreated(BaseModel):
    case_id: str
    status: CaseStatus
    duplicate: bool = False
    events_url: str


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int


class UploadInit(BaseModel):
    filename: str = Field(max_length=255)
    size: int = Field(gt=0)


class UploadState(BaseModel):
    upload_id: str
    received_bytes: int
    total_bytes: int
    chunk_bytes: int


class UploadComplete(BaseModel):
    upload_id: str
    pseudonym_code: str = Field(min_length=2, max_length=64)
    hospital: str | None = Field(default=None, max_length=120, description="admin only; defaults to your hospital")
    slide_id: str | None = Field(default=None, max_length=64, description="optional original slide id (e.g. PANDA)")


# ---------------------------------------------------------------- stats / admin


class Stats(BaseModel):
    total_cases: int
    cases_today: int
    by_status: dict[str, int]
    by_grade: dict[str, int]
    awaiting_review: int
    low_confidence_awaiting: int
    agreement_pct: float | None
    reviewed: int
    decisions: dict[str, int]
    mean_runtime_s: float | None
    median_turnaround_s: float | None
    queue_length: int


class AuditOut(ORM):
    id: int
    at: datetime
    user_id: str | None
    user_email: str | None
    action: str
    entity: str
    entity_id: str | None
    ip: str | None
    details: dict[str, Any]


class ModelCard(BaseModel):
    model_version: str | None
    preprocessing_version: str
    ready: bool
    workers: list[dict[str, str]]
    thresholds: dict[str, Any]
    thesis_results: dict[str, Any]
    training_data: dict[str, Any]
    intended_use: str
    limitations: list[str]


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    redis: bool
    storage: bool
    workers: int
    models_loaded: bool
    queue_length: int
