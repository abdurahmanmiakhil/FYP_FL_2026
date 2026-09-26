# --- artifacts/nb01_features.ipynb ---
# ── Cell 0: Install (idempotent) ──────────────────────────────────────────────
import subprocess, sys, os
if os.path.exists('/kaggle/working'):
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-U',
                    'openslide-python', 'openslide-bin', 'huggingface_hub', 'kaggle'], check=False)
print('✅ install step done')

# ---
# ── Common infrastructure (identical in all v2 notebooks) ─────────────────────
# Secrets, logging, atomic saves, retries, Hugging Face + Kaggle backup,
# and a session time-guard so work is flushed before Kaggle kills the session.
import os, sys, json, time, glob, shutil, random, subprocess, traceback, hashlib, math, re
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import torch
import warnings
try:   # small validation sets can miss a grade → sklearn warns on every round; the metric code already handles it (NaN)
    from sklearn.exceptions import UndefinedMetricWarning
    warnings.filterwarnings('ignore', category=UndefinedMetricWarning)
except Exception:
    pass
warnings.filterwarnings('ignore', message='.*Only one class is present in y_true.*')

ON_KAGGLE     = os.path.exists('/kaggle/working')
WORK          = Path('/kaggle/working') if ON_KAGGLE else Path(os.environ.get('FL_WORK', './work')).resolve()
WORK.mkdir(parents=True, exist_ok=True)
SESSION_START = time.time()

KAGGLE_USERNAME_DEFAULT = 'obaidullahmiakhil2'  # used only if no KAGGLE_USERNAME secret exists
# Accepted Kaggle-secret names (first one found wins) – works with either naming style.
SECRET_HF       = ('HF_WRITE_TOKEN', 'HF_TOKEN')
SECRET_KAGGLE   = ('KAGGLE_API_KEY', 'KAGGLE_KEY')
SECRET_KG_USER  = ('KAGGLE_USERNAME',)


def log(msg: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | +{(time.time()-SESSION_START)/3600:5.2f}h] {msg}"
    print(line, flush=True)
    try:
        with open(WORK / 'run_log.txt', 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass


def get_secret(names):
    """Return the first available secret among `names` (env var or Kaggle secret)."""
    names = (names,) if isinstance(names, str) else names
    for name in names:
        if os.environ.get(name):
            return os.environ[name]
    try:
        from kaggle_secrets import UserSecretsClient
        client = UserSecretsClient()
    except Exception:
        return None
    for name in names:
        try:
            v = client.get_secret(name)
            if v:
                return v.strip()
        except Exception:
            continue
    return None


KAGGLE_USERNAME = (get_secret(SECRET_KG_USER) or KAGGLE_USERNAME_DEFAULT).strip()


def apply_overrides(cfg: dict) -> dict:
    """Allow CFG overrides via env var FL_CFG_OVERRIDE='{"key": value}' (used for testing)."""
    ov = os.environ.get('FL_CFG_OVERRIDE')
    if ov:
        cfg.update(json.loads(ov))
        log(f'CFG overrides applied: {ov}')
    return cfg


def seed_everything(seed: int):
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def atomic_torch_save(obj, path):
    path = Path(path); tmp = path.with_name(path.name + '.tmp')
    torch.save(obj, tmp); os.replace(tmp, path)


def atomic_json(obj, path):
    path = Path(path); tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'w') as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _is_not_found(e):
    """True only for a definite 'file/repo does not exist' answer from the server.
    LocalEntryNotFoundError means 'could not reach the server' → treated as transient."""
    n = type(e).__name__
    if n.startswith('Local') or 'Connection' in n or 'Timeout' in n:
        return False
    return 'NotFound' in n or ' 404' in str(e)[:200]


def retry(fn, tries=5, base_wait=5, what='operation'):
    """Retry transient failures with exponential back-off; 'not found' errors are raised at once."""
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if _is_not_found(e):
                raise
            log(f'⚠️  {what} failed (attempt {i+1}/{tries}): {type(e).__name__}: {str(e)[:300]}')
            if i == tries - 1:
                raise
            time.sleep(base_wait * 2 ** i)


def hours_elapsed():
    return (time.time() - SESSION_START) / 3600


def time_left_h(max_session_h):
    return max_session_h - hours_elapsed()


class _LocalFakeHub:
    """TEST ONLY: mimics the few HfApi calls we use, backed by a local folder.
    Activated only when env FL_FAKE_HF_DIR is set. Never used on Kaggle."""
    def __init__(self, root):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
    def whoami(self): return {'name': 'localtest'}
    def create_repo(self, repo_id, **kw): (self.root / repo_id).mkdir(parents=True, exist_ok=True)
    def upload_file(self, path_or_fileobj, path_in_repo, repo_id, **kw):
        dst = self.root / repo_id / path_in_repo; dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path_or_fileobj, dst)
    def list_repo_files(self, repo_id, **kw):
        base = self.root / repo_id
        return [str(p.relative_to(base)) for p in base.rglob('*') if p.is_file()]
    def download(self, repo_id, filename, dest):
        src = self.root / repo_id / filename
        if not src.exists(): return None
        dest = Path(dest); dest.parent.mkdir(parents=True, exist_ok=True); shutil.copy(src, dest); return dest


class CloudBackup:
    """
    Two independent off-site backups:
      • Hugging Face Hub (private *dataset* repo) – continuous: every file is pushed
        the moment it is written (shards, checkpoints, results).
      • PRIVATE Kaggle Dataset – a new dataset version after EVERY chunk (shard /
        finished experiment) plus a blocking final sync; each version holds all files.
    A failure of one backup never stops training; it is logged and retried.
    """
    def __init__(self, hf_repo_name, kaggle_slug, kaggle_title, local_dir,
                 kaggle_every_h=2.0, enable_hf=True, enable_kaggle=True):
        self.local_dir = Path(local_dir); self.local_dir.mkdir(parents=True, exist_ok=True)
        self.hf_ok, self.kg_ok = False, False
        import threading
        self._kg_lock, self._kg_thread, self._kg_pending = threading.Lock(), None, None
        self._kg_exists, self._kaggle_failed, self.kaggle_versions = False, False, 0
        self.fake = None
        for old in self.local_dir.parent.glob(f'.kg_stage_{self.local_dir.name}_*'):   # leftovers of a killed session
            shutil.rmtree(old, ignore_errors=True)

        # ── Hugging Face ──────────────────────────────────────────────────────
        if os.environ.get('FL_FAKE_HF_DIR'):
            self.fake = _LocalFakeHub(os.environ['FL_FAKE_HF_DIR'])
            self.api, self.token = self.fake, None
            self.hf_repo = f'localtest/{hf_repo_name}'
            self.fake.create_repo(self.hf_repo); self.hf_ok = True
            log(f'🧪 Using LOCAL FAKE HF hub at {self.fake.root}')
        elif enable_hf:
            tok = get_secret(SECRET_HF)
            if tok:
                try:
                    from huggingface_hub import HfApi, login
                    login(token=tok, add_to_git_credential=False)
                    os.environ['HF_TOKEN'] = tok            # authenticated model downloads too
                    self.api, self.token = HfApi(token=tok), tok
                    user = retry(lambda: self.api.whoami()['name'], what='HF whoami')
                    self.hf_repo = f'{user}/{hf_repo_name}'
                    retry(lambda: self.api.create_repo(self.hf_repo, repo_type='dataset',
                                                       private=True, exist_ok=True), what='HF create_repo')
                    self.hf_ok = True
                    log(f'✅ HF backup ON  → https://huggingface.co/datasets/{self.hf_repo}')
                except Exception as e:
                    log(f'❌ HF backup could not start: {e}')
            else:
                log(f'⚠️  HF backup OFF (none of the secrets {SECRET_HF} found – attach one under Add-ons → Secrets)')

        # ── Kaggle ────────────────────────────────────────────────────────────
        if enable_kaggle and (ON_KAGGLE or os.environ.get('FL_TEST_KAGGLE_CLI')):
            key = get_secret(SECRET_KAGGLE)
            if key:
                os.environ['KAGGLE_USERNAME'] = KAGGLE_USERNAME
                os.environ['KAGGLE_KEY'] = key
                if key.startswith('KGAT_'):
                    os.environ['KAGGLE_API_TOKEN'] = key
                self.kg_id = f'{KAGGLE_USERNAME}/{kaggle_slug}'
                atomic_json({'title': kaggle_title[:50], 'id': self.kg_id,
                             'licenses': [{'name': 'CC0-1.0'}]},
                            self.local_dir / 'dataset-metadata.json')
                self.kg_ok = True
                log(f'✅ Kaggle backup ON → https://www.kaggle.com/datasets/{self.kg_id} (private; new version after every chunk)')
            else:
                log(f'⚠️  Kaggle backup OFF (none of the secrets {SECRET_KAGGLE} found)')

    # ── HF helpers ────────────────────────────────────────────────────────────
    def push(self, local_path, path_in_repo=None, msg=None):
        """Upload one file to HF (with retries). Returns True on success."""
        if not self.hf_ok:
            return False
        local_path = Path(local_path)
        path_in_repo = path_in_repo or local_path.name
        try:
            retry(lambda: self.api.upload_file(path_or_fileobj=str(local_path), path_in_repo=path_in_repo,
                                               repo_id=self.hf_repo, repo_type='dataset',
                                               commit_message=msg or f'upload {path_in_repo}'),
                  what=f'HF upload {path_in_repo}')
            return True
        except Exception as e:
            log(f'❌ HF upload permanently failed for {path_in_repo}: {e} (file kept locally; will retry next flush)')
            return False

    def push_many(self, local_paths, msg='batch upload'):
        """Upload several files in ONE commit (keeps HF commit count low)."""
        if not self.hf_ok:
            return False
        local_paths = [Path(p) for p in local_paths if Path(p).exists()]
        if self.fake:
            for p in local_paths:
                self.fake.upload_file(str(p), p.name, self.hf_repo)
            return True
        try:
            from huggingface_hub import CommitOperationAdd
            ops = [CommitOperationAdd(path_in_repo=p.name, path_or_fileobj=str(p)) for p in local_paths]
            retry(lambda: self.api.create_commit(repo_id=self.hf_repo, repo_type='dataset',
                                                 operations=ops, commit_message=msg), what=f'HF commit ({msg})')
            return True
        except Exception as e:
            log(f'❌ HF batch upload failed ({msg}): {e} – files kept locally')
            return False

    def require(self, allow_no_backup=False):
        """Refuse to start long work without the primary (HF) backup – no silent local-only runs."""
        if self.hf_ok or allow_no_backup:
            if not self.kg_ok:
                log('⚠️  Kaggle backup is OFF – HF is still on, continuing.')
            return
        raise RuntimeError('Hugging Face backup is OFF, so progress would only live in this session. '
                           f'Attach a secret named one of {SECRET_HF} (Add-ons → Secrets, tick it for this notebook) '
                           'and re-run. (Set CFG allow_no_backup=True only for throw-away tests.)')

    def remote_files_strict(self):
        """Remote file list, or an exception if HF cannot be reached (never a silent empty list).
        Used before any decision that could overwrite existing cloud data."""
        if not self.hf_ok:
            return None
        return retry(lambda: self.api.list_repo_files(self.hf_repo, repo_type='dataset'), what='HF list (strict)')

    def list_remote(self, prefix=''):
        if not self.hf_ok:
            return []
        try:
            files = retry(lambda: self.api.list_repo_files(self.hf_repo, repo_type='dataset'), what='HF list')
            return [f for f in files if f.startswith(prefix)]
        except Exception:
            return []

    def pull(self, path_in_repo, dest):
        """Download a file from HF into `dest` (full path). Returns Path or None if absent."""
        if not self.hf_ok:
            return None
        dest = Path(dest)
        if self.fake:
            return self.fake.download(self.hf_repo, path_in_repo, dest)
        try:
            from huggingface_hub import hf_hub_download
            p = retry(lambda: hf_hub_download(self.hf_repo, path_in_repo, repo_type='dataset',
                                              token=self.token, local_dir=str(WORK / '_hf_cache')),
                      tries=3, what=f'HF download {path_in_repo}')
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + '.tmp')
            shutil.move(str(p), str(tmp)); os.replace(tmp, dest)    # move, not copy: no double disk use
            return dest
        except Exception as e:
            if not _is_not_found(e):
                log(f'   HF download failed: {path_in_repo}: {type(e).__name__}: {str(e)[:150]}')
            return None

    # ── Kaggle helpers ────────────────────────────────────────────────────────
    # Every chunk (feature shard / finished experiment) triggers a new version of the
    # PRIVATE Kaggle dataset. Uploads run in a background thread so the GPU keeps
    # working; chunks finished while an upload is running are coalesced into the
    # next version (each version always contains ALL files written so far).
    def _make_stage(self, msg, stamp):
        """Consistent point-in-time snapshot of local_dir via hard links (no extra disk, no copying).
        Files being written (*.tmp) are skipped, so the training thread is never disturbed."""
        import uuid
        stage = self.local_dir.parent / f'.kg_stage_{self.local_dir.name}_{uuid.uuid4().hex[:6]}'
        stage.mkdir()
        files = []
        for p in sorted(self.local_dir.iterdir()):
            if not p.is_file() or p.name.endswith('.tmp') or p.name == 'kaggle_sync.json':
                continue
            try:
                os.link(p, stage / p.name)
            except FileNotFoundError:
                continue                                       # replaced/removed meanwhile
            except OSError:
                os.symlink(p.resolve(), stage / p.name)        # cross-device fallback
            if p.name != 'dataset-metadata.json':
                files.append(p.name)
        atomic_json({'message': msg, 'time': stamp, 'files': files}, stage / 'kaggle_sync.json')
        return stage, files

    def _kaggle_version_once(self, msg):
        stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        stage, files = self._make_stage(msg, stamp)
        try:
            return self._kaggle_upload(stage, files, msg, stamp)
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def _kaggle_upload(self, stage, files, msg, stamp):
        d = str(stage)
        if not self._kg_exists:
            st = subprocess.run(['kaggle', 'datasets', 'status', self.kg_id], capture_output=True, text=True)
            self._kg_exists = st.returncode == 0 and not re.search(r'404|not found|error', st.stdout + st.stderr, re.I)
        # `create` without --public ⇒ the dataset is PRIVATE
        cmd = (['kaggle', 'datasets', 'version', '-p', d, '-m', f'{msg} @ {stamp}'] if self._kg_exists
               else ['kaggle', 'datasets', 'create', '-p', d])
        t0 = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        ok = r.returncode == 0 and not re.search(r'\berror\b|failed', out[-400:], re.I)
        if ok:
            self._kg_exists = True
            self.kaggle_versions += 1
        last = out.splitlines()[-1] if out else ''
        log(f'   Kaggle {"version" if cmd[2] == "version" else "CREATE (private)"} '
            f'{"✅" if ok else "❌"} [{msg}] {len(files)} files, {time.time()-t0:.0f}s | {last[:160]}')
        return ok

    def _kaggle_worker(self, msg):
        while True:
            ok = False
            for attempt in range(3):
                try:
                    ok = self._kaggle_version_once(msg)
                except Exception as e:
                    log(f'   Kaggle upload exception: {e}')
                if ok:
                    break
                time.sleep(60 * (attempt + 1))      # e.g. previous version still processing
            if not ok:
                self._kaggle_failed = True          # final sync will retry
            with self._kg_lock:
                if self._kg_pending is None:
                    self._kg_thread = None
                    return
                msg, self._kg_pending = self._kg_pending, None

    def kaggle_push_async(self, msg):
        """Queue a Kaggle dataset version containing everything in local_dir (non-blocking)."""
        if not self.kg_ok:
            return
        import threading
        with self._kg_lock:
            if self._kg_thread is not None:
                self._kg_pending = msg              # coalesce: next version includes this chunk too
                return
            self._kg_thread = threading.Thread(target=self._kaggle_worker, args=(msg,), daemon=True)
            self._kg_thread.start()
        log(f'☁️  Kaggle push queued: {msg}')

    def kaggle_final_sync(self, msg, timeout_min=25):
        """Blocking: wait for any running upload, then make sure the LAST version holds every file."""
        if not self.kg_ok:
            return False
        th = self._kg_thread
        if th is not None:
            log('⏳ waiting for running Kaggle upload to finish …')
            th.join(timeout=timeout_min * 60)
        for attempt in range(4):
            try:
                if self._kaggle_version_once(msg):
                    self._kaggle_failed = False
                    log(f'✅ Kaggle dataset fully synced (private): https://www.kaggle.com/datasets/{self.kg_id}')
                    return True
            except Exception as e:
                log(f'   Kaggle final sync exception: {e}')
            if attempt < 3:
                time.sleep(90 * (attempt + 1))
        log('❌ Kaggle final sync failed – all files are safe on HF and in /kaggle/working; re-run this cell later.')
        return False

    # backwards-compatible name used by older cells
    def kaggle_snapshot(self, msg='checkpoint', force=False):
        return self.kaggle_final_sync(msg) if force else self.kaggle_push_async(msg)


