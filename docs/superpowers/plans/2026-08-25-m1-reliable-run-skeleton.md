# ShadowOps M1 Reliable Run Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create an audit run and reliably advance it from `QUEUED` to `COMPLETED` through a queryable, resumable, cancellable, database-backed walking skeleton that remains idempotent under duplicate HTTP requests and duplicate broker delivery.

**Architecture:** PostgreSQL is the source of truth. API commands atomically persist aggregate changes and outbox events; Celery maintenance tasks dispatch outbox rows and reconcile stale leases, while a Celery consumer claims and finalizes exactly one logical stage per message using optimistic run versions, unique step keys, leases, and fencing tokens. M1 stage handlers are explicitly identified no-ops and will be replaced by later milestones without changing the lifecycle protocol.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL 16, Celery 5, Redis 7, Pytest, Ruff, Mypy, Docker Compose.

**Spec:** `PRD.md` task state machine, `docs/ARCHITECTURE.md` sections 3, 4, 6, 10, and 12, and `docs/DEVELOPMENT_PLAN.md` M1.

## Global Constraints

- Keep the existing four long-lived services: API, worker, Control PostgreSQL, and Redis.
- PostgreSQL is authoritative; Redis and Celery result state are never used as run truth.
- `domain` imports no FastAPI, Celery, SQLAlchemy, Docker, or LLM SDK.
- Every business behavior follows RED -> observed expected failure -> GREEN -> refactor.
- M1 never reads or executes the supplied repository; repository safety starts in M2.
- Every M1 stage records handler version `m1.noop.v1` so successful orchestration cannot be mistaken for a real audit.
- HTTP idempotency uses a required `Idempotency-Key` header; a reused key with a different normalized request returns `409 IDEMPOTENCY_CONFLICT`.
- No Kubernetes, production database access, multi-database support, OAuth, tenancy, repository mutation, or multi-Agent orchestration.
- No completion metric is reported unless remeasured in the current branch.

---

## File Responsibility Map

```text
src/shadowops/domain/runs.py                 Framework-free states, transitions, run and step values
src/shadowops/domain/errors.py               Stable domain/application error types
src/shadowops/application/ports.py           Repository, UoW, publisher, clock, and stage ports
src/shadowops/application/runs.py             Create/get/cancel use cases
src/shadowops/application/run_execution.py    Claim, heartbeat, finalize, and failure use cases
src/shadowops/application/run_timeline.py     Ordered durable timeline queries
src/shadowops/persistence/database.py         Engine and session factory
src/shadowops/persistence/models.py           SQLAlchemy control-plane mappings
src/shadowops/persistence/repositories.py     Run, step, and outbox adapters
src/shadowops/persistence/uow.py              SQLAlchemy Unit of Work
src/shadowops/api/schemas/runs.py             Versioned public request/response DTOs
src/shadowops/api/routes/runs.py              Create/get/cancel/timeline endpoints
src/shadowops/api/routes/run_events.py        Database-backed resumable SSE
src/shadowops/worker/tasks.py                 Celery consumers and periodic maintenance tasks
src/shadowops/worker/outbox.py                Transactional outbox dispatcher
src/shadowops/worker/reconciler.py            Stale lease and lost-delivery recovery
migrations/control/versions/0002_*.py          M1 control schema
tests/unit/...                                 Framework-free behavior and boundary tests
tests/contract/...                             Public DTO compatibility tests
tests/integration/...                          PostgreSQL, broker, API, and worker tests
tests/e2e/test_reliable_run.py                 Full M1 lifecycle and restart checks
```

## Locked Interfaces

```python
class RunState(StrEnum): ...

class AuditRun:
    def transition(self, target: RunState, *, now: datetime) -> None: ...
    def request_cancel(self, *, expected_version: int, now: datetime) -> None: ...

class UnitOfWork(Protocol):
    runs: RunRepository
    steps: RunStepRepository
    outbox: OutboxRepository
    def commit(self) -> None: ...
    def rollback(self) -> None: ...

class RunExecutionService:
    def claim(self, event_id: UUID, worker_id: str) -> StageClaim | None: ...
    def heartbeat(self, claim: StageClaim) -> bool: ...
    def finalize(self, claim: StageClaim) -> AuditRun: ...

class OutboxDispatcher:
    def dispatch_batch(self, *, limit: int) -> int: ...

class RunReconciler:
    def reconcile_batch(self, *, limit: int) -> int: ...
```

