"""M7 回收作业编排：核验、拒收、续跑与并发只接纳一次。"""
from __future__ import annotations
from hashlib import sha256

from app.errors import DomainError
from app.manifest import run_id as make_run_id
from app.reasons import reason
from .guard import parse_evidence_csv, restore_from_payload, table_digest


def file_digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _job_id(file_digest_value: str) -> str:
    return "job-" + make_run_id(["reclaim", file_digest_value])[4:]


def _verify_against_evidence(store, manifest: dict, restored_rows: list[dict]) -> None:
    """核验回收表与服务端证据存证是否配套；不配套整批拒收。"""
    evidence = store.get_evidence(manifest["run_id"])
    if evidence is None:
        raise DomainError("RUN_UNKNOWN",
                          f"运行编号 {manifest['run_id']} 无证据存证，批次错配")
    if evidence["original_digest"] != manifest["original_digest"]:
        raise DomainError("RUN_MISMATCH", "清单原文摘要与服务端存证不匹配，批次错配")
    actual = table_digest(manifest["columns"], restored_rows)
    if actual != evidence["original_digest"]:
        raise DomainError("DIGEST_MISMATCH", "还原结果与证据存证摘要不符，整批拒收")
    if manifest["columns"] != evidence["header"]:
        raise DomainError("RUN_MISMATCH", "列结构与证据存证不一致，批次错配")


def _process(store, job_id: str, restored_rows: list[dict]) -> dict:
    """实际入库处理。行级幂等，中断后重入只补未完成的行。"""
    if not store.claim_pending_job(job_id):
        # 已被并发执行者领取或已结束：直接返回当前状态，绝不二次接纳
        current = store.get_job(job_id)
        if current is not None:
            return current
        raise DomainError("INVALID_VALUE", f"作业 {job_id} 已被其他执行者接管")
    try:
        store.insert_reclaim_rows(job_id, restored_rows)
        accepted = store.count_reclaim_rows(job_id)
        if accepted != len(restored_rows):
            # 存证行数必须与回收行数守恒，否则判定为不完整批次
            raise DomainError("ROW_COUNT_MISMATCH",
                              f"入库行数 {accepted} 与回收行数 {len(restored_rows)} 不一致")
        store.finish_job(job_id, "ACCEPTED", None, None, accepted)
    except Exception:
        store.finish_job(job_id, "FAILED", "PROCESSING_ERROR", "处理中断，请重试续跑",
                         store.count_reclaim_rows(job_id))
        raise
    return store.get_job(job_id)


def _record_rejection(store, digest: str, job_id: str, rejected: DomainError,
                      run_id_value: str, row_count: int) -> None:
    """登记拒收作业；并发下若同文件作业已存在则以已存在者为准。"""
    created = store.create_job({
        "job_id": job_id, "run_id": run_id_value, "file_digest": digest,
        "status": "REJECTED", "reason_code": rejected.code,
        "reason_message": rejected.message, "row_count": row_count,
        "accepted_rows": 0, "guarded_payload": None,
    })
    if not created:
        # 另一进程/线程已登记同文件结果，拒收留痕让位给既有裁决
        return


def submit_reclaim(store, text: str) -> dict:
    """提交一份回收表。返回作业状态；任何核验失败整批拒收、零行入库。"""
    digest = file_digest(text)
    job_id = _job_id(digest)
    lock = store.job_lock(digest)
    with lock:
        existing = store.get_job_by_file(digest)
        if existing is not None:
            # 同一份表（同文件摘要）重复/并发提交：只接纳一次，重试不重复入库
            return existing | {"dedup": True}

        # 阶段一：只读核验，任何失败整批拒收且不写入业务行
        manifest: dict | None = None
        restored_rows: list[dict] = []
        try:
            parsed = parse_evidence_csv(text)
            manifest, restored_rows = parsed["manifest"], parsed["restored_rows"]
            _verify_against_evidence(store, manifest, restored_rows)
        except DomainError as rejected:
            run_id_value = manifest["run_id"] if manifest else "UNKNOWN"
            _record_rejection(store, digest, job_id, rejected,
                              run_id_value, len(restored_rows))
            raise

        # 阶段二：数据库唯一约束做最终裁决（覆盖多 worker 跨进程并发）
        created = store.create_job({
            "job_id": job_id, "run_id": manifest["run_id"], "file_digest": digest,
            "status": "PENDING", "reason_code": None, "reason_message": None,
            "row_count": len(restored_rows), "accepted_rows": 0,
            "guarded_payload": parsed["guarded_payload"],
        })
        if not created:
            winner = store.get_job_by_file(digest)
            return winner | {"dedup": True}
        return _process(store, job_id, restored_rows) | {"dedup": False}


def resume_job(store, job_id: str) -> dict:
    """中断重启后续跑：从存证载荷重建原文，补齐未入库的行。"""
    job = store.get_job(job_id)
    if job is None:
        raise DomainError("INVALID_VALUE", f"作业 {job_id} 不存在")
    if job["status"] in ("ACCEPTED", "REJECTED"):
        return job | {"dedup": True}
    digest = job["file_digest"]
    with store.job_lock(digest):
        job = store.get_job(job_id)
        if job is None:
            raise DomainError("INVALID_VALUE", f"作业 {job_id} 不存在")
        if job["status"] in ("ACCEPTED", "REJECTED"):
            return job | {"dedup": True}
        if job["guarded_payload"] is None:
            raise DomainError("INVALID_VALUE", "作业缺少可续跑的存证载荷")
        # 从持久化的防护表载荷重新核验并还原，避免信任内存状态
        evidence = store.get_evidence(job["run_id"])
        if evidence is None:
            raise DomainError("RUN_UNKNOWN", "续跑所需证据存证缺失")
        parsed = restore_from_payload(
            job["run_id"], job["guarded_payload"]["header"],
            job["guarded_payload"]["table"], evidence["original_digest"],
        )
        _verify_against_evidence(store, parsed["manifest"], parsed["restored_rows"])
        return _process(store, job_id, parsed["restored_rows"]) | {"dedup": False}


def describe(code: str | None) -> str:
    return reason(code) if code else "OK"
