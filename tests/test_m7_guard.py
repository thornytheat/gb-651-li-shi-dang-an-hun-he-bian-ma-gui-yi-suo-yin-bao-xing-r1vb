"""M7 防护规则、清单与逐字还原（题目要求的四类验证 + 篡改拒收）。"""
import pytest

from app.errors import DomainError
from app.modules.m7_reclaim.guard import (
    GUARD_PREFIX, MANIFEST_PREFIX, build_evidence_csv, needs_guard,
    parse_evidence_csv, table_digest,
)

HEADER = ["source_line", "archive_id", "reason"]


def _roundtrip(rows):
    digest = table_digest(HEADER, rows)
    text = build_evidence_csv("run-x", HEADER, rows, digest)
    parsed = parse_evidence_csv(text)
    assert parsed["restored_rows"] == rows
    return text, parsed


def test_needs_guard_formula_variants():
    # 空白后公式、全角触发符都必须防护
    for value in ["=1+1", "-2+2", "+1", "@SUM(A1)", "\t=1", "  ＝x",
                  "＝HYPERLINK()", "＋1", "－1", "＠name"]:
        assert needs_guard(value), value
    # 普通文本不防护
    for value in ["OK", "BAD_COLUMNS", "年度 汇编", "普通档号A1", ""]:
        assert not needs_guard(value), value


def test_needs_guard_fullwidth_symbols():
    for value in ["＝1", "＋1", "－1", "＠x"]:
        assert needs_guard(value)


def test_needs_guard_oversized_and_leading_zero():
    assert needs_guard("123456789012345678901234567890")  # 30 位超大档号
    assert needs_guard("9007199254740993")  # 16 位，超过 Excel 15 位精度
    assert needs_guard("000123")  # 前导零
    assert needs_guard("000000000000000042")
    assert not needs_guard("12345")  # 普通短数字
    assert needs_guard("true") and needs_guard("FALSE")
    assert needs_guard("2026-11-17") and needs_guard("2026年11月17日")


def test_needs_guard_original_quote():
    # 原文自带单引号必须防护，否则与防护字符无法区分
    assert needs_guard("'007")
    assert needs_guard("'=1+1")


def test_formula_after_whitespace_roundtrip():
    rows = [
        {"source_line": "1", "archive_id": "=1+1", "reason": "OK"},
        {"source_line": "2", "archive_id": "  @SUM(A1)", "reason": "OK"},
    ]
    text, parsed = _roundtrip(rows)
    # 防护字符确实写入导出文件，表格软件不会执行
    assert "'=1+1" in text and "'  @SUM(A1)" in text
    # 清单逐格登记防护位置
    assert "\n1,'=1+1,OK,1\n" in text


def test_fullwidth_roundtrip():
    rows = [
        {"source_line": "1", "archive_id": "＝HYPERLINK(\"http://x\")", "reason": "OK"},
        {"source_line": "2", "archive_id": "＋1", "reason": "OK"},
        {"source_line": "3", "archive_id": "＠表", "reason": "OK"},
    ]
    text, _ = _roundtrip(rows)
    assert "'＝HYPERLINK" in text
    assert "'＋1" in text and "'＠表" in text


def test_oversized_archive_id_roundtrip():
    huge = "123456789012345678901234567890"
    rows = [{"source_line": "6", "archive_id": huge, "reason": "OK"}]
    text, parsed = _roundtrip(rows)
    assert GUARD_PREFIX + huge in text
    assert parsed["restored_rows"][0]["archive_id"] == huge  # 逐字、不数值化


def test_original_quote_preserved():
    rows = [
        {"source_line": "8", "archive_id": "'007", "reason": "OK"},
        {"source_line": "9", "archive_id": "''double", "reason": "OK"},
    ]
    text, parsed = _roundtrip(rows)
    # 原文一个引号 -> 导出两个引号；回收仍为一个
    assert "''007" in text
    assert "'''double" in text
    assert parsed["restored_rows"] == rows


def test_line_number_and_leading_zero_preserved():
    rows = [
        {"source_line": "0002", "archive_id": "000123", "reason": "OK"},
        {"source_line": "0010", "archive_id": "000456", "reason": "OK"},
    ]
    _, parsed = _roundtrip(rows)
    assert [r["source_line"] for r in parsed["restored_rows"]] == ["0002", "0010"]
    assert [r["archive_id"] for r in parsed["restored_rows"]] == ["000123", "000456"]


def test_quoted_comma_newline_title_roundtrip():
    rows = [{"source_line": "9", "archive_id": "0001",
             "reason": '题名,含"双引号"\n与换行'}]
    _, parsed = _roundtrip(rows)
    assert parsed["restored_rows"] == rows


def test_manifest_first_line_and_bom():
    text, _ = _roundtrip([{"source_line": "1", "archive_id": "x", "reason": "OK"}])
    bom = "﻿"
    # BOM 由导出 HTTP 层附加；解析端对带/不带 BOM 均兼容
    assert text.startswith(MANIFEST_PREFIX)
    assert parse_evidence_csv(bom + text)["manifest"]["run_id"] == "run-x"
    assert parse_evidence_csv(text)["manifest"]["run_id"] == "run-x"


def test_tampered_cell_rejected():
    rows = [{"source_line": "1", "archive_id": "=1+1", "reason": "OK"}]
    text, _ = _roundtrip(rows)
    tampered = text.replace("'=1+1", "'=1+2", 1)
    with pytest.raises(DomainError) as exc:
        parse_evidence_csv(tampered)
    assert exc.value.code == "DIGEST_MISMATCH"


def test_guard_stripped_but_mark_remains_rejected():
    rows = [{"source_line": "1", "archive_id": "=1+1", "reason": "OK"}]
    text, _ = _roundtrip(rows)
    stripped = text.replace("'=1+1", "=1+1", 1)
    with pytest.raises(DomainError) as exc:
        parse_evidence_csv(stripped)
    assert exc.value.code == "GUARD_MISMATCH"  # 不猜测还原


def test_unguarded_cell_gains_quote_rejected():
    rows = [{"source_line": "1", "archive_id": "PLAIN", "reason": "OK"}]
    text, _ = _roundtrip(rows)
    tampered = text.replace("PLAIN", "'PLAIN", 1)
    with pytest.raises(DomainError) as exc:
        parse_evidence_csv(tampered)
    assert exc.value.code == "GUARD_MISMATCH"


def test_missing_manifest_rejected():
    with pytest.raises(DomainError) as exc:
        parse_evidence_csv("source_line,archive_id\n1,x\n")
    assert exc.value.code == "MANIFEST_MISSING"


def test_row_count_mismatch_rejected():
    rows = [{"source_line": "1", "archive_id": "x", "reason": "OK"}]
    text, _ = _roundtrip(rows)
    # 删掉一条数据行，清单仍登记 1 行
    lines = text.split("\n")
    removed = "\n".join(lines[:2] + lines[3:])
    with pytest.raises(DomainError) as exc:
        parse_evidence_csv(removed)
    assert exc.value.code == "ROW_COUNT_MISMATCH"


def test_bad_columns_rejected():
    rows = [{"source_line": "1", "archive_id": "x", "reason": "OK"}]
    text, _ = _roundtrip(rows)
    tampered = text.replace("1,x,OK,", "1,x,OK,extra,")
    with pytest.raises(DomainError) as exc:
        parse_evidence_csv(tampered)
    assert exc.value.code == "BAD_COLUMNS"


def test_empty_rows_roundtrip():
    text, parsed = _roundtrip([])
    assert parsed["restored_rows"] == []
    assert parsed["manifest"]["row_count"] == 0
