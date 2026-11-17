import csv, io
from app.manifest import canonical_digest, run_id
from app.modules.m7_reclaim.guard import build_evidence_csv, table_digest

SAMPLE = {"store": None, "rows": [
    {"source_line": 2, "archive_id": "000123", "reason": "OK"},
    {"source_line": 5, "archive_id": "BAD", "reason": "BAD_COLUMNS"},
]}
HEADER = ["source_line", "archive_id", "reason"]
_BOM = "﻿"


def csv_text(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=HEADER, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def _normalize(rows):
    """统一字符串化，保证存证原文与导出/回收逐字一致（行号、前导零不被数值化）。"""
    return [{h: ("" if row.get(h) is None else str(row.get(h))) for h in HEADER}
            for row in rows]


def run(payload):
    rows = payload.get("rows", SAMPLE["rows"])
    rows = _normalize(rows)
    digest = canonical_digest(rows)
    rid = run_id(rows)
    store = payload.get("store")
    result = {
        "input_count": len(rows),
        "valid_count": sum(r["reason"] == "OK" for r in rows),
        "quarantine_count": sum(r["reason"] != "OK" for r in rows),
    }
    if store:
        rid = store.record(digest, rid, result)["run_id"]
        # 原文权威存证：回收配套核验与逐字还原的唯一依据
        store.save_evidence(rid, HEADER, rows, table_digest(HEADER, rows))
    return {"status": "READY", "run_id": rid, "digest": digest,
            "csv": csv_text(rows), **result, "reason": "OK"}


def safe_export(store, run_id_value: str | None = None) -> tuple[str, str]:
    """返回 (run_id, 带清单行与 BOM 的安全 CSV 文本)。"""
    if run_id_value:
        evidence = store.get_evidence(run_id_value)
        if evidence is None:
            from app.errors import DomainError
            raise DomainError("RUN_UNKNOWN",
                              f"运行编号 {run_id_value} 无证据存证，无法导出")
    else:
        evidence = store.latest_evidence()
        if evidence is None:
            # 无显式存证时维持旧行为：登记样例证据后导出
            data = run({"store": store})
            evidence = store.get_evidence(data["run_id"])
    text = build_evidence_csv(
        evidence["run_id"], evidence["header"], evidence["rows"],
        evidence["original_digest"],
    )
    return evidence["run_id"], _BOM + text