def find_panda_dir():
    cands = ['/kaggle/input/competitions/prostate-cancer-grade-assessment',
             '/kaggle/input/prostate-cancer-grade-assessment',
             os.environ.get('PANDA_DIR', '')]
    for c in cands:
        if c and (Path(c) / 'train.csv').exists():
            return Path(c)
    hits = glob.glob('/kaggle/input/**/train.csv', recursive=True)
    for h in hits:
        if (Path(h).parent / 'train_images').exists():
            return Path(h).parent
    return None


ISUP_FROM_GLEASON = {'0+0': 0, 'negative': 0, '3+3': 1, '3+4': 2, '4+3': 3,
                     '4+4': 4, '3+5': 4, '5+3': 4, '4+5': 5, '5+4': 5, '5+5': 5}


def run_names(backbone, smoke=False):
    """All cloud/local names for one run type. Smoke-test runs get a '-smoke' suffix everywhere,
    so they can never mix with (or block) the real run."""
    s, u = ('-smoke', '_smoke') if smoke else ('', '')
    return {'run': 'smoke' if smoke else 'full',
            'features': f'panda-fl-v2-{backbone}-features{s}', 'training': f'panda-fl-v2-{backbone}-training{s}',
            'results': f'panda-fl-v2-{backbone}-results{s}',
            'feat_dir': WORK / f'features_{backbone}{u}', 'out_dir': WORK / f'fl_outputs_{backbone}{u}',
            'fig_dir': WORK / f'results_{backbone}{u}', 'title_sfx': ' SMOKE' if smoke else ''}


def fetch_feature_store(backbone, smoke=False):
    """Locate the NB01 feature store and return (feature_dir, manifest).
    Candidates: this session's /kaggle/working, attached Kaggle input datasets, and HF.
    The copy with the MOST chunks wins (an attached Kaggle version can be older than HF);
    shards already present locally / in /kaggle/input are copied instead of re-downloaded."""
    N = run_names(backbone, smoke)
    repo = N['features']
    best_dir, best_m = None, None
    cands = [N['feat_dir'], Path(str(N['feat_dir']) + '_hf')] + \
            [Path(p).parent for p in glob.glob('/kaggle/input/**/manifest.json', recursive=True)]
    for c in cands:
        m = read_json(Path(c) / 'manifest.json')
        if m and m.get('backbone') == backbone and m.get('run', 'full') == N['run'] \
                and all((Path(c) / s).exists() for s in m['shards']):
            if best_m is None or len(m['shards']) > len(best_m['shards']):
                best_dir, best_m = Path(c), m
    dl = Path(str(N['feat_dir']) + '_hf')
    fb = CloudBackup(repo, repo, repo, dl, enable_kaggle=False)
    hf_m = None
    tmp = WORK / '_hf_manifest_check.json'
    if fb.hf_ok and fb.pull('manifest.json', tmp):
        hf_m = read_json(tmp)
        if hf_m and hf_m.get('run', 'full') != N['run']:
            hf_m = None
    if best_m is not None and (hf_m is None or len(hf_m['shards']) <= len(best_m['shards'])):
        log(f'📦 Feature store: {best_dir} ({len(best_m["shards"])} chunks; HF has {len(hf_m["shards"]) if hf_m else "n/a"})')
        return best_dir, best_m
    assert hf_m is not None, f'Feature store not found locally, in /kaggle/input, or on HF ({repo}). Run NB01 first.'
    dl.mkdir(parents=True, exist_ok=True)
    shutil.copy(tmp, dl / 'manifest.json')
    for i, s in enumerate(hf_m['shards']):
        if (dl / s).exists():
            continue
        if best_dir is not None and (best_dir / s).exists():
            shutil.copy(best_dir / s, dl / s)          # reuse chunk from Kaggle input
        else:
            fb.pull(s, dl / s)
        if i % 10 == 0:
            log(f'   feature chunks ready: {i+1}/{len(hf_m["shards"])}')
    fb.pull('slides_meta.csv', dl / 'slides_meta.csv')
    missing = [s for s in hf_m['shards'] if not (dl / s).exists()]
    assert not missing, f'Could not download chunks: {missing[:5]}'
    log(f'📦 Feature store assembled from HF (+ Kaggle input) at {dl}: {len(hf_m["shards"])} chunks')
    return dl, hf_m


def load_features(feat_dir, manifest, ids=None, with_coords=False):
    """Load shards into RAM: {image_id: fp16 tensor [N, D]} (and coords if asked)."""
    ids = set(ids) if ids is not None else None
    feats, coords = {}, {}
    for s, v in manifest['shards'].items():
        if ids is not None and not ids.intersection(v['ids']):
            continue
        d = torch.load(Path(feat_dir) / s, map_location='cpu', weights_only=True)
        for k, x in d.items():
            if ids is None or k in ids:
                feats[k] = x['feats']
                if with_coords:
                    coords[k] = x['coords']
    return (feats, coords) if with_coords else feats


# ── Thesis figures & tables: one consistent style, PNG(300 dpi)+PDF, CSV+MD+LaTeX ──
# Colours validated for colour-vision deficiency (fixed order, entity-bound, never re-cycled).
METHOD_ORDER  = ['fedprox', 'fedavg', 'centralized', 'local_A_Radboud', 'local_B_Karolinska']
METHOD_COLORS = {'fedprox': '#2a78d6', 'fedavg': '#eb6834', 'centralized': '#1baf7a',
                 'local_A_Radboud': '#eda100', 'local_B_Karolinska': '#e87ba4'}
METHOD_LS     = {'fedprox': '-', 'fedavg': '-', 'centralized': '-', 'local_A_Radboud': '--', 'local_B_Karolinska': '--'}
METHOD_MARK   = {'fedprox': 'o', 'fedavg': 's', 'centralized': 'D', 'local_A_Radboud': '^', 'local_B_Karolinska': 'v'}
METHOD_NICE   = {'centralized': 'Centralised (pooled)', 'local_A_Radboud': 'Local only – Radboud',
                 'local_B_Karolinska': 'Local only – Karolinska', 'fedavg': 'FedAvg', 'fedprox': 'FedProx'}
SITE_COLORS   = {'A_Radboud': '#2a78d6', 'radboud': '#2a78d6', 'B_Karolinska': '#eb6834', 'karolinska': '#eb6834'}
SITE_NICE     = {'A_Radboud': 'Hospital A – Radboud', 'radboud': 'Hospital A – Radboud',
                 'B_Karolinska': 'Hospital B – Karolinska', 'karolinska': 'Hospital B – Karolinska', 'pooled': 'Both hospitals'}
SPLIT_COLORS  = {'train': '#2a78d6', 'val': '#eb6834', 'test': '#1baf7a'}
ISUP_COLORS   = ['#86b6ef', '#5598e7', '#2a78d6', '#1c5cab', '#104281', '#0b2a52']   # ordinal ramp, ISUP 0→5
INK, INK2, GRID = '#0b0b0b', '#52514e', '#e4e3df'


def thesis_style():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        'figure.dpi': 110, 'savefig.dpi': 300, 'savefig.bbox': 'tight', 'figure.facecolor': 'white',
        'axes.facecolor': 'white', 'axes.edgecolor': INK2, 'axes.labelcolor': INK, 'axes.titleweight': 'bold',
        'axes.titlesize': 11, 'axes.labelsize': 10, 'axes.spines.top': False, 'axes.spines.right': False,
        'axes.grid': True, 'grid.color': GRID, 'grid.linewidth': 0.6, 'axes.axisbelow': True,
        'xtick.color': INK2, 'ytick.color': INK2, 'xtick.labelsize': 9, 'ytick.labelsize': 9,
        'legend.fontsize': 8.5, 'legend.frameon': False, 'lines.linewidth': 2.0, 'lines.markersize': 6,
        'font.family': 'DejaVu Sans', 'text.color': INK, 'pdf.fonttype': 42})
    return plt


class Report:
    """Collects thesis figures/tables for one notebook. Every artefact is written flat into
    `out_dir` (so it lands in the HF repo and the Kaggle dataset) and listed with its caption
    in REPORT_INDEX_<tag>.md / .csv."""
    def __init__(self, out_dir, tag, title):
        self.dir, self.tag, self.title = Path(out_dir), tag, title
        self.dir.mkdir(parents=True, exist_ok=True)
        self.items, self.nf, self.nt = [], 0, 0

    def fig(self, fig, key, caption):
        import matplotlib.pyplot as plt
        self.nf += 1
        base = f'fig_{self.tag}{self.nf:02d}_{key}'
        for ext in ('png', 'pdf'):
            fig.savefig(self.dir / f'{base}.{ext}')
        plt.close(fig)
        self.items.append({'kind': 'figure', 'id': f'Figure {self.tag}{self.nf}', 'file': f'{base}.png',
                           'also': f'{base}.pdf', 'caption': caption})
        log(f'   🖼  {base}.png')
        return base

    def table(self, df, key, caption, index=False, floatfmt=4):
        self.nt += 1
        base = f'tab_{self.tag}{self.nt:02d}_{key}'
        d = df.copy()
        d.to_csv(self.dir / f'{base}.csv', index=index)
        try:
            md = d.round(floatfmt).to_markdown(index=index)
        except Exception:
            md = d.round(floatfmt).to_string(index=index)
        (self.dir / f'{base}.md').write_text(f'**Table {self.tag}{self.nt}.** {caption}\n\n{md}\n')
        try:
            tex = d.round(floatfmt).to_latex(index=index, caption=caption, label=f'tab:{self.tag}{self.nt}', escape=True, float_format=f'%.{floatfmt}f')
            (self.dir / f'{base}.tex').write_text(tex)
        except Exception:
            pass
        self.items.append({'kind': 'table', 'id': f'Table {self.tag}{self.nt}', 'file': f'{base}.csv',
                           'also': f'{base}.md/.tex', 'caption': caption})
        log(f'   📋 {base}.csv')
        return base

    def safe(self, fn, what):
        """Run one figure/table builder; a failure is logged and never stops the notebook."""
        try:
            fn()
        except Exception as e:
            log(f'   ⚠️  {what} skipped: {type(e).__name__}: {e}')

    def finalize(self):
        idx = pd.DataFrame(self.items)
        idx.to_csv(self.dir / f'REPORT_INDEX_{self.tag}.csv', index=False)
        lines = [f'# {self.title}', f'Generated {datetime.now():%Y-%m-%d %H:%M}', '']
        for it in self.items:
            lines.append(f'- **{it["id"]}** — `{it["file"]}` (+ {it["also"]}): {it["caption"]}')
        (self.dir / f'REPORT_INDEX_{self.tag}.md').write_text('\n'.join(lines) + '\n')
        files = [self.dir / f'REPORT_INDEX_{self.tag}.csv', self.dir / f'REPORT_INDEX_{self.tag}.md']
        for it in self.items:
            stem = Path(it['file']).stem
            files += [p for p in self.dir.glob(f'{stem}.*')]
        log(f'📚 {self.title}: {sum(i["kind"]=="figure" for i in self.items)} figures, '
            f'{sum(i["kind"]=="table" for i in self.items)} tables')
        return files


def env_versions():
    """Exact software/hardware versions of this run (saved with the outputs for reproducibility)."""
    import platform, importlib
    v = {'python': platform.python_version(), 'platform': platform.platform(), 'time': str(datetime.now()),
         'on_kaggle': ON_KAGGLE, 'cuda_available': torch.cuda.is_available(),
         'cuda': getattr(torch.version, 'cuda', None),
         'gpus': [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]}
    for mod in ['torch', 'numpy', 'pandas', 'sklearn', 'scipy', 'transformers', 'huggingface_hub', 'timm',
                'openslide', 'cv2', 'matplotlib']:
        try:
            m = importlib.import_module(mod)
            v[mod] = getattr(m, '__version__', getattr(m, '__library_version__', 'unknown'))
        except Exception:
            v[mod] = None
    return v

# ---
# ── Cell 2: Configuration ─────────────────────────────────────────────────────
import cv2
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

CFG = dict(
    backbone        = 'phikon',   # 'phikon' (open, default) | 'phikon-v2' | 'uni' (gated: request access first)
    tile_px         = 224,        # tile size at level 0 (20x)
    tissue_min      = 0.50,       # min tissue fraction for a tile to be kept
    max_tiles       = 768,        # cap per slide (random, seeded); smoke test showed 56% of slides > 512 tissue tiles
    batch_tiles     = 256,        # tiles per GPU forward pass
    num_workers     = 4,          # parallel slide readers (Kaggle gives 4 CPUs)
    shard_slides    = 250,        # slides per shard file
    flush_every_min = 30,         # also flush a shard at least every N minutes
    # every shard is pushed to HF (blocking) AND a new private Kaggle dataset version (background)
    max_session_h   = 11.0,       # stop extracting at 11 h → ≥ 1 h left for report + final uploads before Kaggle's 12 h kill
    smoke           = False,      # True = quick test on `smoke_slides` slides, saved under *-smoke names only
    smoke_slides    = 100,
    max_slides      = None,       # (advanced) limit slides in a FULL run – normally leave None
    allow_no_backup = False,      # never run the real job without the HF backup
    seed            = 42,
)
CFG = apply_overrides(CFG)
seed_everything(CFG['seed'])

BACKBONES = {
    'phikon':    dict(hf='owkin/phikon',    dim=768,  loader='hf_vit'),
    'phikon-v2': dict(hf='owkin/phikon-v2', dim=1024, loader='hf_auto'),
    'uni':       dict(hf='MahmoodLab/UNI',  dim=1024, loader='timm_uni'),
    'debug-tiny':dict(hf=None,              dim=64,   loader='debug'),   # tests only
}
BB = BACKBONES[CFG['backbone']]

PANDA = find_panda_dir()
assert PANDA is not None, 'PANDA competition data not attached! (+ Add Data → prostate-cancer-grade-assessment)'
IMG_DIR = PANDA / 'train_images'
NAMES = run_names(CFG['backbone'], CFG['smoke'])
if CFG['smoke']:
    CFG['max_slides'] = CFG['max_slides'] or CFG['smoke_slides']
    log(f'🧪 SMOKE TEST: {CFG["max_slides"]} slides → everything saved under "{NAMES["features"]}" (the real run is untouched)')
elif CFG['max_slides']:
    log(f'⚠️  FULL run limited to max_slides={CFG["max_slides"]} – set it to None for the complete dataset')
FEAT_DIR = NAMES['feat_dir']
FEAT_DIR.mkdir(parents=True, exist_ok=True)
REPO_NAME = NAMES['features']
log(f'PANDA dir: {PANDA}\nFeature dir: {FEAT_DIR}\nConfig: {CFG}')

DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
NUM_GPUS = torch.cuda.device_count()
log(f'Device: {DEVICE} | GPUs: {NUM_GPUS} | torch {torch.__version__}')

