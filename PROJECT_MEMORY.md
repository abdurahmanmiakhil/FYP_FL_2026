# PROJECT_MEMORY.md - everything an LLM (or new developer) needs to know about GleasonAI

> Read this first. It describes what the project is, how it was built, every technology used, where
> each piece lives, how to run/test it, the rules that must not be broken, and known gaps.
> Last updated: 2026-09-26 (commit after `0435549` on branch `clean-main`).

---

## 1. What this project is

**GleasonAI** is the *telehealth review layer* of the BS thesis
**"Federated Deep Learning for Prostate Cancer Gleason Grading via Histopathology Images and Telehealth
Integration"** (NUML, 2026, author GitHub `abdurahmanmiakhil`, repo `FL_FYP`).

A clinician uploads an H&E prostate core-biopsy **whole-slide image** (.tif/.tiff/.svs, ≤ 2 GB). The
server runs the thesis's **FedAvg 5-seed ensemble** and shows:
- ISUP grade (0-5) + Gleason pattern hint,
- P(cancer) = P(ISUP ≥ 1), P(csPCa) = P(ISUP ≥ 2), probability of each ISUP class,
- clinical operating-point flags (Youden, 90 % / 95 % sensitivity thresholds),
- attention heatmap over the slide + the 8 top-attention tiles,
- low-confidence / out-of-distribution warnings.

The clinician **confirms / amends / rejects** the result, downloads a **PDF report**, and every action is in an
**append-only, hash-chained audit trail**. Disclaimer everywhere: *"AI decision support only. Final diagnosis
requires a pathologist."* It is a research prototype, **not a medical device**.

It was built from the spec `~/Downloads/Prostate Grading Dashboard — 5-Phase Build Prompts.pdf` (5 phases:
inference engine → backend → frontend → security/validation → deployment). The user asked for **all 5 phases**.

---

## 2. Architecture

```
Browser (Next.js dashboard)
   │ HTTPS, httpOnly JWT cookies, CSRF header
   ▼
Caddy (auto-HTTPS, HSTS, blocks /metrics)  ──►  frontend  (Next.js 15 standalone server, :3000)
   │ /api/*
   ▼
api  (FastAPI, :8000) ──► PostgreSQL 16 (users, sessions, patients, cases, predictions, reviews, audit_log, uploads)
   │                  ──► SeaweedFS S3 (slides, heatmaps, tile crops, result JSON; encrypted at rest)
   │                  ──► Redis 7 (RQ job queue, live progress, rate limits)
   ▼ job "backend.worker.tasks.process_case"
worker (RQ SimpleWorker + prostate_infer: Phikon + 5 FedAvg heads, models loaded once)
scheduler (daily retention, weekly drift report)   backup (nightly encrypted pg_dump + bucket tar)
optional profiles: monitoring (Prometheus, Alertmanager, Grafana), antivirus (ClamAV)
```

Key principle: **the slide never leaves the server**; the browser only receives DeepZoom JPEG tiles and results.
The API image has **no PyTorch** (it enqueues jobs by dotted path); only the worker imports torch.

---

## 3. Repository layout

