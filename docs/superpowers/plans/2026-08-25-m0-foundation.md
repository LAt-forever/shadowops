# ShadowOps M0 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible Python 3.12 control-plane skeleton whose CLI, FastAPI service, Control PostgreSQL, Redis, and Celery worker start together and expose verified liveness/readiness behavior without implementing audit business logic.

**Architecture:** A `src`-layout Python package provides one shared settings object, structured logging, HTTP health routes, a thin CLI, database/Redis probes, and a Celery application. Docker Compose runs the long-lived API, worker, PostgreSQL, and Redis services; PostgreSQL remains the authoritative future task store, while Redis is only the broker.

**Tech Stack:** Python 3.12, uv, FastAPI, Pydantic Settings, SQLAlchemy 2, psycopg 3, Alembic, Celery, Redis, Typer, HTTPX, Structlog, Pytest, Ruff, Mypy, Docker Compose.

**Spec:** `docs/DEVELOPMENT_PLAN.md` M0, constrained by `docs/ARCHITECTURE.md`

## Global Constraints

- Python runtime is exactly the 3.12 minor line: `requires-python = ">=3.12,<3.13"`.
- PostgreSQL major version is 16; implementation locks the exact image digest before M0 is declared complete.
- Default HTTP bind address is `127.0.0.1`; Compose may bind the published port to host loopback only.
- The package must not contain audit runs, Agent, rule-engine, sandbox, report, or approval behavior in M0.
- `domain` remains free of FastAPI, Celery, SQLAlchemy, Docker, and LLM SDK imports.
- No secret is committed; `.env.example` contains non-secret local defaults only.
- New behavior follows RED → verify failure → GREEN → verify pass → refactor.
- Configuration-only bootstrap files are created before the first Python test because they are required to install the test runner; no production behavior is added in that bootstrap step.

---

## File Responsibility Map

```text
pyproject.toml                         Package metadata, dependency groups, tool configuration
.python-version                        uv/Python minor selection
.gitignore                             Generated files, local env, artifacts, worktrees
.dockerignore                          Minimal container build context
.env.example                           Non-secret local service defaults
README.md                              Pre-MVP status and documentation links
compose.yaml                           api/worker/control-postgres/redis topology
docker/api.Dockerfile                  Shared control-plane runtime image
src/shadowops/__init__.py              Package version only
src/shadowops/config.py                Cached Pydantic settings factory
src/shadowops/observability/logging.py Structured JSON logging setup
src/shadowops/application/readiness.py Framework-free readiness aggregation
src/shadowops/infrastructure/health.py SQLAlchemy and Redis readiness adapters
src/shadowops/api/app.py               FastAPI application factory
src/shadowops/api/routes/health.py     /health/live and /health/ready routes
src/shadowops/worker/celery_app.py     Celery app and broker configuration
src/shadowops/cli/app.py               Typer version and ping commands
alembic.ini                            Control-plane Alembic entry configuration
migrations/control/env.py              Control-plane migration environment
migrations/control/versions/...        Empty bootstrap revision
tests/unit/...                         Behavior tests without external services
tests/integration/...                  PostgreSQL/Redis/API checks against Compose
docs/development.md                    M0 setup and verification commands
.github/workflows/ci.yml               Fast and service-backed CI jobs
```

### Task 1: Bootstrap Package Metadata and Version Contract

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Modify: `.gitignore`
- Create: `README.md`
- Create: `src/shadowops/__init__.py`
- Create: `tests/unit/test_package.py`

**Interfaces:**
- Consumes: none
- Produces: `shadowops.__version__: str == "0.1.0"`; console entry point `shadowops = shadowops.cli.app:app` reserved for Task 7

- [ ] **Step 1: Create configuration-only bootstrap files**

Create `.python-version`:

```text
3.12
```

Replace `.gitignore` with the complete development ignore set:

```gitignore
.DS_Store
.env
.mypy_cache/
.pytest_cache/
.ruff_cache/
.venv/
.worktrees/
__pycache__/
*.py[cod]
artifacts/
htmlcov/
.coverage
```

Create `README.md`:

```markdown
# ShadowOps

ShadowOps is in pre-MVP development. See [the PRD](./PRD.md),
[architecture](./docs/ARCHITECTURE.md), and
[development plan](./docs/DEVELOPMENT_PLAN.md).
```

Create `pyproject.toml` with the package metadata and test tooling required to begin TDD:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "shadowops"
version = "0.1.0"
description = "AI-assisted safety auditing for PostgreSQL Alembic migrations"
readme = "README.md"
requires-python = ">=3.12,<3.13"
dependencies = []

[project.scripts]
shadowops = "shadowops.cli.app:app"

[dependency-groups]
dev = [
  "mypy",
  "pytest",
  "pytest-cov",
  "ruff",
]

[tool.hatch.build.targets.wheel]
packages = ["src/shadowops"]

[tool.pytest.ini_options]
addopts = "-ra --strict-config --strict-markers"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.12"
strict = true
packages = ["shadowops"]
```

Run:

```bash
uv sync --python 3.12
```

Expected: uv creates `.venv` and `uv.lock` and installs the dev group without errors.

- [ ] **Step 2: Write the failing package version test**

Create a behavior-free `src/shadowops/__init__.py` containing only a package docstring so pytest can collect the test. Create `tests/unit/test_package.py`:

```python
import shadowops


def test_package_exposes_initial_version() -> None:
    assert getattr(shadowops, "__version__", None) == "0.1.0"
```

- [ ] **Step 3: Run the test and verify RED**

Run:

```bash
uv run pytest tests/unit/test_package.py -v
```

Expected: FAIL with `AssertionError` because `shadowops.__version__` is absent.

- [ ] **Step 4: Add the minimal package implementation**

Replace `src/shadowops/__init__.py` with:

```python
"""ShadowOps package."""

__version__ = "0.1.0"
```

- [ ] **Step 5: Run the test and verify GREEN**

Run:

```bash
uv run pytest tests/unit/test_package.py -v
```

Expected: one test passes.

- [ ] **Step 6: Commit Task 1**

```bash
git add .python-version .gitignore README.md pyproject.toml uv.lock src/shadowops/__init__.py tests/unit/test_package.py
git commit -m "build: bootstrap Python package"
```

### Task 2: Typed Settings and Structured JSON Logging

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `.env.example`
- Create: `src/shadowops/config.py`
- Create: `src/shadowops/observability/__init__.py`
- Create: `src/shadowops/observability/logging.py`
- Create: `tests/unit/test_config.py`
- Create: `tests/unit/observability/test_logging.py`

**Interfaces:**
- Consumes: `shadowops.__version__`
- Produces: `Settings`, `get_settings() -> Settings`, `configure_logging() -> None`

- [ ] **Step 1: Add runtime dependencies**

Run:

```bash
uv add pydantic-settings structlog
```

Expected: `pyproject.toml` and `uv.lock` contain resolved compatible dependencies.

- [ ] **Step 2: Write failing settings tests**

Create a behavior-free `src/shadowops/config.py` containing only a module docstring. Create `tests/unit/test_config.py`:

```python
import shadowops.config as config


def test_settings_use_safe_local_defaults() -> None:
    settings_type = getattr(config, "Settings", None)
    assert settings_type is not None
    settings = settings_type(_env_file=None)

    assert settings.app_name == "ShadowOps"
    assert settings.environment == "development"
    assert settings.http_host == "127.0.0.1"
    assert settings.http_port == 8000
    assert settings.postgres_major == 16


def test_environment_variables_override_defaults(monkeypatch) -> None:
    monkeypatch.setenv("SHADOWOPS_HTTP_PORT", "8123")
    settings_type = getattr(config, "Settings", None)
    assert settings_type is not None
    settings = settings_type(_env_file=None)

    assert settings.http_port == 8123