# ---
# ── Cell 3: GPU self-test (guards against the cuDNN crash seen in v1) ─────────
if DEVICE.type == 'cuda':
    try:
        _c = nn.Conv2d(3, 8, 3).to(DEVICE).half()
        with torch.no_grad():
            _ = _c(torch.randn(2, 3, 64, 64, device=DEVICE).half())
        torch.cuda.synchronize(); log('✅ cuDNN conv self-test passed')
    except Exception as e:
        torch.backends.cudnn.enabled = False
        log(f'⚠️  cuDNN self-test failed ({e}); cuDNN DISABLED – ViT still runs fine (only patch-embed is a conv)')

# ---
# ── Cell 4: Backups + resume state ────────────────────────────────────────────
backup = CloudBackup(REPO_NAME, kaggle_slug=REPO_NAME,
                     kaggle_title=f'PANDA FL v2 {CFG["backbone"]} features{NAMES["title_sfx"]}',
                     local_dir=FEAT_DIR)
backup.require(CFG['allow_no_backup'])

MANIFEST = FEAT_DIR / 'manifest.json'
atomic_json(env_versions(), FEAT_DIR / 'environment_nb01.json'); backup.push(FEAT_DIR / 'environment_nb01.json')   # exact versions → HF + Kaggle

def empty_manifest():
    return {'backbone': CFG['backbone'], 'run': NAMES['run'], 'dim': BB['dim'], 'tile_px': CFG['tile_px'],
            'level': 0, 'max_tiles': CFG['max_tiles'], 'shards': {}, 'failed': {}, 'created': str(datetime.now())}

def manifest_problem(m):
    """None if manifest `m` matches this run's settings, else a short reason."""
    if m.get('run', 'full') != NAMES['run']:
        return f'it belongs to a {m.get("run", "full")} run, this is a {NAMES["run"]} run'
    saved = (m.get('backbone'), m.get('tile_px'), m.get('max_tiles'))
    want = (CFG['backbone'], CFG['tile_px'], CFG['max_tiles'])
    if saved != want:
        return f'settings differ (saved backbone/tile_px/max_tiles={saved}, now {want})'
    return None

def quarantine_local(reason):
    """Move stale LOCAL files aside (never deletes, never touches the cloud)."""
    stale = FEAT_DIR.parent / f'{FEAT_DIR.name}_stale_{datetime.now():%Y%m%d_%H%M%S}'
    stale.mkdir()
    for f in FEAT_DIR.iterdir():
        if f.is_file() and f.name != 'dataset-metadata.json':
            shutil.move(str(f), stale / f.name)
    log(f'🧹 Local files in {FEAT_DIR.name} were from an older/different run ({reason}) → moved to {stale.name}')

def restore_state():
    """Priority: local manifest (same session) → HF manifest → Kaggle input copy → fresh.
    A stale LOCAL copy that was never backed up is moved aside automatically;
    a mismatching CLOUD copy stops the run (it may be real work – never overwritten)."""
    m, src = read_json(MANIFEST), 'local'
    if m is not None and manifest_problem(m):
        quarantine_local(manifest_problem(m)); m = None
    if m is None and backup.pull('manifest.json', MANIFEST):
        m, src = read_json(MANIFEST), 'huggingface'
        if manifest_problem(m):
            raise RuntimeError(f'The HF repo {REPO_NAME} already holds features with different settings: '
                               f'{manifest_problem(m)}. Not overwriting cloud data – use the saved settings or ask for help.')
    if m is None:
        for p in glob.glob(f'/kaggle/input/**/{REPO_NAME}/manifest.json', recursive=True) + \
                 glob.glob(f'/kaggle/input/{REPO_NAME}/manifest.json'):
            cand = read_json(p, {})
            if not cand or manifest_problem(cand):
                continue
            shutil.copy(p, MANIFEST); m, src = read_json(MANIFEST), f'kaggle-input:{p}'; break
    if m is None:
        # Before starting fresh, PROVE the cloud really holds no progress – a network blip must
        # never lead to overwriting shard_00000 / manifest.json on Hugging Face.
        remote = backup.remote_files_strict()            # raises if HF is unreachable
        if remote and any(f.startswith('shard_') or f == 'manifest.json' for f in remote):
            raise RuntimeError('HF already holds feature shards but the manifest could not be loaded. '
                               'Not starting fresh (would overwrite cloud data). Re-run the notebook; '
                               'if this persists, check the HF repo.')
        for f in FEAT_DIR.glob('shard_*.pt'):            # orphan local shards without a manifest
            quarantine_local('shards without a manifest'); break
        log('🆕 No previous progress found' + (' (verified on HF)' if backup.hf_ok else '') + ' – starting fresh.')
        return empty_manifest()
    # make sure every shard listed in the manifest exists locally & loads
    kin_dirs = [Path(q).parent for q in glob.glob('/kaggle/input/**/manifest.json', recursive=True)
                if read_json(q, {}).get('run', 'full') == NAMES['run']]
    for shard in list(m['shards']):
        p = FEAT_DIR / shard
        if not p.exists():
            kin = [d_ / shard for d_ in kin_dirs if (d_ / shard).exists()]
            if kin: shutil.copy(kin[0], p)
            else: backup.pull(shard, p)
        if not p.exists():
            log(f'❗ shard {shard} missing everywhere – its slides will be re-extracted')
            m['shards'].pop(shard)
    n_done = sum(len(v['ids']) for v in m['shards'].values())
    log(f'♻️  Resumed from {src}: {len(m["shards"])} shards, {n_done} slides done, {len(m["failed"])} failed')
    return m

manifest = restore_state()
atomic_json(manifest, MANIFEST)

# ---
# ── Cell 5: Slide table (ALL slides, both centres) ───────────────────────────
df = pd.read_csv(PANDA / 'train.csv')
df['file'] = df['image_id'].apply(lambda x: str(IMG_DIR / f'{x}.tiff'))
df = df[df['file'].apply(os.path.exists)].reset_index(drop=True)
df['isup_from_gleason'] = df['gleason_score'].map(ISUP_FROM_GLEASON)
df['label_consistent'] = df['isup_from_gleason'] == df['isup_grade']
log(f'Slides on disk: {len(df)}')
print(pd.crosstab(df['data_provider'], df['isup_grade'], margins=True))
print(f'Label-inconsistent slides (gleason vs isup): {(~df.label_consistent).sum()} (kept here, handled in NB02)')

done_ids = {i for v in manifest['shards'].values() for i in v['ids']}
todo = df[~df['image_id'].isin(done_ids) & ~df['image_id'].isin(manifest['failed'].keys())]
if CFG['max_slides']:
    todo = todo.head(max(0, CFG['max_slides'] - len(done_ids)))
log(f'To process this session: {len(todo)} slides')

# ---
# ── Cell 6: Tissue detection + tiling (aspect-ratio-correct) ─────────────────
import openslide

def read_rgb(slide, loc, level, size):
    """read_region → RGB, compositing transparent pixels onto white."""
    rgba = np.asarray(slide.read_region(loc, level, size))
    rgb = rgba[..., :3].copy()
    rgb[rgba[..., 3] == 0] = 255
    return rgb

