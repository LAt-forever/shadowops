# M2A 安全仓库发现验证交接

验证日期：2026-08-26（Asia/Shanghai）

分支：`codex/m2a-secure-discovery`

## 已验证范围

- `repository_path` 只能是 configured allowed root 下的相对路径；API 做早期校验，worker 在读取前重复执行权威校验。
- 拒绝路径逃逸、symlink、硬链接、非普通文件，以及超出文件数、单文件大小和总字节预算的输入；默认排除 VCS/cache/build/credential 内容并保留 `.env.example`。
- `WORKING_TREE` 固定当前 HEAD，读取 tracked 当前内容和非忽略 untracked 文件；`RANGE` 把 refs 固定为 commit SHA、校验祖先关系并只读取 head tree，不 checkout 或修改源仓库。
- Git 使用参数数组且关闭 hooks、fsmonitor、external diff 和交互提示；输入仓库 Python 不 import、不运行，fixture `env.py` 带 import-time failure 用于证明此边界。
- 快照以 canonical manifest 的 SHA-256 寻址，写入临时同级目录，完整校验后原子发布；重复内容复用同一 artifact。
- `SnapshotReader` 只能读取 manifest 中的相对路径，执行独立大小限制、内容 hash 校验并返回稳定 evidence ID。
- Alembic 发现只解析根目录 `alembic.ini` 和 revision Python AST；支持 literal relative/`%(here)s` script location 与线性单 head chain。
- 动态 metadata、缺失 parent、cycle、merge、multiple heads、branch labels/dependencies 等不会被猜测，而是持久化 `supported=false` 和结构化原因。
- `repo_snapshots` 和 `revision_graphs` 通过 `0003_secure_discovery` 迁移持久化，并以 unique run ID + create-or-get 保证重试收敛。
- `QUEUED → DISCOVERING` 使用真实 `m2.discovery.v1` handler；其他阶段保持 `m1.noop.v1`。输入安全失败会原子地把 step/run 标记为 `FAILED`，不产生下一条 outbox event。
- discovery 在 Git selection、snapshot、AST discovery 边界续租并检查取消；worker 重放会复用已提交 snapshot/graph。
- Compose 默认只读挂载版本化 fixture 根，共享 content-addressed artifact volume；worker 仍以 UID `10001` 非 root 运行。

M2A 不包含静态风险规则或 public discovery API。四条规则和 `GET /api/v1/runs/{id}/static-report` 属于后续 M2B。

## 本次实测结果

| 检查 | 本次结果 |
|---|---|
| Ruff check | passed |
| Ruff format | 89 files conform |
| Mypy strict | 43 source files, no issues |
| Unit + contract tests | 94 passed |
| PostgreSQL/Redis/Compose integration tests | 24 passed |
| Black-box E2E tests | 4 passed |
| Clean-volume Alembic | `0001_bootstrap → 0002_reliable_runs → 0003_secure_discovery` |
| Compose services | 4 healthy |
| Worker UID | `10001` |
| E2E durable discovery evidence | 2 snapshots, 2 revision graphs |
| Stable input failure evidence | 1 `REPOSITORY_NOT_FOUND` terminal run |
| Compose cleanup | project containers, network and test volumes removed |

这些数字只描述 2026-08-26 的一次本地功能验证，不是性能、可用性、可靠性成功率或审计准确率基线。CI 远端结果需在分支推送后单独确认。
