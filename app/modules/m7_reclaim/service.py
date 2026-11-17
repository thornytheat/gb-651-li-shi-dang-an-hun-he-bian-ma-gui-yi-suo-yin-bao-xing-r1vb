"""M7 回收服务层：文本解码与作业查询。"""
from __future__ import annotations
from . import core


def submit(store, raw: bytes | str) -> dict:
    if isinstance(raw, bytes):
        # 表格软件导出的 CSV 常见为 UTF-8（带/不带 BOM）；本项目证据表统一 UTF-8
        text = raw.decode("utf-8")
    else:
        text = raw
    return core.submit_reclaim(store, text)


def resume(store, job_id: str) -> dict:
    return core.resume_job(store, job_id)


def jobs(store) -> list[dict]:
    return store.list_jobs()


def job_detail(store, job_id: str) -> dict:
    job = store.get_job(job_id)
    if job is None:
        from app.errors import DomainError
        raise DomainError("INVALID_VALUE", f"作业 {job_id} 不存在")
    job["rows"] = store.get_reclaim_rows(job_id) if job["status"] == "ACCEPTED" else []
    return job
