import json
from datetime import UTC, datetime
from uuid import UUID

from shadowops.agent.contracts import ProviderResponseV1, ReadOnlyToolName, ToolObservationV1
from shadowops.agent.llm import FakeLLMProvider, LLMRequestV1
from shadowops.reporting.runtime import RiskReporter

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
NOW = datetime(2026, 9, 5, tzinfo=UTC)


class StubGateway:
    tool_schema_version = "m6.reporting-tools.v1"

    def __init__(self, risk: str = "HIGH") -> None:
        evidence_items = [
            {"id": f"e{index}", "kind": kind, "scope": "observed_in_shadow", "content": {}}
            for index, kind in enumerate(
                ("SEED_SUMMARY", "SCHEMA_FINGERPRINT", "SMOKE_SUMMARY", "ROLLBACK_ROUNDTRIP")
            )
        ]
        self.observations = (
            ToolObservationV1(
                tool_name=ReadOnlyToolName.GET_STATIC_FINDINGS,
                tool_version="2.0",
                data={"risk_level": risk, "findings": []},
                evidence_ids=("static:1",),
            ),
            ToolObservationV1(
                tool_name=ReadOnlyToolName.GET_AUDIT_PLAN,
                tool_version="1.0",
                data={},
            ),
            ToolObservationV1(
                tool_name=ReadOnlyToolName.GET_STEP_RESULT,
                tool_version="1.0",
                data={"executions": []},
            ),
            ToolObservationV1(
                tool_name=ReadOnlyToolName.GET_EVIDENCE,
                tool_version="1.0",
                data={"items": evidence_items},
                evidence_ids=("e0", "e1", "e2", "e3"),
            ),
            ToolObservationV1(
                tool_name=ReadOnlyToolName.INSPECT_SCHEMA_DIFF,
                tool_version="1.0",
                data={"observations": []},
            ),
        )

    def gather(self, run_id: UUID) -> tuple[ToolObservationV1, ...]:
        assert run_id == RUN_ID
        return self.observations


class InvalidFactProvider:
    provider_name = "recorded"
    model_name = "recorded-invalid"

    def invoke(self, request: LLMRequestV1) -> ProviderResponseV1:
        return ProviderResponseV1(
            text=json.dumps(
                {
                    "schema_version": "1.0",
                    "summary": "Invalid citation",
                    "assessed_risk": "INFO",
                    "facts": [{"statement": "Unsupported fact", "evidence_ids": ["missing"]}],
                    "recommendations": [],
                    "unknowns": [],
                }
            )
        )


def test_reporter_cannot_lower_deterministic_high_risk() -> None:
    reporter = RiskReporter(
        FakeLLMProvider(),
        StubGateway(),  # type: ignore[arg-type]
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    result = reporter.report(RUN_ID)

    assert result.report.final_risk == "HIGH"
    assert result.report.requires_approval is True
    assert result.invocation.phase == "REPORTER"
    assert len(result.tool_calls) == 5


def test_unknown_fact_citation_exhausts_repair_and_uses_safe_fallback() -> None:
    result = RiskReporter(
        InvalidFactProvider(),
        StubGateway("INFO"),  # type: ignore[arg-type]
        clock=lambda: NOW,
    ).report(RUN_ID)

    assert result.invocation.status == "FAILED"
    assert result.invocation.error_code == "REPORT_INVALID"
    assert result.report.generated_by == "deterministic_fallback"
    assert result.report.draft.facts == ()