| Path | Contents |
|---|---|
| `inference/` | Python package **`prostate_infer`** (Phase 1) |
| `inference/prostate_infer/model.py` | Encoder (Phikon [CLS]), GatedABMIL, mono_sigmoid, class_probs(_from_s), grade(_from_s), tune_thresholds - **verbatim from notebooks** |
| `.../preprocess.py` | read_rgb, lowres_rgb, tissue_mask, tile_coords, SlideTiles; constants TILE_PX=224, TISSUE_MIN=0.5, MAX_TILES=768, MAX_BAG=512 - **verbatim nb01** |
| `.../predict.py` | Engine singleton (loads Phikon + 5 heads), predict_slide(), encode_tiles, bag_indices, make_heatmap, top_tiles; low-confidence rules |
| `.../assets.py` | Settings (MODEL_SOURCE local/hf, MODEL_DIR, HF_*, PHIKON_*, OFFLINE), bundle verification (sha256 manifest), model_version, load_phikon, HF token from `/run/secrets/hf_token` |
| `.../bundle.py` | build_bundle (thesis outputs → bundle), fit_ensemble_thresholds |
| `.../qc.py` | validate_slide_file (suffix, ≤2 GB, OpenSlide, ≥2 levels), slide_mpp, colour_stats, slide_qc (OOD vs PANDA Table P03) |
| `.../schemas.py` | SlidePrediction, SlideQC, OperatingPointFlags, DISCLAIMER, GLEASON_HINT, PREPROCESSING_VERSION (torch-free) |
| `.../synthetic.py` | writes synthetic pyramidal H&E-like TIFFs (tests/demos only) |
| `.../testing.py` | write_fake_bundle, FakeViT, use_fake_models (test helpers shared with backend tests) |
| `inference/scripts/` | fit_ensemble_thresholds.py (+HF upload), publish_model_repo.py |
| `inference/tests/` | test_fast.py, test_cli.py, test_thesis_bundle.py (needs THESIS_BUNDLE), test_parity.py (slow, needs PANDA_DIR) |
| `backend/` | FastAPI app (Phase 2), package imported as `backend.*` (PYTHONPATH=/app) |
| `backend/main.py` | create_app(): CORS, request-id/logging/metrics/CSRF/security-header middleware, routers, `/metrics`, `/healthz` |
| `backend/core/` | config.py (Settings), security.py (argon2id, JWT, CSRF, TOTP AES-GCM), logging.py (JSON logs, redaction), ratelimit.py, redis.py, metrics.py (Prometheus) |
| `backend/db/` | models.py (SQLAlchemy 2.0), session.py (async engine for API, sync engine for worker/alembic) |
| `backend/migrations/` | Alembic; `0001_initial` incl. PostgreSQL trigger blocking UPDATE/DELETE/TRUNCATE on audit_log |
| `backend/api/` | auth, users, cases (+uploads router), reviews (+report.pdf), slides (DZI), stats, admin (model card, audit, patient export/erase), health, deps (auth/roles/row access) |
| `backend/services/` | storage (Local/S3), uploads, audit (hash chain), jobs (RQ + progress), cases (list queries), reports (reportlab PDF), slides (DeepZoom LRU), antivirus (clamd), model_info, maintenance (retention, drift) |
| `backend/worker/` | tasks.py (process_case), run.py (worker entry, publishes model info), healthcheck.py |
| `backend/scripts/` | seed.py (`admin`, `demo`), scheduler.py |
| `backend/tests/` | pytest suites (auth, permissions, cases, audit/admin, maintenance) |
| `frontend/` | Next.js dashboard (Phase 3) |
| `frontend/app/` | `login`, `about` (public); `(app)/` group with AuthProvider+AppShell: `dashboard`, `cases`, `cases/new`, `cases/[id]`, `admin/users`, `admin/audit`, `admin/model`, `settings`; error/not-found pages |
| `frontend/components/` | app-shell (sidebar, user menu, disclaimer banner), slide-viewer (OpenSeadragon), result-card, review-panel, case-progress (SSE), top-tiles, clinical (GradeBadge, ProbabilityBar, charts, KPI), audit-list, fl-diagram, login-form, `ui/` (shadcn-style Radix components) |
| `frontend/lib/` | api/client.ts (openapi-fetch, CSRF, silent refresh), api/hooks.ts (TanStack Query), api/schema.d.ts (**generated**), auth.tsx (session, idle timeout), upload.ts (resumable chunks), grades.ts, format.ts, validation.ts |
| `frontend/middleware.ts` | redirects to /login without session; /admin role gate (UX only) |
| `frontend/tests/` | Vitest unit tests; `frontend/e2e/` Playwright (global-setup makes a synthetic slide via the worker image) |
| `docker/` | api.Dockerfile, worker.Dockerfile (targets deps/test/runtime), worker-cuda.Dockerfile, frontend.Dockerfile, api-entrypoint.sh (runs migrations), caddy/Caddyfile, backup/ (Dockerfile, backup.sh, restore.sh, entrypoint.sh), monitoring/ (prometheus.yml, alerts.yml, alertmanager.yml, grafana provisioning + dashboard JSON) |
| `docker-compose.yml` | production stack (see §6) |
| `docker-compose.override.yml` | dev hot-reload overrides (used by `make dev`) |
| `docker-compose.dev.yml` | backend-only dev stack on http://localhost:8000 |
| `docker-compose.gpu.yml` | NVIDIA worker variant |
| `Makefile` | all commands (`make help`) |
| `scripts/` | make-env.sh, security-scan.sh, restore-test.sh, loadtest.sh + loadtest/locustfile.py, validate_on_test_split.py, gen_api_docs.py |
| `.github/workflows/ci.yml` | CI/CD |
| `docs/` | user_guide, runbook, deployment, threat_model, security_checklist, model_card, validation_report, loadtest/, api.md + openapi.json (generated), demo_script, screenshots/, security-scan/ |
| `artifacts/` | nb01_features.ipynb, nb02_federated.ipynb, nb03_evaluation.ipynb, extracted_code.py - **source of truth for model code** |
| Not in git | `.env`, `secrets/`, `models/` (bundle), `backups/`, `storage/`, `node_modules/` |

