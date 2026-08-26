# ShadowOps

ShadowOps 是面向 PostgreSQL/Alembic Migration 的 AI Agent 安全审计与 Docker 影子环境平台。目前处于 **pre-MVP / M2A 安全发现**：控制面已能可靠创建任务，并把允许目录中的本地 Git 仓库转成不可变快照和可持久化的 Alembic revision graph。静态风险规则、Agent 循环和影子数据库仍在后续里程碑。

## 当前已实现

- Python 3.12 `src` 工程、锁定依赖与结构化 JSON 日志。
- FastAPI health API，以及创建、查询、取消 audit run 的版本化 API。
- PostgreSQL 持久化的 `audit_runs`、`run_steps`、`outbox_events`、`repo_snapshots`、`revision_graphs` 与乐观版本控制。
- Transactional outbox、Celery 幂等消费、step claim/heartbeat/fencing 与 reconciler 恢复。
- 显式状态转换：`DISCOVERING` 执行真实安全发现，其余阶段暂沿 M1 no-op 状态链推进；非法跳转被拒绝。
- 单一 allowed root、相对路径校验、symlink/特殊文件/硬链接拒绝、文件与总量预算，以及默认凭据文件排除。
- `WORKING_TREE` 与 `RANGE` Git selector；不 checkout、不写源仓库、不通过 shell 执行 Git。
- content-addressed 只读快照 artifact，以及只用 AST 读取 Alembic 元数据的线性 revision graph 发现；仓库 Python 从不 import 或执行。
- HTTP idempotency key、重复 broker 消息幂等、协作式取消。
- 可查询 timeline 与支持 `Last-Event-ID` 恢复的 SSE 状态流。
- Redis 仅作 broker，权威任务状态保存在 PostgreSQL；worker 非 root 运行。
- Control PostgreSQL 的 Alembic migrations。
- Typer `shadowops version`、`shadowops ping`。
- Docker Compose 本地四服务拓扑：API、worker、PostgreSQL、Redis。
- 单元测试、Compose 集成测试、Ruff、Mypy 与 GitHub Actions 门禁。

当前**没有 Web 可视化界面**。M1 的可见产物是 REST JSON、SSE 时间线、结构化日志和 PostgreSQL 持久状态；任务列表、详情页、findings 与证据报告 UI 计划在 M7 实现。

当前已经完成 Migration 配置/revision 的静态发现，但**还没有**四条 M2B 静态风险规则、Agent 规划、影子数据库动态执行、证据报告或人工审批；这些按精简后的开发计划在后续里程碑实现。

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

## M2A API 示例

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