def lowres_rgb(slide, target_ds=16):
    """Low-resolution RGB image + its exact downsample factor. Uses the smallest pyramid level
    (PANDA: 16×); if a slide has no small level, falls back to an aspect-correct thumbnail
    (the v1 bug: thumbnail size must be READ BACK, never assumed)."""
    low = slide.level_count - 1
    if slide.level_downsamples[low] >= 8:
        return read_rgb(slide, (0, 0), low, slide.level_dimensions[low]), float(slide.level_downsamples[low])
    W, H = slide.level_dimensions[0]
    th = np.asarray(slide.get_thumbnail((max(1, W // target_ds), max(1, H // target_ds))).convert('RGB'))
    return th, W / th.shape[1]

def tissue_mask(rgb):
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    sat = hsv[..., 1]
    otsu, _ = cv2.threshold(sat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thr = float(np.clip(otsu, 15, 60))
    gray = rgb.mean(-1)
    r, g, b = [rgb[..., i].astype(int) for i in range(3)]
    pen = (g > r + 15) | ((b > r + 25) & (b > g + 15)) | (gray < 40)    # green/blue/black pen marks
    m = ((sat > thr) & (gray < 235) & ~pen).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
    return m

def tile_coords(slide, image_id):
    """Grid of level-0 tiles whose tissue fraction ≥ tissue_min (computed on the lowest level)."""
    T = CFG['tile_px']
    W, H = slide.level_dimensions[0]
    low_rgb, ds = lowres_rgb(slide)
    lh, lw = low_rgb.shape[:2]
    mask = tissue_mask(low_rgb).astype(np.float32)
    ncol, nrow = W // T, H // T
    if ncol == 0 or nrow == 0:
        return np.zeros((0, 2), np.int32), 0
    cw, ch = min(lw, int(round(ncol * T / ds))), min(lh, int(round(nrow * T / ds)))
    frac = cv2.resize(mask[:ch, :cw], (ncol, nrow), interpolation=cv2.INTER_AREA)
    for thr in (CFG['tissue_min'], 0.25, 0.10):          # graceful fallback for tiny biopsies
        ys, xs = np.where(frac >= thr)
        if len(xs): break
    coords = np.stack([xs * T, ys * T], 1).astype(np.int32)
    n_all = len(coords)
    if n_all > CFG['max_tiles']:
        rng = np.random.default_rng(int(hashlib.md5(image_id.encode()).hexdigest()[:8], 16))
        coords = coords[np.sort(rng.choice(n_all, CFG['max_tiles'], replace=False))]
    return coords, n_all

class SlideTiles(Dataset):
    def __init__(self, rows): self.rows = rows.reset_index(drop=True)
    def __len__(self): return len(self.rows)
    def __getitem__(self, i):
        r = self.rows.iloc[i]
        out = {'image_id': r.image_id, 'err': ''}
        try:
            sl = openslide.OpenSlide(r.file)
            coords, n_all = tile_coords(sl, r.image_id)
            if len(coords) == 0:
                sl.close(); out['err'] = 'no tissue tiles'; return out
            T = CFG['tile_px']
            tiles = np.stack([read_rgb(sl, (int(x), int(y)), 0, (T, T)) for x, y in coords])
            sl.close()
            out.update(tiles=torch.from_numpy(tiles), coords=torch.from_numpy(coords), n_all=int(n_all))
        except Exception as e:
            out['err'] = f'{type(e).__name__}: {str(e)[:200]}'
        return out

# quick visual QC on 1 slide (saved, not blocking)
try:
    _r = df.sample(1, random_state=1).iloc[0]
    _sl = openslide.OpenSlide(_r.file); _c, _n = tile_coords(_sl, _r.image_id)
    thumb, ds = lowres_rgb(_sl); _sl.close()
    import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 4)); ax.imshow(thumb)
    for x, y in _c:
        ax.add_patch(plt.Rectangle((x/ds, y/ds), CFG['tile_px']/ds, CFG['tile_px']/ds, fill=False, ec='lime', lw=0.4))
    ax.set_title(f'{_r.image_id} ({_r.data_provider}, ISUP {_r.isup_grade}) – {len(_c)} tiles kept of {_n}')
    ax.axis('off'); plt.savefig(WORK / 'qc_tiling_example.png', dpi=120, bbox_inches='tight'); plt.close()
    log(f'QC example saved: {len(_c)}/{_n} tiles')
except Exception as e:
    log(f'QC example skipped: {e}')

# ---
# ── Cell 7: Load the frozen foundation model ─────────────────────────────────
MEAN = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
STD  = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)

class Encoder(nn.Module):
    def __init__(self, bb):
        super().__init__()
        self.kind = bb['loader']
        if self.kind == 'hf_vit':
            from transformers import ViTModel
            self.m = ViTModel.from_pretrained(bb['hf'], add_pooling_layer=False)
        elif self.kind == 'hf_auto':
            from transformers import AutoModel
            self.m = AutoModel.from_pretrained(bb['hf'])
        elif self.kind == 'timm_uni':
            import timm
            self.m = timm.create_model('hf-hub:MahmoodLab/uni', pretrained=True,
                                       init_values=1e-5, dynamic_img_size=True)
        else:  # debug
            self.m = nn.Sequential(nn.Conv2d(3, 16, 7, 4), nn.ReLU(), nn.AdaptiveAvgPool2d(1),
                                   nn.Flatten(), nn.Linear(16, bb['dim']))
        self.register_buffer('mean', MEAN.clone()); self.register_buffer('std', STD.clone())
    def forward(self, x_uint8_nhwc):
        x = x_uint8_nhwc.permute(0, 3, 1, 2).float().div_(255.)
        x = (x - self.mean) / self.std
        # mixed precision: matmuls in fp16 on the T4, LayerNorm/softmax kept in fp32 (numerically safe).
        # autocast is entered INSIDE forward so it also applies in each DataParallel replica thread.
        with torch.autocast('cuda', dtype=torch.float16, enabled=x.is_cuda):
            if self.kind in ('hf_vit', 'hf_auto'):
                out = self.m(pixel_values=x).last_hidden_state[:, 0]   # CLS token
            else:
                out = self.m(x)
        return out.float()

encoder = Encoder(BB).to(DEVICE).eval()
if NUM_GPUS > 1:
    encoder = nn.DataParallel(encoder)
with torch.no_grad():
    _o = encoder(torch.randint(0, 255, (4, CFG['tile_px'], CFG['tile_px'], 3), dtype=torch.uint8, device=DEVICE))
assert _o.shape == (4, BB['dim']), _o.shape
log(f'✅ Encoder {CFG["backbone"]} ready, output dim {BB["dim"]}')

# ---
# ── Cell 8: Extraction loop – every chunk goes to HF + private Kaggle dataset ─
buffer, last_flush = {}, time.time()

def write_slides_meta():
    """Per-slide status table, rewritten at every chunk so each backup version is self-contained."""
    rows = [{'image_id': i, 'shard': sh, 'n_tiles': v['n_tiles'][i],
             'n_tiles_total': v.get('n_tiles_total', {}).get(i, v['n_tiles'][i])}
            for sh, v in manifest['shards'].items() for i in v['ids']]
    meta = df[['image_id', 'data_provider', 'isup_grade', 'gleason_score', 'label_consistent']].merge(
        pd.DataFrame(rows, columns=['image_id', 'shard', 'n_tiles', 'n_tiles_total']), on='image_id', how='left')
    meta['status'] = np.where(meta['shard'].notna(), 'done',
                              np.where(meta['image_id'].isin(manifest['failed'].keys()), 'failed', 'pending'))
    meta.to_csv(FEAT_DIR / 'slides_meta.csv.tmp', index=False)
    os.replace(FEAT_DIR / 'slides_meta.csv.tmp', FEAT_DIR / 'slides_meta.csv')
    return meta
stats = {'slides': 0, 'tiles': 0, 't0': time.time()}

def flush(reason):
    """Write buffer → shard file → manifest → HF. Manifest is uploaded last so it
    only ever references shards that are already safely stored."""
    global buffer, last_flush
    if not buffer:
        return
    idx = 1 + max([int(re.findall(r'\d+', s)[0]) for s in manifest['shards']] or [-1])
    name = f'shard_{idx:05d}.pt'
    atomic_torch_save({k: {'feats': v['feats'], 'coords': v['coords']} for k, v in buffer.items()}, FEAT_DIR / name)
    manifest['shards'][name] = {'ids': list(buffer),
                                'n_tiles': {k: int(v['feats'].shape[0]) for k, v in buffer.items()},
                                'n_tiles_total': {k: v['n_all'] for k, v in buffer.items()},
                                'written': str(datetime.now())}
    atomic_json(manifest, MANIFEST)
    write_slides_meta()
    t_up, mb = time.time(), (FEAT_DIR / name).stat().st_size / 1e6
    ok = backup.push_many([FEAT_DIR / name, FEAT_DIR / 'slides_meta.csv'], msg=f'add {name}') \
         and backup.push(MANIFEST, 'manifest.json')          # manifest last: only lists stored shards
    dt = time.time() - t_up
    n_done = sum(len(v['ids']) for v in manifest['shards'].values())
    log(f'💾 {name}: {len(buffer)} slides ({reason}) | total done {n_done} | HF {"✅" if ok else "❌ (local only)"} '
        f'{mb:.0f} MB in {dt:.0f}s ({mb / max(dt, 1e-6):.1f} MB/s)')
    buffer, last_flush = {}, time.time()
    backup.kaggle_push_async(f'{name} – {n_done}/{len(df)} slides extracted')

def process(rows, label):
    global last_flush
    if len(rows) == 0:
        return
    nw = CFG['num_workers']
    try:
        shm_gb = shutil.disk_usage('/dev/shm').free / 1e9
        if nw and shm_gb < 2:
            nw = min(nw, 2); log(f'   /dev/shm only {shm_gb:.1f} GB free → using {nw} reader workers')
    except Exception:
        pass
    # prefetch_factor=1: ≤ nw slides (≤77 MB each) in shared memory at once
    dl = DataLoader(SlideTiles(rows), batch_size=None, shuffle=False,
                    num_workers=nw, prefetch_factor=1 if nw else None, persistent_workers=False)
    for item in dl:
        iid = item['image_id']
        if item['err']:
            manifest['failed'][iid] = item['err']
            log(f'   ✗ {iid}: {item["err"]}')
            continue
        tiles = item['tiles']
        feats = []
        with torch.no_grad():
            for i in range(0, len(tiles), CFG['batch_tiles']):
                x = tiles[i:i + CFG['batch_tiles']].to(DEVICE, non_blocking=True)
                feats.append(encoder(x).float().cpu().half())
        f = torch.cat(feats)
        if not torch.isfinite(f.float()).all():                 # never let one bad slide stop the run
            manifest['failed'][iid] = 'non-finite features'
            log(f'   ✗ {iid}: non-finite features – skipped'); continue
        buffer[iid] = {'feats': f, 'coords': item['coords'], 'n_all': item['n_all']}
        manifest['failed'].pop(iid, None)
        stats['slides'] += 1; stats['tiles'] += len(tiles)
        if stats['slides'] % 50 == 0:
            el = time.time() - stats['t0']
            rate = stats['slides'] / el
            log(f'[{label}] {stats["slides"]}/{len(rows)} slides | {stats["tiles"]/el:.0f} tiles/s | '
                f'ETA {(len(rows)-stats["slides"])/max(rate,1e-9)/3600:.2f} h')
        if len(buffer) >= CFG['shard_slides']:
            flush('shard full')
        elif time.time() - last_flush > CFG['flush_every_min'] * 60:
            flush('time-based')
        if time_left_h(CFG['max_session_h']) < 0:
            log('⏰ Session time budget reached – flushing and stopping cleanly. Re-run to resume.')
            break
    flush('end of pass')

try:
    process(todo, 'main')
    # one retry pass for transient read errors (not for 'no tissue')
    retry_ids = [k for k, v in manifest['failed'].items() if 'no tissue' not in v]
    if retry_ids and time_left_h(CFG['max_session_h']) > 0.3:
        log(f'🔁 Retrying {len(retry_ids)} failed slides once')
        stats.update(slides=0, tiles=0, t0=time.time())
        process(df[df.image_id.isin(retry_ids)], 'retry')
except BaseException as e:
    log(f'❌ Interrupted: {type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}')
    flush('emergency'); atomic_json(manifest, MANIFEST); backup.push(MANIFEST, 'manifest.json')
    backup.kaggle_final_sync('emergency sync after interruption')
    raise
finally:
    flush('final')
    atomic_json(manifest, MANIFEST)
    backup.push(MANIFEST, 'manifest.json')

# ---
# ── Cell 9: Summary, slide metadata, final Kaggle snapshot ───────────────────
meta = write_slides_meta()
backup.push(FEAT_DIR / 'slides_meta.csv', 'slides_meta.csv')
if (WORK / 'qc_tiling_example.png').exists():
    shutil.copy(WORK / 'qc_tiling_example.png', FEAT_DIR / 'qc_tiling_example.png')
    backup.push(FEAT_DIR / 'qc_tiling_example.png', 'qc_tiling_example.png')

print(meta.groupby(['data_provider', 'status']).size().unstack(fill_value=0))
d = meta[meta.status == 'done']
if len(d):
    print(f"\nTiles/slide kept: median {d.n_tiles.median():.0f}, mean {d.n_tiles.mean():.0f}, "
          f"max {d.n_tiles.max():.0f} | slides hitting the {CFG['max_tiles']} cap: {(d.n_tiles_total > CFG['max_tiles']).mean()*100:.1f}%")
print('Failures:', dict(pd.Series(list(manifest['failed'].values())).str.split(':').str[0].value_counts()) if manifest['failed'] else 0)

n_pending = int((meta.status == 'pending').sum())

# ---
# ── Cell 10: Thesis report – PREPROCESSING figures & tables (→ HF + Kaggle) ───
# Needs ~5–10 min; skipped if the session is nearly out of time (it is rebuilt on the next run).
RUN_REPORT = time_left_h(CFG['max_session_h']) > 0.2
if not RUN_REPORT:
    log('⏭️  Preprocessing report postponed to the next run (session time nearly used).')
plt = thesis_style()
R = Report(FEAT_DIR, 'P', 'Preprocessing & tiling report')
SITES = ['radboud', 'karolinska']
d_all = df.copy()

def p_dataset():
    ct = pd.crosstab(d_all.data_provider, d_all.isup_grade).reindex(SITES)
    fig, ax = plt.subplots(figsize=(8, 4))
    w = 0.38
    for i, s_ in enumerate(SITES):
        ax.bar(np.arange(6) + (i - .5) * w, ct.loc[s_].values, w, color=SITE_COLORS[s_], edgecolor='white',
               linewidth=1.5, label=f'{SITE_NICE[s_]} (n={ct.loc[s_].sum():,})')
    ax.set_xticks(range(6)); ax.set_xticklabels([f'ISUP {g}' for g in range(6)])
    ax.set_ylabel('Number of biopsies (slides)'); ax.set_title('PANDA slides per ISUP grade and hospital'); ax.legend()
    R.fig(fig, 'dataset_isup_by_site', 'Distribution of ISUP grade groups in the full PANDA training set, per data provider (simulated hospital).')
    t = ct.copy(); t['Total'] = t.sum(1); t.loc['Total'] = t.sum(0)
    pct = (ct.T / ct.sum(1)).T.mul(100).round(1).add_suffix(' (%)')
    R.table(t.join(pct).reset_index().rename(columns={'data_provider': 'Hospital'}), 'dataset_composition',
            'Number of slides per ISUP grade and hospital (with row percentages).')

def p_gleason():
    ct = pd.crosstab(d_all.gleason_score, d_all.data_provider).reindex(columns=SITES).fillna(0).astype(int)
    ct = ct.loc[ct.sum(1).sort_values().index]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    y = np.arange(len(ct)); h = 0.4
    for i, s_ in enumerate(SITES):
        ax.barh(y + (i - .5) * h, ct[s_].values, h, color=SITE_COLORS[s_], edgecolor='white', linewidth=1.2, label=SITE_NICE[s_])
    ax.set_yticks(y); ax.set_yticklabels(ct.index); ax.set_xlabel('Number of slides')
    ax.set_title('Gleason score labels per hospital'); ax.legend(loc='lower right'); ax.grid(axis='y', visible=False)
    R.fig(fig, 'gleason_scores_by_site', 'Gleason score labels per hospital; note the different benign label conventions ("0+0" at Radboud vs "negative" at Karolinska).')
    R.table(ct.reset_index().rename(columns={'gleason_score': 'Gleason score'}), 'gleason_by_site',
            'Gleason score label counts per hospital; slides whose Gleason score contradicts the ISUP grade are removed before training.')

def p_tiling_examples():
    picks = pd.concat([d_all[(d_all.data_provider == s_) & (d_all.isup_grade == g)].sample(1, random_state=3)
                       for s_ in SITES for g in (0, 2, 5)])
    fig, axes = plt.subplots(2, 3, figsize=(15, 6.5))
    for ax, (_, r) in zip(axes.ravel(), picks.iterrows()):
        sl = openslide.OpenSlide(r.file); c, n = tile_coords(sl, r.image_id)
        th, ds = lowres_rgb(sl); sl.close()
        ax.imshow(th)
        for x, y in c:
            ax.add_patch(plt.Rectangle((x / ds, y / ds), CFG['tile_px'] / ds, CFG['tile_px'] / ds, fill=False, ec='#1baf7a', lw=0.5))
        ax.set_title(f'{SITE_NICE[r.data_provider]} · ISUP {r.isup_grade}\n{len(c)} tiles kept of {n}', fontsize=9, fontweight='normal')
        ax.axis('off')
    fig.suptitle('Tissue detection and 20× tiling (224 px tiles outlined)', fontweight='bold')
    R.fig(fig, 'tiling_qc_examples', 'Examples of tissue detection and tiling on the lowest-resolution level; each outlined box is one 224×224 px tile at 20× (≈0.5 µm/px).')

def p_tile_gallery():
    fig, axes = plt.subplots(6, 6, figsize=(10, 10.5))
    for g in range(6):
        for j, s_ in enumerate(SITES):
            r = d_all[(d_all.data_provider == s_) & (d_all.isup_grade == g)].sample(1, random_state=11 + g).iloc[0]
            sl = openslide.OpenSlide(r.file); c, _ = tile_coords(sl, r.image_id)
            sel = c[np.random.default_rng(g).choice(len(c), min(3, len(c)), replace=False)] if len(c) else []
            for k in range(3):
                ax = axes[g, j * 3 + k]; ax.axis('off')
                if k < len(sel):
                    ax.imshow(read_rgb(sl, (int(sel[k][0]), int(sel[k][1])), 0, (CFG['tile_px'], CFG['tile_px'])))
            sl.close()
        axes[g, 0].text(-0.15, 0.5, f'ISUP {g}', transform=axes[g, 0].transAxes, ha='right', va='center', fontsize=10, fontweight='bold')
    axes[0, 1].set_title(SITE_NICE['radboud'], fontsize=10); axes[0, 4].set_title(SITE_NICE['karolinska'], fontsize=10)
    fig.suptitle('Example 20× tiles per ISUP grade and hospital', fontweight='bold')
    R.fig(fig, 'tile_gallery', 'Random tissue tiles (224×224 px, 20×) per ISUP grade: left three columns Radboud, right three Karolinska – illustrating the stain/scanner difference between hospitals.')

def p_tiles_per_slide():
    m = meta[meta.status == 'done']
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8), sharey=True)
    for ax, col, ttl in [(axes[0], 'n_tiles_total', 'Tissue tiles detected'), (axes[1], 'n_tiles', f'Tiles used (cap {CFG["max_tiles"]})')]:
        bins = np.linspace(0, max(m[col].max(), 1), 40)
        for s_ in SITES:
            ax.hist(m[m.data_provider == s_][col], bins=bins, color=SITE_COLORS[s_], alpha=0.6, label=SITE_NICE[s_], edgecolor='white', linewidth=0.5)
        if CFG['max_tiles'] <= m[col].max() * 1.15:        # only draw the cap when it is inside the data range
            ax.axvline(CFG['max_tiles'], color=INK2, ls=':', lw=1.2)
        ax.set_xlim(0, max(m[col].max(), 1) * 1.05); ax.set_title(ttl); ax.set_xlabel('Tiles per slide')
    axes[0].set_ylabel('Number of slides'); axes[0].legend()
    R.fig(fig, 'tiles_per_slide', 'Number of 20× tissue tiles per slide before and after the 512-tile cap, per hospital (dotted line = cap).')

def p_colour_shift():
    rows = []
    for s_ in SITES:
        for _, r in d_all[d_all.data_provider == s_].sample(min(150, (d_all.data_provider == s_).sum()), random_state=5).iterrows():
            try:
                sl = openslide.OpenSlide(r.file); rgb, _ = lowres_rgb(sl); sl.close()
                m_ = tissue_mask(rgb).astype(bool)
                if m_.sum() < 50: continue
                hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[m_]
                od = -np.log(np.clip(rgb[m_].astype(float) / 255, 1e-3, 1))
                rows.append({'site': s_, 'hue': hsv[:, 0].mean() * 2, 'saturation': hsv[:, 1].mean() / 255,
                             'brightness': hsv[:, 2].mean() / 255, 'optical_density': od.sum(1).mean()})
            except Exception:
                pass
    c = pd.DataFrame(rows)
    fig, axes = plt.subplots(1, 4, figsize=(15, 3.4))
    for ax, col, lab in zip(axes, ['hue', 'saturation', 'brightness', 'optical_density'],
                            ['Mean hue (°)', 'Mean saturation', 'Mean brightness', 'Mean optical density']):
        bins = np.linspace(c[col].min(), c[col].max(), 30)
        for s_ in SITES:
            ax.hist(c[c.site == s_][col], bins=bins, color=SITE_COLORS[s_], alpha=0.6, label=SITE_NICE[s_], edgecolor='white', linewidth=0.5)
        ax.set_xlabel(lab)
    axes[0].set_ylabel('Slides'); axes[0].legend(fontsize=7.5)
    fig.suptitle('Colour / stain characteristics of tissue per hospital (evidence of non-IID data)', fontweight='bold', y=1.03)
    R.fig(fig, 'colour_shift_by_site', 'Distribution of mean tissue colour statistics per slide (random 150 slides per hospital), showing the stain/scanner domain shift between the two hospitals.')
    t = c.groupby('site')[['hue', 'saturation', 'brightness', 'optical_density']].agg(['mean', 'std']).round(3)
    t.columns = [f'{a} {b}' for a, b in t.columns]
    R.table(t.reset_index().rename(columns={'site': 'Hospital'}), 'colour_stats_by_site',
            'Mean (SD) tissue colour statistics per hospital on 150 random slides each.')

def p_extraction_table():
    m = meta.copy(); dn = m[m.status == 'done']
    rows = []
    for s_ in SITES + ['all']:
        a_ = m if s_ == 'all' else m[m.data_provider == s_]; b_ = dn if s_ == 'all' else dn[dn.data_provider == s_]
        rows.append({'Hospital': SITE_NICE.get(s_, 'Both hospitals'), 'Slides': len(a_), 'Extracted': len(b_),
                     'Failed': int((a_.status == 'failed').sum()), 'Pending': int((a_.status == 'pending').sum()),
                     'Tiles used': int(b_.n_tiles.sum()), 'Tiles/slide median': float(b_.n_tiles.median()),
                     'Tiles/slide mean': round(float(b_.n_tiles.mean()), 1), 'Tiles/slide max': int(b_.n_tiles.max()) if len(b_) else 0,
                     '% slides capped': round(float((b_.n_tiles_total > CFG['max_tiles']).mean() * 100), 1)})
    R.table(pd.DataFrame(rows), 'extraction_summary', f'Tile-extraction summary (tiles of {CFG["tile_px"]} px at 20×, ≥{int(CFG["tissue_min"]*100)}% tissue, ≤{CFG["max_tiles"]} per slide; backbone {CFG["backbone"]}).')
    if manifest['failed']:
        f_ = pd.DataFrame([{'image_id': k, 'reason': v} for k, v in manifest['failed'].items()]).merge(
            d_all[['image_id', 'data_provider', 'isup_grade']], on='image_id', how='left')
        R.table(f_, 'failed_slides', 'Slides excluded during extraction and the logged reason.')

for fn, what in ([] if not RUN_REPORT else [(p_dataset, 'dataset figure'), (p_gleason, 'gleason figure'), (p_tiling_examples, 'tiling examples'),
                 (p_tile_gallery, 'tile gallery'), (p_tiles_per_slide, 'tiles/slide'), (p_colour_shift, 'colour shift'),
                 (p_extraction_table, 'extraction table')]):
    R.safe(fn, what)
