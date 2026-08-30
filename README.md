# ShadowOps

ShadowOps 是面向 PostgreSQL/Alembic Migration 的 AI Agent 安全审计与 Docker 影子环境平台。目前处于 **pre-MVP / M4 动态 Alembic upgrade**：控制面能可靠创建任务，把允许目录中的本地 Git 仓库转成不可变快照和 revision graph，由受约束 Fake Agent 生成能力计划，再由确定性 orchestrator 在隔离 PostgreSQL 16 环境中执行 baseline 与 target upgrade。真实 LLM 仍在后续里程碑。

## 当前已实现

- Python 3.12 `src` 工程、锁定依赖与结构化 JSON 日志。
- FastAPI health API，以及创建、查询、取消 audit run 的版本化 API。
- PostgreSQL 持久化的 `audit_runs`、`run_steps`、`outbox_events`、`repo_snapshots`、`revision_graphs`、`static_reports`、`agent_invocations`、`agent_tool_calls`、`audit_plans` 与乐观版本控制。
- Transactional outbox、Celery 幂等消费、step claim/heartbeat/fencing 与 reconciler 恢复。
- 显式状态转换：`DISCOVERING` 执行安全发现，`STATIC_ANALYSIS` 执行确定性规则，`PLANNING` 执行 M3 Agent 规划，其余阶段暂沿 M1 no-op 状态链推进；非法跳转被拒绝。
- 单一 allowed root、相对路径校验、symlink/特殊文件/硬链接拒绝、文件与总量预算，以及默认凭据文件排除。
- `WORKING_TREE` 与 `RANGE` Git selector；不 checkout、不写源仓库、不通过 shell 执行 Git。
- content-addressed 只读快照 artifact，以及只用 AST 读取 Alembic 元数据的线性 revision graph 发现；仓库 Python 从不 import 或执行。
- 四条聚焦静态规则：破坏性 DDL、直接新增 NOT NULL、非并发索引、缺失或显式不可逆 downgrade。
- 每个 finding 包含规则版本、严重度、置信度、相对文件位置、修复建议、显式 unknowns 与确定性 evidence ID；不支持的 revision 结构形成独立高风险 finding。
- `GET /api/v1/runs/{id}/static-report` 返回 PostgreSQL 中的版本化 JSON 报告；重复执行收敛到同一份不可变结果。
- `AuditPlanV1` 只允许固定 capability、DAG 依赖、预算、原因和 evidence refs；不存在命令字符串、镜像、网络或宿主路径字段。
- 单一 Fake Agent 通过五个只读工具读取 revision graph、相关 revision、静态 findings、固定 capability catalog 和测试数据覆盖缺口；工具不能执行 shell、Docker 或数据库变更。
- Plan Validator 强制 mandatory capability、预算上限、依赖前置条件和无环结构；畸形 JSON/schema 只允许一次修复，预算耗尽后以稳定 `PLAN_INVALID` 失败。
- prompt/tool schema、输入/输出 hash、Agent invocation 与 tool-call trace 均持久化；相同 Fake 输入得到确定性的 reference plan 和 trace identity。
- `GET /api/v1/runs/{id}/plan` 返回版本化计划；尚未完成 PLANNING 时返回稳定的 `AUDIT_PLAN_NOT_READY`。
- `RunnerRequestV1` 只接受固定 action、revision、Shadow DB alias 与预算，不接受 command、image、network 或 host path；Runner 镜像与 PostgreSQL 16 镜像由服务配置固定并记录 content-addressed image ID。
- 每个 `run + generation` 使用带 lease/标签的 internal network、PostgreSQL 数据卷和只读 snapshot 卷；相同 environment/action 的持久化唯一键阻止重复 apply。
- Runner 以 UID 10002、只读 rootfs、`cap_drop=ALL`、`no-new-privileges`、tmpfs、CPU/内存/PID/时长限制运行；只获得临时 Shadow DB 凭据，不挂载 Docker socket、控制库 DSN、Redis URL、LLM secret 或宿主任意路径。
- baseline 与 target upgrade 分阶段执行，数据库 statement timeout 和 wall-clock timeout 双重限制；stdout/stderr 受大小限制、SHA-256 校验并脱敏后持久化。
- finalizer 在成功、结构化失败与协作式取消后删除 Runner/PostgreSQL 容器、internal network 和临时卷；周期 Sweeper 按过期 lease 回收孤儿资源。
- `GET /api/v1/runs/{id}/dynamic-result` 返回 environment 清理状态、版本化 Runner request/result、current revision 与受控 stdout/stderr artifact。
- HTTP idempotency key、重复 broker 消息幂等、协作式取消。
- 可查询 timeline 与支持 `Last-Event-ID` 恢复的 SSE 状态流。
- Redis 仅作 broker，权威任务状态保存在 PostgreSQL；worker 非 root 运行。
- Control PostgreSQL 的 Alembic migrations。
- Typer `shadowops version`、`shadowops ping`。
- Docker Compose 本地四服务拓扑：API、worker、PostgreSQL、Redis。
- 单元测试、Compose 集成测试、Ruff、Mypy 与 GitHub Actions 门禁。

