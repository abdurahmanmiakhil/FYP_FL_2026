# Set up GleasonAI on a new machine and put it online (step by step)

This guide takes a fresh computer (macOS, Windows or Linux) to a running GleasonAI that anyone on the internet
can open through a free **Cloudflare Tunnel** link. No coding needed - copy the commands one by one.

Time: about 45 minutes (most of it is automatic downloading and building).

---

## Part A - Install the tools (once per machine)

**Hardware:** 16 GB RAM recommended (8 GB minimum), 30 GB free disk, internet connection.

### macOS
1. Install **Docker Desktop**: https://www.docker.com/products/docker-desktop/ → open it once and wait until it says *Engine running*.
2. Docker Desktop → **Settings → Resources**: Memory **8 GB or more**, Disk **64 GB or more** → *Apply & restart*.
3. Open **Terminal** and install the command-line tools (a window pops up - click *Install*):
   ```bash
   xcode-select --install
   ```
4. Install Homebrew (skip if you have it), then cloudflared (the tunnel program) and the GitHub CLI:
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   brew install cloudflared gh
   ```

### Windows 10/11
1. Open **PowerShell as Administrator** and install WSL (Ubuntu), then restart the PC:
   ```powershell
   wsl --install -d Ubuntu
   ```
2. Install **Docker Desktop** (https://www.docker.com/products/docker-desktop/), and in its Settings enable
   *Use the WSL 2 based engine* and *Resources → WSL integration → Ubuntu*. Give it 8 GB+ memory.
3. Open the **Ubuntu** app (all following commands run there, not in PowerShell):
   ```bash
   sudo apt update && sudo apt install -y git make openssl curl
   curl -L -o cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
   sudo dpkg -i cloudflared.deb && rm cloudflared.deb
   ```
4. Work inside the Linux home folder (`cd ~`), not in `C:\` paths - it is much faster.

### Linux (Ubuntu/Debian)
```bash
sudo apt update && sudo apt install -y git make openssl curl
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER      # then log out and back in
curl -L -o cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb && rm cloudflared.deb
```
(On ARM machines such as Raspberry Pi or Oracle Ampere use `cloudflared-linux-arm64.deb`.)

**Check:** `docker run --rm hello-world` prints "Hello from Docker!".

---

## Part B - Get the project and run it locally

### Step 1 - Download the code
```bash
cd ~
git clone https://github.com/abdurahmanmiakhil/FYP_FL_2026.git gleasonai
cd gleasonai
```

### Step 2 - Bring the AI models (they are private and are NOT on GitHub)
The trained thesis models (~8 MB) must be copied to `models/bundle` in the project. Choose **one** way:

- **a) Copy from your old machine** (easiest). On the old machine the folder is
  `…/gleasonai/models/bundle`. Copy the whole `bundle` folder with a USB stick, Google Drive, or:
  ```bash
  scp -r OLD_USER@OLD_MACHINE:/path/to/gleasonai/models/bundle ./models/
  ```
  Afterwards `models/bundle/manifest.json` must exist.
- **b) Build it from the thesis output folder** (`submission_files` from Kaggle, which contains
  `fl_outputs_phikon/` and `results_phikon/`):
  ```bash
  make bundle THESIS_DIR="/full/path/to/submission_files"
  ```
- **c) Download from Hugging Face** (only if you published the models with `inference/scripts/publish_model_repo.py`):
  after Step 3 set `MODEL_SOURCE=hf` and `HF_MODEL_REVISION=<commit>` in `.env`, and put a read-only token in
  `secrets/hf_token`.

Keep a backup copy of `models/bundle` somewhere safe - without it the app cannot make predictions.

### Step 3 - One-command setup
```bash
make setup
```
This checks Docker/memory/disk, creates `.env` with random passwords, verifies the models, downloads the
Phikon encoder (~330 MB, once), builds and starts everything, and waits until the AI is ready
(first time 10-20 minutes). It ends with **"GleasonAI is ready at https://localhost"**.

> Important: open `.env` and copy **BACKUP_PASSPHRASE** into your password manager. Backups cannot be
> restored without it.

### Step 4 - Create the accounts
```bash
make seed-demo
```
Prints 4 demo accounts (admin, 2 pathologists, urologist) with one shared password and adds 6 demo cases.
For your own administrator account:
```bash
make seed-admin EMAIL=you@example.com HOSPITAL="NUML Demo Hospital"
```

### Step 5 - Open it
Go to **https://localhost** → the browser warns about the certificate (it is a local one) → *Advanced →
Proceed to localhost* → sign in.

---

## Part C - Put it online with Cloudflare Tunnel (free)

The tunnel makes an outgoing connection to Cloudflare, so **no router or firewall changes** are needed and
visitors get a real HTTPS link with a valid certificate.

### C1 - Instant public link (no account)
```bash
make tunnel
```
After a few seconds (it retries automatically if Cloudflare is slow) it prints:
```
  GleasonAI is online at:  https://some-random-words.trycloudflare.com
