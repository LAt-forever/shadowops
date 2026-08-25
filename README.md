# ShadowOps

ShadowOps 是面向 PostgreSQL/Alembic Migration 的 AI 安全审计与 Docker 影子环境平台。目前处于 **pre-MVP / M1 可靠任务骨架**：控制面可以可靠创建、推进、恢复、取消和查询一个 no-op audit run，但尚未执行真实 Migration 审计。

## 当前已实现

- Python 3.12 `src` 工程、锁定依赖与结构化 JSON 日志。
- FastAPI health API，以及创建、查询、取消 audit run 的版本化 API。
- PostgreSQL 持久化的 `audit_runs`、`run_steps`、`outbox_events` 与乐观版本控制。
- Transactional outbox、Celery 幂等消费、step claim/heartbeat/fencing 与 reconciler 恢复。
- 显式状态转换：run 从 `QUEUED` 沿 M1 no-op 状态链推进到 `COMPLETED`，非法跳转被拒绝。
- HTTP idempotency key、重复 broker 消息幂等、协作式取消。
- 可查询 timeline 与支持 `Last-Event-ID` 恢复的 SSE 状态流。
- Redis 仅作 broker，权威任务状态保存在 PostgreSQL；worker 非 root 运行。
- Control PostgreSQL 的 Alembic migrations。
- Typer `shadowops version`、`shadowops ping`。
- Docker Compose 本地四服务拓扑：API、worker、PostgreSQL、Redis。
- 单元测试、Compose 集成测试、Ruff、Mypy 与 GitHub Actions 门禁。

当前**没有 Web 可视化界面**。M1 的可见产物是 REST JSON、SSE 时间线、结构化日志和 PostgreSQL 持久状态；任务列表、详情页、findings 与证据报告 UI 计划在 M7 实现。

当前也**没有** Migration 识别、静态风险规则、Agent 规划、影子数据库动态执行、证据报告或人工审批；这些按开发计划在后续里程碑实现。

## 本地启动

需要 Python 3.12、[uv](https://docs.astral.sh/uv/) 与 Docker Desktop/Engine。

```bash
uv sync --frozen
docker compose up --build --detach --wait
docker compose exec api alembic -c alembic.ini upgrade head
uv run shadowops ping --api-url http://127.0.0.1:8000
```

预期最后一条命令输出 `ready`。结束后清理本地环境和测试数据卷：

```bash
docker compose down --volumes
```

## M1 API 示例

创建 run；相同 `Idempotency-Key` 与相同请求会返回同一个 run：

```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/runs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-run-1' \
  -d '{"repository_path":"projects/demo"}'
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

## 项目文档

- [一页式 PRD](./PRD.md)
- [架构设计](./docs/ARCHITECTURE.md)
- [M0–M9 开发计划](./docs/DEVELOPMENT_PLAN.md)
- [M0 实施计划](./docs/superpowers/plans/2026-08-25-m0-foundation.md)
- [M0 验证交接](./docs/handoffs/M0.md)
- [M1 实施计划](./docs/superpowers/plans/2026-08-25-m1-reliable-run-skeleton.md)
- [M1 验证交接](./docs/handoffs/M1.md)