---

## 4. Technologies and versions

### Machine learning / inference (Python 3.12)
- **PyTorch 2.14 (CPU wheels)**, torchvision 0.29; CUDA image `pytorch/pytorch:2.9.0-cuda12.8-cudnn9-runtime`.
- **transformers 5.17** (ViTModel for **owkin/phikon**, pinned rev `057cc0295895c2df3dd7681a89680da6015cbefe`, 85.8 M params, `add_pooling_layer=False`), huggingface_hub 1.33, timm 1.0.30.
- **OpenSlide** (openslide-python 1.4.6 + openslide-bin 4.0.1.2), **OpenCV** headless 5.0, numpy 2.5, pandas, scikit-learn 1.9, Pillow 12.3, tifffile, pydantic 2.13 / pydantic-settings.

### Backend
- **FastAPI 0.141**, uvicorn 0.54, Pydantic v2, **SQLAlchemy 2.1** (async via asyncpg 0.31; sync via psycopg 3 + OS libpq5), **Alembic 1.20**, SQLite (aiosqlite) for tests.
- **Redis 7 + RQ 2.12** (SimpleWorker, retries), **argon2-cffi** (argon2id), **PyJWT** (HS256), **cryptography** (AES-GCM TOTP secrets), **pyotp** + **segno** (QR), **reportlab** (PDF), **boto3** (S3), **prometheus-client**, sentry-sdk (optional), python-multipart, httpx.
- Tests: pytest 9, pytest-asyncio, pytest-cov, fakeredis; lint: **ruff** (lint + format), **mypy**.

### Frontend (Node 20.20)
- **Next.js 15.5.26 (App Router, standalone output) + React 19.3**, TypeScript 5.9 (strict, noUncheckedIndexedAccess).
- **Tailwind CSS 3.4** + tailwindcss-animate, **shadcn/ui-style components on Radix UI**, lucide-react icons, next-themes (light/dark/system), sonner (toasts).
- **TanStack Query 5**, **openapi-fetch** + **openapi-typescript 7** (typed client from backend OpenAPI), **React Hook Form 7 + Zod 3**, react-dropzone, **OpenSeadragon 6** (slide viewer).
- Tests: **Vitest 5** + Testing Library + jsdom 26; **Playwright 1.63** + **@axe-core/playwright** (WCAG 2.1 AA); ESLint 8 (next config) + Prettier 3.
- `package.json` has `"overrides": {"postcss": "8.5.28"}`; `.npmrc` has `legacy-peer-deps=true` (npm resolver bug with jsdom peer deps).

### Infrastructure
- **Docker Compose**; **Caddy 2.10** (automatic HTTPS; local CA for `localhost`), **PostgreSQL 16**, **Redis 7**, **SeaweedFS 4.47** (S3 API, `-s3.encryptVolumeData`), backup image = Alpine + postgresql16-client + openssl + **rclone**; **Prometheus 3.5, Alertmanager 0.28, Grafana 12.1**; ClamAV 1.4 (optional).
- Security tooling: **pip-audit, npm audit, gitleaks 8.28, Trivy 0.66**; load test **Locust 2.46**.
- CI/CD: **GitHub Actions** (lint, types, tests with coverage ≥ 80 %, gitleaks, e2e on compose with a *fake* bundle, Trivy, push to GHCR, SSH deploy to staging, manual-approval production).

---

