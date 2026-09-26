# Runbook (administrators)

## Everyday commands

| Task | Command |
|---|---|
| Start / stop | `make up` / `make down` (data is kept) |
| Status, logs | `make ps`, `make logs` |
| Create an administrator | `make seed-admin EMAIL=you@hospital.org HOSPITAL="Radboud UMC"` (password prompted) |
| Apply migrations | `make migrate` (also automatic when the API starts) |
| Backup now | `make backup` |
| Restore | `make restore STAMP=20260926T013000Z` (or `STAMP=latest`) |
| Prove backups work | `make restore-test` |
| Monitoring | `make monitoring` then Grafana http://127.0.0.1:3001 (admin / `GRAFANA_ADMIN_PASSWORD`) |
| Security scan | `make security-scan` |

## First installation
1. `make env` - creates `.env` with random secrets. **Copy `BACKUP_PASSPHRASE` to a password manager off the server.**
2. Set `DOMAIN` and `PUBLIC_URL` in `.env` (a real DNS name gives a Let's Encrypt certificate; ports 80/443 must be reachable).
3. Models: `make bundle THESIS_DIR=...` (local files) or set `MODEL_SOURCE=hf` + pinned `HF_MODEL_REVISION` and put a read-only token in `secrets/hf_token`.
4. `make fetch` (verifies the bundle, downloads Phikon once), `make up`, `make seed-admin ...`.
5. Enable host disk encryption for Docker volumes (LUKS on Linux: put `/var/lib/docker` on an encrypted volume).

## Backups
- Nightly at `BACKUP_HOUR`:30 UTC into `./backups` (`BACKUP_DIR`), kept `BACKUP_KEEP_DAYS` days: `db-<stamp>.dump.enc`, `storage-<stamp>.tar.gz.enc`, `<stamp>.sha256`.
- Copy `./backups` to off-site storage (e.g. `rclone sync backups remote:gleasonai-backups` from cron).
- **Restore procedure (tested by `make restore-test`)**
  1. `make down` then `docker compose -f docker-compose.yml up -d db s3` (only data services).
  2. `make restore STAMP=<stamp>` - checks sha256, restores the database (`pg_restore --clean`) and the bucket.
  3. `make up`, sign in, open a case, run *Audit trail -> Verify integrity*.
- Last drill: 92 audit rows, 12 cases, 12 predictions, 144 objects (78.7 MB) restored identically.

## Alerts (Alertmanager -> `ALERT_WEBHOOK_URL`)
| Alert | Meaning | Action |
|---|---|---|
| WorkerDown | no worker with loaded models | `make logs` for `worker`; model integrity error -> re-run `make bundle`/`make fetch`; out of memory -> give Docker more RAM |
| QueueBacklog | > 10 jobs waiting for 10 min | add a worker (`docker compose up -d --scale worker=2`) or use a GPU |
| HighErrorRate | > 5 % 5xx | `make logs`, check database/storage health at `/api/v1/health` |
| PredictionFailures | > 3 failed analyses in 1 h | open the failed cases (error message shown), check slide format |
| SlowTiles | tile p95 > 300 ms | check CPU/disk; slide cache on a fast disk |
| ApiDown | API unreachable | `make ps`, restart `docker compose restart api` |

## Model drift
Every Monday the scheduler writes `reports/drift-<date>.md` to object storage and logs `model drift flagged` warnings
when the mean P(csPCa) moves > 0.15 from the PANDA reference or > 20 % of slides are outside the PANDA stain range.
Read it with `docker compose exec api python -c "from backend.services.storage import get_storage;print(get_storage().get_bytes('reports/drift-YYYY-MM-DD.md').decode())"`.

## Common problems
| Symptom | Fix |
|---|---|
| Browser warns about the certificate on `https://localhost` | Caddy's local CA. Export it with `docker compose -f docker-compose.yml cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt` and trust it (macOS: `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain caddy-root.crt`), or accept the warning once (local demo only). |
| "The AI worker is not ready" banner | worker still loading models (1-2 min) or failed: `docker compose logs worker`. |
| Upload rejected "not a readable whole-slide image" | the file is not a pyramidal TIFF/SVS; re-export from the scanner software. |
| "No tissue found in slide" | blank or extremely faint slide; check the scan. |
| Docker "read-only file system" | host disk full: free space, restart Docker Desktop, `docker builder prune -f`. |
| Locked-out user | Admin -> Users -> toggle Active off and on (clears the lockout). |
| Forgotten admin password | `make seed-admin EMAIL=... HOSPITAL=...` resets it. |
