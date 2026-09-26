"""Seed accounts and demo data.

    python -m backend.scripts.seed admin --email admin@hospital.org --hospital "Radboud UMC"
        (password from SEED_ADMIN_PASSWORD, or prompted; never a default)
    python -m backend.scripts.seed demo
        demo accounts (admin, 2 pathologists, 1 urologist) + 6 demo cases, queued for the worker

Demo cases use real PANDA test slides when DEMO_SLIDES_DIR holds them (ISUP 0, 2 and 5 from each
hospital, labels from the model bundle); otherwise clearly-labelled SYNTHETIC slides are generated
(not tissue - their AI results are meaningless and exist only to show the workflow).
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import secrets
import string
import sys
from pathlib import Path

from sqlalchemy import select

from ..core.config import get_settings
from ..core.security import hash_password, password_problems
from ..db.models import Case, CaseStatus, Patient, Role, User
from ..db.session import sync_session
from ..services.audit import Actions, record_sync
from ..services.jobs import enqueue_prediction
from ..services.storage import case_prefix, get_storage

HOSPITALS = {"A_Radboud": "Radboud UMC", "B_Karolinska": "Karolinska Institutet"}
DEMO_USERS = [
    ("admin@gleasonai.demo", "Demo Administrator", Role.admin, "Radboud UMC"),
    ("pathologist.radboud@gleasonai.demo", "Dr. Pathologist (Radboud)", Role.pathologist, "Radboud UMC"),
    (
        "pathologist.karolinska@gleasonai.demo",
        "Dr. Pathologist (Karolinska)",
        Role.pathologist,
        "Karolinska Institutet",
    ),
    ("urologist.radboud@gleasonai.demo", "Dr. Urologist (Radboud)", Role.urologist, "Radboud UMC"),
]


def strong_password() -> str:
    alphabet = string.ascii_letters + string.digits
    core = "".join(secrets.choice(alphabet) for _ in range(14))
    return f"{core}-{secrets.randbelow(90) + 10}!"


def upsert_user(email: str, name: str, role: Role, hospital: str, password: str) -> tuple[User, bool]:
    with sync_session() as db:
        u = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        created = u is None
        if u is None:
            u = User(email=email, full_name=name, role=role, hospital=hospital, password_hash="")
            db.add(u)
        u.password_hash, u.is_active, u.failed_logins, u.locked_until = hash_password(password), True, 0, None
        u.must_change_password = False
        db.flush()
        record_sync(
            db,
            Actions.USER_CREATE if created else Actions.PASSWORD_RESET,
            "user",
            u.id,
            details={"seed": True, "role": role.value},
        )
        db.commit()
        return u, created


def cmd_admin(a: argparse.Namespace) -> int:
    pw = os.environ.get("SEED_ADMIN_PASSWORD") or getpass.getpass("Password for the admin account: ")
    if problems := password_problems(pw, a.email):
        print("Password needs: " + "; ".join(problems), file=sys.stderr)
        return 1
    _, created = upsert_user(a.email.lower(), a.name, Role.admin, a.hospital, pw)
    print(f"admin {'created' if created else 'updated'}: {a.email}")
    return 0


def _demo_slides() -> list[tuple[Path, str, str, str | None]]:
    """(slide path, hospital, pseudonym code, PANDA image_id or None)."""
    s = get_settings()
    if s.DEMO_SLIDES_DIR and Path(s.DEMO_SLIDES_DIR).is_dir():
        import pandas as pd
        from prostate_infer.assets import get_settings as infer_settings

        bundle = Path(infer_settings().MODEL_DIR)
        preds = pd.read_csv(bundle / "calibration/preds__fedavg__s0.csv")
        test = preds[preds.split == "test"]
        chosen = []
        for hosp_key, hosp in HOSPITALS.items():
            for grade in (0, 2, 5):
                rows = test[(test.hospital == hosp_key) & (test.isup_grade == grade)].sort_values("image_id")
                for iid in rows.image_id:
                    p = Path(s.DEMO_SLIDES_DIR) / f"{iid}.tiff"
                    if p.exists():
                        chosen.append((p, hosp, f"DEMO-{hosp_key[0]}-ISUP{grade}", iid))
                        break
        if len(chosen) == 6:
            return chosen
        print(f"DEMO_SLIDES_DIR has only {len(chosen)}/6 matching PANDA test slides; using synthetic slides.")
    from prostate_infer.synthetic import write_synthetic_slide

    out = s.STORAGE_DIR / ".demo-synthetic"
    out.mkdir(parents=True, exist_ok=True)
    slides: list[tuple[Path, str, str, str | None]] = []
    for i, hosp in enumerate([*HOSPITALS.values()] * 3):
        p = write_synthetic_slide(out / f"synthetic-{i}.tiff", 8960, 4480, seed=100 + i, n_cores=2 + i % 2)
        slides.append((p, hosp, f"SYNTH-DEMO-{i + 1}", None))
    return slides


def cmd_demo(a: argparse.Namespace) -> int:
    password = os.environ.get("DEMO_PASSWORD") or strong_password()
    if problems := password_problems(password):
        print("DEMO_PASSWORD needs: " + "; ".join(problems), file=sys.stderr)
        return 1
    creds = []
    for email, name, role, hosp in DEMO_USERS:
        upsert_user(email, name, role, hosp, password)
        creds.append(f"{role.value:12s} {email:42s} ({hosp})")

    storage = get_storage()
    uploader: dict[str, str | None] = {h: None for h in HOSPITALS.values()}
    with sync_session() as db:
        for h in uploader:
            uploader[h] = db.execute(
                select(User.id).where(User.hospital == h, User.role == Role.pathologist)
            ).scalar_one_or_none()
    queued = 0
    for path, hosp, code, image_id in _demo_slides():
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        with sync_session() as db:
            if db.execute(
                select(Case.id).where(Case.hospital == hosp, Case.slide_sha256 == sha, Case.deleted_at.is_(None))
            ).first():
                continue
            patient = db.execute(
                select(Patient).where(Patient.hospital == hosp, Patient.pseudonym_code == code)
            ).scalar_one_or_none()
            if patient is None:
                patient = Patient(pseudonym_code=code, hospital=hosp)
                db.add(patient)
                db.flush()
            case = Case(
                patient_id=patient.id,
                hospital=hosp,
                slide_file="",
                slide_sha256=sha,
                slide_bytes=path.stat().st_size,
                slide_format="tiff",
                slide_seed_id=image_id or sha[:32],
                uploaded_by=uploader[hosp],
                status=CaseStatus.queued,
            )
            db.add(case)
            db.flush()
            key = f"{case_prefix(case.id)}/slide.tiff"
            tmp = path.with_suffix(".copy")
            tmp.write_bytes(path.read_bytes())
            storage.put_file(key, tmp, "image/tiff")
            case.slide_file = key
            record_sync(db, Actions.UPLOAD, "case", case.id, user_id=uploader[hosp], details={"seed": True})
            db.commit()
            case_id = case.id
        job_id = enqueue_prediction(case_id)
        with sync_session() as db:
            c = db.get(Case, case_id)
            if c is not None:
                c.job_id = job_id
                db.commit()
        queued += 1

    s = get_settings()
    cred_file = s.STORAGE_DIR / "demo-credentials.txt"
    cred_file.write_text(
        "GleasonAI demo accounts (all share one password)\n\n" + "\n".join(creds) + f"\n\npassword: {password}\n"
    )
    cred_file.chmod(0o600)
    print("Demo accounts (all share one password):")
    print("\n".join("  " + c for c in creds))
    print(f"  password: {password}")
    print(f"(also saved to {cred_file})")
    print(f"{queued} demo case(s) queued for AI analysis.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.scripts.seed")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ad = sub.add_parser("admin", help="create or reset an administrator")
    ad.add_argument("--email", required=True)
    ad.add_argument("--name", default="Administrator")
    ad.add_argument("--hospital", required=True)
    sub.add_parser("demo", help="demo accounts + 6 demo cases")
    a = ap.parse_args(argv)
    return cmd_admin(a) if a.cmd == "admin" else cmd_demo(a)


if __name__ == "__main__":
    sys.exit(main())