### Task 1: Domain State Machine and Public Contracts

**Files:** create `src/shadowops/domain/{__init__,errors,runs}.py`, `src/shadowops/api/schemas/{__init__,runs}.py`, `tests/unit/domain/test_run_state.py`, and `tests/contract/api/test_run_schemas.py`.

**Produces:** full PRD state graph, `AuditRun`, stable error codes, `CreateAuditRunRequestV1`, `AuditRunViewV1`, `RunTimelineEventV1`, and `CancelAuditRunRequestV1`.

- [ ] Write focused tests for the legal main path, illegal jumps, terminal-state rejection, failure/cancel edges, request normalization, and invalid diff-ref combinations.
- [ ] Run `uv run pytest tests/unit/domain/test_run_state.py tests/contract/api/test_run_schemas.py -v`; verify failures are caused by missing behavior.
- [ ] Implement only enough framework-free domain and Pydantic code to pass.
- [ ] Re-run the focused tests and `uv run pytest tests/unit -v`.
- [ ] Refactor while green and commit `feat: define reliable run state machine`.

### Task 2: Control Schema, Repositories, and Unit of Work

**Files:** create the persistence package and `migrations/control/versions/0002_reliable_runs.py`; modify `migrations/control/env.py`; add `tests/integration/persistence/test_run_uow.py` and shared integration fixtures.

**Produces:** `audit_runs`, `run_steps`, `outbox_events`; SQLAlchemy mappings; repository ports/adapters; `SqlAlchemyUnitOfWork`.

- [ ] Write real-PostgreSQL tests for atomic run/outbox commit and rollback, idempotency uniqueness, optimistic conflicts, step/outbox uniqueness, and `SKIP LOCKED` claims.
- [ ] Start a clean test database, run migration `0002`, then run the focused tests and observe RED.
- [ ] Implement the migration and minimal persistence adapters.
- [ ] Verify focused integration tests, migration downgrade to `0001`, and upgrade back to head.
- [ ] Run unit tests and commit `feat: persist reliable audit runs`.

### Task 3: Idempotent Create and Get APIs

**Files:** create `src/shadowops/application/runs.py` and `src/shadowops/api/routes/runs.py`; modify `src/shadowops/api/app.py`; add unit API/application and PostgreSQL-backed idempotency tests.

**Produces:** `POST /api/v1/runs` and `GET /api/v1/runs/{run_id}`.

- [ ] Write RED tests for first create, same-key replay, same-key/different-payload conflict, concurrent replay, get, and not-found.
- [ ] Implement canonical request hashing and atomic creation of `QUEUED` plus the first outbox event.
- [ ] Return `202` with `Location`; map stable errors without exposing database exceptions.
- [ ] Verify focused tests and all unit/contract tests.
- [ ] Commit `feat: create and query audit runs`.

### Task 4: Idempotent Stage Execution and Celery Consumption

**Files:** create `src/shadowops/application/run_execution.py` and `src/shadowops/worker/tasks.py`; modify `src/shadowops/worker/celery_app.py`; add execution and task tests.

**Produces:** claim/heartbeat/finalize workflow, `m1.noop.v1` handler, and `shadowops.runs.process_event` Celery task.

- [ ] Write RED tests for one-state advancement, next-event atomicity, duplicate message no-op, stale version no-op, expired reclaim, and fencing-token rejection.
- [ ] Implement short claim/finalize transactions with handler execution outside the transaction.
- [ ] Configure late ack and worker-lost rejection without enabling a result backend.
- [ ] Verify the complete main chain reaches `COMPLETED` only via legal edges.
- [ ] Commit `feat: advance audit runs idempotently`.

### Task 5: Transactional Outbox Dispatcher

**Files:** create `src/shadowops/worker/outbox.py`; extend worker tasks/config; add unit and broker-backed dispatcher tests.

