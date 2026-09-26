"""Load test: 20 clinicians browsing slides (DeepZoom tiles) while 5 slides are uploaded in parallel.

    locust -f scripts/loadtest/locustfile.py --host https://localhost -u 25 -r 5 -t 3m --headless \
        --csv docs/loadtest/results --html docs/loadtest/report.html

Env: LOAD_VIEWER / LOAD_UPLOADER (emails), LOAD_PASSWORD, LOAD_SLIDES_DIR (5 unique .tiff files).
Reports: request latency per endpoint (tiles grouped) and a custom "time-to-result" per uploaded slide.
"""

from __future__ import annotations

import math
import os
import random
import re
import time
from pathlib import Path

import requests
import urllib3
from locust import HttpUser, between, events, task

urllib3.disable_warnings()
VIEWER = os.environ.get("LOAD_VIEWER", "pathologist.radboud@gleasonai.demo")
UPLOADER = os.environ.get("LOAD_UPLOADER", "pathologist.karolinska@gleasonai.demo")
PASSWORD = os.environ.get("LOAD_PASSWORD", "E2e-Demo-Password-2026!")
SLIDES = sorted(Path(os.environ.get("LOAD_SLIDES_DIR", "/tmp/gl/load")).glob("*.tif*"))
API = "/api/v1"
shared: dict[str, object] = {}


def _login(host: str, email: str) -> requests.Session:
    s = requests.Session()
    s.verify = False
    r = s.post(f"{host}{API}/auth/login", json={"email": email, "password": PASSWORD}, timeout=30)
    r.raise_for_status()
    return s


@events.test_start.add_listener
def prepare(environment, **_):  # type: ignore[no-untyped-def]
    host = environment.host
    viewer = _login(host, VIEWER)
    shared["viewer_cookies"] = viewer.cookies.get_dict()
    shared["uploader_cookies"] = _login(host, UPLOADER).cookies.get_dict()
    cases = viewer.get(f"{host}{API}/cases", params={"status": "done", "page_size": 50}, timeout=30).json()["items"]
    slides = []
    for c in cases:
        dzi = viewer.get(f"{host}{API}/slides/{c['id']}.dzi", timeout=30).text
        w, h = (int(x) for x in re.search(r'Width="(\d+)" Height="(\d+)"', dzi).groups())  # type: ignore[union-attr]
        slides.append((c["id"], w, h))
    shared["slides"] = slides
    shared["next_slide"] = 0
    print(f"load test: {len(slides)} slides to browse, {len(SLIDES)} slides to upload")


class Clinician(HttpUser):
    """Opens a case and pans/zooms around the slide like a pathologist."""

    weight = 20
    wait_time = between(0.3, 1.5)

    def on_start(self) -> None:
        self.client.verify = False
        self.client.cookies.update(shared["viewer_cookies"])  # type: ignore[arg-type]

    @task
    def browse(self) -> None:
        case_id, w, h = random.choice(shared["slides"])  # type: ignore[arg-type]
        self.client.get(f"{API}/slides/{case_id}.dzi", name="/slides/[id].dzi")
        max_level = math.ceil(math.log2(max(w, h)))
        level = random.randint(max(0, max_level - 5), max_level)
        scale = 2 ** (max_level - level)
        cols, rows = math.ceil(w / scale / 254), math.ceil(h / scale / 254)
        c0, r0 = random.randrange(cols), random.randrange(rows)
        for dc in range(3):  # a 3x2 viewport of neighbouring tiles
            for dr in range(2):
                c, r = min(cols - 1, c0 + dc), min(rows - 1, r0 + dr)
                self.client.get(f"{API}/slides/{case_id}_files/{level}/{c}_{r}.jpeg", name="/slides/[id]_files/tile")


class Uploader(HttpUser):
    """Uploads one slide, then waits for the AI result and reports the time to result."""

    fixed_count = 5
    wait_time = between(1, 2)

    def on_start(self) -> None:
        self.client.verify = False
        self.client.cookies.update(shared["uploader_cookies"])  # type: ignore[arg-type]
        i = int(shared["next_slide"])  # type: ignore[call-overload]
        shared["next_slide"] = i + 1
        self.slide = SLIDES[i % len(SLIDES)] if SLIDES else None
        self.case_id: str | None = None
        self.started = 0.0

    @task
    def work(self) -> None:
        if self.slide is None or self.case_id == "done":
            time.sleep(1)
            return
        if self.case_id is None:
            csrf = self.client.cookies.get("csrf_token", "")
            with open(self.slide, "rb") as f:
                self.started = time.monotonic()
                r = self.client.post(f"{API}/cases", headers={"X-CSRF-Token": csrf}, name="/cases [upload]",
                                     files={"file": (self.slide.name, f, "image/tiff")},
                                     data={"pseudonym_code": f"LOAD-{self.slide.stem}-{int(time.time())}"})
            self.case_id = r.json().get("case_id") if r.ok else "done"
            return
        r = self.client.get(f"{API}/cases/{self.case_id}", name="/cases/[id]")
        status = r.json().get("status") if r.ok else "failed"
        if status in ("done", "failed"):
            events.request.fire(request_type="JOB", name="time-to-result" if status == "done" else "job-failed",
                                response_time=(time.monotonic() - self.started) * 1000, response_length=0,
                                exception=None, context={})
            self.case_id = "done"
        else:
            time.sleep(3)
