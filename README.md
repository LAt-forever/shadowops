# ShadowOps

ShadowOps 是面向 PostgreSQL/Alembic Migration 的 AI 安全审计与 Docker 影子环境平台。目前处于 **pre-MVP / M0 工程基线**：控制面可以完整启动，但尚未实现 Migration 审计业务闭环。

## 当前已实现

- Python 3.12 `src` 工程、锁定依赖与结构化 JSON 日志。
- FastAPI `/health/live` 与可区分 PostgreSQL/Redis 状态的 `/health/ready`。
- Celery worker：Redis 仅作 broker，结果状态不存 Redis，worker 非 root 运行。
- Control PostgreSQL 的 Alembic 迁移骨架。
- Typer `shadowops version`、`shadowops ping`。
- Docker Compose 本地四服务拓扑：API、worker、PostgreSQL、Redis。
- 单元测试、Compose 集成测试、Ruff、Mypy 与 GitHub Actions 门禁。

当前**没有** Migration 识别、静态风险规则、Agent 规划、影子数据库动态执行、证据报告或人工审批；这些按开发计划在后续里程碑实现。

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

完整开发与验证流程见 [开发手册](./docs/development.md)。

## 项目文档

- [一页式 PRD](./PRD.md)
- [架构设计](./docs/ARCHITECTURE.md)
- [M0–M9 开发计划](./docs/DEVELOPMENT_PLAN.md)
- [M0 实施计划](./docs/superpowers/plans/2026-08-25-m0-foundation.md)
- [M0 验证交接](./docs/handoffs/M0.md)
