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
uv run pytest tests/unit tests/contract -v
```

## Compose 集成检查

```bash
docker compose -f compose.yaml -f compose.test.yaml down --volumes
bash scripts/init-fixture-repositories.sh
docker compose -f compose.yaml -f compose.test.yaml up --build --detach --wait
docker compose -f compose.yaml -f compose.test.yaml exec -T api alembic -c alembic.ini upgrade head
uv run pytest tests/integration tests/e2e -v
docker compose -f compose.yaml -f compose.test.yaml down --volumes
```

可在服务运行时手工确认 API：

```bash
uv run shadowops ping --api-url http://127.0.0.1:8000
docker compose ps
docker compose logs --no-color api worker
```

`compose.test.yaml` 只把 PostgreSQL/Redis 暴露到 loopback 测试端口。E2E 会停止并重启 API/worker，必须在专用本地栈中串行运行。无论测试成功或失败，都应执行带两个 Compose 文件的 `down --volumes`；测试数据不需要保留。

若本机 `8000` 已占用，可设置 `SHADOWOPS_HTTP_PORT_HOST=58000` 启动，并在测试时设置 `SHADOWOPS_API_BASE=http://127.0.0.1:58000`。`SHADOWOPS_REPO_ROOT_HOST` 只控制宿主机只读挂载来源；容器内可信根固定为 `/repositories`。

## M5 手工检查

创建任务后，内嵌 Celery Beat 的 worker 会周期调用 outbox dispatcher 与 reconciler，无需手工触发任务：

```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/runs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: development-demo-1' \
  -d '{"repository_path":"projects/m1-noop-demo"}'
```

将响应的 id 代入 `/api/v1/runs/<run-id>`、`/timeline`、`/static-report`、`/plan`、`/dynamic-result` 或 `/events`。M5 在 M4 upgrade 后继续执行 `SEEDING`、`SMOKE_TESTING`、`ROLLBACK_VERIFYING` 与 `REPORTING`：固定 seed、schema/row/constraint checks、`target → baseline → target` 往返，以及 content-addressed evidence collection。到达 `COMPLETED` 表示这些 shadow checks 已成功且动态资源已清理；它仍不代表生产数据分布、生产锁行为或真实 LLM 风险解释已被验证。

Worker 是唯一挂载 Docker socket 的受信任基础设施组件，等价于本机 Docker daemon 权限；Agent、Runner、API 与 migration 都不能访问该 socket。此信任模型只面向可信开发机上的本地单用户 Demo，不是敌对多租户沙箱。

## TDD 与里程碑规则

- 行为变更先写一个可以正常收集且因缺少行为而失败的测试，再写最小实现。
- 每个任务完成前运行相关测试；每个里程碑完成前运行完整质量与集成门禁。
- 未实际测量的准确率、成功率、时延或成本不得写入 README、Demo 或简历。
- `domain` 层不得依赖 FastAPI、Celery、SQLAlchemy、Docker 或 LLM SDK。
