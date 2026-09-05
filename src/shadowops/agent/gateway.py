"""Allowlisted read-only tools over already trusted M2 evidence."""

from collections.abc import Callable
from typing import Any, Never
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from shadowops.agent.catalog import CAPABILITY_CATALOG
from shadowops.agent.contracts import ReadOnlyToolName, ToolObservationV1
from shadowops.application.ports import UnitOfWork
from shadowops.domain.errors import RepositoryInputError
from shadowops.repository.snapshot import SnapshotReader


class _NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _RunArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: UUID


class _RevisionArguments(_RunArguments):
    revision: str


class ReadOnlyToolGateway:
    tool_schema_version = "m3.read-only-tools.v1"

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        snapshot_reader: SnapshotReader,
        *,
        max_revision_bytes: int = 256 * 1024,
    ) -> None:
        self._uow_factory = uow_factory
        self._snapshot_reader = snapshot_reader
        self._max_revision_bytes = max_revision_bytes

    def call(self, tool_name: ReadOnlyToolName, arguments: dict[str, Any]) -> ToolObservationV1:
        if tool_name is ReadOnlyToolName.DISCOVER_MIGRATIONS:
            request = _RunArguments.model_validate(arguments)
            return self._discover(request.run_id)
        if tool_name is ReadOnlyToolName.READ_REVISION:
            request = _RevisionArguments.model_validate(arguments)
            return self._read_revision(request.run_id, request.revision)
        if tool_name is ReadOnlyToolName.GET_STATIC_FINDINGS:
            request = _RunArguments.model_validate(arguments)
            return self._static_findings(request.run_id)
        if tool_name is ReadOnlyToolName.DESCRIBE_SHADOW_CAPABILITIES:
            _NoArguments.model_validate(arguments)
            return ToolObservationV1(
                tool_name=tool_name,
                tool_version="1.0",
                data={
                    "capabilities": [item.model_dump(mode="json") for item in CAPABILITY_CATALOG]
                },
            )
        if tool_name is ReadOnlyToolName.GET_TEST_DATA_PROFILE:
            request = _RunArguments.model_validate(arguments)
            return ToolObservationV1(
                tool_name=tool_name,
                tool_version="1.0",
                data={
                    "run_id": str(request.run_id),
                    "profile": "fixture_manifest_or_bounded_synthetic",
                    "available": True,
                    "coverage_gap": (
                        "Exact unsupported types and production-data unknowns are determined "
                        "by the fixed Runner."
                    ),
                },
            )
        raise RepositoryInputError("TOOL_NOT_ALLOWED", "Tool is not allowlisted")

    def _discover(self, run_id: UUID) -> ToolObservationV1:
        with self._uow_factory() as uow:
            graph = uow.revision_graphs.get_for_run(run_id)
        if graph is None:
            self._missing()
        return ToolObservationV1(
            tool_name=ReadOnlyToolName.DISCOVER_MIGRATIONS,
            tool_version="1.0",
            data=graph.model_dump(mode="json"),
            evidence_ids=tuple(node.evidence_id for node in graph.nodes),
        )

    def _read_revision(self, run_id: UUID, revision: str) -> ToolObservationV1:
        with self._uow_factory() as uow:
            graph = uow.revision_graphs.get_for_run(run_id)
            snapshot = uow.snapshots.get_for_run(run_id)
        if graph is None or snapshot is None:
            self._missing()
        node = next((item for item in graph.nodes if item.revision == revision), None)
        if node is None:
            raise RepositoryInputError("TOOL_ARGUMENT_INVALID", "Revision is not in the run graph")
        source = self._snapshot_reader.read_text(
            snapshot.id, node.relative_path, max_bytes=self._max_revision_bytes
        )
        return ToolObservationV1(
            tool_name=ReadOnlyToolName.READ_REVISION,
            tool_version="1.0",
            data={
                "revision": revision,
                "relative_path": node.relative_path,
                "content_hash": source.content_hash,
                "text": source.text,
            },
            evidence_ids=(node.evidence_id, source.evidence_id),
        )

    def _static_findings(self, run_id: UUID) -> ToolObservationV1:
        with self._uow_factory() as uow:
            report = uow.static_reports.get_for_run(run_id)
        if report is None:
            self._missing()
        return ToolObservationV1(
            tool_name=ReadOnlyToolName.GET_STATIC_FINDINGS,
            tool_version="1.0",
            data={
                "risk_level": report.risk_level,
                "findings": [item.model_dump(mode="json") for item in report.findings],
                "unsupported_reasons": [
                    item.model_dump(mode="json") for item in report.unsupported_reasons
                ],
            },
            evidence_ids=tuple(
                evidence for finding in report.findings for evidence in finding.evidence_ids
            ),
        )

    @staticmethod
    def _missing() -> Never:
        raise RepositoryInputError("AGENT_CONTEXT_NOT_READY", "Agent context is not ready")
