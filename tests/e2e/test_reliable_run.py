import json
import os
import subprocess
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx

PROJECT_ROOT = Path(__file__).parents[2]
API_BASE = os.environ.get("SHADOWOPS_API_BASE", "http://127.0.0.1:8000")


def _compose(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("docker", "compose", *arguments),
        check=True,
        capture_output=True,
        cwd=PROJECT_ROOT,
        text=True,
    )


def _request(method: str, path: str, **kwargs: object) -> httpx.Response:
    with httpx.Client(base_url=API_BASE, trust_env=False, timeout=10.0) as client:
        return client.request(method, path, **kwargs)


def _create(key: str, repository_path: str = "projects/m1-noop-demo") -> dict[str, object]:
    response = _request(
        "POST",
        "/api/v1/runs",
        headers={"Idempotency-Key": key},
        json={"repository_path": repository_path},
    )
    assert response.status_code == 202
    return response.json()


def _wait_for_state(run_id: str, expected: str, *, timeout: float = 30.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last: dict[str, object] | None = None
    while time.monotonic() < deadline:
        response = _request("GET", f"/api/v1/runs/{run_id}")
        if response.status_code == 200:
            last = response.json()
            if last["state"] == expected:
                return last
        time.sleep(0.25)
    raise AssertionError(f"run {run_id} did not reach {expected}; last response was {last}")


def _wait_for_api(*, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if _request("GET", "/health/ready").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise AssertionError("API did not become ready")


def _initial_event_id(run_id: str) -> str:
    result = _compose(
        "exec",
        "-T",
        "control-postgres",
        "psql",
        "-U",
        "shadowops",
        "-d",
        "shadowops",
        "-Atc",
        (
            "SELECT id FROM outbox_events "
            f"WHERE aggregate_id = '{UUID(run_id)}' AND aggregate_version = 1"
        ),
    )
    return result.stdout.strip()


def _publish_event(event_id: str) -> None:
    _compose(
        "exec",
        "-T",
        "api",
        "celery",
        "-A",
        "shadowops.worker.celery_app:celery_app",
        "call",
        "shadowops.runs.process_event",
        "--args",
        json.dumps([event_id]),
    )


def test_run_completes_idempotently_and_survives_api_restart() -> None:
    request_key = f"e2e-completion-{uuid4()}"
    first = _create(request_key)
    replay = _create(request_key)
    assert replay["id"] == first["id"]

    completed = _wait_for_state(str(first["id"]), "COMPLETED")
    assert completed["version"] == 12
    report_before_restart = _request("GET", f"/api/v1/runs/{first['id']}/static-report").json()

    timeline = _request("GET", f"/api/v1/runs/{first['id']}/timeline")
    assert timeline.status_code == 200
    assert [event["version"] for event in timeline.json()["events"]] == list(range(1, 13))

    resumed = _request(
        "GET",
        f"/api/v1/runs/{first['id']}/events",
        headers={"Last-Event-ID": "10"},
    )
    assert resumed.status_code == 200
    assert "id: 10\n" not in resumed.text
    assert "id: 11\n" in resumed.text
    assert "id: 12\n" in resumed.text

    _compose("restart", "api")
    _wait_for_api()
    assert _request("GET", f"/api/v1/runs/{first['id']}").json()["state"] == "COMPLETED"
    assert (
        _request("GET", f"/api/v1/runs/{first['id']}/static-report").json() == report_before_restart
    )


def test_queued_run_and_duplicate_messages_recover_after_worker_restart() -> None:
    _compose("stop", "worker")
    try:
        run = _create(f"e2e-worker-restart-{uuid4()}")
        assert run["state"] == "QUEUED"
        pending_report = _request("GET", f"/api/v1/runs/{run['id']}/static-report")
        assert pending_report.status_code == 409
        assert pending_report.json()["detail"] == {"code": "STATIC_REPORT_NOT_READY"}
        event_id = _initial_event_id(str(run["id"]))
        assert event_id
        _publish_event(event_id)
        _publish_event(event_id)
    finally:
        _compose("start", "worker")

    completed = _wait_for_state(str(run["id"]), "COMPLETED")
    assert completed["version"] == 12
    timeline = _request("GET", f"/api/v1/runs/{run['id']}/timeline").json()
    assert len(timeline["events"]) == 12


def test_cancel_request_is_honoured_after_worker_restart() -> None:
    _compose("stop", "worker")
    try:
        run = _create(f"e2e-cancellation-{uuid4()}")
        cancelled = _request(
            "POST",
            f"/api/v1/runs/{run['id']}/cancel",
            json={"expected_version": 1},
        )
        assert cancelled.status_code == 202
        assert cancelled.json()["cancel_requested_at"] is not None
    finally:
        _compose("start", "worker")

    terminal = _wait_for_state(str(run["id"]), "CANCELLED")
    assert terminal["version"] == 2
    timeline = _request("GET", f"/api/v1/runs/{run['id']}/timeline").json()
    assert [event["state"] for event in timeline["events"]] == ["QUEUED", "CANCELLED"]


def test_missing_repository_becomes_a_stable_failed_timeline() -> None:
    response = _request(
        "POST",
        "/api/v1/runs",
        headers={"Idempotency-Key": f"e2e-missing-repository-{uuid4()}"},
        json={"repository_path": "projects/not-present"},
    )
    assert response.status_code == 202

    failed = _wait_for_state(response.json()["id"], "FAILED")

    assert failed["version"] == 2
    timeline = _request("GET", f"/api/v1/runs/{failed['id']}/timeline").json()
    assert [event["state"] for event in timeline["events"]] == ["QUEUED", "FAILED"]
    assert timeline["events"][-1]["error_code"] == "REPOSITORY_NOT_FOUND"


def test_safe_changed_revision_produces_an_information_only_static_report() -> None:
    run = _create(f"e2e-safe-static-{uuid4()}", "projects/safe-add-column")

    _wait_for_state(str(run["id"]), "COMPLETED")
    response = _request("GET", f"/api/v1/runs/{run['id']}/static-report")

    assert response.status_code == 200
    report = response.json()
    assert report["risk_level"] == "INFO"
    assert report["findings"] == []
    assert report["revision_graph"]["changed_revisions"] == ["002"]


def test_dangerous_changed_revision_produces_located_high_risk_findings() -> None:
    run = _create(f"e2e-dangerous-static-{uuid4()}", "projects/dangerous-drop")

    _wait_for_state(str(run["id"]), "COMPLETED")
    response = _request("GET", f"/api/v1/runs/{run['id']}/static-report")

    assert response.status_code == 200
    report = response.json()
    assert report["risk_level"] == "HIGH"
    by_rule = {finding["rule_id"]: finding for finding in report["findings"]}
    assert {"SOPS001", "SOPS004"}.issubset(by_rule)
    assert by_rule["SOPS001"]["relative_path"].endswith("002_drop_legacy.py")
    assert by_rule["SOPS001"]["line"] == 10
    assert by_rule["SOPS001"]["evidence_ids"][0].startswith("evidence:")
