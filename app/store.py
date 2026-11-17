from __future__ import annotations
import json
import threading
from pathlib import Path
import sqlite3

class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS runs (digest TEXT PRIMARY KEY, run_id TEXT NOT NULL, result TEXT NOT NULL)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS evidence "
                "(run_id TEXT PRIMARY KEY, header TEXT NOT NULL, rows_json TEXT NOT NULL, "
                "original_digest TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS reclaim_jobs "
                "(job_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, file_digest TEXT NOT NULL UNIQUE, "
                "status TEXT NOT NULL, reason_code TEXT, reason_message TEXT, "
                "row_count INTEGER NOT NULL, accepted_rows INTEGER NOT NULL DEFAULT 0, "
                "guarded_payload TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')), "
                "updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS reclaim_rows "
                "(job_id TEXT NOT NULL, row_index INTEGER NOT NULL, row_json TEXT NOT NULL, "
                "PRIMARY KEY (job_id, row_index))"
            )

    def connect(self):
        # check_same_thread=False：FastAPI 同步端点在线程池中运行，配合短连接与行级锁使用
        db = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def job_lock(self, file_digest: str) -> threading.Lock:
        """同一文件摘要串行化，保证并发回收同一份表只接纳一次。"""
        with self._locks_guard:
            lock = self._locks.get(file_digest)
            if lock is None:
                lock = threading.Lock()
                self._locks[file_digest] = lock
            return lock

    def record(self, digest: str, run_id: str, result: dict) -> dict:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO runs VALUES (?, ?, ?)",
                       (digest, run_id, json.dumps(result, ensure_ascii=False, sort_keys=True)))
            row = db.execute("SELECT run_id, result FROM runs WHERE digest=?", (digest,)).fetchone()
        return {"run_id": row[0], "result": json.loads(row[1])}

    def count(self) -> int:
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    def all(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT run_id, digest, result FROM runs ORDER BY run_id").fetchall()
        return [{"run_id": r[0], "digest": r[1], "result": json.loads(r[2])} for r in rows]

    # ---- M7 证据存证 ----
    def save_evidence(self, run_id: str, header: list[str], rows: list[dict], original_digest: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO evidence (run_id, header, rows_json, original_digest) VALUES (?, ?, ?, ?)",
                (run_id, json.dumps(header, ensure_ascii=False),
                 json.dumps(rows, ensure_ascii=False, sort_keys=True), original_digest),
            )

    def get_evidence(self, run_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT run_id, header, rows_json, original_digest FROM evidence WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row[0],
            "header": json.loads(row[1]),
            "rows": json.loads(row[2]),
            "original_digest": row[3],
        }

    def latest_evidence(self) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT run_id, header, rows_json, original_digest FROM evidence "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row[0],
            "header": json.loads(row[1]),
            "rows": json.loads(row[2]),
            "original_digest": row[3],
        }

    # ---- M7 回收作业 ----
    def get_job_by_file(self, file_digest: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT job_id, run_id, file_digest, status, reason_code, reason_message, "
                "row_count, accepted_rows FROM reclaim_jobs WHERE file_digest=?",
                (file_digest,),
            ).fetchone()
        if row is None:
            return None
        return {
            "job_id": row[0], "run_id": row[1], "file_digest": row[2], "status": row[3],
            "reason_code": row[4], "reason_message": row[5],
            "row_count": row[6], "accepted_rows": row[7],
        }

    def get_job(self, job_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT job_id, run_id, file_digest, status, reason_code, reason_message, "
                "row_count, accepted_rows, guarded_payload FROM reclaim_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "job_id": row[0], "run_id": row[1], "file_digest": row[2], "status": row[3],
            "reason_code": row[4], "reason_message": row[5],
            "row_count": row[6], "accepted_rows": row[7],
            "guarded_payload": json.loads(row[8]) if row[8] else None,
        }

    def create_job(self, job: dict) -> bool:
        """返回 False 表示同一文件摘要的作业已存在（并发下只接纳一次）。"""
        with self.connect() as db:
            try:
                db.execute(
                    "INSERT INTO reclaim_jobs "
                    "(job_id, run_id, file_digest, status, reason_code, reason_message, "
                    "row_count, accepted_rows, guarded_payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (job["job_id"], job["run_id"], job["file_digest"], job["status"],
                     job.get("reason_code"), job.get("reason_message"),
                     job["row_count"], job.get("accepted_rows", 0),
                     json.dumps(job["guarded_payload"], ensure_ascii=False)
                     if job.get("guarded_payload") is not None else None),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def claim_pending_job(self, job_id: str) -> bool:
        """原子领取未完成作业；已被并发执行者领取或已结束时返回 False。

        PENDING 为正常待处理；PROCESSING 仅可能来自上次进程崩溃（同文件摘要有
        进程内锁串行化，存活进程不会并发处理同一作业），续跑时安全接管。
        """
        with self.connect() as db:
            cur = db.execute(
                "UPDATE reclaim_jobs SET status='PROCESSING', updated_at=datetime('now') "
                "WHERE job_id=? AND status IN ('PENDING','PROCESSING')",
                (job_id,),
            )
            return cur.rowcount == 1

    def insert_reclaim_rows(self, job_id: str, rows: list[dict]) -> int:
        """幂等批量入库：主键冲突忽略，重试不重复。"""
        if not rows:
            return 0
        payload = [
            (job_id, i, json.dumps(row, ensure_ascii=False, sort_keys=True))
            for i, row in enumerate(rows)
        ]
        with self.connect() as db:
            before = db.execute(
                "SELECT COUNT(*) FROM reclaim_rows WHERE job_id=?", (job_id,)
            ).fetchone()[0]
            db.executemany(
                "INSERT OR IGNORE INTO reclaim_rows (job_id, row_index, row_json) VALUES (?, ?, ?)",
                payload,
            )
            after = db.execute(
                "SELECT COUNT(*) FROM reclaim_rows WHERE job_id=?", (job_id,)
            ).fetchone()[0]
        return after - before

    def count_reclaim_rows(self, job_id: str) -> int:
        with self.connect() as db:
            return db.execute(
                "SELECT COUNT(*) FROM reclaim_rows WHERE job_id=?", (job_id,)
            ).fetchone()[0]

    def finish_job(self, job_id: str, status: str, reason_code: str | None,
                   reason_message: str | None, accepted_rows: int) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE reclaim_jobs SET status=?, reason_code=?, reason_message=?, "
                "accepted_rows=?, updated_at=datetime('now') WHERE job_id=?",
                (status, reason_code, reason_message, accepted_rows, job_id),
            )

    def list_jobs(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT job_id, run_id, file_digest, status, reason_code, reason_message, "
                "row_count, accepted_rows, created_at, updated_at "
                "FROM reclaim_jobs ORDER BY rowid DESC"
            ).fetchall()
        return [
            {"job_id": r[0], "run_id": r[1], "file_digest": r[2], "status": r[3],
             "reason_code": r[4], "reason_message": r[5], "row_count": r[6],
             "accepted_rows": r[7], "created_at": r[8], "updated_at": r[9]}
            for r in rows
        ]

    def get_reclaim_rows(self, job_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT row_json FROM reclaim_rows WHERE job_id=? ORDER BY row_index",
                (job_id,),
            ).fetchall()
        return [json.loads(r[0]) for r in rows]
