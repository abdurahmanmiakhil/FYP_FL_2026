# Deployment options

The same `docker-compose.yml` runs everywhere. Minimum: 4 CPU cores, 8 GB RAM (16 GB comfortable), 50 GB disk
plus slide storage (PANDA slides are 20-200 MB each). Measured on an Apple M3 laptop (CPU, 4 cores): 12-25 s per
PANDA-sized slide for ~100-150 tiles, about 1.5-2 min for a slide with the maximum 768 tiles.

## 1. University or hospital server with an NVIDIA GPU (recommended for real use)
- Hardware: any recent NVIDIA GPU with ≥ 8 GB (T4, A2, RTX 4000), 32 GB RAM, SSD storage. A GPU slide takes seconds.
- Steps: install Docker + NVIDIA Container Toolkit, clone the repository, `make env`, set `DOMAIN`, `make bundle` (or HF),
  `make fetch`, `make up-gpu`, `make seed-admin ...`. Put the server behind the hospital firewall; open 443 only.
- Cost: existing hardware; a used T4 server ≈ USD 1,500-3,000 one-off.

## 2. Cloud VM with a T4 GPU, started only for demos
- AWS `g4dn.xlarge` (4 vCPU, 16 GB, T4) ≈ USD 0.53/h on demand (≈ USD 0.20/h spot); GCP `n1-standard-4` + T4 ≈ USD 0.54/h.
  Plus ≈ USD 8-10/month for a 100 GB disk kept between demos.
- Steps: Ubuntu 22.04 + NVIDIA driver image, Docker, point a DNS name at the VM (for a Let's Encrypt certificate),
  then the same commands as option 1. **Stop the VM after the demo**; restore data from `./backups` if recreated.
- A 3-hour viva demo costs about USD 2.

## 3. Laptop demo on CPU (what this repository was tested on)
- `make env && make fetch && make up && make seed-demo` - https://localhost with Caddy's local certificate.
- The 6 demo cases are processed once at seeding and stored, so the demo never waits; a new upload takes
  0.3-2 minutes on CPU. Cost: none.
- Tip: `DEMO_SLIDES_DIR=/path/to/panda/test/slides` makes the demo use real PANDA slides (ISUP 0, 2, 5 from each
  hospital) instead of synthetic ones.

## Production checklist
- `ENV=prod` (default), real `DOMAIN`/`PUBLIC_URL`, `COOKIE_SECURE=true` (forced), secrets from `make env`.
- Model revision pinned (`HF_MODEL_REVISION` = commit hash) and `OFFLINE=1` after `make fetch`.
- Host disk encryption, off-site backup copy, monitoring profile with a real `ALERT_WEBHOOK_URL`.
- CI/CD (`.github/workflows/ci.yml`): pushes to `main` build and push images to GHCR, deploy to staging;
  production deploys need a manual approval (GitHub environment `production` with required reviewers) and take
  a backup first. Required repository secrets: `DEPLOY_HOST/USER/SSH_KEY`, `PROD_HOST/USER/SSH_KEY`.