```

- [ ] **Step 3: Verify settings tests fail for the missing module**

Run:

```bash
uv run pytest tests/unit/test_config.py -v
```

Expected: two assertion failures because `shadowops.config.Settings` is absent.

- [ ] **Step 4: Implement the minimal settings model**

Replace `src/shadowops/config.py` with:

```python
"""Application configuration."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SHADOWOPS_",
        extra="ignore",
    )

    app_name: str = "ShadowOps"
    environment: str = "development"
    http_host: str = "127.0.0.1"
    http_port: int = 8000
    database_url: str = (
        "postgresql+psycopg://shadowops:shadowops@control-postgres:5432/shadowops"
    )
    redis_url: str = "redis://redis:6379/0"
    postgres_major: int = 16
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Create `.env.example`:

```dotenv
SHADOWOPS_ENVIRONMENT=development
SHADOWOPS_HTTP_HOST=127.0.0.1
SHADOWOPS_HTTP_PORT=8000
SHADOWOPS_DATABASE_URL=postgresql+psycopg://shadowops:shadowops@control-postgres:5432/shadowops
SHADOWOPS_REDIS_URL=redis://redis:6379/0
SHADOWOPS_POSTGRES_MAJOR=16
SHADOWOPS_LOG_LEVEL=INFO
```

- [ ] **Step 5: Verify settings tests pass**

Run:

```bash
uv run pytest tests/unit/test_config.py -v
```

Expected: two tests pass.

- [ ] **Step 6: Write the failing JSON logging test**

Create behavior-free module docstrings in `src/shadowops/observability/__init__.py` and `src/shadowops/observability/logging.py`. Create `tests/unit/observability/test_logging.py`:

```python
import json

import structlog

import shadowops.observability.logging as logging_config


def test_configured_logger_emits_json(capsys) -> None:
    configure_logging = getattr(logging_config, "configure_logging", None)
    assert configure_logging is not None
    configure_logging("INFO")

    structlog.get_logger().info("service_started", service="api")

    payload = json.loads(capsys.readouterr().out)
    assert payload["event"] == "service_started"
    assert payload["service"] == "api"
    assert payload["level"] == "info"
```

- [ ] **Step 7: Verify logging test fails for the missing module**

Run:

```bash
uv run pytest tests/unit/observability/test_logging.py -v
```

Expected: FAIL with `AssertionError` because `configure_logging` is absent.

- [ ] **Step 8: Implement minimal structured logging**

Keep the package docstring in `src/shadowops/observability/__init__.py` and replace `src/shadowops/observability/logging.py` with:

```python
"""Structured logging configuration."""

import logging
import sys

import structlog


def configure_logging(level: str) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
```

- [ ] **Step 9: Verify Task 2 tests and full unit suite pass**

Run:

```bash
uv run pytest tests/unit -v
```

Expected: all Task 1–2 unit tests pass with no warnings.

- [ ] **Step 10: Commit Task 2**

```bash
git add pyproject.toml uv.lock .env.example src/shadowops/config.py src/shadowops/observability tests/unit/test_config.py tests/unit/observability
git commit -m "feat: add typed settings and structured logging"
```

### Task 3: Framework-Free Readiness Aggregation

**Files:**
- Create: `src/shadowops/application/__init__.py`
- Create: `src/shadowops/application/readiness.py`
- Create: `tests/unit/application/test_readiness.py`

**Interfaces:**
- Consumes: callables with signature `Callable[[], None]`
- Produces: `ReadinessService(checks: Mapping[str, Callable[[], None]])`, `ReadinessService.run() -> ReadinessResult`

- [ ] **Step 1: Write failing readiness tests**

Create `tests/unit/application/test_readiness.py`:

```python
from shadowops.application.readiness import ReadinessService


def test_readiness_is_ready_when_all_checks_succeed() -> None:
    result = ReadinessService({"database": lambda: None, "redis": lambda: None}).run()

    assert result.ready is True
    assert result.dependencies == {"database": "ok", "redis": "ok"}


def test_readiness_reports_each_failed_dependency() -> None:
    def fail() -> None:
        raise ConnectionError("offline")

    result = ReadinessService({"database": fail, "redis": lambda: None}).run()

    assert result.ready is False
    assert result.dependencies == {"database": "unavailable", "redis": "ok"}
```

- [ ] **Step 2: Verify readiness tests fail for the missing module**

Run:

```bash
uv run pytest tests/unit/application/test_readiness.py -v
```

Expected: FAIL with `ModuleNotFoundError` for `shadowops.application`.

- [ ] **Step 3: Implement readiness aggregation**

Create an empty `src/shadowops/application/__init__.py` and create `src/shadowops/application/readiness.py`:

```python
from collections.abc import Callable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    dependencies: dict[str, str]


class ReadinessService:
    def __init__(self, checks: Mapping[str, Callable[[], None]]) -> None:
        self._checks = dict(checks)

    def run(self) -> ReadinessResult:
        dependencies: dict[str, str] = {}
        for name, check in self._checks.items():
            try:
                check()
            except Exception:
                dependencies[name] = "unavailable"
            else:
                dependencies[name] = "ok"
        return ReadinessResult(
            ready=all(status == "ok" for status in dependencies.values()),
            dependencies=dependencies,
        )
```

- [ ] **Step 4: Verify readiness tests pass**

Run:

```bash
uv run pytest tests/unit/application/test_readiness.py -v
```

Expected: two tests pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/shadowops/application tests/unit/application
git commit -m "feat: add readiness aggregation"
```

### Task 4: FastAPI Liveness and Readiness Routes

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/shadowops/api/__init__.py`
- Create: `src/shadowops/api/app.py`
- Create: `src/shadowops/api/routes/__init__.py`
- Create: `src/shadowops/api/routes/health.py`
- Create: `tests/unit/api/test_health.py`

**Interfaces:**
- Consumes: `ReadinessService.run() -> ReadinessResult`, `Settings`, `configure_logging`
- Produces: `create_app(readiness_service: ReadinessService | None = None) -> FastAPI`, `app`

- [ ] **Step 1: Add HTTP dependencies**

Run:

```bash
uv add fastapi 'uvicorn[standard]' httpx
```

- [ ] **Step 2: Write failing API health tests**

Create `tests/unit/api/test_health.py`:

```python
from fastapi.testclient import TestClient

from shadowops.api.app import create_app
from shadowops.application.readiness import ReadinessService


def test_liveness_does_not_depend_on_external_services() -> None:
    client = TestClient(create_app(ReadinessService({})))

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_returns_503_with_dependency_status() -> None:
    def fail() -> None:
        raise ConnectionError("offline")

    client = TestClient(create_app(ReadinessService({"database": fail})))

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": {"database": "unavailable"},
    }
```

- [ ] **Step 3: Verify API tests fail for the missing module**

Run:

```bash
uv run pytest tests/unit/api/test_health.py -v
```

Expected: FAIL with `ModuleNotFoundError` for `shadowops.api`.

- [ ] **Step 4: Implement the health router and application factory**

Create empty `__init__.py` files under `src/shadowops/api/` and `src/shadowops/api/routes/`.

Create `src/shadowops/api/routes/health.py`:

```python
from fastapi import APIRouter, Request, Response, status

from shadowops.application.readiness import ReadinessService

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/ready")
def ready(request: Request, response: Response) -> dict[str, object]:
    service: ReadinessService = request.app.state.readiness_service
    result = service.run()
    if not result.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if result.ready else "not_ready",
        "dependencies": result.dependencies,
    }
```

Create `src/shadowops/api/app.py`:

```python
from fastapi import FastAPI

from shadowops.api.routes.health import router as health_router
from shadowops.application.readiness import ReadinessService


def create_app(readiness_service: ReadinessService | None = None) -> FastAPI:
    application = FastAPI(title="ShadowOps", version="0.1.0")
    application.state.readiness_service = readiness_service or ReadinessService({})
    application.include_router(health_router)
    return application


app = create_app()
```

- [ ] **Step 5: Verify API tests and unit suite pass**

Run:

```bash
uv run pytest tests/unit/api/test_health.py tests/unit/application/test_readiness.py -v
```

Expected: four tests pass.

- [ ] **Step 6: Commit Task 4**

```bash
git add pyproject.toml uv.lock src/shadowops/api tests/unit/api
git commit -m "feat: add service health endpoints"
```

### Task 5: PostgreSQL and Redis Health Adapters with Control Alembic

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/shadowops/api/app.py`
- Create: `src/shadowops/infrastructure/__init__.py`
- Create: `src/shadowops/infrastructure/health.py`
- Create: `tests/unit/infrastructure/test_health.py`
- Create: `alembic.ini`
- Create: `migrations/control/env.py`
- Create: `migrations/control/script.py.mako`
- Create: `migrations/control/versions/0001_bootstrap.py`

**Interfaces:**
- Consumes: `Settings.database_url`, `Settings.redis_url`, `ReadinessService`
- Produces: `DatabaseHealthCheck(engine)`, `RedisHealthCheck(client)`, default app readiness wiring

- [ ] **Step 1: Add persistence and broker client dependencies**

Run:

```bash
uv add 'sqlalchemy>=2,<3' 'psycopg[binary]>=3,<4' alembic redis
```

- [ ] **Step 2: Write failing adapter tests using real adapter behavior at the client boundary**

Create `tests/unit/infrastructure/test_health.py`:

```python
from unittest.mock import Mock

from shadowops.infrastructure.health import DatabaseHealthCheck, RedisHealthCheck


def test_database_health_executes_select_one() -> None:
    connection = Mock()
    context_manager = Mock()
    context_manager.__enter__ = Mock(return_value=connection)
    context_manager.__exit__ = Mock(return_value=False)
    engine = Mock()
    engine.connect.return_value = context_manager

    DatabaseHealthCheck(engine)()

    statement = connection.execute.call_args.args[0]
    assert str(statement) == "SELECT 1"


def test_redis_health_requires_successful_ping() -> None:
    client = Mock()
    client.ping.return_value = True

    RedisHealthCheck(client)()

    client.ping.assert_called_once_with()
```

- [ ] **Step 3: Verify adapter tests fail for the missing module**

Run:

```bash
uv run pytest tests/unit/infrastructure/test_health.py -v
```

Expected: FAIL with `ModuleNotFoundError` for `shadowops.infrastructure`.

- [ ] **Step 4: Implement minimal health adapters**

Create an empty `src/shadowops/infrastructure/__init__.py` and create `src/shadowops/infrastructure/health.py`:

```python
from typing import Any

from sqlalchemy import Engine, text


class DatabaseHealthCheck:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __call__(self) -> None:
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))


class RedisHealthCheck:
    def __init__(self, client: Any) -> None:
        self._client = client

    def __call__(self) -> None:
        if self._client.ping() is not True:
            raise ConnectionError("Redis ping returned a non-true response")
```

- [ ] **Step 5: Verify adapter tests pass**

Run:

```bash
uv run pytest tests/unit/infrastructure/test_health.py -v
```

Expected: two tests pass.

- [ ] **Step 6: Wire real checks into the default FastAPI application**

Update `src/shadowops/api/app.py` so the default factory creates a SQLAlchemy engine and Redis client from `get_settings()`, then passes `DatabaseHealthCheck` and `RedisHealthCheck` into `ReadinessService`. Preserve explicit injection in unit tests.

The resulting factory signature remains:

```python
def create_app(readiness_service: ReadinessService | None = None) -> FastAPI:
    ...
```

When `readiness_service is None`, construct:

```python
settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
redis_client = redis.from_url(settings.redis_url)
readiness_service = ReadinessService(
    {
        "database": DatabaseHealthCheck(engine),
        "redis": RedisHealthCheck(redis_client),
    }
)
```

- [ ] **Step 7: Create the empty control-plane Alembic baseline**

Generate the standard file layout:

```bash
uv run alembic init migrations/control
```

Set `script_location = %(here)s/migrations/control` in `alembic.ini`. Replace the generated `migrations/control/env.py` with:

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from shadowops.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

Create `migrations/control/versions/0001_bootstrap.py` with:

```python
revision = "0001_bootstrap"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
```

The revision intentionally creates no audit-domain tables; it only proves migration plumbing.

- [ ] **Step 8: Run unit verification**

Run:

```bash
uv run pytest tests/unit -v
uv run alembic -c alembic.ini history
```

Expected: all unit tests pass; Alembic lists `0001_bootstrap` without connecting to PostgreSQL.

- [ ] **Step 9: Commit Task 5**

```bash
git add pyproject.toml uv.lock src/shadowops/api/app.py src/shadowops/infrastructure tests/unit/infrastructure alembic.ini migrations/control
git commit -m "feat: add dependency readiness adapters"
```

### Task 6: Celery Application Configuration

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/shadowops/worker/__init__.py`
- Create: `src/shadowops/worker/celery_app.py`
- Create: `tests/unit/worker/test_celery_app.py`

**Interfaces:**
- Consumes: `Settings.redis_url`
- Produces: `create_celery_app(settings: Settings | None = None) -> Celery`, module singleton `celery_app`

- [ ] **Step 1: Add Celery dependency**

Run:

```bash
uv add 'celery[redis]'
```

- [ ] **Step 2: Write the failing Celery configuration test**

Create `tests/unit/worker/test_celery_app.py`:

```python
from shadowops.config import Settings
from shadowops.worker.celery_app import create_celery_app


def test_celery_uses_redis_for_delivery_not_result_truth() -> None:
    settings = Settings(redis_url="redis://example.test:6379/4", _env_file=None)

    application = create_celery_app(settings)

    assert application.conf.broker_url == "redis://example.test:6379/4"
    assert application.conf.result_backend is None
    assert application.conf.task_serializer == "json"
    assert application.conf.accept_content == ["json"]
    assert application.conf.task_acks_late is True
```

- [ ] **Step 3: Verify the test fails for the missing worker module**

Run:

```bash
uv run pytest tests/unit/worker/test_celery_app.py -v
```

Expected: FAIL with `ModuleNotFoundError` for `shadowops.worker`.

- [ ] **Step 4: Implement minimal Celery configuration**

Create an empty `src/shadowops/worker/__init__.py` and create `src/shadowops/worker/celery_app.py`:

```python
from celery import Celery

from shadowops.config import Settings, get_settings


def create_celery_app(settings: Settings | None = None) -> Celery:
    resolved = settings or get_settings()
    application = Celery("shadowops", broker=resolved.redis_url)
    application.conf.update(
        result_backend=None,
        task_serializer="json",
        accept_content=["json"],
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        timezone="UTC",
        enable_utc=True,
    )
    return application


celery_app = create_celery_app()
```

- [ ] **Step 5: Verify Celery and full unit tests pass**

Run:

```bash
uv run pytest tests/unit/worker/test_celery_app.py -v
uv run pytest tests/unit -v
```

Expected: all tests pass without contacting Redis.

- [ ] **Step 6: Commit Task 6**

```bash
git add pyproject.toml uv.lock src/shadowops/worker tests/unit/worker
git commit -m "feat: configure Celery worker"
```

### Task 7: Typer Version and API Ping Commands

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/shadowops/cli/__init__.py`
- Create: `src/shadowops/cli/app.py`
- Create: `tests/unit/cli/test_cli.py`

**Interfaces:**
- Consumes: `shadowops.__version__`, HTTP `GET /health/ready`
- Produces: Typer `app`; commands `shadowops version`, `shadowops ping --api-url URL`

- [ ] **Step 1: Add Typer dependency**

Run:

```bash
uv add typer
```

- [ ] **Step 2: Write failing CLI tests**

Create `tests/unit/cli/test_cli.py`:

```python
from typer.testing import CliRunner

from shadowops.cli.app import app

runner = CliRunner()


def test_version_prints_package_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_ping_reports_ready_api(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"status": "ready"}

    monkeypatch.setattr("shadowops.cli.app.httpx.get", lambda *args, **kwargs: Response())

    result = runner.invoke(app, ["ping", "--api-url", "http://127.0.0.1:8000"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "ready"
```

- [ ] **Step 3: Verify CLI tests fail for the missing module**

Run:

```bash
uv run pytest tests/unit/cli/test_cli.py -v
```

Expected: FAIL with `ModuleNotFoundError` for `shadowops.cli`.

- [ ] **Step 4: Implement minimal CLI**

Create an empty `src/shadowops/cli/__init__.py` and create `src/shadowops/cli/app.py`:

```python
import httpx
import typer

from shadowops import __version__

app = typer.Typer(no_args_is_help=True)


@app.command()
def version() -> None:
    typer.echo(__version__)


@app.command()
def ping(
    api_url: str = typer.Option("http://127.0.0.1:8000", help="ShadowOps API base URL"),
) -> None:
    response = httpx.get(f"{api_url.rstrip('/')}/health/ready", timeout=5.0)
    response.raise_for_status()
    typer.echo(response.json()["status"])
```

- [ ] **Step 5: Verify CLI tests and installed entry point pass**

Run:

```bash
uv run pytest tests/unit/cli/test_cli.py -v
uv run shadowops version
```

Expected: two tests pass; command prints `0.1.0`.

- [ ] **Step 6: Commit Task 7**

```bash
git add pyproject.toml uv.lock src/shadowops/cli tests/unit/cli
git commit -m "feat: add control-plane CLI"
```

### Task 8: Docker Compose Topology and Service Integration

**Files:**
- Create: `.dockerignore`
- Create: `docker/api.Dockerfile`
- Create: `compose.yaml`
- Create: `tests/integration/test_service_health.py`

**Interfaces:**
- Consumes: FastAPI `app`, Celery `celery_app`, settings env variables
- Produces: long-lived services `api`, `worker`, `control-postgres`, `redis`; host API at `127.0.0.1:8000`

- [ ] **Step 1: Write the failing service integration test**

Create `tests/integration/test_service_health.py`:

```python
import httpx


def test_compose_api_reports_database_and_redis_ready() -> None:
    response = httpx.get("http://127.0.0.1:8000/health/ready", timeout=5.0)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {"database": "ok", "redis": "ok"},
    }
```

- [ ] **Step 2: Verify integration test fails before Compose exists**

Run:

```bash
uv run pytest tests/integration/test_service_health.py -v
```

Expected: FAIL with an HTTP connection error because no service listens on port 8000.

- [ ] **Step 3: Create the control-plane container image**

Create `.dockerignore`:

```dockerignore
.git
.venv
.worktrees
artifacts
__pycache__
*.pyc
```

Resolve the current immutable image digests before writing the Dockerfile:

```bash
docker buildx imagetools inspect python:3.12-slim
docker buildx imagetools inspect ghcr.io/astral-sh/uv:0.11.12 --format '{{json .Manifest.Digest}}'
```

Create `docker/api.Dockerfile`; append the Python digest reported above to the first `FROM` reference before committing:

```dockerfile
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.12@sha256:3a59a3cdd5f7c217faa36e32dbc7fddbb0412889c2a0a5229f6d790e5a019dd7 /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
```

Record every resolved digest in the M0 handoff.

- [ ] **Step 4: Create Compose services with health checks**

Create `compose.yaml`:

```yaml
name: shadowops

services:
  control-postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: shadowops
      POSTGRES_USER: shadowops
      POSTGRES_PASSWORD: shadowops
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U shadowops -d shadowops"]
      interval: 2s
      timeout: 3s
      retries: 20
    volumes:
      - control-postgres-data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--save", "", "--appendonly", "no"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      timeout: 3s
      retries: 20

  api:
    build:
      context: .
      dockerfile: docker/api.Dockerfile
    command: ["uvicorn", "shadowops.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
    environment: &shadowops-environment
      SHADOWOPS_DATABASE_URL: postgresql+psycopg://shadowops:shadowops@control-postgres:5432/shadowops
      SHADOWOPS_REDIS_URL: redis://redis:6379/0
      SHADOWOPS_POSTGRES_MAJOR: "16"
      SHADOWOPS_LOG_LEVEL: INFO
    depends_on:
      control-postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)
      interval: 2s
      timeout: 3s
      retries: 20
    ports:
      - "127.0.0.1:8000:8000"

  worker:
    build:
      context: .
      dockerfile: docker/api.Dockerfile
    command: ["celery", "-A", "shadowops.worker.celery_app:celery_app", "worker", "--loglevel=INFO"]
    environment: *shadowops-environment
    depends_on:
      control-postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

volumes:
  control-postgres-data:
```

The Worker intentionally has no host Docker socket mount in M0.

- [ ] **Step 5: Resolve and pin service image digests**

Run:

```bash
docker buildx imagetools inspect postgres:16 --format '{{json .Manifest.Digest}}'
docker buildx imagetools inspect redis:7-alpine --format '{{json .Manifest.Digest}}'
```

Expected: each command prints an immutable `sha256:...` manifest digest. Append the exact digest to both image references in `compose.yaml` before validation and commit.

- [ ] **Step 6: Validate Compose configuration**

Run:

```bash
docker compose config --quiet
```

Expected: exit code 0 and no configuration errors.

- [ ] **Step 7: Build and start the four services**

Run:

```bash
docker compose up --build --detach --wait
```

Expected: all four services start; API, PostgreSQL, and Redis health checks report healthy; worker remains running.

- [ ] **Step 8: Apply the control-plane migration**

Run:

```bash
docker compose exec api alembic -c alembic.ini upgrade head
docker compose exec control-postgres psql -U shadowops -d shadowops -c "select version_num from alembic_version;"
```

Expected: query returns `0001_bootstrap`.

- [ ] **Step 9: Verify the integration test turns GREEN**

Run:

```bash
uv run pytest tests/integration/test_service_health.py -v
```

Expected: one integration test passes.

- [ ] **Step 10: Verify service logs and tear down cleanly**

Run:

```bash
docker compose ps
docker compose logs --no-color api worker
docker compose down --volumes
docker compose ps --all
```

Expected: services were healthy; logs contain no traceback; final `ps --all` has no project containers.

- [ ] **Step 11: Commit Task 8**

```bash
git add .dockerignore docker/api.Dockerfile compose.yaml tests/integration/test_service_health.py
git commit -m "build: add local control-plane services"
```

### Task 9: CI Gates and Developer Runbook

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `README.md`
- Create: `docs/development.md`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Consumes: all M0 commands and services
- Produces: reproducible local instructions and CI fast/service lanes

- [ ] **Step 1: Write the README and development runbook as executable contracts**

Create a concise `README.md` identifying ShadowOps as pre-MVP and linking PRD, architecture, development plan, and `docs/development.md`. Do not claim working audit features.

Create `docs/development.md` with these exact flows:

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest tests/unit -v

docker compose up --build --detach --wait
docker compose exec api alembic -c alembic.ini upgrade head
uv run pytest tests/integration -v
docker compose down --volumes
```

- [ ] **Step 2: Create the CI workflow**

Create `.github/workflows/ci.yml` with:

- Trigger on pushes to `main` and pull requests.
- A `quality` job on Ubuntu that installs uv, Python 3.12, runs `uv sync --frozen`, Ruff check/format, Mypy, and unit tests.
- An `integration` job after `quality` that runs `docker compose up --build --detach --wait`, upgrades Alembic, runs `tests/integration`, always uploads Compose logs on failure, and always tears down with volumes.
- Concurrency cancellation by workflow and ref.
- No API secrets or live LLM tests.

- [ ] **Step 3: Run complete local quality verification**

Run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest tests/unit -v
docker compose config --quiet
```

Expected: every command exits 0 with no test failures, lint errors, format differences, or type errors.

- [ ] **Step 4: Run complete M0 integration verification**

Run:

```bash
docker compose up --build --detach --wait
docker compose exec api alembic -c alembic.ini upgrade head
uv run pytest tests/integration -v
uv run shadowops ping --api-url http://127.0.0.1:8000
docker compose down --volumes
```

Expected: integration tests pass; CLI prints `ready`; teardown exits 0.

- [ ] **Step 5: Re-read M0 requirements and record evidence**

Verify each M0 item in `docs/DEVELOPMENT_PLAN.md` against a file or command output. Record exact test counts, image digests, Docker versions, and command outcomes in the M0 handoff; do not insert unmeasured metrics.

- [ ] **Step 6: Commit Task 9**

```bash
git add .github/workflows/ci.yml README.md docs/development.md pyproject.toml uv.lock
git commit -m "ci: verify M0 control-plane foundation"
```

## Execution Checkpoints

- **Checkpoint A — after Task 3:** package, configuration, logging, and framework-free readiness reviewed before HTTP/framework wiring grows.
- **Checkpoint B — after Task 6:** API, dependency probes, control Alembic, and Celery configuration reviewed before CLI/containers.
- **Checkpoint C — after Task 8:** Compose integration and cleanup evidence reviewed before CI/documentation polish.
- **M0 exit — after Task 9:** invoke `superpowers:verification-before-completion`, then `superpowers:requesting-code-review`; only after review findings are resolved may M0 be described as complete.
