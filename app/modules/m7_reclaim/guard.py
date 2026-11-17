"""M7 安全导出与回收：公式注入防护、清单（manifest）与逐字还原。

防护字符与原文的区分规则
------------------------
导出时对危险单元格前置 ASCII 单引号 ``'``（Excel/WPS/LibreOffice 通用的
"强制文本"前缀，表格软件不会把它当公式执行，也不会把数字串转成数值）。
防护只改变导出文件中的呈现，**绝不改写原文**：

1. 哪些单元格被防护，逐格登记在文件首行的清单里（``__guard__`` 列）；
2. 回收时严格按清单剥离且仅剥离一个前缀，不做任何启发式猜测；
3. 原文若本身以单引号开头，该格一定也在防护清单内，导出为双单引号，
   回收后逐字还原，与防护字符无歧义；
4. 未登记防护的单元格若出现防护前缀，视为篡改，整批拒收。
"""
from __future__ import annotations
import csv
import io
import json
import re

from app.errors import DomainError
from app.manifest import canonical_digest

GUARD_PREFIX = "'"
MANIFEST_PREFIX = "#!ARCHIVECODEC-EVIDENCE "
GUARD_COLUMN = "__guard__"
MANIFEST_VERSION = 1
_BOM = "﻿"

_FORMULA_FIRST = set("=+-@＝＋－＠")
_LEADING_ZERO_DIGITS = re.compile(r"^0\d+$")
_ALL_DIGITS = re.compile(r"^\d+$")
_BOOL = re.compile(r"^(true|false)$", re.IGNORECASE)
_DATE = re.compile(r"^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?$")


def needs_guard(value: str) -> bool:
    """判定单元格在表格软件中是否会被改写或当公式执行。"""
    if value == "":
        return False
    # 原文自带单引号前缀：必须防护，否则与防护字符无法区分
    if value.startswith(GUARD_PREFIX):
        return True
    # 前导空白后接公式触发符（含全角），Excel 同样会触发
    stripped = value.lstrip()
    if stripped and stripped[0] in _FORMULA_FIRST:
        return True
    # 前导零数字串：数值化会丢零
    if _LEADING_ZERO_DIGITS.match(value):
        return True
    # 超长数字串：超过 15 位会被科学计数法/精度截断（覆盖超大档号、超大流水号）
    if _ALL_DIGITS.match(value) and len(value) > 15:
        return True
    # 布尔与日期样式：打开即被转型
    if _BOOL.match(value) or _DATE.match(value):
        return True
    return False


def table_digest(header: list[str], rows: list[dict]) -> str:
    """原文表格（含表头）的规范摘要，导出与回收两端同算法。"""
    table = [list(header)]
    for row in rows:
        table.append([str(row.get(h, "")) for h in header])
    return canonical_digest(table)


def _guard_row(cells: list[str]) -> tuple[list[str], list[int]]:
    guarded_cols: list[int] = []
    out: list[str] = []
    for index, cell in enumerate(cells):
        if needs_guard(cell):
            guarded_cols.append(index)
            out.append(GUARD_PREFIX + cell)
        else:
            out.append(cell)
    return out, guarded_cols


def build_evidence_csv(run_id: str, header: list[str], rows: list[dict],
                       original_digest: str) -> str:
    """生成带清单行的安全证据表（调用方负责加 UTF-8 BOM）。"""
    full_header = list(header) + [GUARD_COLUMN]
    guarded_table: list[list[str]] = []
    for row in rows:
        cells = [str(row.get(h, "")) for h in header]
        guarded, cols = _guard_row(cells)
        guarded.append(",".join(map(str, cols)))
        guarded_table.append(guarded)
    guarded_digest = canonical_digest([full_header] + guarded_table)
    manifest = {
        "v": MANIFEST_VERSION,
        "run_id": run_id,
        "columns": list(header),
        "row_count": len(rows),
        "original_digest": original_digest,
        "guarded_digest": guarded_digest,
        "guard_prefix": GUARD_PREFIX,
    }
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(full_header)
    writer.writerows(guarded_table)
    return MANIFEST_PREFIX + json.dumps(manifest, ensure_ascii=False,
                                        separators=(",", ":")) + "\n" + buffer.getvalue()