if RUN_REPORT:
    backup.push_many(R.finalize(), msg='preprocessing report (figures & tables)')

# ---
# blocking: guarantees the latest private Kaggle version contains EVERY shard before the session ends
backup.kaggle_final_sync('features: ' + ('SMOKE TEST' if CFG['smoke'] else
                                         'COMPLETE' if n_pending == 0 else f'{n_pending} slides pending'))
if CFG['smoke']:
    log(f'🏁 SMOKE TEST COMPLETE ({int((meta.status == "done").sum())} slides) – next: NB02 with smoke=True, '
        'or set smoke=False here for the full run.')
else:
    log('🏁 ' + ('ALL SLIDES DONE – proceed to NB02.' if n_pending == 0 else
                 f'{n_pending} slides still pending – re-run this notebook to continue.'))

# ---
# --- artifacts/nb02_federated.ipynb ---
# ── Cell 0: Install (idempotent) ──────────────────────────────────────────────
import subprocess, sys, os
if os.path.exists('/kaggle/working'):
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-U', 'huggingface_hub', 'kaggle'], check=False)
print('✅ install step done')

# ---
# ── Common infrastructure (identical in all v2 notebooks) ─────────────────────
# Secrets, logging, atomic saves, retries, Hugging Face + Kaggle backup,
# and a session time-guard so work is flushed before Kaggle kills the session.
import os, sys, json, time, glob, shutil, random, subprocess, traceback, hashlib, math, re
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import torch
import warnings
try:   # small validation sets can miss a grade → sklearn warns on every round; the metric code already handles it (NaN)
    from sklearn.exceptions import UndefinedMetricWarning
    warnings.filterwarnings('ignore', category=UndefinedMetricWarning)
except Exception:
    pass
warnings.filterwarnings('ignore', message='.*Only one class is present in y_true.*')

ON_KAGGLE     = os.path.exists('/kaggle/working')
WORK          = Path('/kaggle/working') if ON_KAGGLE else Path(os.environ.get('FL_WORK', './work')).resolve()
WORK.mkdir(parents=True, exist_ok=True)
SESSION_START = time.time()

KAGGLE_USERNAME_DEFAULT = 'obaidullahmiakhil2'  # used only if no KAGGLE_USERNAME secret exists
# Accepted Kaggle-secret names (first one found wins) – works with either naming style.
SECRET_HF       = ('HF_WRITE_TOKEN', 'HF_TOKEN')
SECRET_KAGGLE   = ('KAGGLE_API_KEY', 'KAGGLE_KEY')
SECRET_KG_USER  = ('KAGGLE_USERNAME',)


def log(msg: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | +{(time.time()-SESSION_START)/3600:5.2f}h] {msg}"
    print(line, flush=True)
    try:
        with open(WORK / 'run_log.txt', 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass


def get_secret(names):
    """Return the first available secret among `names` (env var or Kaggle secret)."""
    names = (names,) if isinstance(names, str) else names
    for name in names:
        if os.environ.get(name):
            return os.environ[name]
    try:
        from kaggle_secrets import UserSecretsClient
        client = UserSecretsClient()
    except Exception:
        return None
    for name in names:
        try:
            v = client.get_secret(name)
            if v:
                return v.strip()
        except Exception:
            continue
    return None


KAGGLE_USERNAME = (get_secret(SECRET_KG_USER) or KAGGLE_USERNAME_DEFAULT).strip()


def apply_overrides(cfg: dict) -> dict:
    """Allow CFG overrides via env var FL_CFG_OVERRIDE='{"key": value}' (used for testing)."""
    ov = os.environ.get('FL_CFG_OVERRIDE')
    if ov:
        cfg.update(json.loads(ov))
        log(f'CFG overrides applied: {ov}')
    return cfg


def seed_everything(seed: int):
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def atomic_torch_save(obj, path):
    path = Path(path); tmp = path.with_name(path.name + '.tmp')
    torch.save(obj, tmp); os.replace(tmp, path)


def atomic_json(obj, path):
    path = Path(path); tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'w') as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _is_not_found(e):
    """True only for a definite 'file/repo does not exist' answer from the server.
    LocalEntryNotFoundError means 'could not reach the server' → treated as transient."""
    n = type(e).__name__
    if n.startswith('Local') or 'Connection' in n or 'Timeout' in n:
        return False
    return 'NotFound' in n or ' 404' in str(e)[:200]


def retry(fn, tries=5, base_wait=5, what='operation'):
    """Retry transient failures with exponential back-off; 'not found' errors are raised at once."""
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if _is_not_found(e):
                raise
            log(f'⚠️  {what} failed (attempt {i+1}/{tries}): {type(e).__name__}: {str(e)[:300]}')
            if i == tries - 1:
                raise
            time.sleep(base_wait * 2 ** i)


def hours_elapsed():
    return (time.time() - SESSION_START) / 3600


def time_left_h(max_session_h):
    return max_session_h - hours_elapsed()


class _LocalFakeHub:
    """TEST ONLY: mimics the few HfApi calls we use, backed by a local folder.
    Activated only when env FL_FAKE_HF_DIR is set. Never used on Kaggle."""
    def __init__(self, root):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
    def whoami(self): return {'name': 'localtest'}
    def create_repo(self, repo_id, **kw): (self.root / repo_id).mkdir(parents=True, exist_ok=True)
    def upload_file(self, path_or_fileobj, path_in_repo, repo_id, **kw):
        dst = self.root / repo_id / path_in_repo; dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path_or_fileobj, dst)
    def list_repo_files(self, repo_id, **kw):
        base = self.root / repo_id
        return [str(p.relative_to(base)) for p in base.rglob('*') if p.is_file()]
    def download(self, repo_id, filename, dest):
        src = self.root / repo_id / filename
        if not src.exists(): return None
        dest = Path(dest); dest.parent.mkdir(parents=True, exist_ok=True); shutil.copy(src, dest); return dest


class CloudBackup:
    """
    Two independent off-site backups:
      • Hugging Face Hub (private *dataset* repo) – continuous: every file is pushed
        the moment it is written (shards, checkpoints, results).
      • PRIVATE Kaggle Dataset – a new dataset version after EVERY chunk (shard /
        finished experiment) plus a blocking final sync; each version holds all files.
    A failure of one backup never stops training; it is logged and retried.
    """
    def __init__(self, hf_repo_name, kaggle_slug, kaggle_title, local_dir,
                 kaggle_every_h=2.0, enable_hf=True, enable_kaggle=True):
        self.local_dir = Path(local_dir); self.local_dir.mkdir(parents=True, exist_ok=True)
        self.hf_ok, self.kg_ok = False, False
        import threading
        self._kg_lock, self._kg_thread, self._kg_pending = threading.Lock(), None, None
        self._kg_exists, self._kaggle_failed, self.kaggle_versions = False, False, 0
        self.fake = None
        for old in self.local_dir.parent.glob(f'.kg_stage_{self.local_dir.name}_*'):   # leftovers of a killed session
            shutil.rmtree(old, ignore_errors=True)

        # ── Hugging Face ──────────────────────────────────────────────────────
        if os.environ.get('FL_FAKE_HF_DIR'):
            self.fake = _LocalFakeHub(os.environ['FL_FAKE_HF_DIR'])
            self.api, self.token = self.fake, None
            self.hf_repo = f'localtest/{hf_repo_name}'
            self.fake.create_repo(self.hf_repo); self.hf_ok = True
            log(f'🧪 Using LOCAL FAKE HF hub at {self.fake.root}')
        elif enable_hf:
            tok = get_secret(SECRET_HF)
            if tok:
                try:
                    from huggingface_hub import HfApi, login
                    login(token=tok, add_to_git_credential=False)
                    os.environ['HF_TOKEN'] = tok            # authenticated model downloads too
                    self.api, self.token = HfApi(token=tok), tok
                    user = retry(lambda: self.api.whoami()['name'], what='HF whoami')
                    self.hf_repo = f'{user}/{hf_repo_name}'
                    retry(lambda: self.api.create_repo(self.hf_repo, repo_type='dataset',
                                                       private=True, exist_ok=True), what='HF create_repo')
                    self.hf_ok = True
                    log(f'✅ HF backup ON  → https://huggingface.co/datasets/{self.hf_repo}')
                except Exception as e:
                    log(f'❌ HF backup could not start: {e}')
            else:
                log(f'⚠️  HF backup OFF (none of the secrets {SECRET_HF} found – attach one under Add-ons → Secrets)')

        # ── Kaggle ────────────────────────────────────────────────────────────
        if enable_kaggle and (ON_KAGGLE or os.environ.get('FL_TEST_KAGGLE_CLI')):
            key = get_secret(SECRET_KAGGLE)
            if key:
                os.environ['KAGGLE_USERNAME'] = KAGGLE_USERNAME
                os.environ['KAGGLE_KEY'] = key
                if key.startswith('KGAT_'):
                    os.environ['KAGGLE_API_TOKEN'] = key
                self.kg_id = f'{KAGGLE_USERNAME}/{kaggle_slug}'
                atomic_json({'title': kaggle_title[:50], 'id': self.kg_id,
                             'licenses': [{'name': 'CC0-1.0'}]},
                            self.local_dir / 'dataset-metadata.json')
                self.kg_ok = True
                log(f'✅ Kaggle backup ON → https://www.kaggle.com/datasets/{self.kg_id} (private; new version after every chunk)')
            else:
                log(f'⚠️  Kaggle backup OFF (none of the secrets {SECRET_KAGGLE} found)')

    # ── HF helpers ────────────────────────────────────────────────────────────
    def push(self, local_path, path_in_repo=None, msg=None):
        """Upload one file to HF (with retries). Returns True on success."""
        if not self.hf_ok:
            return False
        local_path = Path(local_path)
        path_in_repo = path_in_repo or local_path.name
        try:
            retry(lambda: self.api.upload_file(path_or_fileobj=str(local_path), path_in_repo=path_in_repo,
                                               repo_id=self.hf_repo, repo_type='dataset',
                                               commit_message=msg or f'upload {path_in_repo}'),
                  what=f'HF upload {path_in_repo}')
            return True
        except Exception as e:
            log(f'❌ HF upload permanently failed for {path_in_repo}: {e} (file kept locally; will retry next flush)')
            return False

    def push_many(self, local_paths, msg='batch upload'):
        """Upload several files in ONE commit (keeps HF commit count low)."""
        if not self.hf_ok:
            return False
        local_paths = [Path(p) for p in local_paths if Path(p).exists()]
        if self.fake:
            for p in local_paths:
                self.fake.upload_file(str(p), p.name, self.hf_repo)
            return True
        try:
            from huggingface_hub import CommitOperationAdd
            ops = [CommitOperationAdd(path_in_repo=p.name, path_or_fileobj=str(p)) for p in local_paths]
            retry(lambda: self.api.create_commit(repo_id=self.hf_repo, repo_type='dataset',
                                                 operations=ops, commit_message=msg), what=f'HF commit ({msg})')
            return True
        except Exception as e:
            log(f'❌ HF batch upload failed ({msg}): {e} – files kept locally')
            return False

    def require(self, allow_no_backup=False):
        """Refuse to start long work without the primary (HF) backup – no silent local-only runs."""
        if self.hf_ok or allow_no_backup:
            if not self.kg_ok:
                log('⚠️  Kaggle backup is OFF – HF is still on, continuing.')
            return
        raise RuntimeError('Hugging Face backup is OFF, so progress would only live in this session. '
                           f'Attach a secret named one of {SECRET_HF} (Add-ons → Secrets, tick it for this notebook) '
                           'and re-run. (Set CFG allow_no_backup=True only for throw-away tests.)')

    def remote_files_strict(self):
        """Remote file list, or an exception if HF cannot be reached (never a silent empty list).
        Used before any decision that could overwrite existing cloud data."""
        if not self.hf_ok:
            return None
        return retry(lambda: self.api.list_repo_files(self.hf_repo, repo_type='dataset'), what='HF list (strict)')

    def list_remote(self, prefix=''):
        if not self.hf_ok:
            return []
        try:
            files = retry(lambda: self.api.list_repo_files(self.hf_repo, repo_type='dataset'), what='HF list')
            return [f for f in files if f.startswith(prefix)]
        except Exception:
            return []

    def pull(self, path_in_repo, dest):
        """Download a file from HF into `dest` (full path). Returns Path or None if absent."""
        if not self.hf_ok:
            return None
        dest = Path(dest)
        if self.fake:
            return self.fake.download(self.hf_repo, path_in_repo, dest)
        try:
            from huggingface_hub import hf_hub_download
            p = retry(lambda: hf_hub_download(self.hf_repo, path_in_repo, repo_type='dataset',
                                              token=self.token, local_dir=str(WORK / '_hf_cache')),
                      tries=3, what=f'HF download {path_in_repo}')
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + '.tmp')
            shutil.move(str(p), str(tmp)); os.replace(tmp, dest)    # move, not copy: no double disk use
            return dest
        except Exception as e:
            if not _is_not_found(e):
                log(f'   HF download failed: {path_in_repo}: {type(e).__name__}: {str(e)[:150]}')
            return None

    # ── Kaggle helpers ────────────────────────────────────────────────────────
    # Every chunk (feature shard / finished experiment) triggers a new version of the
    # PRIVATE Kaggle dataset. Uploads run in a background thread so the GPU keeps
    # working; chunks finished while an upload is running are coalesced into the
    # next version (each version always contains ALL files written so far).
    def _make_stage(self, msg, stamp):
        """Consistent point-in-time snapshot of local_dir via hard links (no extra disk, no copying).
        Files being written (*.tmp) are skipped, so the training thread is never disturbed."""
        import uuid
        stage = self.local_dir.parent / f'.kg_stage_{self.local_dir.name}_{uuid.uuid4().hex[:6]}'
        stage.mkdir()
        files = []
        for p in sorted(self.local_dir.iterdir()):
            if not p.is_file() or p.name.endswith('.tmp') or p.name == 'kaggle_sync.json':
                continue
            try:
                os.link(p, stage / p.name)
            except FileNotFoundError:
                continue                                       # replaced/removed meanwhile
            except OSError:
                os.symlink(p.resolve(), stage / p.name)        # cross-device fallback
            if p.name != 'dataset-metadata.json':
                files.append(p.name)
        atomic_json({'message': msg, 'time': stamp, 'files': files}, stage / 'kaggle_sync.json')
        return stage, files

    def _kaggle_version_once(self, msg):
        stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        stage, files = self._make_stage(msg, stamp)
        try:
            return self._kaggle_upload(stage, files, msg, stamp)
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def _kaggle_upload(self, stage, files, msg, stamp):
        d = str(stage)
        if not self._kg_exists:
            st = subprocess.run(['kaggle', 'datasets', 'status', self.kg_id], capture_output=True, text=True)
            self._kg_exists = st.returncode == 0 and not re.search(r'404|not found|error', st.stdout + st.stderr, re.I)
        # `create` without --public ⇒ the dataset is PRIVATE
        cmd = (['kaggle', 'datasets', 'version', '-p', d, '-m', f'{msg} @ {stamp}'] if self._kg_exists
               else ['kaggle', 'datasets', 'create', '-p', d])
        t0 = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        ok = r.returncode == 0 and not re.search(r'\berror\b|failed', out[-400:], re.I)
        if ok:
            self._kg_exists = True
            self.kaggle_versions += 1
        last = out.splitlines()[-1] if out else ''
        log(f'   Kaggle {"version" if cmd[2] == "version" else "CREATE (private)"} '
            f'{"✅" if ok else "❌"} [{msg}] {len(files)} files, {time.time()-t0:.0f}s | {last[:160]}')
        return ok

    def _kaggle_worker(self, msg):
        while True:
            ok = False
            for attempt in range(3):
                try:
                    ok = self._kaggle_version_once(msg)
                except Exception as e:
                    log(f'   Kaggle upload exception: {e}')
                if ok:
                    break
                time.sleep(60 * (attempt + 1))      # e.g. previous version still processing
            if not ok:
                self._kaggle_failed = True          # final sync will retry
            with self._kg_lock:
                if self._kg_pending is None:
                    self._kg_thread = None
                    return
                msg, self._kg_pending = self._kg_pending, None

    def kaggle_push_async(self, msg):
        """Queue a Kaggle dataset version containing everything in local_dir (non-blocking)."""
        if not self.kg_ok:
            return
        import threading
        with self._kg_lock:
            if self._kg_thread is not None:
                self._kg_pending = msg              # coalesce: next version includes this chunk too
                return
            self._kg_thread = threading.Thread(target=self._kaggle_worker, args=(msg,), daemon=True)
            self._kg_thread.start()
        log(f'☁️  Kaggle push queued: {msg}')

    def kaggle_final_sync(self, msg, timeout_min=25):
        """Blocking: wait for any running upload, then make sure the LAST version holds every file."""
        if not self.kg_ok:
            return False
        th = self._kg_thread
        if th is not None:
            log('⏳ waiting for running Kaggle upload to finish …')
            th.join(timeout=timeout_min * 60)
        for attempt in range(4):
            try:
                if self._kaggle_version_once(msg):
                    self._kaggle_failed = False
                    log(f'✅ Kaggle dataset fully synced (private): https://www.kaggle.com/datasets/{self.kg_id}')
                    return True
            except Exception as e:
                log(f'   Kaggle final sync exception: {e}')
            if attempt < 3:
                time.sleep(90 * (attempt + 1))
        log('❌ Kaggle final sync failed – all files are safe on HF and in /kaggle/working; re-run this cell later.')
        return False

    # backwards-compatible name used by older cells
    def kaggle_snapshot(self, msg='checkpoint', force=False):
        return self.kaggle_final_sync(msg) if force else self.kaggle_push_async(msg)


