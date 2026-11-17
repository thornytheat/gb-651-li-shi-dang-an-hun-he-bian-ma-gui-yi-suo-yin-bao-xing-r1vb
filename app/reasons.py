REASONS = {
    "BAD_COLUMNS": "字段数量不符", "EMPTY_VALUE": "必要值为空",
    "DUPLICATE_CONFLICT": "同一业务键取值冲突", "TRUNCATED": "尾记录不完整",
    "INVALID_VALUE": "值不符合定义", "STALE_VERSION": "提交版本已过期",
    # M7 安全导出与回收
    "MANIFEST_MISSING": "缺少证据清单行，文件不受信",
    "MANIFEST_BAD": "证据清单无法解析",
    "ROW_COUNT_MISMATCH": "数据行数与清单登记不符",
    "DIGEST_MISMATCH": "内容摘要不符，文件已被篡改",
    "RUN_UNKNOWN": "清单中的运行编号无存证，批次错配",
    "RUN_MISMATCH": "清单摘要与服务端存证不匹配，批次错配",
    "GUARD_MISMATCH": "防护标记与单元格不一致，拒绝猜测还原",
}

def reason(code: str) -> str:
    return REASONS.get(code, "未登记原因")
