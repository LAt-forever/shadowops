# M2B 静态审计验证交接

验证日期：2026-08-28（Asia/Shanghai）

分支：`codex/m2b-static-findings`

## 已验证范围

- `STATIC_ANALYSIS` 使用真实 `m2.static-analysis.v1` handler，从已持久化 snapshot/revision graph 读取本次变更的 revision，不执行或 import 仓库 Python。
- `SOPS001` 检测 upgrade 中的 `drop_table/drop_column/drop_index` 和 literal-string destructive SQL；动态 SQL 不被误报成已知事实。
- `SOPS002` 区分无 server default 的直接 NOT NULL 新列（HIGH）与带非空 default 的情况（MEDIUM），并显式保留生产表大小、rewrite 与锁时长未知项。
- `SOPS003` 检测已知未使用 `postgresql_concurrently=True` 的索引创建；动态参数不被猜测。
- `SOPS004` 检测缺失 downgrade、仅 `pass` 和显式 `NotImplementedError`；downgrade 内合法的反向 drop 不触发 `SOPS001`。
- discovery 的 unsupported reasons 生成独立 `STRUCTURE` HIGH finding，不把不可信 revision graph 伪装成可执行链。
- finding 包含规则/契约版本、严重度、置信度、相对路径、行列、事实消息、修复建议、evidence IDs、observation scope 与 unknowns，并以稳定顺序进入报告。
- `StaticReportV1` 包含 snapshot/selector/commit、revision graph 摘要、findings、unsupported reasons、ruleset version 和确定性最高风险。
- `static_reports` 通过 `0004_static_reports` 保存完整版本化 JSON，并以 unique run ID + create-or-get 让重试收敛。
- `GET /api/v1/runs/{id}/static-report` 对未知 run 返回 `404 RUN_NOT_FOUND`，对尚未提交报告的 run 返回 `409 STATIC_REPORT_NOT_READY`。
- safe nullable-column fixture 产生 INFO/零 finding 报告；dangerous drop fixture 产生带位置和 evidence ID 的 `SOPS001`、`SOPS004` HIGH findings。
- 报告在 API restart 后保持完全一致；worker restart、重复消息、取消和稳定失败路径继续由原可靠任务骨架覆盖。

## 本次实测结果

| 检查 | 本次结果 |
|---|---|
| Ruff check | passed |
| Ruff format | 106 files conform |
| Mypy strict | 48 source files, no issues |
| Unit + contract tests | 106 passed |
| PostgreSQL/Redis integration tests | 24 passed |
| Black-box E2E tests | 6 passed |
| Clean-volume Alembic | `0001_bootstrap → 0002_reliable_runs → 0003_secure_discovery → 0004_static_reports` |
| `0004` downgrade/upgrade roundtrip | passed; current head `0004_static_reports` |
| Compose services | 4 healthy |
| Worker UID | `10001` |
| Safe fixture persisted report | `INFO`, 0 findings |
| Dangerous fixture persisted report | `HIGH`, 2 findings |
| Compose cleanup | project containers, network and test volumes removed |

这些数字只描述 2026-08-28 的一次本地功能验证，不是性能、可用性、可靠性成功率或审计准确率基线。远端 CI 结果需在分支推送后单独确认。

M2B 不包含 Agent/LLM、Tool Gateway、影子 PostgreSQL、Migration 执行、Web UI 或人工审批；下一里程碑是 M3。
