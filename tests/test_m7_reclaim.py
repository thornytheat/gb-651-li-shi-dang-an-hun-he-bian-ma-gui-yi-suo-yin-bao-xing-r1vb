"""M7 回收作业：配套核验、整批拒收、续跑、幂等与并发只接纳一次。"""
import threading

import pytest

from app.errors import DomainError
from app.modules.m6_runs.core import run as m6_run, safe_export
from app.modules.m7_reclaim import core as m7
from app.modules.m7_reclaim.guard import parse_evidence_csv


@pytest.fixture
def evidence(client):
    store = client.app.state.store
    m6_run({"store": store, "rows": [
        {"source_line": 1, "archive_id": "000123", "reason": "OK"},
        {"source_line": 2, "archive_id": "9007199254740993", "reason": "OK"},
        {"source_line": 3, "archive_id": "=1+1", "reason": "BAD_COLUMNS"},
    ]})
    run_id, text = safe_export(store)
    return store, run_id, text


# ---------- API 层 ----------
def test_export_is_safe_csv_with_manifest(client, evidence):
    _, run_id, _ = evidence
    res = client.get(f"/api/export/evidence.csv?run_id={run_id}")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert res.headers["x-evidence-run-id"] == run_id
    assert res.content.startswith("﻿#!ARCHIVECODEC-EVIDENCE ".encode())
    # 超大档号被防护，表格软件不会数值化
    assert "'9007199254740993".encode() in res.content
    assert "'=1+1".encode() in res.content


def test_export_unknown_run_rejected(client, evidence):
    res = client.get("/api/export/evidence.csv?run_id=run-000000000000")
    assert res.status_code == 422
    assert res.json()["error"] == "RUN_UNKNOWN"


