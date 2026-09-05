"""Bounded Reporter runtime over fixed read-only evidence observations."""

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

from shadowops.agent.contracts import AgentInvocationV1, ProviderResponseV1, ToolCallV1
from shadowops.agent.llm import LLMProvider, LLMProviderError, LLMRequestV1
from shadowops.reporting.contracts import (
    ReporterDraftV1,
    ReportingResultV1,
    RiskReportV1,
    risk_report_hash,
)
from shadowops.reporting.gateway import ReportingEvidenceGateway
from shadowops.reporting.policy import PolicyEngine
from shadowops.rules.contracts import Severity


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class RiskReporter:
    prompt_version = "m6.reporter.v1"
    instructions = (
        "Return only the requested risk report draft JSON. Every factual statement must cite one "
        "or more evidence_ids from the input. Put uncited content only in recommendations or "
        "unknowns. Never lower deterministic risk, request approval, or propose executing changes."
    )

    def __init__(
        self,
        provider: LLMProvider,
        gateway: ReportingEvidenceGateway,
        *,
        policy: PolicyEngine | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        max_provider_output_bytes: int = 256 * 1024,
    ) -> None:
        self._provider = provider
        self._gateway = gateway
        self._policy = policy or PolicyEngine()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self._max_provider_output_bytes = max_provider_output_bytes

    def report(self, run_id: UUID) -> ReportingResultV1:
        started_at = self._clock()
        observations = self._gateway.gather(run_id)
        serialized = [item.model_dump(mode="json") for item in observations]
        input_hash = _hash(serialized)
        invocation_id = uuid5(NAMESPACE_URL, f"shadowops:{run_id}:REPORTER:{input_hash}")
        tool_calls = self._tool_calls(run_id, invocation_id, observations)
        available = frozenset(
            evidence_id for observation in observations for evidence_id in observation.evidence_ids
        )
        static_risk, failures, kinds, gaps = self._policy_inputs(observations)
        response: ProviderResponseV1 | None = None
        output_hash: str | None = None
        error_code: str | None = None
        error_detail: str | None = None
        draft: ReporterDraftV1 | None = None
        repair_errors: tuple[str, ...] = ()
        prior_output: str | None = None
        for attempt in range(2):
            request = LLMRequestV1(
                phase="REPORTER",
                instructions=self.instructions,
                input_payload={
                    "input_hash": input_hash,
                    "observations": serialized,
                    "repair_errors": repair_errors,
                    "prior_output": prior_output,
                },
                output_schema_name="ReporterDraftV1",
                output_schema=ReporterDraftV1.model_json_schema(),
                max_output_tokens=4_096,
            )
            try:
                response = self._provider.invoke(request)
                output_hash = hashlib.sha256(response.text.encode()).hexdigest()
                if len(response.text.encode()) > self._max_provider_output_bytes:
                    raise ValueError("OUTPUT_TOO_LARGE")
                candidate = ReporterDraftV1.model_validate(json.loads(response.text))
                unknown = sorted(
                    {
                        evidence_id
                        for fact in candidate.facts
                        for evidence_id in fact.evidence_ids
                        if evidence_id not in available
                    }
                )
                if unknown:
                    raise ValueError("UNKNOWN_EVIDENCE_REFERENCE")
                draft = candidate
                break
            except LLMProviderError as error:
                error_code, error_detail = error.code, error.detail
                break
            except (json.JSONDecodeError, ValidationError, ValueError) as error:
                repair_errors = self._safe_errors(error)
                prior_output = None if response is None else response.text[:262_144]
                if attempt == 1:
                    error_code = "REPORT_INVALID"
                    error_detail = ";".join(repair_errors)
        fallback = draft is None
        if draft is None:
            preliminary = self._policy.evaluate(
                static_risk=static_risk,
                model_risk="INFO",
                execution_failures=failures,
                evidence_kinds=kinds,
                coverage_gaps=gaps,
            )
            draft = ReporterDraftV1(
                summary=(
                    "A deterministic fallback report was produced because Reporter output failed."
                ),
                assessed_risk=preliminary.risk_level,
                recommendations=("Review persisted evidence and provider diagnostics manually.",),
                unknowns=(f"reporter_error:{error_code or 'REPORT_INVALID'}",),
            )
        decision = self._policy.evaluate(
            static_risk=static_risk,
            model_risk=draft.assessed_risk,
            execution_failures=failures,
            evidence_kinds=kinds,
            coverage_gaps=gaps,
        )
        invocation = AgentInvocationV1(
            id=invocation_id,
            run_id=run_id,
            phase="REPORTER",
            provider=self._provider.provider_name,
            model=self._provider.model_name,
            prompt_version=self.prompt_version,
            tool_schema_version=self._gateway.tool_schema_version,
            input_hash=input_hash,
            output_hash=output_hash,
            status="FAILED" if fallback else "SUCCEEDED",
            repair_attempts=1 if repair_errors else 0,
            error_code=error_code,
            error_detail=error_detail,
            provider_response_id=None if response is None else response.response_id,
            input_tokens=None if response is None else response.input_tokens,
            output_tokens=None if response is None else response.output_tokens,
            latency_ms=0 if response is None else response.latency_ms,
            started_at=started_at,
            completed_at=self._clock(),
        )
        now = self._clock()
        values: dict[str, object] = {
            "schema_version": "1.0",
            "id": str(uuid5(NAMESPACE_URL, f"shadowops:{run_id}:risk-report:{input_hash}")),
            "run_id": str(run_id),
            "invocation_id": str(invocation_id),
            "input_hash": input_hash,
            "final_risk": decision.risk_level,
            "requires_approval": decision.requires_approval,
            "policy_reasons": decision.reasons,
            "provider_metadata": {
                "provider": invocation.provider,
                "model": invocation.model,
                "status": invocation.status,
                "response_id": invocation.provider_response_id,
                "input_tokens": invocation.input_tokens,
                "output_tokens": invocation.output_tokens,
                "latency_ms": invocation.latency_ms,
                "error_code": invocation.error_code,
            },
            "draft": draft.model_dump(mode="json"),
            "evidence_ids": tuple(sorted(available)),
            "generated_by": "deterministic_fallback" if fallback else self._provider.provider_name,
            "created_at": now.isoformat(),
        }
        values["report_hash"] = risk_report_hash(values)
        report = RiskReportV1.model_validate(values)
        return ReportingResultV1(invocation=invocation, tool_calls=tool_calls, report=report)

    def _tool_calls(
        self,
        run_id: UUID,
        invocation_id: UUID,
        observations: tuple[Any, ...],
    ) -> tuple[ToolCallV1, ...]:
        calls = []
        for sequence, observation in enumerate(observations, 1):
            result_hash = _hash(observation.model_dump(mode="json"))
            calls.append(
                ToolCallV1(
                    id=uuid5(
                        NAMESPACE_URL,
                        f"shadowops:{run_id}:report-tool:{sequence}:{result_hash}",
                    ),
                    invocation_id=invocation_id,
                    run_id=run_id,
                    sequence=sequence,
                    tool_name=observation.tool_name,
                    tool_version=observation.tool_version,
                    arguments_hash=_hash({"run_id": str(run_id)}),
                    result_hash=result_hash,
                    duration_ms=0,
                    correlation_id=str(run_id),
                    observation=observation,
                )
            )
        return tuple(calls)

    @staticmethod
    def _policy_inputs(
        observations: tuple[Any, ...],
    ) -> tuple[Severity, tuple[str, ...], frozenset[str], tuple[str, ...]]:
        static_risk: Severity = "INFO"
        failures: list[str] = []
        kinds: set[str] = set()
        gaps: list[str] = []
        for observation in observations:
            if observation.tool_name.value == "get_static_findings":
                candidate = observation.data.get("risk_level")
                if candidate in {"INFO", "LOW", "MEDIUM", "HIGH"}:
                    static_risk = candidate
            elif observation.tool_name.value == "get_audit_plan":
                gaps.extend(observation.data.get("coverage_gaps") or [])
            elif observation.tool_name.value == "get_step_result":
                for execution in observation.data.get("executions", []):
                    if execution.get("status") == "FAILED":
                        failures.append(execution.get("error_code") or "UNKNOWN")
                    gaps.extend(execution.get("coverage_gaps") or [])
            elif observation.tool_name.value == "get_evidence":
                for item in observation.data.get("items", []):
                    kinds.add(item["kind"])
                    if item["kind"] == "COVERAGE_GAPS" and isinstance(item.get("content"), dict):
                        gaps.extend(item["content"].get("coverage_gaps") or [])
        return static_risk, tuple(failures), frozenset(kinds), tuple(sorted(set(gaps)))

    @staticmethod
    def _safe_errors(error: Exception) -> tuple[str, ...]:
        if isinstance(error, ValidationError):
            return tuple(
                sorted(
                    {
                        f"SCHEMA_INVALID:{item['type']}:{'.'.join(map(str, item['loc']))}"
                        for item in error.errors()
                    }
                )
            )
        return (str(error)[:100],)
