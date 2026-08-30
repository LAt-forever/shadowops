"""Fixed Docker adapter for one PostgreSQL 16 shadow environment per generation."""

import hashlib
import io
import json
import re
import secrets
import tarfile
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from docker.errors import APIError, DockerException, ImageNotFound  # type: ignore[import-not-found]

import docker
from shadowops.application.ports import UnitOfWork
from shadowops.domain.errors import RepositoryInputError
from shadowops.repository.contracts import RepoSnapshotV1
from shadowops.sandbox.contracts import (
    BoundedArtifactV1,
    RunnerExecutionV1,
    RunnerRequestV1,
    RunnerResultV1,
    ShadowEnvironmentLease,
    ShadowEnvironmentStatus,
    ShadowEnvironmentV1,
)

_MANAGED_LABEL = "shadowops.managed"
_RUN_LABEL = "shadowops.run_id"
_GENERATION_LABEL = "shadowops.generation"
_LEASE_LABEL = "shadowops.lease_expires_epoch"
_ROLE_LABEL = "shadowops.role"
_SECRET_PATTERN = re.compile(r"(?i)(password|token|secret)=([^\s&]+)")


class DockerResourceManager:
    """Trusted adapter; all Docker arguments are selected by service configuration."""

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        artifact_root: Path,
        *,
        postgres_image: str,
        runner_image: str,
        lease_duration: timedelta = timedelta(minutes=10),
        readiness_timeout_seconds: int = 30,
        execution_timeout_seconds: int = 210,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
        client: Any | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._artifact_root = artifact_root
        self._postgres_image = postgres_image
        self._runner_image = runner_image
        self._lease_duration = lease_duration
        self._readiness_timeout_seconds = readiness_timeout_seconds
        self._execution_timeout_seconds = execution_timeout_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory or uuid4
        self._client = client

    def _docker(self) -> Any:
        if self._client is None:
            try:
                self._client = docker.from_env()  # type: ignore[attr-defined]
                self._client.ping()
            except DockerException as exc:
                raise RepositoryInputError(
                    "SANDBOX_UNAVAILABLE", "Docker Engine is unavailable to the trusted Worker"
                ) from exc
        return self._client

    def provision(
        self,
        run_id: UUID,
        generation: int,
        snapshot: RepoSnapshotV1,
        *,
        checkpoint: Callable[[], None],
    ) -> ShadowEnvironmentLease:
        existing = self.get_environment(run_id, generation)
        if existing is not None:
            if existing.environment.status is not ShadowEnvironmentStatus.ACTIVE:
                raise RepositoryInputError(
                    "SANDBOX_UNAVAILABLE", "The requested shadow generation was already finalized"
                )
            return existing
        client = self._docker()
        now = self._clock()
        lease = now + self._lease_duration
        stem = f"shadowops-{run_id.hex[:12]}-{generation}"
        labels = self._labels(run_id, generation, lease)
        password = secrets.token_urlsafe(24)
        try:
            postgres_image = client.images.get(self._postgres_image)
            runner_image = client.images.get(self._runner_image)
            self._validate_image_id(postgres_image.id)
            self._validate_image_id(runner_image.id)
            network = client.networks.create(
                f"{stem}-network",
                internal=True,
                check_duplicate=True,
                labels=labels | {_ROLE_LABEL: "network"},
            )
            data_volume = client.volumes.create(
                name=f"{stem}-pgdata", labels=labels | {_ROLE_LABEL: "postgres-data"}
            )
            snapshot_volume = client.volumes.create(
                name=f"{stem}-snapshot", labels=labels | {_ROLE_LABEL: "snapshot"}
            )
            self._populate_snapshot(snapshot, snapshot_volume.name, runner_image.id, labels)
            checkpoint()
            postgres = client.containers.create(
                postgres_image.id,
                name=f"{stem}-postgres",
                detach=True,
                environment={
                    "POSTGRES_DB": "shadow",
                    "POSTGRES_USER": "shadow",
                    "POSTGRES_PASSWORD": password,
                },
                labels=labels | {_ROLE_LABEL: "postgres"},
                hostname="shadow-postgres",
                volumes={data_volume.name: {"bind": "/var/lib/postgresql/data", "mode": "rw"}},
                mem_limit="384m",
                nano_cpus=750_000_000,
                pids_limit=128,
            )
            network.connect(postgres, aliases=["shadow-postgres"])
            postgres.start()
            self._wait_ready(postgres, checkpoint)
            environment = ShadowEnvironmentV1(
                id=self._uuid_factory(),
                run_id=run_id,
                generation=generation,
                status=ShadowEnvironmentStatus.ACTIVE,
                postgres_container_id=postgres.id,
                network_id=network.id,
                volume_name=data_volume.name,
                snapshot_volume_name=snapshot_volume.name,
                postgres_image=self._postgres_image,
                postgres_image_id=postgres_image.id,
                runner_image=self._runner_image,
                runner_image_id=runner_image.id,
                lease_expires_at=lease,
                created_at=now,
            )
            with self._uow_factory() as uow:
                durable = uow.sandbox.create_or_get_environment(
                    environment, database_password=password
                )
                uow.commit()
            return durable
        except RepositoryInputError:
            self._cleanup_by_labels(run_id, generation)
            raise
        except (APIError, DockerException, ImageNotFound, OSError) as exc:
            self._cleanup_by_labels(run_id, generation)
            raise RepositoryInputError(
                "SANDBOX_UNAVAILABLE", "The fixed shadow environment could not be provisioned"
            ) from exc

    def execute(
        self,
        run_id: UUID,
        generation: int,
        request: RunnerRequestV1,
        *,
        checkpoint: Callable[[], None],
    ) -> RunnerExecutionV1:
        lease = self._require_active(run_id, generation)
        with self._uow_factory() as uow:
            existing = uow.sandbox.get_execution(lease.environment.id, request.action)
        if existing is not None:
            return existing
        client = self._docker()
        labels = self._labels(run_id, generation, lease.environment.lease_expires_at) | {
            _ROLE_LABEL: "runner"
        }
        name = f"shadowops-{run_id.hex[:12]}-{generation}-{request.action.value.lower()}"
        container = None
        try:
            network = client.networks.get(lease.environment.network_id)
            container = client.containers.create(
                lease.environment.runner_image_id,
                name=name,
                detach=True,
                user="10002:10002",
                read_only=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                network=network.name,
                labels=labels,
                environment={
                    "SHADOWOPS_DB_USER": "shadow",
                    "SHADOWOPS_DB_PASSWORD": lease.database_password,
                    "SHADOWOPS_DB_NAME": "shadow",
                    "SHADOWOPS_RUNNER_REQUEST": request.model_dump_json(),
                },
                volumes={
                    lease.environment.snapshot_volume_name: {
                        "bind": "/repository",
                        "mode": "ro",
                    }
                },
                tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=16m"},
                mem_limit="256m",
                nano_cpus=500_000_000,
                pids_limit=64,
            )
            container.start()
            try:
                wait_result = container.wait(timeout=self._execution_timeout_seconds)
            except Exception as exc:
                container.kill()
                raise RepositoryInputError(
                    "CHECK_TIMEOUT", "The fixed Runner exceeded its wall-clock budget"
                ) from exc
            raw = container.logs(stdout=True, stderr=True, tail=1).decode("utf-8", errors="replace")
            if int(wait_result.get("StatusCode", 1)) != 0 and not raw:
                raise RepositoryInputError("MIGRATION_FAILED", "The fixed Runner exited abnormally")
            result = self._redacted_result(raw, lease.database_password)
            execution = RunnerExecutionV1(
                id=self._uuid_factory(),
                environment_id=lease.environment.id,
                run_id=run_id,
                generation=generation,
                request=request,
                result=result,
                created_at=self._clock(),
            )
            checkpoint()
            with self._uow_factory() as uow:
                durable = uow.sandbox.create_or_get_execution(execution)
                uow.commit()
            return durable
        except RepositoryInputError:
            raise
        except (APIError, DockerException, ValueError, json.JSONDecodeError) as exc:
            raise RepositoryInputError(
                "SANDBOX_UNAVAILABLE", "The fixed Runner result could not be collected"
            ) from exc
        finally:
            if container is not None:
                with suppress(APIError, DockerException):
                    container.remove(force=True)

    def finalize_run(self, run_id: UUID, generation: int = 1) -> bool:
        lease = self.get_environment(run_id, generation)
        if lease is None:
            return True
        if lease.environment.status is ShadowEnvironmentStatus.CLEANED:
            return True
        cleaned = self._cleanup_by_labels(run_id, generation)
        now = self._clock()
        with self._uow_factory() as uow:
            uow.sandbox.set_environment_status(
                lease.environment.id,
                status=(
                    ShadowEnvironmentStatus.CLEANED
                    if cleaned
                    else ShadowEnvironmentStatus.CLEANUP_FAILED
                ),
                cleaned_at=now if cleaned else None,
            )
            uow.commit()
        if not cleaned:
            raise RepositoryInputError(
                "CLEANUP_FAILED", "One or more labeled shadow resources could not be removed"
            )
        return True

    def sweep_expired(self) -> int:
        client = self._docker()
        now_epoch = int(self._clock().timestamp())
        pairs: set[tuple[UUID, int]] = set()
        resources = [
            *client.containers.list(all=True, filters={"label": f"{_MANAGED_LABEL}=true"}),
            *client.networks.list(filters={"label": f"{_MANAGED_LABEL}=true"}),
            *client.volumes.list(filters={"label": f"{_MANAGED_LABEL}=true"}),
        ]
        for resource in resources:
            labels = resource.attrs.get("Labels") or resource.attrs.get("Config", {}).get(
                "Labels", {}
            )
            try:
                if int(labels[_LEASE_LABEL]) <= now_epoch:
                    pairs.add((UUID(labels[_RUN_LABEL]), int(labels[_GENERATION_LABEL])))
            except (KeyError, TypeError, ValueError):
                continue
        cleaned = 0
        for run_id, generation in pairs:
            if self._cleanup_by_labels(run_id, generation):
                cleaned += 1
                lease = self.get_environment(run_id, generation)
                if lease is not None:
                    with self._uow_factory() as uow:
                        uow.sandbox.set_environment_status(
                            lease.environment.id,
                            status=ShadowEnvironmentStatus.CLEANED,
                            cleaned_at=self._clock(),
                        )
                        uow.commit()
        return cleaned

    def get_environment(self, run_id: UUID, generation: int) -> ShadowEnvironmentLease | None:
        with self._uow_factory() as uow:
            return uow.sandbox.get_environment(run_id, generation)

    def _require_active(self, run_id: UUID, generation: int) -> ShadowEnvironmentLease:
        lease = self.get_environment(run_id, generation)
        if lease is None or lease.environment.status is not ShadowEnvironmentStatus.ACTIVE:
            raise RepositoryInputError("SANDBOX_UNAVAILABLE", "No active shadow generation exists")
        return lease

    def _populate_snapshot(
        self,
        snapshot: RepoSnapshotV1,
        volume_name: str,
        runner_image_id: str,
        labels: dict[str, str],
    ) -> None:
        source = self._artifact_root / "snapshots" / snapshot.content_hash / "tree"
        if not source.is_dir():
            raise RepositoryInputError("SNAPSHOT_INTEGRITY_FAILED", "Snapshot tree is unavailable")
        client = self._docker()
        loader = client.containers.create(
            runner_image_id,
            entrypoint=["/bin/sh", "-c"],
            command=["sleep 30"],
            user="0:0",
            network_mode="none",
            labels=labels | {_ROLE_LABEL: "snapshot-loader"},
            volumes={volume_name: {"bind": "/repository", "mode": "rw"}},
        )
        try:
            loader.start()
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w") as bundle:
                for path in sorted(source.rglob("*")):
                    bundle.add(path, arcname=path.relative_to(source), recursive=False)
            archive.seek(0)
            if not loader.put_archive("/repository", archive.getvalue()):
                raise RepositoryInputError(
                    "SNAPSHOT_INTEGRITY_FAILED", "Snapshot could not be copied into its volume"
                )
        finally:
            with suppress(APIError, DockerException):
                loader.remove(force=True)

    def _wait_ready(self, container: Any, checkpoint: Callable[[], None]) -> None:
        deadline = time.monotonic() + self._readiness_timeout_seconds
        while time.monotonic() < deadline:
            checkpoint()
            result = container.exec_run(
                ["pg_isready", "-U", "shadow", "-d", "shadow", "-h", "127.0.0.1"]
            )
            if result.exit_code == 0:
                return
            time.sleep(0.25)
        raise RepositoryInputError("SANDBOX_UNAVAILABLE", "Shadow PostgreSQL readiness timed out")

    def _cleanup_by_labels(self, run_id: UUID, generation: int) -> bool:
        try:
            client = self._docker()
            label_filter = [
                f"{_MANAGED_LABEL}=true",
                f"{_RUN_LABEL}={run_id}",
                f"{_GENERATION_LABEL}={generation}",
            ]
            for attempt in range(5):
                for container in client.containers.list(all=True, filters={"label": label_filter}):
                    with suppress(APIError, DockerException):
                        container.remove(force=True, v=False)
                for network in client.networks.list(filters={"label": label_filter}):
                    with suppress(APIError, DockerException):
                        network.remove()
                for volume in client.volumes.list(filters={"label": label_filter}):
                    with suppress(APIError, DockerException):
                        volume.remove(force=True)
                remaining = (
                    client.containers.list(all=True, filters={"label": label_filter})
                    or client.networks.list(filters={"label": label_filter})
                    or client.volumes.list(filters={"label": label_filter})
                )
                if not remaining:
                    return True
                if attempt < 4:
                    time.sleep(0.1 * (attempt + 1))
            return False
        except (RepositoryInputError, APIError, DockerException):
            return False

    @staticmethod
    def _labels(run_id: UUID, generation: int, lease: datetime) -> dict[str, str]:
        return {
            _MANAGED_LABEL: "true",
            _RUN_LABEL: str(run_id),
            _GENERATION_LABEL: str(generation),
            _LEASE_LABEL: str(int(lease.timestamp())),
        }

    @staticmethod
    def _validate_image_id(image_id: str) -> None:
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
            raise RepositoryInputError(
                "SANDBOX_UNAVAILABLE", "An allowlisted image has no content-addressed image ID"
            )

    @staticmethod
    def _redacted_result(raw: str, password: str) -> RunnerResultV1:
        payload = json.loads(raw.strip().splitlines()[-1])
        result = RunnerResultV1.model_validate(payload)

        def redact(value: str) -> str:
            value = value.replace(password, "[REDACTED]")
            return _SECRET_PATTERN.sub(r"\1=[REDACTED]", value)

        def artifact(item: BoundedArtifactV1) -> BoundedArtifactV1:
            text = redact(item.text)
            encoded = text.encode()
            return BoundedArtifactV1(
                byte_count=len(encoded),
                sha256=hashlib.sha256(encoded).hexdigest(),
                truncated=item.truncated,
                text=text,
            )

        return result.model_copy(
            update={
                "error_detail": redact(result.error_detail) if result.error_detail else None,
                "stdout": artifact(result.stdout),
                "stderr": artifact(result.stderr),
            }
        )