def test_reclaim_api_accepts_and_restores_verbatim(client, evidence):
    _, run_id, text = evidence
    res = client.post("/api/reclaim", content=text.encode("utf-8"),
                      headers={"content-type": "text/csv"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ACCEPTED" and body["run_id"] == run_id
    assert body["row_count"] == body["accepted_rows"] == 3
    detail = client.get(f"/api/reclaim/jobs/{body['job_id']}").json()
    assert detail["rows"][0]["archive_id"] == "000123"
    assert detail["rows"][1]["archive_id"] == "9007199254740993"
    assert detail["rows"][2]["archive_id"] == "=1+1"  # 防护字符已剥离，逐字还原


def test_reclaim_api_tamper_rejected_entire_batch(client, evidence):
    store, _, text = evidence
    tampered = text.replace("'=1+1", "'=1+2", 1)
    res = client.post("/api/reclaim", content=tampered.encode("utf-8"))
    assert res.status_code == 422
    assert res.json()["error"] == "DIGEST_MISMATCH"
    # 整批拒收：零行入库
    jobs = store.list_jobs()
    rejected = [j for j in jobs if j["status"] == "REJECTED"]
    assert len(rejected) == 1
    assert store.count_reclaim_rows(rejected[0]["job_id"]) == 0


def test_reclaim_api_wrong_run_rejected(client, evidence):
    _, run_id, text = evidence
    wrong = text.replace(run_id, "run-000000000000", 1)
    res = client.post("/api/reclaim", content=wrong.encode("utf-8"))
    assert res.status_code == 422
    assert res.json()["error"] == "RUN_UNKNOWN"


def test_reclaim_api_missing_manifest_rejected(client, evidence):
    res = client.post("/api/reclaim", content="a,b,c\n1,2,3\n".encode())
    assert res.status_code == 422
    assert res.json()["error"] == "MANIFEST_MISSING"


def test_reclaim_api_dedup_same_file(client, evidence):
    _, _, text = evidence
    first = client.post("/api/reclaim", content=text.encode("utf-8")).json()
    second = client.post("/api/reclaim", content=text.encode("utf-8")).json()
    assert first["job_id"] == second["job_id"]
    assert second["dedup"] is True
    jobs = client.get("/api/reclaim/jobs").json()["jobs"]
    accepted = [j for j in jobs if j["status"] == "ACCEPTED"]
    assert len(accepted) == 1  # 同一份表只接纳一次


# ---------- 核心层：并发、续跑、幂等 ----------
def test_concurrent_reclaim_accepted_once(evidence):
    store, _, text = evidence
    outcomes = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        outcomes.append(m7.submit_reclaim(store, text))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    firsts = [o for o in outcomes if not o.get("dedup")]
    assert len(firsts) == 1
    job_id = firsts[0]["job_id"]
    assert store.count_reclaim_rows(job_id) == 3  # 无重复入库


def test_resume_after_interruption(evidence):
    store, run_id, text = evidence
    parsed = parse_evidence_csv(text)
    digest = m7.file_digest(text)
    job_id = "job-interrupted"
    store.create_job({
        "job_id": job_id, "run_id": run_id, "file_digest": digest + "-x",
        "status": "PENDING", "row_count": 3,
        "guarded_payload": parsed["guarded_payload"],
    })
    # 仅第一行落库后中断
    store.insert_reclaim_rows(job_id, parsed["restored_rows"][:1])
    assert store.count_reclaim_rows(job_id) == 1

    result = m7.resume_job(store, job_id)
    assert result["status"] == "ACCEPTED"
    assert store.count_reclaim_rows(job_id) == 3
    # 再续跑：幂等，不重复
    again = m7.resume_job(store, job_id)
    assert again["dedup"] is True
    assert store.count_reclaim_rows(job_id) == 3
    assert store.get_reclaim_rows(job_id) == parsed["restored_rows"]


def test_resume_takes_over_stale_processing(evidence):
    store, run_id, text = evidence
    parsed = parse_evidence_csv(text)
    job_id = "job-crashed"
    store.create_job({
        "job_id": job_id, "run_id": run_id,
        "file_digest": m7.file_digest(text) + "-y",
        "status": "PROCESSING", "row_count": 3,
        "guarded_payload": parsed["guarded_payload"],
    })
    result = m7.resume_job(store, job_id)
    assert result["status"] == "ACCEPTED"
    assert result["accepted_rows"] == 3


def test_resume_rejects_tampered_payload(evidence):
    store, run_id, text = evidence
    parsed = parse_evidence_csv(text)
    payload = parsed["guarded_payload"]
    payload["table"][0][1] = "'999999"  # 直接改库里的存证载荷
    job_id = "job-tampered-payload"
    store.create_job({
        "job_id": job_id, "run_id": run_id,
        "file_digest": m7.file_digest(text) + "-z",
        "status": "PENDING", "row_count": 3, "guarded_payload": payload,
    })
    with pytest.raises(DomainError) as exc:
        m7.resume_job(store, job_id)
    assert exc.value.code == "DIGEST_MISMATCH"
    assert store.count_reclaim_rows(job_id) == 0


def test_rejected_batch_leaves_no_rows_and_is_retryable(evidence):
    store, _, text = evidence
    tampered = text.replace("'=1+1", "'=9+9", 1)
    with pytest.raises(DomainError):
        m7.submit_reclaim(store, tampered)
    rejected_jobs = [j for j in store.list_jobs() if j["status"] == "REJECTED"]
    assert len(rejected_jobs) == 1
    assert store.count_reclaim_rows(rejected_jobs[0]["job_id"]) == 0
    # 提交未篡改的原表仍可正常接纳（拒收不阻塞后续合法回收）
    ok = m7.submit_reclaim(store, text)
    assert ok["status"] == "ACCEPTED"


def test_evidence_run_mismatch_rejected(evidence):
    store, run_id, text = evidence
    # 登记第二份证据，用其 run_id 替换第一份表的清单
    m6_run({"store": store, "rows": [
        {"source_line": 9, "archive_id": "OTHER", "reason": "OK"},
    ]})
    other = store.latest_evidence()
    wrong = text.replace(run_id, other["run_id"], 1)
    with pytest.raises(DomainError) as exc:
        m7.submit_reclaim(store, wrong)
    assert exc.value.code == "RUN_MISMATCH"


def test_jobs_listing_and_detail(client, evidence):
    _, _, text = evidence
    client.post("/api/reclaim", content=text.encode("utf-8"))
    jobs = client.get("/api/reclaim/jobs").json()["jobs"]
    assert jobs and jobs[0]["status"] == "ACCEPTED"
    detail = client.get(f"/api/reclaim/jobs/{jobs[0]['job_id']}")
    assert detail.status_code == 200
    assert len(detail.json()["rows"]) == 3
    assert client.get("/api/reclaim/jobs/job-missing").status_code == 422


def test_db_unique_constraint_settles_cross_worker_race(evidence):
    """模拟两个 worker 同时通过核验后写库：唯一约束保证只接纳一次。"""
    store, run_id, text = evidence
    parsed = parse_evidence_csv(text)
    digest = m7.file_digest(text)
    job_id = m7._job_id(digest)
    payload = {
        "job_id": job_id, "run_id": run_id, "file_digest": digest,
        "status": "PENDING", "row_count": len(parsed["restored_rows"]),
        "guarded_payload": parsed["guarded_payload"],
    }
    assert store.create_job(payload) is True
    assert store.create_job(payload) is False
    # 先由一个 worker 完成处理
    m7._process(store, job_id, parsed["restored_rows"])
    # 另一 worker 的 submit 走既有裁决，不重复入库
    result = m7.submit_reclaim(store, text)
    assert result["dedup"] is True and result["job_id"] == job_id
    assert store.count_reclaim_rows(job_id) == 3
