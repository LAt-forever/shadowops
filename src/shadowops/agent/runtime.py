"""Bounded Plan -> read-only tools -> validate -> one repair runtime."""

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

from shadowops.agent.contracts import (
    AgentInvocationV1,
    AuditPlanRecordV1,
    AuditPlanV1,
    PlannerRequestV1,
    PlanningResultV1,
    ProviderResponseV1,
    ReadOnlyToolName,
    ToolCallV1,
    ToolObservationV1,
)
from shadowops.agent.gateway import ReadOnlyToolGateway
from shadowops.agent.llm import LLMProviderError
from shadowops.agent.provider import AgentProvider
from shadowops.agent.validator import PlanValidationError, PlanValidator

_MAX_REPAIR_OUTPUT_CHARS = 262_144


def _hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


class AgentPlanner:
    prompt_version = "m3.planner.v1"

    def __init__(
        self,
        provider: AgentProvider,
        gateway: ReadOnlyToolGateway,
        *,
        validator: PlanValidator | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        max_revision_reads: int = 16,
        max_provider_output_bytes: int = 256 * 1024,
    ) -> None:
        if max_revision_reads < 0 or max_provider_output_bytes < 1:
            raise ValueError("Agent planning budgets must be positive")
        self._provider = provider
        self._gateway = gateway
        self._validator = validator or PlanValidator()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self._max_revision_reads = max_revision_reads
        self._max_provider_output_bytes = max_provider_output_bytes

    def plan(self, run_id: UUID) -> PlanningResultV1:
        started_at = self._clock()
        observations, calls_without_invocation = self._gather_context(run_id)
        input_hash = _hash([item.model_dump(mode="json") for item in observations])
        invocation_id = uuid5(NAMESPACE_URL, f"shadowops:{run_id}:PLANNER:{input_hash}")
        tool_calls = tuple(
            call.model_copy(update={"invocation_id": invocation_id})
            for call in calls_without_invocation
        )
        errors: tuple[str, ...] = ()
        prior_output_hash: str | None = None
        prior_output: str | None = None
        final_output_hash: str | None = None
        for attempt in range(2):
            request = PlannerRequestV1(
                run_id=run_id,
                prompt_version=self.prompt_version,
                tool_schema_version=self._gateway.tool_schema_version,
                input_hash=input_hash,
                observations=observations,
                repair_errors=errors,
                prior_output_hash=prior_output_hash,
                prior_output=prior_output,
            )
            try:
                response = self._provider.invoke(request)
            except LLMProviderError as error:
                invocation = self._invocation(
                    invocation_id,
                    run_id,
                    input_hash,
                    None,
                    "FAILED",
                    attempt,
                    started_at,
                    error_code=error.code,
                    error_detail=error.detail,
                )
                return PlanningResultV1(invocation=invocation, tool_calls=tool_calls)
            final_output_hash = hashlib.sha256(response.text.encode()).hexdigest()
            try:
                if len(response.text.encode()) > self._max_provider_output_bytes:
                    raise PlanValidationError(("OUTPUT_TOO_LARGE",))
                plan = self._validator.validate(
                    AuditPlanV1.model_validate(json.loads(response.text))
                )
            except (json.JSONDecodeError, ValidationError, PlanValidationError) as error:
                errors = self._safe_errors(error)
                prior_output_hash = final_output_hash
                prior_output = response.text[:_MAX_REPAIR_OUTPUT_CHARS]
                if attempt == 0:
                    continue
                invocation = self._invocation(
                    invocation_id,
                    run_id,
                    input_hash,
                    final_output_hash,
                    "FAILED",
                    1,
                    started_at,
                    error_code="PLAN_INVALID",
                    error_detail=";".join(errors),
                    response=response,
                )
                return PlanningResultV1(invocation=invocation, tool_calls=tool_calls)
            invocation = self._invocation(
                invocation_id,
                run_id,
                input_hash,
                final_output_hash,
                "SUCCEEDED",
                attempt,
                started_at,
                response=response,
            )
            record = AuditPlanRecordV1(
                id=uuid5(NAMESPACE_URL, f"shadowops:{run_id}:plan:{input_hash}"),
                run_id=run_id,
                invocation_id=invocation_id,
                input_hash=input_hash,
                plan=plan,
                created_at=self._clock(),
            )
            return PlanningResultV1(invocation=invocation, tool_calls=tool_calls, plan=record)
        raise AssertionError("Planner repair budget loop did not return")

    def _gather_context(
        self, run_id: UUID
    ) -> tuple[tuple[ToolObservationV1, ...], tuple[ToolCallV1, ...]]:
        calls: list[ToolCallV1] = []
        observations: list[ToolObservationV1] = []

        def call(tool_name: ReadOnlyToolName, arguments: dict[str, Any]) -> ToolObservationV1:
            started = self._monotonic()
            observation = self._gateway.call(tool_name, arguments)
            duration_ms = max(0, round((self._monotonic() - started) * 1000))
            sequence = len(calls) + 1
            arguments_hash = _hash(arguments)
            result_hash = _hash(observation.model_dump(mode="json"))
            calls.append(
                ToolCallV1(
                    id=uuid5(
                        NAMESPACE_URL,
                        f"shadowops:{run_id}:tool:{sequence}:{tool_name.value}:{arguments_hash}",
                    ),
                    invocation_id=UUID(int=0),
                    run_id=run_id,
                    sequence=sequence,
                    tool_name=tool_name,
                    tool_version=observation.tool_version,
                    arguments_hash=arguments_hash,
                    result_hash=result_hash,
                    duration_ms=duration_ms,
                    correlation_id=str(run_id),
                    observation=observation,
                )
            )
            observations.append(observation)
            return observation

        discovery = call(ReadOnlyToolName.DISCOVER_MIGRATIONS, {"run_id": str(run_id)})
        call(ReadOnlyToolName.GET_STATIC_FINDINGS, {"run_id": str(run_id)})
        call(ReadOnlyToolName.DESCRIBE_SHADOW_CAPABILITIES, {})
        call(ReadOnlyToolName.GET_TEST_DATA_PROFILE, {"run_id": str(run_id)})
        changed = discovery.data.get("changed_revisions", [])
        if isinstance(changed, list):
            revisions = sorted(item for item in changed if isinstance(item, str))
            for revision in revisions[: self._max_revision_reads]:
                call(
                    ReadOnlyToolName.READ_REVISION,
                    {"run_id": str(run_id), "revision": revision},
                )
        return tuple(observations), tuple(calls)

    def _invocation(
        self,
        invocation_id: UUID,
        run_id: UUID,
        input_hash: str,
        output_hash: str | None,
        status: Literal["SUCCEEDED", "FAILED"],
        repairs: int,
        started_at: datetime,
        *,
        error_code: str | None = None,
        error_detail: str | None = None,
        response: ProviderResponseV1 | None = None,
    ) -> AgentInvocationV1:
        return AgentInvocationV1(
            id=invocation_id,
            run_id=run_id,
            provider=self._provider.provider_name,
            model=self._provider.model_name,
            prompt_version=self.prompt_version,
            tool_schema_version=self._gateway.tool_schema_version,
            input_hash=input_hash,
            output_hash=output_hash,
            status=status,
            repair_attempts=repairs,
            error_code=error_code,
            error_detail=error_detail,
            provider_response_id=None if response is None else response.response_id,
            input_tokens=None if response is None else response.input_tokens,
            output_tokens=None if response is None else response.output_tokens,
            latency_ms=0 if response is None else response.latency_ms,
            started_at=started_at,
            completed_at=self._clock(),
        )

    @staticmethod
    def _safe_errors(error: Exception) -> tuple[str, ...]:
        if isinstance(error, PlanValidationError):
            return error.errors
        if isinstance(error, ValidationError):
            return tuple(
                sorted(
                    {
                        f"SCHEMA_INVALID:{item['type']}:{'.'.join(map(str, item['loc']))}"
                        for item in error.errors()
                    }
                )
            )
        return ("JSON_INVALID",)
