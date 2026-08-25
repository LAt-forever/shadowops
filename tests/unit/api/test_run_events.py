from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from shadowops.api.app import create_app
from shadowops.application.readiness import ReadinessService
from shadowops.application.run_timeline import RunTimeline, TimelineEvent
from shadowops.domain.runs import RunState, StepStatus

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
NOW = datetime(2026, 8, 25, 2, 0, tzinfo=UTC)


class StubTimelineService:
    def get(self, run_id: UUID, *, after_version: int = 0) -> RunTimeline:
        events = [
            TimelineEvent(version=1, state=RunState.QUEUED, at=NOW),
            TimelineEvent(
                version=2,
                state=RunState.COMPLETED,
                at=NOW,
                step_key="queued-to-completed",
                attempt=1,
                status=StepStatus.SUCCEEDED,
                handler_version="m1.noop.v1",
            ),
        ]
        return RunTimeline(
            run_id=run_id,
            run_version=2,
            terminal=True,
            events=[event for event in events if event.version > after_version],
            current_step=None,
        )


def _client() -> TestClient:
    return TestClient(
        create_app(
            ReadinessService({}),
            timeline_service=StubTimelineService(),
        )
    )


def test_get_timeline_returns_version_ordered_durable_events() -> None:
    response = _client().get(f"/api/v1/runs/{RUN_ID}/timeline")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1.0",
        "run_id": str(RUN_ID),
        "run_version": 2,
        "terminal": True,
        "events": [
            {
                "version": 1,
                "state": "QUEUED",
                "at": "2026-08-25T02:00:00Z",
                "step_key": None,
                "attempt": None,
                "status": None,
                "handler_version": None,
                "error_code": None,
            },
            {
                "version": 2,
                "state": "COMPLETED",
                "at": "2026-08-25T02:00:00Z",
                "step_key": "queued-to-completed",
                "attempt": 1,
                "status": "SUCCEEDED",
                "handler_version": "m1.noop.v1",
                "error_code": None,
            },
        ],
        "current_step": None,
    }


def test_events_stream_emits_durable_versions_and_then_ends_for_terminal_run() -> None:
    response = _client().get(f"/api/v1/runs/{RUN_ID}/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "id: 1\nevent: run.state.changed\n" in response.text
    assert "id: 2\nevent: run.state.changed\n" in response.text


def test_events_stream_resumes_exclusively_after_last_event_id() -> None:
    response = _client().get(f"/api/v1/runs/{RUN_ID}/events", headers={"Last-Event-ID": "1"})

    assert response.status_code == 200
    assert "id: 1\n" not in response.text
    assert "id: 2\n" in response.text


def test_events_stream_rejects_invalid_last_event_id() -> None:
    response = _client().get(
        f"/api/v1/runs/{RUN_ID}/events", headers={"Last-Event-ID": "not-a-version"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {"code": "INVALID_EVENT_CURSOR"}
