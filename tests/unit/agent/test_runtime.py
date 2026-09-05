from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from shadowops.agent.contracts import (
    PlannerRequestV1,
    ProviderResponseV1,
    ReadOnlyToolName,
    ToolObservationV1,
)
from shadowops.agent.llm import LLMProviderError
from shadowops.agent.provider import AgentProvider, FakeAgentProvider
from shadowops.agent.runtime import AgentPlanner

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
NOW = datetime(2026, 8, 28, tzinfo=UTC)


class StubGateway:
    tool_schema_version = "m3.read-only-tools.v1"

    def __init__(self, changed_revisions: list[str] | None = None) -> None:
        self._changed_revisions = changed_revisions or ["002"]

    def call(self, tool_name: ReadOnlyToolName, arguments: dict[str, Any]) -> ToolObservationV1:
        data: dict[str, Any] = {"arguments": arguments}
        evidence = ()
        if tool_name is ReadOnlyToolName.DISCOVER_MIGRATIONS:
            data = {"changed_revisions": self._changed_revisions}
            evidence = ("revision:002",)
        elif tool_name is ReadOnlyToolName.GET_STATIC_FINDINGS:
            evidence = ("finding:SOPS001",)
        return ToolObservationV1(
            tool_name=tool_name,
            tool_version="1.0",
            data=data,
            evidence_ids=evidence,
        )


class RecordingRepairProvider:
    provider_name = "recording"
    model_name = "repair-test"

    def __init__(self) -> None:
        self.requests: list[PlannerRequestV1] = []
        self._fallback = FakeAgentProvider()

    def invoke(self, request: PlannerRequestV1) -> ProviderResponseV1:
        self.requests.append(request)
        if len(self.requests) == 1:
            return ProviderResponseV1(text="{}")
        return self._fallback.invoke(request)


class RateLimitedProvider:
    provider_name = "openai"
    model_name = "configured-model"

    def invoke(self, request: PlannerRequestV1) -> ProviderResponseV1:
        raise LLMProviderError(
            "LLM_RATE_LIMITED", "Provider retry budget exhausted", retryable=True
        )


def _planner(provider: AgentProvider) -> AgentPlanner:
    ticks = iter(range(20))
    return AgentPlanner(
        provider,
        StubGateway(),  # type: ignore[arg-type]
        clock=lambda: NOW,
        monotonic=lambda: float(next(ticks)),
    )


def test_same_fake_input_produces_the_same_plan_and_trace_identity() -> None:
    first = _planner(FakeAgentProvider()).plan(RUN_ID)
    second = _planner(FakeAgentProvider()).plan(RUN_ID)

    assert first.plan == second.plan
    assert first.invocation == second.invocation
    assert first.tool_calls == second.tool_calls
    assert [call.tool_name for call in first.tool_calls] == [
        ReadOnlyToolName.DISCOVER_MIGRATIONS,
        ReadOnlyToolName.GET_STATIC_FINDINGS,
        ReadOnlyToolName.DESCRIBE_SHADOW_CAPABILITIES,
        ReadOnlyToolName.GET_TEST_DATA_PROFILE,
        ReadOnlyToolName.READ_REVISION,
    ]


def test_invalid_schema_is_repaired_once() -> None:
    provider = RecordingRepairProvider()

    result = _planner(provider).plan(RUN_ID)

    assert result.plan is not None
    assert result.invocation.status == "SUCCEEDED"
    assert result.invocation.repair_attempts == 1
    assert provider.requests[1].prior_output == "{}"
    assert provider.requests[1].prior_output_hash is not None
    assert provider.requests[1].repair_errors == (
        "SCHEMA_INVALID:missing:objective",
        "SCHEMA_INVALID:missing:steps",
    )


def test_invalid_schema_after_repair_returns_a_persistable_failure() -> None:
    result = _planner(FakeAgentProvider(outputs=["{}", "{}"])).plan(RUN_ID)

    assert result.plan is None
    assert result.invocation.status == "FAILED"
    assert result.invocation.repair_attempts == 1
    assert result.invocation.error_code == "PLAN_INVALID"
    assert result.invocation.error_detail is not None


def test_revision_reads_and_provider_output_are_bounded() -> None:
    ticks = iter(range(20))
    revisions = [f"{index:03}" for index in range(20)]
    planner = AgentPlanner(
        FakeAgentProvider(),
        StubGateway(revisions),  # type: ignore[arg-type]
        clock=lambda: NOW,
        monotonic=lambda: float(next(ticks)),
        max_revision_reads=2,
    )

    result = planner.plan(RUN_ID)

    assert len(result.tool_calls) == 6
    assert [call.observation.data["arguments"]["revision"] for call in result.tool_calls[-2:]] == [
        "000",
        "001",
    ]

    oversized = AgentPlanner(
        FakeAgentProvider(outputs=["x" * 11, "x" * 11]),
        StubGateway(),  # type: ignore[arg-type]
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        max_provider_output_bytes=10,
    ).plan(RUN_ID)
    assert oversized.plan is None
    assert oversized.invocation.error_detail == "OUTPUT_TOO_LARGE"


def test_provider_failure_is_returned_as_persistable_diagnostic() -> None:
    result = _planner(RateLimitedProvider()).plan(RUN_ID)

    assert result.plan is None
    assert result.invocation.status == "FAILED"
    assert result.invocation.error_code == "LLM_RATE_LIMITED"
    assert result.invocation.error_detail == "Provider retry budget exhausted"