def find_panda_dir():
    cands = ['/kaggle/input/competitions/prostate-cancer-grade-assessment',
             '/kaggle/input/prostate-cancer-grade-assessment',
             os.environ.get('PANDA_DIR', '')]
    for c in cands:
        if c and (Path(c) / 'train.csv').exists():
            return Path(c)
    hits = glob.glob('/kaggle/input/**/train.csv', recursive=True)
    for h in hits:
        if (Path(h).parent / 'train_images').exists():
            return Path(h).parent
    return None


ISUP_FROM_GLEASON = {'0+0': 0, 'negative': 0, '3+3': 1, '3+4': 2, '4+3': 3,
                     '4+4': 4, '3+5': 4, '5+3': 4, '4+5': 5, '5+4': 5, '5+5': 5}


def run_names(backbone, smoke=False):
    """All cloud/local names for one run type. Smoke-test runs get a '-smoke' suffix everywhere,
    so they can never mix with (or block) the real run."""
    s, u = ('-smoke', '_smoke') if smoke else ('', '')
    return {'run': 'smoke' if smoke else 'full',
            'features': f'panda-fl-v2-{backbone}-features{s}', 'training': f'panda-fl-v2-{backbone}-training{s}',
            'results': f'panda-fl-v2-{backbone}-results{s}',
            'feat_dir': WORK / f'features_{backbone}{u}', 'out_dir': WORK / f'fl_outputs_{backbone}{u}',
            'fig_dir': WORK / f'results_{backbone}{u}', 'title_sfx': ' SMOKE' if smoke else ''}


def fetch_feature_store(backbone, smoke=False):
    """Locate the NB01 feature store and return (feature_dir, manifest).
    Candidates: this session's /kaggle/working, attached Kaggle input datasets, and HF.
    The copy with the MOST chunks wins (an attached Kaggle version can be older than HF);
    shards already present locally / in /kaggle/input are copied instead of re-downloaded."""
    N = run_names(backbone, smoke)
    repo = N['features']
    best_dir, best_m = None, None
    cands = [N['feat_dir'], Path(str(N['feat_dir']) + '_hf')] + \
            [Path(p).parent for p in glob.glob('/kaggle/input/**/manifest.json', recursive=True)]
    for c in cands:
        m = read_json(Path(c) / 'manifest.json')
        if m and m.get('backbone') == backbone and m.get('run', 'full') == N['run'] \
                and all((Path(c) / s).exists() for s in m['shards']):
            if best_m is None or len(m['shards']) > len(best_m['shards']):
                best_dir, best_m = Path(c), m
    dl = Path(str(N['feat_dir']) + '_hf')
    fb = CloudBackup(repo, repo, repo, dl, enable_kaggle=False)
    hf_m = None
    tmp = WORK / '_hf_manifest_check.json'
    if fb.hf_ok and fb.pull('manifest.json', tmp):
        hf_m = read_json(tmp)
        if hf_m and hf_m.get('run', 'full') != N['run']:
            hf_m = None
    if best_m is not None and (hf_m is None or len(hf_m['shards']) <= len(best_m['shards'])):
        log(f'📦 Feature store: {best_dir} ({len(best_m["shards"])} chunks; HF has {len(hf_m["shards"]) if hf_m else "n/a"})')
        return best_dir, best_m
    assert hf_m is not None, f'Feature store not found locally, in /kaggle/input, or on HF ({repo}). Run NB01 first.'
    dl.mkdir(parents=True, exist_ok=True)
    shutil.copy(tmp, dl / 'manifest.json')
    for i, s in enumerate(hf_m['shards']):
        if (dl / s).exists():
            continue
        if best_dir is not None and (best_dir / s).exists():
            shutil.copy(best_dir / s, dl / s)          # reuse chunk from Kaggle input
        else:
            fb.pull(s, dl / s)
        if i % 10 == 0:
            log(f'   feature chunks ready: {i+1}/{len(hf_m["shards"])}')
    fb.pull('slides_meta.csv', dl / 'slides_meta.csv')
    missing = [s for s in hf_m['shards'] if not (dl / s).exists()]
    assert not missing, f'Could not download chunks: {missing[:5]}'
    log(f'📦 Feature store assembled from HF (+ Kaggle input) at {dl}: {len(hf_m["shards"])} chunks')
    return dl, hf_m


def load_features(feat_dir, manifest, ids=None, with_coords=False):
    """Load shards into RAM: {image_id: fp16 tensor [N, D]} (and coords if asked)."""
    ids = set(ids) if ids is not None else None
    feats, coords = {}, {}
    for s, v in manifest['shards'].items():
        if ids is not None and not ids.intersection(v['ids']):
            continue
        d = torch.load(Path(feat_dir) / s, map_location='cpu', weights_only=True)
        for k, x in d.items():
            if ids is None or k in ids:
                feats[k] = x['feats']
                if with_coords:
                    coords[k] = x['coords']
    return (feats, coords) if with_coords else feats


# ── Thesis figures & tables: one consistent style, PNG(300 dpi)+PDF, CSV+MD+LaTeX ──
# Colours validated for colour-vision deficiency (fixed order, entity-bound, never re-cycled).
METHOD_ORDER  = ['fedprox', 'fedavg', 'centralized', 'local_A_Radboud', 'local_B_Karolinska']
METHOD_COLORS = {'fedprox': '#2a78d6', 'fedavg': '#eb6834', 'centralized': '#1baf7a',
                 'local_A_Radboud': '#eda100', 'local_B_Karolinska': '#e87ba4'}
METHOD_LS     = {'fedprox': '-', 'fedavg': '-', 'centralized': '-', 'local_A_Radboud': '--', 'local_B_Karolinska': '--'}
METHOD_MARK   = {'fedprox': 'o', 'fedavg': 's', 'centralized': 'D', 'local_A_Radboud': '^', 'local_B_Karolinska': 'v'}
METHOD_NICE   = {'centralized': 'Centralised (pooled)', 'local_A_Radboud': 'Local only – Radboud',
                 'local_B_Karolinska': 'Local only – Karolinska', 'fedavg': 'FedAvg', 'fedprox': 'FedProx'}
SITE_COLORS   = {'A_Radboud': '#2a78d6', 'radboud': '#2a78d6', 'B_Karolinska': '#eb6834', 'karolinska': '#eb6834'}
SITE_NICE     = {'A_Radboud': 'Hospital A – Radboud', 'radboud': 'Hospital A – Radboud',
                 'B_Karolinska': 'Hospital B – Karolinska', 'karolinska': 'Hospital B – Karolinska', 'pooled': 'Both hospitals'}
SPLIT_COLORS  = {'train': '#2a78d6', 'val': '#eb6834', 'test': '#1baf7a'}
ISUP_COLORS   = ['#86b6ef', '#5598e7', '#2a78d6', '#1c5cab', '#104281', '#0b2a52']   # ordinal ramp, ISUP 0→5
INK, INK2, GRID = '#0b0b0b', '#52514e', '#e4e3df'


def thesis_style():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        'figure.dpi': 110, 'savefig.dpi': 300, 'savefig.bbox': 'tight', 'figure.facecolor': 'white',
        'axes.facecolor': 'white', 'axes.edgecolor': INK2, 'axes.labelcolor': INK, 'axes.titleweight': 'bold',
        'axes.titlesize': 11, 'axes.labelsize': 10, 'axes.spines.top': False, 'axes.spines.right': False,
        'axes.grid': True, 'grid.color': GRID, 'grid.linewidth': 0.6, 'axes.axisbelow': True,
        'xtick.color': INK2, 'ytick.color': INK2, 'xtick.labelsize': 9, 'ytick.labelsize': 9,
        'legend.fontsize': 8.5, 'legend.frameon': False, 'lines.linewidth': 2.0, 'lines.markersize': 6,
        'font.family': 'DejaVu Sans', 'text.color': INK, 'pdf.fonttype': 42})
    return plt


class Report:
    """Collects thesis figures/tables for one notebook. Every artefact is written flat into
    `out_dir` (so it lands in the HF repo and the Kaggle dataset) and listed with its caption
    in REPORT_INDEX_<tag>.md / .csv."""
    def __init__(self, out_dir, tag, title):
        self.dir, self.tag, self.title = Path(out_dir), tag, title
        self.dir.mkdir(parents=True, exist_ok=True)
        self.items, self.nf, self.nt = [], 0, 0

    def fig(self, fig, key, caption):
        import matplotlib.pyplot as plt
        self.nf += 1
        base = f'fig_{self.tag}{self.nf:02d}_{key}'
        for ext in ('png', 'pdf'):
            fig.savefig(self.dir / f'{base}.{ext}')
        plt.close(fig)
        self.items.append({'kind': 'figure', 'id': f'Figure {self.tag}{self.nf}', 'file': f'{base}.png',
                           'also': f'{base}.pdf', 'caption': caption})
        log(f'   🖼  {base}.png')
        return base

    def table(self, df, key, caption, index=False, floatfmt=4):
        self.nt += 1
        base = f'tab_{self.tag}{self.nt:02d}_{key}'
        d = df.copy()
        d.to_csv(self.dir / f'{base}.csv', index=index)
        try:
            md = d.round(floatfmt).to_markdown(index=index)
        except Exception:
            md = d.round(floatfmt).to_string(index=index)
        (self.dir / f'{base}.md').write_text(f'**Table {self.tag}{self.nt}.** {caption}\n\n{md}\n')
        try:
            tex = d.round(floatfmt).to_latex(index=index, caption=caption, label=f'tab:{self.tag}{self.nt}', escape=True, float_format=f'%.{floatfmt}f')
            (self.dir / f'{base}.tex').write_text(tex)
        except Exception:
            pass
        self.items.append({'kind': 'table', 'id': f'Table {self.tag}{self.nt}', 'file': f'{base}.csv',
                           'also': f'{base}.md/.tex', 'caption': caption})
        log(f'   📋 {base}.csv')
        return base

    def safe(self, fn, what):
        """Run one figure/table builder; a failure is logged and never stops the notebook."""
        try:
            fn()
        except Exception as e:
            log(f'   ⚠️  {what} skipped: {type(e).__name__}: {e}')

    def finalize(self):
        idx = pd.DataFrame(self.items)
        idx.to_csv(self.dir / f'REPORT_INDEX_{self.tag}.csv', index=False)
        lines = [f'# {self.title}', f'Generated {datetime.now():%Y-%m-%d %H:%M}', '']
        for it in self.items:
            lines.append(f'- **{it["id"]}** — `{it["file"]}` (+ {it["also"]}): {it["caption"]}')
        (self.dir / f'REPORT_INDEX_{self.tag}.md').write_text('\n'.join(lines) + '\n')
        files = [self.dir / f'REPORT_INDEX_{self.tag}.csv', self.dir / f'REPORT_INDEX_{self.tag}.md']
        for it in self.items:
            stem = Path(it['file']).stem
            files += [p for p in self.dir.glob(f'{stem}.*')]
        log(f'📚 {self.title}: {sum(i["kind"]=="figure" for i in self.items)} figures, '
            f'{sum(i["kind"]=="table" for i in self.items)} tables')
        return files


def env_versions():
    """Exact software/hardware versions of this run (saved with the outputs for reproducibility)."""
    import platform, importlib
    v = {'python': platform.python_version(), 'platform': platform.platform(), 'time': str(datetime.now()),
         'on_kaggle': ON_KAGGLE, 'cuda_available': torch.cuda.is_available(),
         'cuda': getattr(torch.version, 'cuda', None),
         'gpus': [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]}
    for mod in ['torch', 'numpy', 'pandas', 'sklearn', 'scipy', 'transformers', 'huggingface_hub', 'timm',
                'openslide', 'cv2', 'matplotlib']:
        try:
            m = importlib.import_module(mod)
            v[mod] = getattr(m, '__version__', getattr(m, '__library_version__', 'unknown'))
        except Exception:
            v[mod] = None
    return v

# ---
# ── Cell 2: Configuration ─────────────────────────────────────────────────────
import copy
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.metrics import roc_auc_score, cohen_kappa_score, accuracy_score, balanced_accuracy_score

CFG = dict(
    backbone      = 'phikon',
    smoke         = False,        # True = quick end-to-end test on the NB01 SMOKE features (3 rounds, 1 seed, *-smoke names)
    hospitals     = {'A_Radboud': 'radboud', 'B_Karolinska': 'karolinska'},
    test_frac_folds = 2, val_folds = 1, n_folds = 10,   # → 20% test, 10% val, 70% train per hospital
    split_seed    = 2026,
    drop_inconsistent = True,     # drop slides whose gleason_score contradicts isup_grade
    dedup         = True, dedup_cos = 0.999,             # group near-duplicate slides into the same split
    # model
    hid = 256, attn = 128, dropout = 0.25,
    # optimisation (identical budget for every method: rounds × local_epochs = epochs)
    lr = 2e-4, wd = 1e-4, batch = 32, rounds = 40, local_epochs = 1, warmup_rounds = 2,
    bag_keep_min = 0.6, max_bag = 512,                    # random tile-dropout augmentation
    fedprox_mu = 0.01,
    methods = ['centralized', 'local_A_Radboud', 'local_B_Karolinska', 'fedavg', 'fedprox'],
    seeds   = [0, 1, 2, 3, 4],
    # safety
    allow_partial_features = False,   # True only for a quick trial: splits are frozen on first run!
    allow_no_backup = False,
    ckpt_push_every_min = 30, max_session_h = 11.3,   # + HF & private-Kaggle push after every experiment
)
CFG = apply_overrides(CFG)
NAMES = run_names(CFG['backbone'], CFG['smoke'])
if CFG['smoke']:
    CFG.update(allow_partial_features=True, rounds=min(CFG['rounds'], 3), seeds=CFG['seeds'][:1])
    log(f'🧪 SMOKE TEST: 3 rounds, 1 seed, reading "{NAMES["features"]}", writing "{NAMES["training"]}"')