当前**没有 Web 可视化界面**。现阶段可见产物是 REST JSON 静态报告、SSE 时间线、结构化日志和 PostgreSQL 持久状态；任务列表、详情页、findings 与证据报告 UI 计划在 M7 实现。

当前 Agent 是可复现的 Fake Provider，只负责生成并校验计划。M4 只执行其中的 provision、baseline upgrade、target apply 与 cleanup；test data、smoke checks、rollback roundtrip 和完整 evidence collection 留在 M5。真实 LLM 与证据解释在 M6，本地 Web UI 和人工审批在 M7。

## 本地启动

需要 Python 3.12、[uv](https://docs.astral.sh/uv/) 与 Docker Desktop/Engine。

```bash
uv sync --frozen
bash scripts/init-fixture-repositories.sh
docker compose up --build --detach --wait
docker compose exec api alembic -c alembic.ini upgrade head
uv run shadowops ping --api-url http://127.0.0.1:8000
```

预期最后一条命令输出 `ready`。结束后清理本地环境和测试数据卷：

```bash
docker compose down --volumes
```

## M4 API 示例

创建 run；相同 `Idempotency-Key` 与相同请求会返回同一个 run：

```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/runs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-run-1' \
  -d '{"repository_path":"projects/m1-noop-demo"}'
```

使用响应中的 run id 查询资源、持久化时间线或 SSE 状态流：

```bash
curl http://127.0.0.1:8000/api/v1/runs/<run-id>
curl http://127.0.0.1:8000/api/v1/runs/<run-id>/timeline
curl http://127.0.0.1:8000/api/v1/runs/<run-id>/static-report
curl http://127.0.0.1:8000/api/v1/runs/<run-id>/plan
curl http://127.0.0.1:8000/api/v1/runs/<run-id>/dynamic-result
curl -N -H 'Last-Event-ID: 0' http://127.0.0.1:8000/api/v1/runs/<run-id>/events
```

取消采用协作式 checkpoint，并要求客户端携带最近读取的版本：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/runs/<run-id>/cancel \
  -H 'Content-Type: application/json' \
  -d '{"expected_version":1}'
```

完整开发与验证流程见 [开发手册](./docs/development.md)。

`repository_path` 必须相对 `SHADOWOPS_REPO_ROOT`；Compose 默认只读挂载 `tests/fixtures/repositories`。扫描其他本地目录时，显式设置 host-only 的 `SHADOWOPS_REPO_ROOT_HOST`，不要把整个主目录作为默认扫描根。

## 项目文档

- [一页式 PRD](./PRD.md)
- [架构设计](./docs/ARCHITECTURE.md)
- [M0–M9 开发计划](./docs/DEVELOPMENT_PLAN.md)
- [M0 实施计划](./docs/superpowers/plans/2026-08-25-m0-foundation.md)
- [M0 验证交接](./docs/handoffs/M0.md)
- [M1 实施计划](./docs/superpowers/plans/2026-08-25-m1-reliable-run-skeleton.md)
- [M1 验证交接](./docs/handoffs/M1.md)
- [M2A 设计](./docs/superpowers/specs/2026-08-25-m2-agent-context-static-audit-design.md)
- [M2A 验证交接](./docs/handoffs/M2A.md)
- [M2B 验证交接](./docs/handoffs/M2B.md)
- [M3 验证交接](./docs/handoffs/M3.md)
- [M4 验证交接](./docs/handoffs/M4.md)