## 5. How it was built (phase by phase)

**Starting point (before 2026-09-26):** a half-migrated repo - old pnpm monorepo `apps/` (Express + Supabase),
a skeleton FastAPI with a hard-coded login, a Next.js 14 frontend still calling Supabase, broken Docker build
(missing cv2, wrong imports). All of that was replaced; the user approved deleting the old parts.

### Phase 1 - inference engine
- Read nb01/nb02/nb03 (in `artifacts/`) and copied the maths verbatim. Important notebook facts:
  - features are stored **fp16** in nb01 (`.float().cpu().half()`) → the web pipeline rounds to fp16 too;
  - nb02 eval sub-samples bags > 512 tiles with an **unseeded** randperm → web seeds it from the slide id
    (so parity is only exact for slides ≤ 512 tiles);
  - nb03 ensemble = mean over seeds of **monotone sigmoid probabilities**, thresholds tuned once on validation;
  - operating points from `tab_E08_operating_points.csv` rows `Method == "FedAvg"` (long format; use `>=`).
- Thesis model files live on the user's Mac: `~/Desktop/abdurahman FYP/v2_pipeline/submission_files/{fl_outputs_phikon,results_phikon}` (copy in `~/Downloads/submission_files`). `make bundle` packages them into `models/bundle` (5 × `model__fedavg__s{k}.pt`, res/preds CSVs, tab_E08, splits, config, `ensemble_thresholds.json`, `manifest.json`). Preds CSV columns are `logit0..logit4`.
- **Verified:** the ensemble reproduces the thesis exactly - csPCa AUC **0.9705**, cancer AUC **0.9935**, QWK **0.9047** on **2,124** test slides, identical bootstrap CIs; ensemble thresholds `[0.3, 0.475, 0.3, 0.45, 0.425]`; model_version `a1e53bfd…af343`.
- Safety rules added (predict.py): low confidence if P(csPCa) between sens95 (0.173) and Youden (0.481) thresholds, < 50 tiles, seed SD of P(csPCa) > 0.10, or stain OOD. QC: mpp outside 0.35-0.65 µm/px, colour stats outside mean ± 3 SD of both PANDA hospitals.
- CPU speed (M3, 4 cores): ~12-25 s for ~110-150 tiles; ~1.5-2 min for 768 tiles.

### Phase 2 - backend
- Data model (`db/models.py`): users, sessions, patients (pseudonym only), cases, predictions (many per case, newest current), reviews (append-only history), uploads (resumable), audit_log (hash chain).
- Auth: access JWT 15 min + rotating refresh 7 days in httpOnly SameSite=strict cookies; server-side `sessions` table (revocation, 15-min idle timeout, refresh reuse ⇒ revoke); CSRF double-submit (`csrf_token` cookie ↔ `X-CSRF-Token` header, login exempt); 5 logins/min/IP; lockout after 5 failures for 15 min; password policy ≥ 12 chars, 3 of 4 classes; optional TOTP.
- Access: roles admin / pathologist / urologist; non-admins see only their hospital (404 otherwise); admins cannot review; only clinicians+admin upload.
- Uploads: multipart `POST /cases` or resumable `POST/PUT/GET /uploads` (8 MB chunks, Content-Range) + `/uploads/{id}/complete`; TIFF magic bytes, optional ClamAV, OpenSlide pyramid check, sha256 dedupe per hospital; pseudonym regex + national-ID (CNIC) rejection; storage keys `cases/{case_id}/...`.
- Worker writes heatmap.png, tile-{k}.jpg, result.json, thumbnail.jpg; progress to Redis (`progress:{case_id}`) → SSE `GET /cases/{id}/events`. Permanent errors (NoTissue, SlideValidation, AssetIntegrity, timeout) fail immediately; others retry twice.
- Endpoints: see `docs/api.md` (44 operations). Emails validated permissively (hospital `.local` domains allowed).

### Phase 3 - frontend
- Same-origin API (`/api/v1`), proxied by Caddy (prod) or Next rewrites (`INTERNAL_API_URL`, dev).
- Case page = OpenSeadragon viewer (navigator, zoom, fullscreen, scale bar from mpp, heatmap as a second image layer at x=0,width=1 with opacity slider, click tile → fitBounds + yellow outline) + ResultCard + tabs (Review / History / Audit for admin).
- Grades always shown with colour **and** text (0-1 green, 2-3 amber, 4-5 red).