HOSP = list(CFG['hospitals'])
OUT = NAMES['out_dir']
OUT.mkdir(parents=True, exist_ok=True)
OUT_REPO = NAMES['training']
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
log(f'Device {DEVICE} | outputs → {OUT}\n{json.dumps(CFG, indent=1)}')

backup = CloudBackup(OUT_REPO, kaggle_slug=OUT_REPO, kaggle_title=f'PANDA FL v2 {CFG["backbone"]} training{NAMES["title_sfx"]}',
                     local_dir=OUT)
backup.require(CFG['allow_no_backup'])
atomic_json(CFG, OUT / 'config_nb02.json'); backup.push(OUT / 'config_nb02.json')
atomic_json(env_versions(), OUT / 'environment_nb02.json'); backup.push(OUT / 'environment_nb02.json')   # exact versions → HF + Kaggle

# ---
# ── Cell 3: Load features + metadata ─────────────────────────────────────────
FEAT_DIR, MANIFEST = fetch_feature_store(CFG['backbone'], CFG['smoke'])
FEATS = load_features(FEAT_DIR, MANIFEST)
D_IN = next(iter(FEATS.values())).shape[1]
log(f'Loaded features for {len(FEATS)} slides (dim {D_IN}), '
    f'{sum(v.numel() for v in FEATS.values())*2/1e9:.2f} GB in RAM')

meta_p = FEAT_DIR / 'slides_meta.csv'
if meta_p.exists():
    meta = pd.read_csv(meta_p)
else:
    pdir = find_panda_dir(); assert pdir, 'Need slides_meta.csv from NB01 or the PANDA competition data attached'
    meta = pd.read_csv(pdir / 'train.csv')
    meta['label_consistent'] = meta['gleason_score'].map(ISUP_FROM_GLEASON) == meta['isup_grade']
if 'status' in meta.columns:
    n_pend = int((meta['status'] == 'pending').sum())
    log(f'Feature store status: {meta.status.value_counts().to_dict()}')
    if n_pend and not CFG['allow_partial_features'] and not (OUT / 'splits.csv').exists():
        raise RuntimeError(f'{n_pend} slides are still pending in NB01. Finish NB01 first (re-run it), '
                           'because the train/val/test splits are frozen the first time NB02 runs. '
                           'For a throw-away trial set allow_partial_features=True.')
META_ALL = meta.copy()   # kept for the data-cleaning table
meta = meta[meta['image_id'].isin(FEATS.keys())].copy()
meta = meta[meta['image_id'].map(lambda i: FEATS[i].shape[0] > 0)]
n0 = len(meta)
if CFG['drop_inconsistent']:
    bad = meta[~meta['label_consistent'].astype(bool)]
    log(f'Dropping {len(bad)} label-inconsistent slides: {bad[["image_id","gleason_score","isup_grade"]].values.tolist()[:10]}')
    meta = meta[meta['label_consistent'].astype(bool)]
BAD = bad if CFG['drop_inconsistent'] else meta.iloc[:0]
inv = {v: k for k, v in CFG['hospitals'].items()}
meta['hospital'] = meta['data_provider'].map(inv)
meta = meta[meta['hospital'].notna()].reset_index(drop=True)
log(f'Usable slides: {len(meta)} (from {n0})')
print(pd.crosstab(meta['hospital'], meta['isup_grade'], margins=True))

# ---
# ── Cell 4: Near-duplicate grouping + FIXED slide-level splits ───────────────
SPLITS = OUT / 'splits.csv'

def near_duplicate_groups(m):
    ids = m['image_id'].tolist()
    M = torch.stack([FEATS[i].float().mean(0) for i in ids]); M = F.normalize(M, dim=1)
    ntile = torch.tensor([FEATS[i].shape[0] for i in ids], dtype=torch.float32)
    parent = list(range(len(ids)))
    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]; a = parent[a]
        return a
    pairs = 0
    for s in range(0, len(ids), 2048):
        S = M[s:s+2048] @ M.T
        a, b = torch.where(S > CFG['dedup_cos'])
        a = a + s
        keep = a < b
        a, b = a[keep], b[keep]
        if pairs + len(a) > 0.05 * len(ids):      # far too many 'duplicates' → threshold is too loose here
            return list(ids), -1
        ratio = torch.minimum(ntile[a], ntile[b]) / torch.maximum(ntile[a], ntile[b])
        for x, y in zip(a[ratio > 0.9].tolist(), b[ratio > 0.9].tolist()):
            parent[find(x)] = find(y); pairs += 1
    groups = [ids[find(i)] for i in range(len(ids))]
    return groups, pairs

def make_splits():
    parts = []
    for h in HOSP:
        d = meta[meta.hospital == h].copy()
        if CFG['dedup']:
            d['group'], npairs = near_duplicate_groups(d)
            in_grp = d['group'].map(d['group'].value_counts()) > 1
            log(f'{h}: {npairs} near-duplicate pairs → {in_grp.sum()} slides grouped' if npairs >= 0 else
                f'{h}: too many near-identical pairs at cos>{CFG["dedup_cos"]} → dedup disabled for this backbone')
            if in_grp.mean() > 0.05:
                log('⚠️  >5% slides grouped – threshold too loose for this backbone; dedup disabled')
                d['group'] = d['image_id']
        else:
            d['group'] = d['image_id']
        nf, tf, vf = CFG['n_folds'], CFG['test_frac_folds'], CFG['val_folds']
        maxc = int(d['isup_grade'].value_counts().max())
        if nf > maxc:                      # only possible on tiny (smoke) data – full PANDA has >> 10 per grade
            nf = max(3, maxc)
            tf, vf = max(1, round(tf * nf / CFG['n_folds'])), max(1, round(vf * nf / CFG['n_folds']))
            log(f'⚠️  {h}: too few slides per grade for {CFG["n_folds"]} folds → using {nf} folds ({tf} test, {vf} val)')
        sgkf = StratifiedGroupKFold(n_splits=nf, shuffle=True, random_state=CFG['split_seed'])
        fold = np.zeros(len(d), int)
        for k, (_, te) in enumerate(sgkf.split(d, d['isup_grade'], groups=d['group'])):
            fold[te] = k
        d['split'] = np.where(fold < tf, 'test', np.where(fold < tf + vf, 'val', 'train'))
        parts.append(d[['image_id', 'hospital', 'data_provider', 'isup_grade', 'gleason_score', 'group', 'split']])
    return pd.concat(parts, ignore_index=True)

if SPLITS.exists() or backup.pull('splits.csv', SPLITS):
    splits = pd.read_csv(SPLITS)
    log('♻️  Using the EXISTING fixed splits (never regenerated once created).')
else:
    _remote = backup.remote_files_strict()        # raises if HF unreachable → never silently re-split
    if _remote and 'splits.csv' in _remote:
        raise RuntimeError('splits.csv exists on HF but could not be downloaded – refusing to create new splits.')
    splits = make_splits(); splits.to_csv(SPLITS, index=False); backup.push(SPLITS)
    backup.kaggle_push_async('fixed splits created')
    log('🆕 Splits created and backed up.')
splits = splits[splits.image_id.isin(FEATS.keys())].reset_index(drop=True)
print(splits.groupby(['hospital', 'split']).size().unstack())
print(pd.crosstab([splits.hospital, splits.split], splits.isup_grade))
# leakage checks
for h in HOSP:
    s = splits[splits.hospital == h]
    for a, b in [('train', 'val'), ('train', 'test'), ('val', 'test')]:
        assert not set(s[s.split == a].group) & set(s[s.split == b].group), f'group leakage {h} {a}/{b}'
assert splits.image_id.is_unique
log('✅ No slide/group leakage between train/val/test.')

# ---
# ── Cell 5: Model, data, ordinal metrics ─────────────────────────────────────
K = 6  # ISUP 0..5

class GatedABMIL(nn.Module):
    """Gated attention MIL (Ilse et al., ICML 2018) with an ordinal (cumulative-logit) head."""
    def __init__(self, d_in, hid=256, attn=128, drop=0.25, n_out=K - 1):
        super().__init__()
        self.fc = nn.Sequential(nn.Linear(d_in, hid), nn.GELU(), nn.Dropout(drop))
        self.att_a = nn.Sequential(nn.Linear(hid, attn), nn.Tanh())
        self.att_b = nn.Sequential(nn.Linear(hid, attn), nn.Sigmoid())
        self.att_w = nn.Linear(attn, 1)
        self.head = nn.Sequential(nn.LayerNorm(hid), nn.Dropout(drop), nn.Linear(hid, n_out))
    def forward(self, x, mask):
        h = self.fc(x)                                              # B,N,H
        s = self.att_w(self.att_a(h) * self.att_b(h)).squeeze(-1)   # B,N
        s = s.masked_fill(~mask, -1e4)
        a = torch.softmax(s, dim=1)
        z = torch.bmm(a.unsqueeze(1), h).squeeze(1)                  # B,H
        return self.head(z), a

def new_model():
    return GatedABMIL(D_IN, CFG['hid'], CFG['attn'], CFG['dropout']).to(DEVICE)

class Bags(Dataset):
    def __init__(self, df, train):
        self.ids = df['image_id'].tolist(); self.y = df['isup_grade'].astype(int).tolist(); self.train = train
    def __len__(self): return len(self.ids)
    def __getitem__(self, i):
        f = FEATS[self.ids[i]]
        n = f.shape[0]
        if self.train:
            k = max(1, int(round(n * np.random.uniform(CFG['bag_keep_min'], 1.0))))
            f = f[torch.randperm(n)[:k]]
        if f.shape[0] > CFG['max_bag']:
            f = f[torch.randperm(f.shape[0])[:CFG['max_bag']]]
        return f, self.y[i]

def collate(b):
    n = max(x[0].shape[0] for x in b)
    X = torch.zeros(len(b), n, b[0][0].shape[1], dtype=torch.float16)
    M = torch.zeros(len(b), n, dtype=torch.bool)
    for j, (f, _) in enumerate(b):
        X[j, :len(f)] = f; M[j, :len(f)] = True
    return X, M, torch.tensor([x[1] for x in b])

def loader(df, train):
    return DataLoader(Bags(df, train), batch_size=CFG['batch'], shuffle=train, collate_fn=collate,
                      num_workers=0, drop_last=False)

def ord_targets(y):
    return (y.unsqueeze(1) > torch.arange(K - 1, device=y.device)).float()

def mono_sigmoid(logits):
    s = 1 / (1 + np.exp(-np.asarray(logits, dtype=np.float64)))
    return np.minimum.accumulate(s, axis=1)          # enforce P(y>0) ≥ P(y>1) ≥ …

def class_probs(logits):
    s = mono_sigmoid(logits)
    pge = np.concatenate([np.ones((len(s), 1)), s, np.zeros((len(s), 1))], 1)
    p = np.clip(pge[:, :-1] - pge[:, 1:], 0, 1)
    return p / p.sum(1, keepdims=True)

def grade(logits, thr=None):
    thr = np.full(K - 1, 0.5) if thr is None else np.asarray(thr)
    return (mono_sigmoid(logits) > thr).sum(1)

def _auc(y, s):
    return float(roc_auc_score(y, s)) if len(np.unique(y)) == 2 else float('nan')

def compute_metrics(y, logits, thr=None):
    y = np.asarray(y).astype(int); s = mono_sigmoid(logits); p = class_probs(logits); g = grade(logits, thr)
    try:
        macro = float(roc_auc_score(y, p, multi_class='ovr', average='macro', labels=list(range(K))))
    except ValueError:
        macro = float('nan')
    return dict(n=int(len(y)),
                auc_cancer=_auc(y >= 1, s[:, 0]), auc_cspca=_auc(y >= 2, s[:, 1]), auc_macro_ovr=macro,
                qwk=float(cohen_kappa_score(y, g, weights='quadratic')) if len(np.unique(y)) > 1 else float('nan'),
                acc=float(accuracy_score(y, g)), bal_acc=float(balanced_accuracy_score(y, g)))

def selection_score(m):
    """Model-selection criterion (validation only): mean of QWK and 6-class macro AUC."""
    return float(np.nanmean([m['qwk'], m['auc_macro_ovr']]))

def tune_thresholds(y, logits, passes=3):
    """Coordinate search of the 5 ordinal thresholds maximising validation QWK."""
    thr = np.full(K - 1, 0.5); grid = np.arange(0.05, 0.951, 0.025)
    best = cohen_kappa_score(y, grade(logits, thr), weights='quadratic')
    for _ in range(passes):
        for k in range(K - 1):
            for t in grid:
                c = thr.copy(); c[k] = t
                q = cohen_kappa_score(y, grade(logits, c), weights='quadratic')
                if q > best + 1e-9:
                    best, thr = q, c
    return thr.tolist(), float(best)

@torch.no_grad()
def predict(model, df):
    model.eval(); out = []
    for X, M, _ in loader(df, False):
        out.append(model(X.to(DEVICE).float(), M.to(DEVICE))[0].float().cpu())
    return torch.cat(out).numpy() if out else np.zeros((0, K - 1))

# sanity
_m = new_model(); _X, _M, _y = next(iter(loader(splits.head(4), True)))
_l, _a = _m(_X.to(DEVICE).float(), _M.to(DEVICE)); assert _l.shape == (len(_y), K - 1)
log(f'✅ GatedABMIL params: {sum(p.numel() for p in _m.parameters()):,}'); del _m

# ---
# ── Cell 5b: Thesis report – DATA SPLIT & FEATURE-SPACE figures/tables (→ HF + Kaggle) ─
plt = thesis_style()
R = Report(OUT, 'S', 'Data split, federated partition & feature-space report')
SPL = ['train', 'val', 'test']

def s_split_sizes():
    ct = splits.groupby(['hospital', 'split']).size().unstack()[SPL]
    fig, ax = plt.subplots(figsize=(7.5, 3.4)); left = np.zeros(len(ct))
    for sp in SPL:
        ax.barh([SITE_NICE[h] for h in ct.index], ct[sp].values, left=left, color=SPLIT_COLORS[sp], edgecolor='white', linewidth=2, label=sp)
        for y, (l_, v) in enumerate(zip(left, ct[sp].values)):
            ax.text(l_ + v / 2, y, f'{v:,}', ha='center', va='center', color='white', fontsize=9, fontweight='bold')
        left += ct[sp].values
    ax.set_xlabel('Slides'); ax.set_title('Train / validation / test split per hospital'); ax.legend(ncol=3, loc='lower center', bbox_to_anchor=(.5, 1.12))
    ax.grid(axis='y', visible=False)
    R.fig(fig, 'split_sizes', 'Slide-level 70/10/20 train/validation/test split within each hospital (stratified by ISUP, near-duplicates grouped). Each hospital trains only on its own training slides.')

def s_split_isup():
    fig, axes = plt.subplots(1, len(HOSP), figsize=(12, 3.8), sharey=True)
    for ax, h in zip(np.atleast_1d(axes), HOSP):
        d = splits[splits.hospital == h]; w = 0.26
        for i, sp in enumerate(SPL):
            p_ = d[d.split == sp].isup_grade.value_counts(normalize=True).reindex(range(6), fill_value=0) * 100
            ax.bar(np.arange(6) + (i - 1) * w, p_.values, w, color=SPLIT_COLORS[sp], edgecolor='white', linewidth=1.2, label=sp)
        ax.set_title(SITE_NICE[h]); ax.set_xticks(range(6)); ax.set_xticklabels([f'ISUP {g}' for g in range(6)], fontsize=8)
    np.atleast_1d(axes)[0].set_ylabel('% of slides in split'); np.atleast_1d(axes)[0].legend()
    R.fig(fig, 'split_isup_distribution', 'ISUP grade proportions in each split of each hospital, confirming stratification (train, validation and test have matching label distributions).')