**Produces:** batched dispatcher using `FOR UPDATE SKIP LOCKED`, stable Celery task IDs, publish attempts, bounded backoff, and post-publish marking.

- [ ] Write RED tests for successful publish, per-row failure isolation, concurrent dispatcher claims, and publish-before-mark duplicate delivery.
- [ ] Implement the dispatcher and periodic Celery maintenance task.
- [ ] Verify duplicate broker delivery creates no duplicate logical step.
- [ ] Run worker/unit integration tests and commit `feat: dispatch run outbox events`.

### Task 6: Heartbeat, Lease, and Reconciler Recovery

**Files:** create `src/shadowops/worker/reconciler.py`; modify settings/environment docs; add unit and PostgreSQL/broker recovery tests.

**Produces:** configurable lease/heartbeat/recovery windows and `reconcile_stale_runs` periodic task.

- [ ] Write RED tests proving live leases are untouched, expired claims are republished/reclaimed, old workers are fenced out, stuck published events reopen, and retry exhaustion fails deterministically.
- [ ] Implement reconciliation without changing healthy runs.
- [ ] Verify a run created while the worker is stopped completes after worker restart.
- [ ] Commit `feat: recover stale run steps`.

### Task 7: Safe Cancellation

**Files:** modify domain/application/routes/execution; add cancellation tests at all layers.

**Produces:** `POST /api/v1/runs/{run_id}/cancel` with expected-version control and safe-checkpoint semantics.

- [ ] Write RED tests for queued cancel, in-flight cancel, repeated cancel, finalize race, stale expected version, and terminal conflict.
- [ ] Implement cancel request persistence and cancellation at claim/finalize checkpoints.
- [ ] Verify cancelled runs create no later stage event.
- [ ] Commit `feat: cancel audit runs safely`.

### Task 8: Timeline Query and Resumable SSE

**Files:** create `src/shadowops/application/run_timeline.py` and `src/shadowops/api/routes/run_events.py`; extend run routes/schemas; add timeline/SSE tests.

**Produces:** `GET /api/v1/runs/{id}/timeline` and `GET /api/v1/runs/{id}/events`.

- [ ] Write RED tests for initial `QUEUED`, version ordering, current attempt, terminal event, SSE framing, keepalive, disconnect, and `Last-Event-ID` resume.
- [ ] Implement database polling through short-lived UoWs and native `StreamingResponse`.
- [ ] Verify reconnect produces neither missing nor duplicate state versions.
- [ ] Commit `feat: expose reliable run timeline`.

### Task 9: Compose Reliability Slice, CI, and Documentation

**Files:** modify Compose/CI/docs; add a test-only Compose override, `tests/e2e/test_reliable_run.py`, and `docs/handoffs/M1.md`.

**Produces:** repeatable M1 API-to-worker demo and measured handoff.

- [ ] Write/extend black-box tests for full completion, duplicate HTTP/broker input, worker restart, API restart, stale lease recovery, cancellation, and SSE resume.
- [ ] Update worker command to run periodic maintenance inside the existing worker service; expose PostgreSQL/Redis only on test-only loopback ports.
- [ ] Run all quality, unit, contract, migration, integration, E2E, and cleanup checks from a clean volume.
- [ ] Record only the current run's measured results in `docs/handoffs/M1.md` and update README/development docs without claiming real audit behavior.
- [ ] Commit `test: verify reliable run lifecycle` and `docs: document M1 reliable skeleton`.

## Final Verification and Review

- [ ] Run `uv sync --frozen`.
- [ ] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [ ] Run `uv run mypy src`.
- [ ] Run `uv run pytest tests/unit tests/contract -v`.
- [ ] Run clean-volume migration, integration, and E2E checks; always tear down volumes.
- [ ] Run `git diff --check` and inspect `git status`.
- [ ] Use `superpowers:verification-before-completion`.
- [ ] Use `superpowers:requesting-code-review`; apply valid feedback through `superpowers:receiving-code-review`.
- [ ] Use `superpowers:finishing-a-development-branch` and present integration options.
