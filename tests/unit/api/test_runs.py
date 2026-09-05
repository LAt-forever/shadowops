from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from shadowops.agent.contracts import (
    AuditPlanRecordV1,
    AuditPlanV1,
    CapabilityName,
    PlanStepV1,
)
from shadowops.api.app import create_app
from shadowops.application.readiness import ReadinessService
from shadowops.domain.errors import (
    AuditPlanNotReadyError,
    IdempotencyConflictError,
    RiskReportNotReadyError,
    RunNotFoundError,
    StaticReportNotReadyError,
    TerminalRunError,
)
from shadowops.domain.runs import AuditRun, RunState
from shadowops.reporting.contracts import ReporterDraftV1, RiskReportV1, risk_report_hash
from shadowops.rules.contracts import RevisionGraphSummaryV1, StaticReportV1

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
NOW = datetime(2026, 8, 25, 2, 0, tzinfo=UTC)


class StubRunService:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def create(self, *args: object, **kwargs: object) -> AuditRun:
        if self._error is not None:
            raise self._error
        return _run()

    def get(self, *args: object, **kwargs: object) -> AuditRun:
        if self._error is not None:
            raise self._error
        return _run()

    def cancel(self, *args: object, **kwargs: object) -> AuditRun:
        if self._error is not None:
            raise self._error
        run = _run()
        run.cancel_requested_at = NOW
        return run


class StubStaticReportService:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def get(self, *args: object, **kwargs: object) -> StaticReportV1:
        if self._error is not None:
            raise self._error
        return StaticReportV1(
            id=UUID("22222222-2222-4222-8222-222222222222"),
            run_id=RUN_ID,
            snapshot_id=UUID("33333333-3333-4333-8333-333333333333"),
            snapshot_hash="a" * 64,
            diff_mode="WORKING_TREE",
            head_commit="b" * 40,
            revision_graph=RevisionGraphSummaryV1(
                supported=True,
                heads=("001",),
                target_chain=("001",),
                changed_revisions=(),
            ),
            findings=(),
            unsupported_reasons=(),
            risk_level="INFO",
            created_at=NOW,
        )


class StubPlanService:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def get(self, *args: object, **kwargs: object) -> AuditPlanRecordV1:
        if self._error is not None:
            raise self._error
        return AuditPlanRecordV1(
            id=UUID("44444444-4444-4444-8444-444444444444"),
            run_id=RUN_ID,
            invocation_id=UUID("55555555-5555-4555-8555-555555555555"),
            input_hash="c" * 64,
            plan=AuditPlanV1(
                objective="Audit migrations",
                steps=(
                    PlanStepV1(
                        id="provision",
                        capability=CapabilityName.PROVISION_SHADOW_DB,
                        timeout_seconds=60,
                        required=True,
                        reason="Create isolated resources",
                    ),
                ),
            ),
            created_at=NOW,
        )


class StubRiskReportService:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def get(self, *args: object, **kwargs: object) -> RiskReportV1:
        if self._error is not None:
            raise self._error
        values = {
            "schema_version": "1.0",
            "id": str(UUID("66666666-6666-4666-8666-666666666666")),
            "run_id": str(RUN_ID),
            "invocation_id": str(UUID("77777777-7777-4777-8777-777777777777")),
            "input_hash": "d" * 64,
            "final_risk": "MEDIUM",
            "requires_approval": False,
            "policy_reasons": ["static_risk:INFO", "shadow_coverage_gaps_present"],
            "provider_metadata": {
                "provider": "fake",
                "model": "shadowops-reference-reporter-v1",
                "status": "SUCCEEDED",
                "response_id": None,
                "input_tokens": None,
                "output_tokens": None,
                "latency_ms": 0,
                "error_code": None,
            },
            "draft": ReporterDraftV1(summary="Grounded report", assessed_risk="MEDIUM").model_dump(
                mode="json"
            ),
            "evidence_ids": [],
            "generated_by": "fake",
            "created_at": NOW.isoformat(),
        }
        values["report_hash"] = risk_report_hash(values)
        return RiskReportV1.model_validate(values)


def _run() -> AuditRun:
    return AuditRun(
        id=RUN_ID,
        state=RunState.QUEUED,
        version=1,
        repository_path="projects/demo",
        idempotency_key="request-1",
        request_fingerprint="a" * 64,
        created_at=NOW,
        updated_at=NOW,
    )


def _client(
    service: StubRunService,
    report_service: StubStaticReportService | None = None,
    plan_service: StubPlanService | None = None,
    risk_report_service: StubRiskReportService | None = None,
) -> TestClient:
    return TestClient(
        create_app(
            ReadinessService({}),
            run_service=service,
            static_report_service=report_service or StubStaticReportService(),
            plan_service=plan_service or StubPlanService(),
            risk_report_service=risk_report_service or StubRiskReportService(),
        )
    )