def s_client_label_skew():
    tr = splits[splits.split == 'train']
    p_ = pd.crosstab(tr.hospital, tr.isup_grade, normalize='index') * 100
    fig, ax = plt.subplots(figsize=(8, 2.8)); left = np.zeros(len(p_))
    for g in range(6):
        v = p_.get(g, pd.Series(0, index=p_.index)).values
        ax.barh([SITE_NICE[h] for h in p_.index], v, left=left, color=ISUP_COLORS[g], edgecolor='white', linewidth=2, label=f'ISUP {g}')
        left += v
    ax.set_xlabel('% of training slides'); ax.set_xlim(0, 100); ax.grid(axis='y', visible=False)
    ax.set_title('Label distribution of each federated client (training data)'); ax.legend(ncol=6, loc='lower center', bbox_to_anchor=(.5, 1.15), fontsize=8)
    R.fig(fig, 'client_label_skew', 'Label (ISUP) distribution of the two federated clients\' training data – the natural label skew between hospitals.')

def s_embedding():
    from sklearn.decomposition import PCA
    from sklearn.manifold import TSNE
    sub = splits.sample(min(3000, len(splits)), random_state=0)
    X = torch.stack([FEATS[i].float().mean(0) for i in sub.image_id]).numpy()
    Xp = PCA(n_components=min(50, X.shape[1], len(X) - 1), random_state=0).fit_transform(X)
    Z = TSNE(n_components=2, perplexity=min(30, max(5, len(X) // 10)), init='pca', random_state=0).fit_transform(Xp)
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for h in HOSP:
        m_ = (sub.hospital == h).values
        axes[0].scatter(Z[m_, 0], Z[m_, 1], s=6, color=SITE_COLORS[h], alpha=.6, label=SITE_NICE[h], linewidths=0)
    axes[0].set_title('Coloured by hospital'); axes[0].legend(markerscale=3)
    for g in range(6):
        m_ = (sub.isup_grade == g).values
        axes[1].scatter(Z[m_, 0], Z[m_, 1], s=6, color=ISUP_COLORS[g], alpha=.7, label=f'ISUP {g}', linewidths=0)
    axes[1].set_title('Coloured by ISUP grade'); axes[1].legend(markerscale=3, ncol=2)
    for ax in axes: ax.set_xticks([]); ax.set_yticks([]); ax.grid(False)
    fig.suptitle('t-SNE of slide-level mean foundation-model embeddings', fontweight='bold')
    R.fig(fig, 'tsne_site_vs_grade', 't-SNE projection of mean Phikon embeddings per slide (random subset). Separation by hospital shows the feature-level domain shift (non-IID clients); the grade gradient shows the features carry diagnostic signal.')
    pca = PCA(n_components=min(20, X.shape[1])).fit(X)
    fig, ax = plt.subplots(figsize=(6.5, 3.4))
    ax.bar(range(1, len(pca.explained_variance_ratio_) + 1), pca.explained_variance_ratio_ * 100, color='#2a78d6', edgecolor='white')
    ax.plot(range(1, len(pca.explained_variance_ratio_) + 1), np.cumsum(pca.explained_variance_ratio_) * 100, color=INK2, marker='o', ms=4, lw=1.5, label='cumulative')
    ax.set_xlabel('Principal component'); ax.set_ylabel('Explained variance (%)'); ax.set_title('PCA of slide embeddings'); ax.legend()
    R.fig(fig, 'pca_explained_variance', 'Explained variance of the first principal components of slide-level mean embeddings.')

def s_site_predictability():
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    sub = splits.sample(min(4000, len(splits)), random_state=1)
    X = torch.stack([FEATS[i].float().mean(0) for i in sub.image_id]).numpy()
    clf = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=0.1))
    auc_site = cross_val_score(clf, X, (sub.hospital == HOSP[0]).values, cv=5, scoring='roc_auc')
    auc_can = cross_val_score(clf, X, (sub.isup_grade >= 1).values, cv=5, scoring='roc_auc')
    auc_cs = cross_val_score(clf, X, (sub.isup_grade >= 2).values, cv=5, scoring='roc_auc')
    R.table(pd.DataFrame([
        {'Linear probe target': 'Hospital (A vs B)', 'CV ROC-AUC mean': auc_site.mean(), 'SD': auc_site.std()},
        {'Linear probe target': 'Cancer (ISUP ≥ 1)', 'CV ROC-AUC mean': auc_can.mean(), 'SD': auc_can.std()},
        {'Linear probe target': 'csPCa (ISUP ≥ 2)', 'CV ROC-AUC mean': auc_cs.mean(), 'SD': auc_cs.std()}]),
        'linear_probe_domain_shift', '5-fold cross-validated linear-probe ROC-AUC on mean slide embeddings. A high hospital AUC quantifies the domain shift between clients; the cancer/csPCa rows give a simple non-MIL baseline.')

def s_tables():
    ct = pd.crosstab([splits.hospital, splits.split], splits.isup_grade, margins=True, margins_name='Total')
    R.table(ct.reset_index(), 'split_counts', 'Number of slides per hospital, split and ISUP grade.')
    rows = []
    for prov, h in [(v, k) for k, v in CFG['hospitals'].items()]:
        a_ = META_ALL[META_ALL.data_provider == prov]
        rows.append({'Hospital': SITE_NICE[h], 'Slides in PANDA': len(a_),
                     'With features': int(a_.image_id.isin(FEATS.keys()).sum()),
                     'Removed: extraction failed / no tissue': int((~a_.image_id.isin(FEATS.keys())).sum()),
                     'Removed: Gleason–ISUP inconsistent': int((BAD.data_provider == prov).sum()),
                     'Used': int((splits.hospital == h).sum()),
                     'Near-duplicate groups (>1 slide)': int((splits[splits.hospital == h].group.value_counts() > 1).sum())})
    R.table(pd.DataFrame(rows), 'data_cleaning', 'Slide accounting from the raw PANDA table to the slides used for training and evaluation.')
    hp = {k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in CFG.items()}
    hp.update({'feature_dim': D_IN, 'model_parameters': sum(p.numel() for p in new_model().parameters())})
    R.table(pd.DataFrame(list(hp.items()), columns=['Hyper-parameter', 'Value']).astype(str), 'hyperparameters',
            'Hyper-parameters and settings used for all experiments (identical budget for every method).')

for fn, what in [(s_split_sizes, 'split sizes'), (s_split_isup, 'split ISUP'), (s_client_label_skew, 'client label skew'),
                 (s_embedding, 't-SNE/PCA'), (s_site_predictability, 'linear probe'), (s_tables, 'split tables')]:
    R.safe(fn, what)
backup.push_many(R.finalize(), msg='data-split report (figures & tables)')
backup.kaggle_push_async('data-split report')

# ---
# ── Cell 6: Federated engine (FedAvg / FedProx / centralised / local) ───────
class SessionTimeout(Exception):
    pass

def lr_at(r):
    R, w = CFG['rounds'], CFG['warmup_rounds']
    if r <= w:
        return CFG['lr'] * r / w
    return CFG['lr'] * 0.5 * (1 + math.cos(math.pi * (r - w) / max(1, R - w)))

def local_train(model, df, lr, mu=0.0, global_params=None):
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=CFG['wd'])
    model.train(); tot, n = 0.0, 0
    for _ in range(CFG['local_epochs']):
        for X, M, y in loader(df, True):
            X, M, y = X.to(DEVICE).float(), M.to(DEVICE), y.to(DEVICE)
            logits, _ = model(X, M)
            loss = F.binary_cross_entropy_with_logits(logits, ord_targets(y))
            if mu > 0:   # FedProx proximal term (Li et al., 2020)
                loss = loss + mu / 2 * sum(((p - g) ** 2).sum() for p, g in zip(model.parameters(), global_params))
            opt.zero_grad(set_to_none=True); loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0); opt.step()
            tot += loss.item() * len(y); n += len(y)
    return tot / max(n, 1)

def fedavg(states, sizes):
    tot = float(sum(sizes))
    return {k: sum(s[k].float() * (n / tot) for s, n in zip(states, sizes)) for k in states[0]}

def experiment_setup(method):
    tr = splits[splits.split == 'train']
    if method == 'centralized':
        clients = {'pooled': tr}; val_h = HOSP
    elif method.startswith('local_'):
        h = method[len('local_'):]; clients = {h: tr[tr.hospital == h]}; val_h = [h]
    elif method in ('fedavg', 'fedprox'):
        clients = {h: tr[tr.hospital == h] for h in HOSP}; val_h = HOSP
    else:
        raise ValueError(method)
    return clients, val_h

def run_experiment(method, seed):
    exp = f'{method}__s{seed}'
    res_p, ck_p = OUT / f'res__{exp}.json', OUT / f'ckpt__{exp}.pt'
    if res_p.exists() or backup.pull(res_p.name, res_p):
        log(f'⏭️  {exp} already finished – skipped'); return read_json(res_p)
    clients, val_h = experiment_setup(method)
    mu = CFG['fedprox_mu'] if method == 'fedprox' else 0.0
    seed_everything(seed); model = new_model()
    st = None
    if ck_p.exists() or backup.pull(ck_p.name, ck_p):
        st = torch.load(ck_p, map_location='cpu', weights_only=False)
        model.load_state_dict(st['global'])
        log(f'♻️  {exp}: resuming after round {st["round"]}')
    start = st['round'] + 1 if st else 1
    hist = st['hist'] if st else []
    best = st['best'] if st else {'score': -1e9, 'round': 0, 'state': copy.deepcopy(model.state_dict())}
    last_push = time.time()
    log(f'▶️  {exp}: clients={ {k: len(v) for k, v in clients.items()} } | val on {val_h} | rounds {start}..{CFG["rounds"]}')
    for r in range(start, CFG['rounds'] + 1):
        t0 = time.time(); lr = lr_at(r)
        gstate = copy.deepcopy(model.state_dict()); gparams = [p.detach().clone() for p in model.parameters()]
        states, sizes, losses = [], [], {}
        for ci, (cname, cdf) in enumerate(clients.items()):
            seed_everything(seed * 1_000_003 + r * 101 + ci)        # deterministic → exact resume
            local = new_model(); local.load_state_dict(gstate)
            losses[cname] = local_train(local, cdf, lr, mu, gparams)
            states.append({k: v.detach().cpu() for k, v in local.state_dict().items()}); sizes.append(len(cdf))
        model.load_state_dict(fedavg(states, sizes))
        vm = {h: compute_metrics(splits[(splits.split == 'val') & (splits.hospital == h)].isup_grade.values,
                                 predict(model, splits[(splits.split == 'val') & (splits.hospital == h)])) for h in val_h}
        w = np.array([vm[h]['n'] for h in val_h], float)
        score = float(np.average([selection_score(vm[h]) for h in val_h], weights=w))
        hist.append({'round': r, 'lr': lr, 'train_loss': losses, 'val': vm, 'val_score': score, 'sec': time.time() - t0})
        if score > best['score']:
            best = {'score': score, 'round': r, 'state': copy.deepcopy(model.state_dict())}
        atomic_torch_save({'global': model.state_dict(), 'round': r, 'hist': hist, 'best': best,
                           'cfg': CFG, 'method': method, 'seed': seed}, ck_p)
        if r % 5 == 0 or r == 1:
            log(f'   {exp} r{r:02d} | loss {np.mean(list(losses.values())):.4f} | val score {score:.4f} '
                f'(best {best["score"]:.4f} @r{best["round"]}) | {time.time()-t0:.1f}s')
        if time.time() - last_push > CFG['ckpt_push_every_min'] * 60:
            backup.push(ck_p); last_push = time.time()
        if time_left_h(CFG['max_session_h']) < 0:
            backup.push(ck_p); raise SessionTimeout(exp)
    # ── finalise with the validation-selected model ──────────────────────────
    model.load_state_dict(best['state'])
    val_df = splits[(splits.split == 'val') & (splits.hospital.isin(val_h))]
    val_logits = predict(model, val_df)
    thr, val_q = tune_thresholds(val_df.isup_grade.values, val_logits)
    te = splits[splits.split == 'test']
    te_logits = predict(model, te)
    rows = []
    for part, df_, lg in [('val', val_df, val_logits), ('test', te, te_logits)]:
        p = df_[['image_id', 'hospital', 'isup_grade']].copy(); p['split'] = part
        for k in range(K - 1):
            p[f'logit{k}'] = lg[:, k]
        rows.append(p)
    preds = pd.concat(rows, ignore_index=True); preds['method'] = method; preds['seed'] = seed
    preds_p = OUT / f'preds__{exp}.csv'; preds.to_csv(preds_p, index=False)
    test_m = {}
    for scope in ['pooled'] + HOSP:
        sel = np.ones(len(te), bool) if scope == 'pooled' else (te.hospital == scope).values
        test_m[scope] = {'thr05': compute_metrics(te.isup_grade.values[sel], te_logits[sel]),
                         'thr_tuned': compute_metrics(te.isup_grade.values[sel], te_logits[sel], thr)}
    res = {'exp': exp, 'method': method, 'seed': seed, 'best_round': best['round'], 'best_val_score': best['score'],
           'thresholds': thr, 'val_qwk_tuned': val_q, 'test': test_m, 'n_train': {k: len(v) for k, v in clients.items()},
           'history': [{k: v for k, v in h.items()} for h in hist], 'cfg': CFG, 'finished': str(datetime.now())}
    model_p = OUT / f'model__{exp}.pt'
    atomic_torch_save({'state': best['state'], 'd_in': D_IN, 'cfg': CFG, 'thresholds': thr}, model_p)
    atomic_json(res, res_p)
    backup.push_many([model_p, preds_p, ck_p, res_p], msg=f'finished {exp}')   # res last in list; one commit
    backup.kaggle_push_async(f'finished {exp}')                                   # new private Kaggle version
    t = test_m['pooled']['thr_tuned']
    log(f'✅ {exp} | best r{best["round"]} | TEST pooled: AUC cancer {t["auc_cancer"]:.4f} · '
        f'AUC csPCa {t["auc_cspca"]:.4f} · macro-AUC {t["auc_macro_ovr"]:.4f} · QWK {t["qwk"]:.4f}')
    return res

# ---
# ── Cell 7: Run all experiments (resumable) ──────────────────────────────────
results = []
try:
    for seed in CFG['seeds']:
        for method in CFG['methods']:
            results.append(run_experiment(method, seed))
except SessionTimeout as e:
    log(f'⏰ Time budget reached during {e}. Everything is checkpointed – re-run this notebook to continue.')
except BaseException as e:
    log(f'❌ Interrupted: {type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}')
    raise
finally:
    unfinished = [p for p in OUT.glob('ckpt__*.pt')
                  if not (OUT / p.name.replace('ckpt__', 'res__').replace('.pt', '.json')).exists()]
    if unfinished:
        backup.push_many(unfinished, msg='checkpoints of unfinished experiments')
    backup.push(WORK / 'run_log.txt', 'run_log_nb02.txt') if (WORK / 'run_log.txt').exists() else None
    backup.kaggle_final_sync('training progress')   # blocking: latest version holds every file

# ---
# ── Cell 8: Summary table (mean ± std over seeds, TEST, tuned thresholds) ────
all_res = [read_json(p) for p in sorted(OUT.glob('res__*.json'))]
rows = []
for r in all_res:
    for scope, mm in r['test'].items():
        rows.append({'method': r['method'], 'seed': r['seed'], 'scope': scope, **mm['thr_tuned']})
tab = pd.DataFrame(rows)
if len(tab):
    cols = ['auc_cancer', 'auc_cspca', 'auc_macro_ovr', 'qwk', 'acc', 'bal_acc']
    summ = tab.groupby(['scope', 'method'])[cols].agg(['mean', 'std']).round(4)
    summ.to_csv(OUT / 'summary_mean_std.csv'); backup.push(OUT / 'summary_mean_std.csv')
    pd.set_option('display.width', 250)
    print(summ)
    n_exp = len(CFG['methods']) * len(CFG['seeds'])
    log(f'Finished experiments: {len(all_res)}/{n_exp}' + (' — ALL DONE, proceed to NB03.' if len(all_res) == n_exp else ''))

# ---
