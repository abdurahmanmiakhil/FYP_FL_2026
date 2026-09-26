"""Upload validation, full job flow (real worker task + fake models), outputs, reviews, reports."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import tifffile
from httpx import AsyncClient

from backend.db.models import Role

from .conftest import login, make_user, upload

API = "/api/v1"


async def test_upload_rejects_wrong_type(pathologist: AsyncClient, tmp_path: Path) -> None:
    p = tmp_path / "photo.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    r = await upload(pathologist, p)
    assert r.status_code == 415


async def test_upload_rejects_fake_tiff(pathologist: AsyncClient, tmp_path: Path) -> None:
    p = tmp_path / "fake.tiff"
    p.write_bytes(b"%PDF-1.4 not a tiff" * 20)
    r = await upload(pathologist, p)
    assert r.status_code == 422 and "not a TIFF" in r.json()["detail"]


async def test_upload_rejects_non_pyramidal(pathologist: AsyncClient, tmp_path: Path) -> None:
    p = tmp_path / "flat.tif"
    tifffile.imwrite(p, np.full((512, 512, 3), 200, np.uint8), tile=(256, 256), photometric="rgb")
    r = await upload(pathologist, p)
    assert r.status_code == 422 and "pyramidal" in r.json()["detail"]


async def test_upload_rejects_names_and_national_ids(pathologist: AsyncClient, slide: Path) -> None:
    for code in ("John Smith", "3520212345671", "35202-1234567-1", "x"):
        r = await upload(pathologist, slide, code=code)
        assert r.status_code == 422, code


async def test_full_flow_upload_predict_view_review_report(pathologist: AsyncClient, slide: Path) -> None:
    r = await upload(pathologist, slide, code="PT-FLOW-1")
    assert r.status_code == 201, r.text
    case_id = r.json()["case_id"]

    case = (await pathologist.get(f"{API}/cases/{case_id}")).json()
    assert case["status"] == "done", case
    pred = case["prediction"]
    assert pred["status"] == "provisional" and "pathologist" in pred["disclaimer"]
    assert len(pred["p_isup"]) == 6 and abs(sum(pred["p_isup"]) - 1) < 1e-6
    assert len(pred["model_version"]) == 64 and pred["preprocessing_version"].startswith("nb01")
    assert set(pred["operating_point_flags"]) == {"cspca_youden", "cspca_sens90", "cspca_sens95", "cancer_youden"}
    assert pred["low_confidence_reasons"]  # few tiles on the small synthetic slide
    assert len(pred["top_tiles"]) == 8

    heat = await pathologist.get(pred["heatmap_url"])
    assert heat.status_code == 200 and heat.content[:8] == b"\x89PNG\r\n\x1a\n"
    tile = await pathologist.get(pred["top_tiles"][0]["url"])
    assert tile.status_code == 200 and tile.content[:2] == b"\xff\xd8"
    full = (await pathologist.get(f"{API}/cases/{case_id}/prediction/result.json")).json()
    assert len(full["attention"]) == full["n_tiles"] and set(full["per_seed"]) == {f"s{k}" for k in range(5)}

    dzi = await pathologist.get(case["dzi_url"])
    assert dzi.status_code == 200 and b"<Image" in dzi.content and b'TileSize="254"' in dzi.content
    t = await pathologist.get(f"{API}/slides/{case_id}_files/8/0_0.jpeg")
    assert t.status_code == 200 and t.content[:2] == b"\xff\xd8"
    assert (await pathologist.get(f"{API}/slides/{case_id}_files/99/0_0.jpeg")).status_code == 404

    # review rules
    bad = await pathologist.post(f"{API}/cases/{case_id}/reviews", json={"decision": "amended", "comment": "x"})
    assert bad.status_code == 422
    bad = await pathologist.post(f"{API}/cases/{case_id}/reviews", json={"decision": "rejected"})
    assert bad.status_code == 422
    ok = await pathologist.post(
        f"{API}/cases/{case_id}/reviews",
        json={"decision": "amended", "final_isup": 3, "comment": "Pattern 4 predominant"},
    )
    assert ok.status_code == 201 and ok.json()["final_isup"] == 3
    conf = await pathologist.post(f"{API}/cases/{case_id}/reviews", json={"decision": "confirmed"})
    assert conf.json()["final_isup"] == pred["isup_grade"]
    history = (await pathologist.get(f"{API}/cases/{case_id}/reviews")).json()
    assert [h["decision"] for h in history] == ["confirmed", "amended"]
    after = (await pathologist.get(f"{API}/cases/{case_id}")).json()
    assert after["prediction"]["status"] == "reviewed" and after["review_decision"] == "confirmed"

    pdf = await pathologist.get(f"{API}/cases/{case_id}/report.pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-" and len(pdf.content) > 20_000

    events = await pathologist.get(f"{API}/cases/{case_id}/events")
    assert events.status_code == 200 and '"status": "done"' in events.text


async def test_duplicate_upload_is_deduplicated(pathologist: AsyncClient, slide_b: Path) -> None:
    a = await upload(pathologist, slide_b, code="PT-DUP")
    b = await upload(pathologist, slide_b, code="PT-DUP")
    assert a.status_code == b.status_code == 201
    assert b.json()["duplicate"] is True and b.json()["case_id"] == a.json()["case_id"]


async def test_no_tissue_slide_fails_with_clear_message(pathologist: AsyncClient, tmp_path: Path) -> None:
    p = tmp_path / "blank.tiff"
    blank = np.full((2048, 2048, 3), 245, np.uint8)
    with tifffile.TiffWriter(p) as tif:
        tif.write(blank, tile=(256, 256), photometric="rgb")
        tif.write(blank[::16, ::16].copy(), subfiletype=1, tile=(128, 128), photometric="rgb")
    r = await upload(pathologist, p, code="PT-BLANK")
    case = (await pathologist.get(f"{API}/cases/{r.json()['case_id']}")).json()
    assert case["status"] == "failed" and case["error"] == "No tissue found in slide"
    assert (
        await pathologist.post(f"{API}/cases/{case['id']}/reviews", json={"decision": "confirmed"})
    ).status_code == 409
    retry = await pathologist.post(f"{API}/cases/{case['id']}/retry")
    assert retry.status_code == 200


async def test_resumable_upload(pathologist: AsyncClient, tmp_path: Path) -> None:
    from prostate_infer.synthetic import write_synthetic_slide

    p = write_synthetic_slide(tmp_path / "chunked.tiff", 3584, 1792, seed=9)
    data = p.read_bytes()
    init = await pathologist.post(f"{API}/uploads", json={"filename": "chunked.tiff", "size": len(data)})
    assert init.status_code == 201
    uid, chunk = init.json()["upload_id"], 256 * 1024
    out_of_order = await pathologist.put(
        f"{API}/uploads/{uid}",
        content=data[chunk : 2 * chunk],
        headers={"Content-Range": f"bytes {chunk}-{2 * chunk - 1}/{len(data)}"},
    )
    assert out_of_order.status_code == 409
    early = await pathologist.post(f"{API}/uploads/{uid}/complete", json={"upload_id": uid, "pseudonym_code": "PT-CH"})
    assert early.status_code == 409
    for start in range(0, len(data), chunk):
        end = min(start + chunk, len(data)) - 1
        r = await pathologist.put(
            f"{API}/uploads/{uid}",
            content=data[start : end + 1],
            headers={"Content-Range": f"bytes {start}-{end}/{len(data)}"},
        )
        assert r.status_code == 200, r.text
        if start == 0:  # resume point is reported
            assert (await pathologist.get(f"{API}/uploads/{uid}")).json()["received_bytes"] == end + 1
    done = await pathologist.post(
        f"{API}/uploads/{uid}/complete", json={"upload_id": uid, "pseudonym_code": "PT-CH", "slide_id": "panda_abc123"}
    )
    assert done.status_code == 201, done.text
    case = (await pathologist.get(f"{API}/cases/{done.json()['case_id']}")).json()
    assert case["status"] == "done" and case["patient_code"] == "PT-CH"


async def test_list_filters_sort_pagination_and_csv(pathologist: AsyncClient, slide: Path, slide_b: Path) -> None:
    await upload(pathologist, slide, code="PT-LIST-A")
    await upload(pathologist, slide_b, code="PT-LIST-B")
    page = (await pathologist.get(f"{API}/cases", params={"q": "pt-list", "page_size": 1})).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    done = (await pathologist.get(f"{API}/cases", params={"q": "PT-LIST", "status": "done"})).json()
    assert done["total"] == 2
    needs = (await pathologist.get(f"{API}/cases", params={"q": "PT-LIST", "needs_review": True})).json()
    assert needs["total"] == 2
    low = (await pathologist.get(f"{API}/cases", params={"q": "PT-LIST", "low_confidence": True})).json()
    assert low["total"] == 2
    g = needs["items"][0]["isup_grade"]
    by_grade = (await pathologist.get(f"{API}/cases", params={"q": "PT-LIST", "grade": g})).json()
    assert all(i["isup_grade"] == g for i in by_grade["items"])
    for sort in ("-created_at", "isup_grade", "-p_cspca", "patient_code"):
        assert (await pathologist.get(f"{API}/cases", params={"sort": sort})).status_code == 200
    csv = await pathologist.get(f"{API}/cases/export.csv", params={"q": "PT-LIST"})
    assert csv.status_code == 200 and csv.text.count("\n") == 3 and "patient_code" in csv.text


async def test_stats(pathologist: AsyncClient, slide: Path) -> None:
    await upload(pathologist, slide, code="PT-STATS")
    s = (await pathologist.get(f"{API}/stats")).json()
    assert s["total_cases"] >= 1 and s["by_status"]["done"] >= 1
    assert sum(s["by_grade"].values()) >= 1 and s["mean_runtime_s"] is not None


async def test_urologist_can_upload_and_review(client: AsyncClient, slide: Path) -> None:
    u = make_user(Role.urologist, hospital="Uro Hospital")
    await login(client, u)
    r = await upload(client, slide, code="PT-URO")
    assert r.status_code == 201
    rv = await client.post(f"{API}/cases/{r.json()['case_id']}/reviews", json={"decision": "confirmed"})
    assert rv.status_code == 201


async def test_model_card_and_health(pathologist: AsyncClient, redis) -> None:  # type: ignore[no-untyped-def]
    card = (await pathologist.get(f"{API}/admin/model")).json()
    assert card["thesis_results"]["fedavg_ensemble"]["csPCa AUC"] == 0.9705 and card["limitations"]
    assert card["ready"] is False
    redis.hset("worker:ready", "worker-x", json.dumps({"model_version": "abc", "device": "cpu"}))
    h = await pathologist.get(f"{API}/ready")
    assert h.status_code == 200 and h.json()["models_loaded"] is True