def test_create_run_requires_idempotency_key_header() -> None:
    response = _client(StubRunService()).post(
        "/api/v1/runs", json={"repository_path": "projects/demo"}
    )

    assert response.status_code == 422


def test_create_run_returns_accepted_resource_and_location() -> None:
    response = _client(StubRunService()).post(
        "/api/v1/runs",
        headers={"Idempotency-Key": "request-1"},
        json={"repository_path": "projects/demo"},
    )

    assert response.status_code == 202
    assert response.headers["location"] == f"/api/v1/runs/{RUN_ID}"
    assert response.json()["id"] == str(RUN_ID)
    assert response.json()["state"] == "QUEUED"
    assert response.json()["links"] == {
        "self": f"/api/v1/runs/{RUN_ID}",
        "events": f"/api/v1/runs/{RUN_ID}/events",
        "timeline": f"/api/v1/runs/{RUN_ID}/timeline",
        "static_report": f"/api/v1/runs/{RUN_ID}/static-report",
        "plan": f"/api/v1/runs/{RUN_ID}/plan",
        "dynamic_result": f"/api/v1/runs/{RUN_ID}/dynamic-result",
        "risk_report": f"/api/v1/runs/{RUN_ID}/risk-report",
    }


def test_create_run_maps_idempotency_conflict() -> None:
    response = _client(StubRunService(IdempotencyConflictError("request-1"))).post(
        "/api/v1/runs",
        headers={"Idempotency-Key": "request-1"},
        json={"repository_path": "projects/demo"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_get_run_maps_not_found_to_stable_error() -> None:
    response = _client(StubRunService(RunNotFoundError(RUN_ID))).get(f"/api/v1/runs/{RUN_ID}")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "RUN_NOT_FOUND"


def test_get_static_report_returns_versioned_json() -> None:
    response = _client(StubRunService()).get(f"/api/v1/runs/{RUN_ID}/static-report")

    assert response.status_code == 200
    assert response.json()["run_id"] == str(RUN_ID)
    assert response.json()["risk_level"] == "INFO"
    assert response.json()["ruleset_version"] == "m2.static-rules.v1"


def test_get_static_report_maps_not_ready_to_stable_conflict() -> None:
    response = _client(
        StubRunService(), StubStaticReportService(StaticReportNotReadyError(RUN_ID))
    ).get(f"/api/v1/runs/{RUN_ID}/static-report")

    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "STATIC_REPORT_NOT_READY"}


def test_get_static_report_maps_unknown_run_to_not_found() -> None:
    response = _client(StubRunService(), StubStaticReportService(RunNotFoundError(RUN_ID))).get(
        f"/api/v1/runs/{RUN_ID}/static-report"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == {"code": "RUN_NOT_FOUND"}


def test_get_plan_returns_the_versioned_agent_plan() -> None:
    response = _client(StubRunService()).get(f"/api/v1/runs/{RUN_ID}/plan")

    assert response.status_code == 200
    assert response.json()["plan"]["schema_version"] == "1.0"
    assert response.json()["input_hash"] == "c" * 64


def test_get_plan_maps_not_ready_to_stable_conflict() -> None:
    response = _client(
        StubRunService(), plan_service=StubPlanService(AuditPlanNotReadyError(RUN_ID))
    ).get(f"/api/v1/runs/{RUN_ID}/plan")

    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "AUDIT_PLAN_NOT_READY"}


def test_get_risk_report_returns_policy_bounded_report() -> None:
    response = _client(StubRunService()).get(f"/api/v1/runs/{RUN_ID}/risk-report")

    assert response.status_code == 200
    assert response.json()["final_risk"] == "MEDIUM"
    assert response.json()["generated_by"] == "fake"


def test_get_risk_report_maps_not_ready_to_stable_conflict() -> None:
    response = _client(
        StubRunService(),
        risk_report_service=StubRiskReportService(RiskReportNotReadyError(RUN_ID)),
    ).get(f"/api/v1/runs/{RUN_ID}/risk-report")

    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "RISK_REPORT_NOT_READY"}


def test_cancel_run_records_a_cooperative_request() -> None:
    response = _client(StubRunService()).post(
        f"/api/v1/runs/{RUN_ID}/cancel", json={"expected_version": 1}
    )

    assert response.status_code == 202
    assert response.json()["cancel_requested_at"] == NOW.isoformat().replace("+00:00", "Z")


def test_cancel_run_maps_terminal_conflict() -> None:
    response = _client(StubRunService(TerminalRunError(RUN_ID, RunState.COMPLETED))).post(
        f"/api/v1/runs/{RUN_ID}/cancel", json={"expected_version": 12}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "RUN_TERMINAL"}
