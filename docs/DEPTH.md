# 深度登记 历史档案混合编码归一与索引保形

        - 规模：中型
        - 核心规则：候选解码必须完整且无替换字符；档号、盒号与超大流水号始终按字符串逐字保留。
        - 使用者与工作流：导入随仓样例，逐模块复核，最后导出单一证据清单。

        ## 功能模块

        | 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
        |---|---|---|---|---|
        | M1 文件采信 | 运行样例并复核证据 | `/api/intake/run` / `/intake` | `app/modules/m1_intake/core.py` `app/modules/m1_intake/service.py` | `tests/test_m1_intake.py` |
| M2 编码判定 | 运行样例并复核证据 | `/api/encoding/detect` / `/encoding` | `app/modules/m2_encoding/core.py` `app/modules/m2_encoding/service.py` | `tests/test_m2_encoding.py` |
| M3 字段保形 | 运行样例并复核证据 | `/api/fields/normalize` / `/fields` | `app/modules/m3_fields/core.py` `app/modules/m3_fields/service.py` | `tests/test_m3_fields.py` |
| M4 内容归一 | 运行样例并复核证据 | `/api/content/normalize` / `/content` | `app/modules/m4_content/core.py` `app/modules/m4_content/service.py` | `tests/test_m4_content.py` |
| M5 索引对账 | 运行样例并复核证据 | `/api/reconcile` / `/reconcile` | `app/modules/m5_reconcile/core.py` `app/modules/m5_reconcile/service.py` | `tests/test_m5_reconcile.py` |
| M6 批次导出 | 运行样例并复核证据 | `/api/export/evidence.csv` / `/runs` | `app/modules/m6_runs/core.py` `app/modules/m6_runs/service.py` | `tests/test_m6_runs.py` |
| M7 安全导出与回收 | 导出安全证据表并回收核验 | `/api/reclaim` / `/runs` | `app/modules/m7_reclaim/guard.py` `app/modules/m7_reclaim/core.py` `app/modules/m7_reclaim/handler.py` | `tests/test_m7_guard.py` `tests/test_m7_reclaim.py` |

        ## 陷阱登记

        | TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
        |---|---|---|---|---|---|
        | TRAP-01 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_intake/core.py` | `tests/test_m1_intake.py::test_trap_1` |
| TRAP-02 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_encoding/core.py` | `tests/test_m2_encoding.py::test_trap_1` |
| TRAP-03 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_fields/core.py` | `tests/test_m3_fields.py::test_trap_1` |
| TRAP-04 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_content/core.py` | `tests/test_m4_content.py::test_trap_1` |
| TRAP-05 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m5_reconcile/core.py` | `tests/test_m5_reconcile.py::test_trap_1` |
| TRAP-07 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m6_runs/core.py` | `tests/test_m6_runs.py::test_trap_1` |
| TRAP-08 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_intake/core.py` | `tests/test_m1_intake.py::test_trap_2` |
| TRAP-09 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_encoding/core.py` | `tests/test_m2_encoding.py::test_trap_2` |
| TRAP-16 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_fields/core.py` | `tests/test_m3_fields.py::test_trap_2` |
| TRAP-18 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_content/core.py` | `tests/test_m4_content.py::test_trap_2` |
| TRAP-19 | 表格软件公式执行 | 直接导出 CSV，`=1+1`、全角 `＝` 被当公式，前导零与超长档号被数值化 | 危险单元格前置 ASCII 单引号强制文本，防护位置逐格登记清单，原文不改写 | `app/modules/m7_reclaim/guard.py` | `tests/test_m7_guard.py::test_formula_after_whitespace_roundtrip` |
| TRAP-20 | 防护字符与原文混淆 | 对所有值机械加引号，回收无法区分引号是防护还是原文，逐字身份被破坏 | 仅防护危险格并写入 `__guard__` 清单；原文自带引号必防护且双写，回收严格按清单剥离，不猜测 | `app/modules/m7_reclaim/guard.py` | `tests/test_m7_guard.py::test_original_quote_preserved` |
| TRAP-21 | 回收错配与篡改 | 信任回传内容直接入库，被改档号或错批次污染档案 | 清单双摘要 + 服务端证据存证配套核验，任一不符整批拒收、零行入库 | `app/modules/m7_reclaim/core.py` | `tests/test_m7_reclaim.py::test_reclaim_api_tamper_rejected_entire_batch` |
| TRAP-22 | 中断与并发重复入库 | 重试或并发回收同一份表产生重复行，中断后状态卡死 | 文件摘要唯一约束 + 进程内锁 + 原子 claim；行级幂等，PENDING/PROCESSING 可续跑 | `app/store.py` `app/modules/m7_reclaim/core.py` | `tests/test_m7_reclaim.py::test_concurrent_reclaim_accepted_once` |

        ## 对账规则

        - 输入记录数 = 有效记录数 + 隔离记录数 + 空记录数。
        - 核心数值或事件数在转换、判定和导出之间保持守恒。
        - 同一输入摘要重跑复用原运行编号，不增加结果行。
        - 回收表行数与入库行数必须守恒；同文件摘要的回收作业全局唯一，重试与并发不产生重复行。

        ## M7 安全导出与回收约定

        - 导出文件为 UTF-8（带 BOM）CSV，首行 `#!ARCHIVECODEC-EVIDENCE ` 清单登记 `run_id`、列、行数、原文摘要、防护表摘要与防护前缀；末列 `__guard__` 逐格登记防护列下标。
        - 防护仅对危险单元格前置 ASCII `'`：公式触发符（`= + - @` 及全角 `＝ ＋ － ＠`，含前导空白）、前导零数字串、超过 15 位数字串、布尔/日期样式、原文自带单引号。
        - 回收严格按清单剥离且仅剥离一个防护前缀；未登记防护却带前缀、登记防护却无前缀，一律 `GUARD_MISMATCH` 整批拒收，不做启发式还原。
        - 回收核验顺序：清单存在性 → 清单版本/字段 → 表头与行数 → 逐格防护一致性 → 防护表摘要 → 原文摘要 → 服务端证据配套。任一失败零行入库。
        - 作业状态：`PENDING` / `PROCESSING` / `ACCEPTED` / `REJECTED` / `FAILED`；中断后调用续跑接口从持久化防护载荷还原并补齐行。

        ## 已知不覆盖

        - 生产系统连接、身份认证和外部密钥不在演示范围内。
