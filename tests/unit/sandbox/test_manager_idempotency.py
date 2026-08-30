import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from shadowops.sandbox.contracts import (
    BoundedArtifactV1,
    RunnerAction,
    RunnerExecutionV1,
    RunnerRequestV1,
    RunnerResultV1,
    RunnerStatus,
    ShadowEnvironmentLease,
    ShadowEnvironmentStatus,
    ShadowEnvironmentV1,
)
from shadowops.sandbox.docker_manager import DockerResourceManager

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
ENVIRONMENT_ID = UUID("22222222-2222-4222-8222-222222222222")
EXECUTION_ID = UUID("33333333-3333-4333-8333-333333333333")
NOW = datetime(2026, 8, 30, tzinfo=UTC)


class SandboxRepository:
    def __init__(self, lease: ShadowEnvironmentLease, execution: RunnerExecutionV1) -> None:
        self._lease = lease
        self._execution = execution

    def get_environment(self, run_id: UUID, generation: int) -> ShadowEnvironmentLease | None:
        assert run_id == RUN_ID
        assert generation == 1
        return self._lease

    def get_execution(self, environment_id: UUID, action: RunnerAction) -> RunnerExecutionV1 | None:
        assert environment_id == ENVIRONMENT_ID
        assert action is RunnerAction.APPLY_TARGET
        return self._execution


class UnitOfWork:
    def __init__(self, sandbox: SandboxRepository) -> None:
        self.sandbox = sandbox

    def __enter__(self) -> "UnitOfWork":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class ForbiddenDockerClient:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"Docker must not be called on idempotent replay: {name}")


def _artifact() -> BoundedArtifactV1:
    return BoundedArtifactV1(
        byte_count=0,
        sha256=hashlib.sha256(b"").hexdigest(),
        truncated=False,
        text="",
    )


def test_same_generation_action_returns_durable_result_without_reapply() -> None:
    request = RunnerRequestV1(
        action=RunnerAction.APPLY_TARGET,
        revision="002",
        statement_timeout_ms=30_000,
        output_limit_bytes=65_536,
    )
    environment = ShadowEnvironmentV1(
        id=ENVIRONMENT_ID,
        run_id=RUN_ID,
        generation=1,
        status=ShadowEnvironmentStatus.ACTIVE,
        postgres_container_id="postgres",
        network_id="network",
        volume_name="data",
        snapshot_volume_name="snapshot",
        postgres_image="postgres:16@sha256:" + "a" * 64,
        postgres_image_id="sha256:" + "a" * 64,
        runner_image="shadowops-runner:0.1.0",
        runner_image_id="sha256:" + "b" * 64,
        lease_expires_at=NOW + timedelta(minutes=10),
        created_at=NOW,
    )
    execution = RunnerExecutionV1(
        id=EXECUTION_ID,
        environment_id=ENVIRONMENT_ID,
        run_id=RUN_ID,
        generation=1,
        request=request,
        result=RunnerResultV1(
            action=RunnerAction.APPLY_TARGET,
            status=RunnerStatus.SUCCEEDED,
            current_revision="002",
            duration_ms=1,
            stdout=_artifact(),
            stderr=_artifact(),
        ),
        created_at=NOW,
    )
    repository = SandboxRepository(ShadowEnvironmentLease(environment, "password"), execution)
    manager = DockerResourceManager(
        lambda: UnitOfWork(repository),
        Path("/unused"),
        postgres_image=environment.postgres_image,
        runner_image=environment.runner_image,
        client=ForbiddenDockerClient(),
    )

    replay = manager.execute(RUN_ID, 1, request, checkpoint=lambda: None)

    assert replay == execution