def _restore_table(manifest: dict, file_header: list[str],
                   data_table: list[list[str]]) -> list[dict]:
    """按清单防护标记逐格还原；任何不一致即拒收，不猜测。"""
    expected_header = list(manifest["columns"]) + [GUARD_COLUMN]
    if file_header != expected_header:
        raise DomainError("GUARD_MISMATCH", "表头与清单登记列不一致")
    if len(data_table) != manifest["row_count"]:
        raise DomainError("ROW_COUNT_MISMATCH",
                          f"数据行数 {len(data_table)} 与清单登记 {manifest['row_count']} 不符")
    guarded_index = len(file_header) - 1
    restored_rows: list[dict] = []
    for line_no, row in enumerate(data_table, start=2):
        if len(row) != len(file_header):
            raise DomainError("BAD_COLUMNS", f"第 {line_no} 行字段数量不符")
        try:
            guard_cols = [] if row[guarded_index] == "" else [
                int(x) for x in row[guarded_index].split(",")
            ]
        except ValueError as exc:
            raise DomainError("GUARD_MISMATCH",
                              f"第 {line_no} 行防护标记无法解析") from exc
        if any(not 0 <= c < guarded_index for c in guard_cols) \
                or len(set(guard_cols)) != len(guard_cols):
            raise DomainError("GUARD_MISMATCH", f"第 {line_no} 行防护标记越界")
        guard_set = set(guard_cols)
        original_cells: list[str] = []
        for index, cell in enumerate(row[:guarded_index]):
            if index in guard_set:
                if not cell.startswith(GUARD_PREFIX):
                    raise DomainError("GUARD_MISMATCH",
                                      f"第 {line_no} 行防护标记与单元格不一致，拒绝猜测还原")
                original_cells.append(cell[len(GUARD_PREFIX):])
            else:
                if cell.startswith(GUARD_PREFIX):
                    raise DomainError("GUARD_MISMATCH",
                                      f"第 {line_no} 行出现未登记的防护字符，拒绝猜测还原")
                original_cells.append(cell)
        restored_rows.append(dict(zip(manifest["columns"], original_cells)))
    return restored_rows


def _check_manifest_fields(manifest: dict) -> None:
    required = {"v", "run_id", "columns", "row_count", "original_digest",
                "guarded_digest", "guard_prefix"}
    if not required.issubset(manifest) or manifest.get("v") != MANIFEST_VERSION \
            or manifest.get("guard_prefix") != GUARD_PREFIX:
        raise DomainError("MANIFEST_BAD", "证据清单字段不完整或版本不受信")


def restore_from_payload(run_id: str, header: list[str], table: list[list[str]],
                         original_digest: str) -> dict:
    """续跑入口：从持久化的防护表载荷还原并复核摘要。"""
    manifest = {
        "v": MANIFEST_VERSION, "run_id": run_id, "columns": header[:-1],
        "row_count": len(table), "original_digest": original_digest,
        "guarded_digest": canonical_digest([header] + table),
        "guard_prefix": GUARD_PREFIX,
    }
    restored_rows = _restore_table(manifest, header, table)
    if table_digest(manifest["columns"], restored_rows) != original_digest:
        raise DomainError("DIGEST_MISMATCH", "原文摘要不符，存证载荷已被篡改")
    return {"manifest": manifest, "restored_rows": restored_rows,
            "guarded_payload": {"header": header, "table": table}}


def parse_evidence_csv(text: str) -> dict:
    """解析回收表并逐字还原。任何不一致都抛 DomainError，整批拒收，不猜测。"""
    if text.startswith(_BOM):
        text = text[1:]
    if "\n" not in text:
        raise DomainError("MANIFEST_MISSING", "缺少证据清单行，文件不受信")
    first_line, rest = text.split("\n", 1)
    first_line = first_line.rstrip("\r")
    if not first_line.startswith(MANIFEST_PREFIX):
        raise DomainError("MANIFEST_MISSING", "缺少证据清单行，文件不受信")
    try:
        manifest = json.loads(first_line[len(MANIFEST_PREFIX):])
    except json.JSONDecodeError as exc:
        raise DomainError("MANIFEST_BAD", "证据清单无法解析") from exc
    _check_manifest_fields(manifest)

    reader = csv.reader(io.StringIO(rest, newline=""))
    table = [row for row in reader]
    if not table:
        raise DomainError("MANIFEST_BAD", "证据表缺少表头")
    file_header, data_table = table[0], table[1:]
    restored_rows = _restore_table(manifest, file_header, data_table)

    # 防护表摘要：覆盖每个单元格与防护列，任一字符被改都能检出
    actual_guarded_digest = canonical_digest([file_header] + data_table)
    if actual_guarded_digest != manifest["guarded_digest"]:
        raise DomainError("DIGEST_MISMATCH", "防护表摘要不符，文件已被篡改")
    actual_original_digest = table_digest(manifest["columns"], restored_rows)
    if actual_original_digest != manifest["original_digest"]:
        raise DomainError("DIGEST_MISMATCH", "原文摘要不符，文件已被篡改")
    return {
        "manifest": manifest,
        "restored_rows": restored_rows,
        "guarded_payload": {"header": file_header, "table": data_table},
    }
