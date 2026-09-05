"""Fixed read-only evidence tools for the Reporter phase."""

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

from shadowops.agent.contracts import ReadOnlyToolName, ToolObservationV1
from shadowops.application.ports import UnitOfWork
from shadowops.evidence.store import LocalArtifactStore


class ReportingEvidenceGateway:
    tool_schema_version = "m6.reporting-tools.v1"

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        store: LocalArtifactStore,
        *,
        max_evidence_items: int = 64,
    ) -> None:
        self._uow_factory = uow_factory
        self._store = store
        self._max_evidence_items = max_evidence_items

    def gather(self, run_id: UUID) -> tuple[ToolObservationV1, ...]:
        return (
            self._static_findings(run_id),
            self._audit_plan(run_id),
            self._step_results(run_id),
            self._evidence(run_id),
            self._schema_diff(run_id),
        )

    def _static_findings(self, run_id: UUID) -> ToolObservationV1:
        with self._uow_factory() as uow:
            report = uow.static_reports.get_for_run(run_id)
        if report is None:
            raise RuntimeError("Reporter requires a persisted static report")
        return ToolObservationV1(
            tool_name=ReadOnlyToolName.GET_STATIC_FINDINGS,
            tool_version="2.0",
            data={
                "risk_level": report.risk_level,
                "findings": [item.model_dump(mode="json") for item in report.findings],
                "unsupported_reasons": [
                    item.model_dump(mode="json") for item in report.unsupported_reasons
                ],
            },
            evidence_ids=tuple(
                evidence_id for finding in report.findings for evidence_id in finding.evidence_ids
            ),
        )

    def _audit_plan(self, run_id: UUID) -> ToolObservationV1:
        with self._uow_factory() as uow:
            record = uow.agent_planning.get_plan_for_run(run_id)
        if record is None:
            raise RuntimeError("Reporter requires a persisted audit plan")
        return ToolObservationV1(
            tool_name=ReadOnlyToolName.GET_AUDIT_PLAN,
            tool_version="1.0",
            data=record.plan.model_dump(mode="json"),
            evidence_ids=tuple(
                sorted({item for step in record.plan.steps for item in step.evidence_refs})
            ),
        )

    def _step_results(self, run_id: UUID) -> ToolObservationV1:
        with self._uow_factory() as uow:
            lease = uow.sandbox.get_environment(run_id, 1)
            executions = [] if lease is None else uow.sandbox.list_executions(lease.environment.id)
        data = [
            {
                "id": str(item.id),
                "action": item.request.action.value,
                "status": item.result.status.value,
                "error_code": item.result.error_code,
                "current_revision": item.result.current_revision,
                "duration_ms": item.result.duration_ms,
                "coverage_gaps": list(item.result.coverage_gaps),
            }
            for item in executions
        ]
        return ToolObservationV1(
            tool_name=ReadOnlyToolName.GET_STEP_RESULT,
            tool_version="1.0",
            data={"executions": data},
            evidence_ids=tuple(f"runner:{item.id}" for item in executions),
        )

    def _evidence(self, run_id: UUID) -> ToolObservationV1:
        with self._uow_factory() as uow:
            items = uow.evidence.list_for_run(run_id)
        if len(items) > self._max_evidence_items:
            raise RuntimeError("Reporter evidence item budget exceeded")
        rendered: list[dict[str, Any]] = []
        for item in items:
            content: Any = None
            if item.media_type == "application/json":
                content = json.loads(self._store.get(item.artifact_uri))
            rendered.append(
                {
                    "id": str(item.id),
                    "kind": item.kind.value,
                    "scope": item.observation_scope,
                    "sha256": item.sha256,
                    "content": content,
                }
            )
        return ToolObservationV1(
            tool_name=ReadOnlyToolName.GET_EVIDENCE,
            tool_version="1.0",
            data={"items": rendered},
            evidence_ids=tuple(str(item.id) for item in items),
        )

    def _schema_diff(self, run_id: UUID) -> ToolObservationV1:
        evidence = self._evidence(run_id)
        items = evidence.data["items"]
        selected = [
            item for item in items if item["kind"] in {"SCHEMA_FINGERPRINT", "ROLLBACK_ROUNDTRIP"}
        ]
        return ToolObservationV1(
            tool_name=ReadOnlyToolName.INSPECT_SCHEMA_DIFF,
            tool_version="1.0",
            data={"observations": selected},
            evidence_ids=tuple(item["id"] for item in selected),
        )
