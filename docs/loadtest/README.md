# Load test results

`make loadtest` (Locust, `scripts/loadtest/locustfile.py`): 20 clinicians browsing slides (DeepZoom tiles in
3x2 viewports at random zoom levels) while 5 slides are uploaded in parallel, 4 minutes, against the full HTTPS
stack (Caddy -> FastAPI -> SeaweedFS) on an Apple M3 laptop, one CPU worker.

| Request | Count | Failures | Median | p95 | p99 | Max |
|---|---|---|---|---|---|---|
| Viewer tile `/slides/{id}_files/...` | 29,054 (121/s) | 0 | 8 ms | **36 ms** | 57 ms | 284 ms |
| DeepZoom descriptor `.dzi` | 4,843 | 0 | 6 ms | 34 ms | 54 ms | 200 ms |
| Case detail | 67 | 0 | 40 ms | 89 ms | 150 ms | 150 ms |
| Upload (multipart, 5.8 MB) | 5 | 0 | 360 ms | 370 ms | - | 371 ms |
| **Time to result** (upload -> AI result) | 5 | 0 | 58 s | 98 s | - | 98 s |

Targets (plan): tile p95 < 300 ms - met (36 ms). Result < 2 min per slide - met on CPU even with 5 slides
queued at once (they are processed one after another by one worker; add workers or a GPU for more throughput).

Raw data: `results_stats.csv`, `results_stats_history.csv`, interactive report `report.html`.
