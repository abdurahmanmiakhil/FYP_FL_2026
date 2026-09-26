# GleasonAI API reference

Generated from the OpenAPI schema (`python scripts/gen_api_docs.py`). Interactive docs: `/api/v1/docs` in development (disabled in production).

**Authentication:** `POST /api/v1/auth/login` sets httpOnly cookies (`access_token` 15 min, `refresh_token` 7 days, rotating) and a readable `csrf_token` cookie. Every state-changing request (POST/PUT/PATCH/DELETE, except login) must send the same value in the `X-CSRF-Token` header. Non-admin users only see cases of their own hospital (other cases answer 404).

Example with curl:

```bash
curl -sk -c jar -b jar -H 'Content-Type: application/json' \
  -d '{"email":"you@hospital.org","password":"..."}' https://localhost/api/v1/auth/login
CSRF=$(awk '/csrf_token/ {print $7}' jar)
curl -sk -c jar -b jar -H "X-CSRF-Token: $CSRF" -F file=@slide.tiff -F pseudonym_code=PT-0001 \
  https://localhost/api/v1/cases
```

## auth

| Method | Path | Summary |
|---|---|---|
| `POST` | `/api/v1/auth/login` | Sign in (sets httpOnly cookies) |
| `POST` | `/api/v1/auth/refresh` | Rotate the refresh token (silent refresh) |
| `POST` | `/api/v1/auth/logout` | Sign out this session |
| `POST` | `/api/v1/auth/logout-all` | Sign out every session of this user |
| `GET` | `/api/v1/auth/me` | Current user and CSRF token |
| `POST` | `/api/v1/auth/password` | Change your password (signs out other sessions) |
| `POST` | `/api/v1/auth/totp/setup` | Start two-factor setup |
| `POST` | `/api/v1/auth/totp/enable` | Confirm two-factor setup with a code |
| `POST` | `/api/v1/auth/totp/disable` | Turn off two-factor authentication |

## users (admin)

Admin only.

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/users` | List Users |
| `POST` | `/api/v1/users` | Create User |
| `PATCH` | `/api/v1/users/{user_id}` | Update User |
| `POST` | `/api/v1/users/{user_id}/reset-password` | Reset Password |

## cases

| Method | Path | Summary |
|---|---|---|
| `POST` | `/api/v1/cases` | Upload a slide (multipart) and start AI analysis |
| `GET` | `/api/v1/cases` | List cases (filters, sort, pagination) |
| `GET` | `/api/v1/cases/export.csv` | Export the filtered case list as CSV |
| `GET` | `/api/v1/cases/{case_id}` | Case with latest prediction and review history |
| `DELETE` | `/api/v1/cases/{case_id}` | Soft-delete a case (admin) |
| `POST` | `/api/v1/cases/{case_id}/retry` | Re-run AI analysis for a failed case |
| `GET` | `/api/v1/cases/{case_id}/prediction` | Latest AI result |
| `GET` | `/api/v1/cases/{case_id}/prediction/result.json` | Full model output (attention per tile, per-seed logits) |
| `GET` | `/api/v1/cases/{case_id}/heatmap.png` | Attention heatmap (RGBA PNG) |
| `GET` | `/api/v1/cases/{case_id}/thumbnail.jpg` | Low-resolution slide thumbnail |
| `GET` | `/api/v1/cases/{case_id}/tiles/{k}.jpg` | Top-attention tile k (0-7) |
| `GET` | `/api/v1/cases/{case_id}/events` | Live job progress (Server-Sent Events) |
| `GET` | `/api/v1/cases/{case_id}/audit` | Audit trail of one case (admin) |

## uploads (resumable)

| Method | Path | Summary |
|---|---|---|
| `POST` | `/api/v1/uploads` | Start a resumable upload |
| `GET` | `/api/v1/uploads/{upload_id}` | Resume point of an upload |
| `PUT` | `/api/v1/uploads/{upload_id}` | Send one chunk (Content-Range: bytes a-b/total) |
| `DELETE` | `/api/v1/uploads/{upload_id}` | Abort an upload |
| `POST` | `/api/v1/uploads/{upload_id}/complete` | Finish an upload: validate, create the case and start AI analysis |

## reviews & reports

| Method | Path | Summary |
|---|---|---|
| `POST` | `/api/v1/cases/{case_id}/reviews` | Record a review decision (pathologist/urologist) |
| `GET` | `/api/v1/cases/{case_id}/reviews` | Review history (newest first) |
| `GET` | `/api/v1/cases/{case_id}/report.pdf` | PDF case report |

## slide viewer

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/slides/{case_id}.dzi` | DeepZoom descriptor |
| `GET` | `/api/v1/slides/{case_id}_files/{level}/{col}_{row}.jpeg` | DeepZoom tile (JPEG q=85) |