### Phase 4 - security & validation
- Docs: threat_model (STRIDE), security_checklist (ASVS L2), model_card, validation_report.
- Scans fixed: Next 14 critical RCE → Next 15; postcss override; psycopg[binary] bundled old pcre2 → plain psycopg + libpq5; setuptools (vendored old jaraco) removed from runtime images; npm removed from frontend runtime; `apk/apt upgrade`. Result: 0 vulns / 0 fixable HIGH-CRITICAL.
- CSV export neutralises formula injection.

### Phase 5 - deployment & ops
- Compose stack, Makefile, `.env` generation, encrypted backups + restore drill, scheduler (retention: 10 years default, soft-deleted cases purged after 30 days, stale uploads after 24 h; weekly drift report to `reports/drift-<date>.md`), Prometheus metrics (`gleasonai_*`), 6 alert rules, Grafana dashboard, CI/CD, demo seeding.

---

## 6. Running it

Requirements: Docker Desktop (≥ 8 GB RAM for Docker, ≥ 20 GB free disk), make, openssl.
**Watch disk space** - a full Mac disk once put Docker into read-only mode (fix: free space, restart Docker Desktop, `docker builder prune -f`).

```bash
make env          # .env with random secrets (never overwrites)
make bundle       # THESIS_DIR=".../submission_files" -> models/bundle
make fetch        # verify bundle + download Phikon into volume gleasonai_model_cache (once, ~330 MB)
make up           # production stack -> https://localhost (accept the local certificate)
make seed-demo    # 4 demo accounts (random shared password printed + saved to storage/demo-credentials.txt) + 6 synthetic demo cases
make seed-admin EMAIL=... HOSPITAL="..."   # real administrator
make monitoring   # Prometheus 127.0.0.1:9090, Alertmanager :9093, Grafana :3001 (admin / GRAFANA_ADMIN_PASSWORD)
make down | make logs | make ps
make dev          # hot reload (API docs at /api/v1/docs)   make dev-backend  # backend only on :8000
```
Demo accounts: `admin@gleasonai.demo`, `pathologist.radboud@gleasonai.demo`, `pathologist.karolinska@gleasonai.demo`,
`urologist.radboud@gleasonai.demo` (password: see `demo-credentials.txt` inside the data volume or re-run `make seed-demo`).
`make seed-e2e` sets the fixed test password `E2e-Demo-Password-2026!` - **test use only**.

Services in `docker-compose.yml`: caddy, frontend, api, worker, scheduler, db, redis, s3 (SeaweedFS), s3-init
(creates bucket), backup, + profiles monitoring (prometheus, alertmanager, grafana) and antivirus (clamav).
Only Caddy publishes 80/443; monitoring UIs bind to 127.0.0.1.

Env vars: documented in `.env.example` (ENV, DOMAIN, PUBLIC_URL, SECRET_KEY, POSTGRES_*, REDIS_PASSWORD,
S3_ADMIN_*, S3_APP_SECRET, BACKUP_PASSPHRASE, GRAFANA_ADMIN_PASSWORD, MODEL_SOURCE, HF_*, PHIKON_*,
RETENTION_DAYS, CLAMAV_HOST, BACKUP_*, DEMO_SLIDES_DIR, SENTRY_DSN, ALERT_WEBHOOK_URL). Backend-only settings
are in `backend/core/config.py` (e.g. JOBS_SYNC for tests, S3_SSE, COOKIE_SECURE, IDLE_TIMEOUT_MINUTES).
Production refuses to start with the dev SECRET_KEY, COOKIE_SECURE=false or SQLite.

---

## 7. Testing & quality commands

