# ShadowOps 开发手册

## 前置条件

- Python 3.12
- uv
- Docker Engine 与 Docker Compose v2

所有命令在仓库根目录执行。`.env.example` 记录本地配置键；不要提交真实密钥或生产数据库连接。

## 快速质量检查

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest tests/unit -v
```

## Compose 集成检查

```bash
docker compose up --build --detach --wait
docker compose exec api alembic -c alembic.ini upgrade head
uv run pytest tests/integration -v
docker compose down --volumes
```

可在服务运行时手工确认 API：

```bash
uv run shadowops ping --api-url http://127.0.0.1:8000
docker compose ps
docker compose logs --no-color api worker
```

无论测试成功或失败，都应执行 `docker compose down --volumes`；M0 集成测试数据不需要保留。

## TDD 与里程碑规则

- 行为变更先写一个可以正常收集且因缺少行为而失败的测试，再写最小实现。
- 每个任务完成前运行相关测试；每个里程碑完成前运行完整质量与集成门禁。
- 未实际测量的准确率、成功率、时延或成本不得写入 README、Demo 或简历。
- `domain` 层不得依赖 FastAPI、Celery、SQLAlchemy、Docker 或 LLM SDK。