## stats

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/stats` | Counts, agreement, runtime, turnaround |

## admin

Admin only, except `GET /admin/model` (all signed-in users).

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/admin/model` | Model card (any signed-in user) |
| `GET` | `/api/v1/admin/audit` | Audit trail (append-only) |
| `GET` | `/api/v1/admin/audit/verify` | Recompute the audit hash chain |
| `GET` | `/api/v1/admin/patients/{hospital}/{code}/export` | Data-subject export (all data for one pseudonym) |
| `DELETE` | `/api/v1/admin/patients/{hospital}/{code}` | Data-subject erasure: permanently delete a patient's slides, results and reviews |

## health

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/health` | Liveness and dependency status |
| `GET` | `/api/v1/ready` | Ready to predict (a worker has the models loaded) |

## Schemas

### AuditOut

| Field | Type | Required |
|---|---|---|
| `id` | integer | yes |
| `at` | string | yes |
| `user_id` | string | null | yes |
| `user_email` | string | null | yes |
| `action` | string | yes |
| `entity` | string | yes |
| `entity_id` | string | null | yes |
| `ip` | string | null | yes |
| `details` | object | yes |

### CaseCreated

| Field | Type | Required |
|---|---|---|
| `case_id` | string | yes |
| `status` | CaseStatus | yes |
| `duplicate` | boolean |  |
| `events_url` | string | yes |

### CaseDetail

| Field | Type | Required |
|---|---|---|
| `id` | string | yes |
| `patient_code` | string | yes |
| `hospital` | string | yes |
| `status` | CaseStatus | yes |
| `progress_stage` | string | null | yes |
| `progress_done` | integer | yes |
| `progress_total` | integer | yes |
| `error` | string | null | yes |
| `created_at` | string | yes |
| `finished_at` | string | null | yes |
| `uploaded_by_name` | string | null | yes |
| `isup_grade` | integer | null |  |
| `p_cspca` | number | null |  |
| `low_confidence` | boolean |  |
| `review_decision` | Decision | null |  |
| `final_isup` | integer | null |  |
| `reviewer_name` | string | null |  |
| `slide_sha256` | string | yes |
| `slide_bytes` | integer | yes |
| `slide_format` | string | yes |
| `prediction` | PredictionOut | null |  |
| `predictions_count` | integer |  |
| `reviews` | array |  |
| `dzi_url` | string | yes |

### CaseStatus

| Field | Type | Required |
|---|---|---|

### CaseSummary

| Field | Type | Required |
|---|---|---|
| `id` | string | yes |
| `patient_code` | string | yes |
| `hospital` | string | yes |
| `status` | CaseStatus | yes |
| `progress_stage` | string | null | yes |
| `progress_done` | integer | yes |
| `progress_total` | integer | yes |
| `error` | string | null | yes |
| `created_at` | string | yes |
| `finished_at` | string | null | yes |
| `uploaded_by_name` | string | null | yes |
| `isup_grade` | integer | null |  |
| `p_cspca` | number | null |  |
| `low_confidence` | boolean |  |
| `review_decision` | Decision | null |  |
| `final_isup` | integer | null |  |
| `reviewer_name` | string | null |  |

### Decision

| Field | Type | Required |
|---|---|---|

### Health

| Field | Type | Required |
|---|---|---|
| `status` | string | yes |
| `database` | boolean | yes |
| `redis` | boolean | yes |
| `storage` | boolean | yes |
| `workers` | integer | yes |
| `models_loaded` | boolean | yes |
| `queue_length` | integer | yes |

### LoginIn

| Field | Type | Required |
|---|---|---|
| `email` | string | yes |
| `password` | string | yes |
| `otp` | string | null |  |

### ModelCard

| Field | Type | Required |
|---|---|---|
| `model_version` | string | null | yes |
| `preprocessing_version` | string | yes |
| `ready` | boolean | yes |
| `workers` | array | yes |
| `thresholds` | object | yes |
| `thesis_results` | object | yes |
| `training_data` | object | yes |
| `intended_use` | string | yes |
| `limitations` | array | yes |

### Page_AuditOut_

| Field | Type | Required |
|---|---|---|
| `items` | array | yes |
| `total` | integer | yes |
| `page` | integer | yes |
| `page_size` | integer | yes |

### Page_CaseSummary_

| Field | Type | Required |
|---|---|---|
| `items` | array | yes |
| `total` | integer | yes |
| `page` | integer | yes |
| `page_size` | integer | yes |

### Page_UserOut_

| Field | Type | Required |
|---|---|---|
| `items` | array | yes |
| `total` | integer | yes |
| `page` | integer | yes |
| `page_size` | integer | yes |

### PasswordChangeIn

| Field | Type | Required |
|---|---|---|
| `current_password` | string | yes |
| `new_password` | string | yes |

### PasswordResetIn

| Field | Type | Required |
|---|---|---|
| `new_password` | string | yes |

### PredictionOut

| Field | Type | Required |
|---|---|---|
| `id` | string | yes |
| `model_version` | string | yes |
| `preprocessing_version` | string | yes |
| `p_cancer` | number | yes |
| `p_cspca` | number | yes |
| `p_isup` | array | yes |
| `isup_grade` | integer | yes |
| `gleason_hint` | string | yes |
| `operating_point_flags` | object | yes |
| `thresholds` | object | yes |
| `n_tiles` | integer | yes |
| `n_tiles_total` | integer | yes |
| `seed_std_p_cspca` | number | yes |
| `low_confidence_reasons` | array | yes |
| `qc` | object | yes |
| `slide_width` | integer | yes |
| `slide_height` | integer | yes |
| `runtime_seconds` | number | yes |
| `device` | string | yes |
| `created_at` | string | yes |
| `heatmap_url` | string |  |
| `top_tiles` | array |  |
| `status` | string |  |
| `disclaimer` | string |  |

### ReviewIn

| Field | Type | Required |
|---|---|---|
| `decision` | Decision | yes |
| `final_isup` | integer | null |  |
| `comment` | string | null |  |

### ReviewOut

| Field | Type | Required |
|---|---|---|
| `id` | string | yes |
| `case_id` | string | yes |
| `prediction_id` | string | null | yes |
| `decision` | Decision | yes |
| `final_isup` | integer | null | yes |
| `comment` | string | null | yes |
| `created_at` | string | yes |
| `reviewer_name` | string | null |  |
| `reviewer_role` | Role | null |  |

### Role

| Field | Type | Required |
|---|---|---|

### SessionOut

| Field | Type | Required |
|---|---|---|
| `user` | UserOut | yes |
| `csrf_token` | string | yes |
| `access_expires_in` | integer | yes |
| `idle_timeout_seconds` | integer | yes |

### Stats

| Field | Type | Required |
|---|---|---|
| `total_cases` | integer | yes |
| `cases_today` | integer | yes |
| `by_status` | object | yes |
| `by_grade` | object | yes |
| `awaiting_review` | integer | yes |
| `low_confidence_awaiting` | integer | yes |
| `agreement_pct` | number | null | yes |
| `reviewed` | integer | yes |
| `decisions` | object | yes |
| `mean_runtime_s` | number | null | yes |
| `median_turnaround_s` | number | null | yes |
| `queue_length` | integer | yes |

### TopTileOut

| Field | Type | Required |
|---|---|---|
| `rank` | integer | yes |
| `x` | integer | yes |
| `y` | integer | yes |
| `attention` | number | yes |
| `url` | string |  |

### TotpCodeIn

| Field | Type | Required |
|---|---|---|
| `code` | string | yes |

### TotpSetupOut

| Field | Type | Required |
|---|---|---|
| `otpauth_uri` | string | yes |
| `secret` | string | yes |
| `qr_svg` | string | yes |

### UploadComplete

| Field | Type | Required |
|---|---|---|
| `upload_id` | string | yes |
| `pseudonym_code` | string | yes |
| `hospital` | string | null |  |
| `slide_id` | string | null |  |

### UploadInit

| Field | Type | Required |
|---|---|---|
| `filename` | string | yes |
| `size` | integer | yes |

### UploadState

| Field | Type | Required |
|---|---|---|
| `upload_id` | string | yes |
| `received_bytes` | integer | yes |
| `total_bytes` | integer | yes |
| `chunk_bytes` | integer | yes |

### UserCreate

| Field | Type | Required |
|---|---|---|
| `email` | string | yes |
| `full_name` | string | yes |
| `role` | Role | yes |
| `hospital` | string | yes |
| `password` | string | yes |

### UserOut

| Field | Type | Required |
|---|---|---|
| `id` | string | yes |
| `email` | string | yes |
| `full_name` | string | yes |
| `role` | Role | yes |
| `hospital` | string | yes |
| `is_active` | boolean | yes |
| `totp_enabled` | boolean | yes |
| `must_change_password` | boolean | yes |
| `created_at` | string | yes |
| `last_login_at` | string | null |  |

### UserUpdate

| Field | Type | Required |
|---|---|---|
| `full_name` | string | null |  |
| `role` | Role | null |  |
| `hospital` | string | null |  |
| `is_active` | boolean | null |  |