```
Share that link. Anyone can open it while this computer is on, awake and Docker is running.

| Command | What it does |
|---|---|
| `make tunnel-url` | show the current link again |
| `make tunnel-down` | stop public access (the app keeps running locally) |
| `make tunnel` | start again - **the link changes every time** |

### C2 - Permanent link (free Cloudflare account)
For a link that never changes, e.g. `https://gleasonai.yourdomain.com`:
1. Create a free account at https://dash.cloudflare.com and add a domain you own (a cheap one is ~$10/year;
   Cloudflare shows how to change the domain's nameservers).
2. Cloudflare dashboard → **Zero Trust → Networks → Tunnels → Create a tunnel** → type *Cloudflared* →
   name it `gleasonai` → copy the **token** (the long text after `--token` in the shown command).
3. In the same wizard, **Public hostname**: subdomain `gleasonai`, your domain, service type **HTTPS**, URL
   `localhost:443` if cloudflared is installed on this machine (or `caddy:443` if you use the Docker service).
   Open *Additional application settings → TLS*: enable **No TLS Verify** and set **Origin Server Name** to
   `localhost`; under *HTTP Settings* set **HTTP Host Header** to `localhost`. Save.
4. Put the token in `.env`:
   ```
   TUNNEL_TOKEN=eyJhIjoi...
   ```
5. Start it:
   ```bash
   make tunnel-named
   ```
   Your app is now at `https://gleasonai.yourdomain.com` permanently (while the machine runs).

### Keep the computer awake
- macOS: System Settings → Battery/Energy → *Prevent automatic sleeping when the display is off*; keep it on power.
- Windows: Settings → System → Power → Sleep: *Never* (when plugged in).
- For 24/7 access without your own computer, use a free cloud server (Oracle Cloud Always Free) - see
  [deployment.md](deployment.md) - and run the same steps there.

---

## Part D - Everyday use

| Task | Command |
|---|---|
| Stop everything | `make down` (data is kept) |
| Start again | `make up`, then `make tunnel` for a new public link |
| See if it is healthy | `make ps` and https://localhost/api/v1/ready |
| Logs | `make logs` |
| Update to the newest code | `git pull && make up` |
| Backup now / restore | `make backup` / `make restore STAMP=latest` |
| All commands | `make help` |

After a restart of the computer: open Docker Desktop, then `cd ~/gleasonai && make up && make tunnel`.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `make setup` says Docker is not running | Open Docker Desktop and wait for *Engine running*. |
| "no models/bundle" | Do Step 2 (copy or build the model bundle). |
| Setup stops with "read-only file system" or builds fail | The disk is full: free space, restart Docker Desktop, run `docker builder prune -f`. |
| "The AI worker is not ready" banner | Models are still loading (1-2 min) or failed: `docker compose -f docker-compose.yml logs worker`. |
| `make tunnel` keeps retrying | Cloudflare's free quick-tunnel service is busy - wait a minute and run it again, or use C2. |
| The public link is slow **only on your own computer** but fast for others | Some networks route IPv4 to Cloudflare slowly (seen on the build machine: 20 s to connect, 0.5 s elsewhere). Visitors are not affected; test from your phone on mobile data. |
| Public link says "Tunnel not found" / stopped working | The quick link expired after a network drop: run `make tunnel` again (new link). |
| Login says "Too many requests" | 5 sign-in attempts per minute per visitor - wait a minute. |
| Account locked | 5 wrong passwords lock it for 15 minutes; an admin can unlock it (Users → toggle Active off/on). |
| Forgot the demo password | `make seed-demo` sets and prints a new one. |
| Windows: very slow | Keep the project inside the Ubuntu home folder (`~/gleasonai`), not under `/mnt/c`. |

Security notes for public links: everyone with the link can reach the sign-in page - use strong passwords,
turn on two-factor authentication for the admin (Account settings), and remember the demo cases are synthetic.
Never upload real patient slides to a demo that is shared publicly.