| Command | What |
|---|---|
| `make test` | inference (28 tests, ~90 % cov, + thesis-metric checks with models/bundle), backend (51 tests, ~86 %), frontend (lint + tsc + 18 Vitest) |
| `make lint` | ruff check/format, mypy (backend with `backend/pyproject.toml`, inference), eslint, prettier, tsc |
| `make e2e` | Playwright against the running stack (run `make seed-e2e` first): full clinical flow + axe accessibility + screenshots to docs/screenshots |
| `make security-scan` | pip-audit, npm audit, gitleaks, Trivy |
| `make restore-test` | backup → restore into scratch Postgres/SeaweedFS → compare counts |
| `make loadtest` | Locust 20 viewers + 5 uploads (last result: tile p95 36 ms, 0 errors, time-to-result 19-98 s on CPU) |
| `make validate [PANDA_DIR=...]` | writes docs/validation_report.md (part A always; part B needs PANDA slides) |
| `make openapi` / `scripts/gen_api_docs.py` | regenerate `frontend/lib/api/schema.d.ts` / `docs/api.md` after API changes |

Python tests run inside the Docker test image `gleasonai-worker-test` (`docker/worker.Dockerfile --target test`)
because the Mac has Python 3.14 and the project needs 3.12. Backend tests use SQLite migrated by Alembic,
fakeredis, `JOBS_SYNC=true` (jobs run in-process) and fake models (`prostate_infer.testing`).

---

## 8. Rules for future changes (do not break)

1. **Never change model/preprocessing maths** (`model.py`, `preprocess.py`, ensemble in `predict.py`/`bundle.py`)
   without re-running the thesis checks (`THESIS_BUNDLE=models/bundle pytest`) and, if PANDA slides exist, the parity test.
2. The API must stay **torch-free**; import torch only in the worker (`prostate_infer.predict`).
3. Every state-changing endpoint: role check + hospital row access (`get_case_for`) + `record()` audit entry.
   Never add UPDATE/DELETE on `audit_log` (a DB trigger forbids it).
4. Never store or log patient names/IDs; pseudonym codes only; do not log tokens/passwords (logging redacts known keys).
5. Keep the disclaimer on every result screen, the PDF and API responses; results stay "provisional" until reviewed.
6. After API schema changes: `make openapi`, update `docs/api.md`, add backend tests; frontend uses generated types.
7. New DB fields → new Alembic migration (`alembic revision --autogenerate`, check `alembic check`).
8. Secrets only in `.env` / `secrets/`; never commit `.env`, `models/`, `storage/`, `backups/` (see `.gitignore`).
9. Keep Trivy/npm audit/pip-audit clean; pin versions.
10. Next.js page files may only export page things (use `lib/` for shared schemas); `params` in pages is a Promise (Next 15 → `use(params)`).

---

## 9. Deviations from the PDF plan (and why)

- **SeaweedFS instead of MinIO** - MinIO images/binaries are no longer published (Docker Hub/quay 404, dl.min.io 410).
  `S3_SSE` stays empty for SeaweedFS (encryption via `encryptVolumeData`; SeaweedFS volumes must be sized:
  `-master.volumeSizeLimitMB=1024 -volume.max=200`, otherwise "no writable volumes").
- **Next.js 15 instead of 14** - CVE-2026-75604 (critical unauthenticated RCE) in 14.2.35.
- **Models from local files by default** (`MODEL_SOURCE=local`); the HF repo path (`MODEL_SOURCE=hf`, pinned
  revision, read-only token in `secrets/hf_token`) is implemented but was not exercised (no HF token on the Mac).

## 10. Known gaps / not yet verified

- No PANDA slides on the build machine: the slide-level **parity test** (`inference/tests/test_parity.py`) and
  **validation part B** (website pipeline on real slides) have never been run. Demo cases are **synthetic** slides
  (their AI grades are meaningless - they all come out ISUP 1).
- CUDA worker image, GitHub Actions workflow, and HTTPS with a real domain / Let's Encrypt were written but not run.
- CSP still allows `'unsafe-inline'` scripts (Next.js bootstrap without nonces).
- 2FA is optional (policy: require for admins).

## 11. History / state

- Commit `0435549` on `clean-main` = the full rebuild (not pushed). Remote: `https://github.com/abdurahmanmiakhil/FL_FYP.git`.
- The running local stack (2026-09-26) had the 6 demo cases visible; earlier test cases were soft-deleted (audited).
- Keep `BACKUP_PASSPHRASE` from `.env` off the machine; backups cannot be restored without it.
- Further reading: `README.md`, `docs/runbook.md`, `docs/user_guide.md`, `docs/demo_script.md`, `docs/model_card.md`.
