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


def _wait_for_state(run_id: str, expected: str, *, timeout: float = 60.0) -> dict[str, object]:
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


def _count_rows(table: str, run_id: str) -> int:
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
        f"SELECT COUNT(*) FROM {table} WHERE run_id = '{UUID(run_id)}'",
    )
    return int(result.stdout.strip())


def _scalar(query: str) -> str:
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
        query,
    )
    return result.stdout.strip()


def _shadow_resource_count(run_id: str) -> int:
    label = f"shadowops.run_id={UUID(run_id)}"
    commands = (
        ("docker", "ps", "-aq", "--filter", f"label={label}"),
        ("docker", "network", "ls", "-q", "--filter", f"label={label}"),
        ("docker", "volume", "ls", "-q", "--filter", f"label={label}"),
    )
    return sum(
        len(subprocess.run(command, check=True, capture_output=True, text=True).stdout.splitlines())
        for command in commands
    )


def _wait_for_runner(run_id: str, action: str, *, timeout: float = 30.0) -> str:
    name = f"shadowops-{UUID(run_id).hex[:12]}-1-{action.lower()}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            (
                "docker",
                "ps",
                "-q",
                "--filter",
                f"label=shadowops.run_id={UUID(run_id)}",
                "--filter",
                "label=shadowops.role=runner",
                "--filter",
                f"name={name}",
            ),
            check=True,
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            return result.stdout.strip()
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not start its {action} Runner container")


def _assert_runner_is_hardened(container_id: str) -> None:
    result = subprocess.run(
        ("docker", "inspect", container_id),
        check=True,
        capture_output=True,
        text=True,
    )
    inspected = json.loads(result.stdout)[0]
    host = inspected["HostConfig"]
    assert inspected["Config"]["User"] == "10002:10002"
    assert host["ReadonlyRootfs"] is True
    assert host["CapDrop"] == ["ALL"]
    assert "no-new-privileges:true" in host["SecurityOpt"]
    assert host["PidsLimit"] == 64
    assert host["Memory"] == 256 * 1024 * 1024
    assert host["NanoCpus"] == 500_000_000
    mounts = []
    for mount in inspected["Mounts"]:
        mounts.append((mount["Type"], mount["Destination"], mount["RW"]))
    assert mounts == [("volume", "/repository", False)]
    environment = inspected["Config"]["Env"]
    assert not any(
        forbidden in item
        for item in environment
        for forbidden in (
            "SHADOWOPS_DATABASE_URL",
            "SHADOWOPS_REDIS_URL",
            "LLM",
            "DOCKER_HOST",
        )
    )
    network_ids = [item["NetworkID"] for item in inspected["NetworkSettings"]["Networks"].values()]
    assert len(network_ids) == 1
    network = subprocess.run(
        ("docker", "network", "inspect", network_ids[0]),
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(network.stdout)[0]["Internal"] is True
    assert inspected["NetworkSettings"]["Ports"] == {}


def test_run_completes_idempotently_and_survives_api_restart() -> None:
    request_key = f"e2e-completion-{uuid4()}"
    first = _create(request_key)
    replay = _create(request_key)
    assert replay["id"] == first["id"]

    completed = _wait_for_state(str(first["id"]), "COMPLETED")
    assert completed["version"] == 12
    report_before_restart = _request("GET", f"/api/v1/runs/{first['id']}/static-report").json()
    plan_response = _request("GET", f"/api/v1/runs/{first['id']}/plan")
    assert plan_response.status_code == 200
    plan_before_restart = plan_response.json()
    assert plan_before_restart["plan"]["schema_version"] == "1.0"
    assert [step["capability"] for step in plan_before_restart["plan"]["steps"]] == [
        "provision_shadow_db",
        "upgrade_baseline",
        "apply_target_migrations",
        "load_test_data",
        "run_smoke_checks",
        "verify_rollback_roundtrip",
        "collect_evidence",
        "cleanup_shadow_environment",
    ]
    assert _count_rows("agent_invocations", str(first["id"])) == 2
    assert _count_rows("agent_tool_calls", str(first["id"])) == 9
    assert _count_rows("audit_plans", str(first["id"])) == 1
    assert _count_rows("risk_reports", str(first["id"])) == 1

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
    assert _request("GET", f"/api/v1/runs/{first['id']}/plan").json() == plan_before_restart


def test_queued_run_and_duplicate_messages_recover_after_worker_restart() -> None:
    _compose("stop", "worker")
    try:
        run = _create(f"e2e-worker-restart-{uuid4()}")
        assert run["state"] == "QUEUED"
        pending_report = _request("GET", f"/api/v1/runs/{run['id']}/static-report")
        assert pending_report.status_code == 409
        assert pending_report.json()["detail"] == {"code": "STATIC_REPORT_NOT_READY"}
        pending_plan = _request("GET", f"/api/v1/runs/{run['id']}/plan")
        assert pending_plan.status_code == 409
        assert pending_plan.json()["detail"] == {"code": "AUDIT_PLAN_NOT_READY"}
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
    assert _request("GET", f"/api/v1/runs/{run['id']}/plan").status_code == 200
    assert _count_rows("agent_invocations", str(run["id"])) == 2
    assert _count_rows("audit_plans", str(run["id"])) == 1


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
    assert (
        _scalar(f"SELECT status FROM shadow_environments WHERE run_id = '{UUID(str(run['id']))}'")
        == "CLEANED"
    )
    assert _count_rows("runner_executions", str(run["id"])) == 5
    dynamic = _request("GET", f"/api/v1/runs/{run['id']}/dynamic-result").json()
    rollback = next(
        item
        for item in dynamic["executions"]
        if item["request"]["action"] == "VERIFY_ROLLBACK_ROUNDTRIP"
    )
    roundtrip = next(
        item for item in rollback["result"]["observations"] if item["kind"] == "ROLLBACK_ROUNDTRIP"
    )
    assert roundtrip["data"]["restored"] is True
    assert roundtrip["data"]["row_counts_before"] == {"users": 1}
    assert roundtrip["data"]["row_counts_after"] == {"users": 1}
    assert {item["kind"] for item in dynamic["evidence_items"]}.issuperset(
        {"SEED_SUMMARY", "SCHEMA_FINGERPRINT", "SMOKE_SUMMARY", "ROLLBACK_ROUNDTRIP"}
    )
    assert all(
        item["artifact_uri"] == f"artifact://sha256/{item['sha256']}"
        for item in dynamic["evidence_items"]
    )
    first_evidence = dynamic["evidence_items"][0]
    digest = str(first_evidence["sha256"])
    stored = _compose(
        "exec",
        "-T",
        "worker",
        "sha256sum",
        f"/var/lib/shadowops/artifacts/evidence/{digest[:2]}/{digest}",
    )
    assert stored.stdout.split()[0] == digest
    risk = _request("GET", f"/api/v1/runs/{run['id']}/risk-report")
    assert risk.status_code == 200
    assert risk.json()["final_risk"] == "MEDIUM"
    assert risk.json()["requires_approval"] is False
    assert risk.json()["generated_by"] == "fake"
    assert risk.json()["report_hash"]
    assert _shadow_resource_count(str(run["id"])) == 0


def test_broken_target_returns_structured_failure_and_cleans_resources() -> None:
    run = _create(f"e2e-broken-upgrade-{uuid4()}", "projects/broken-upgrade")

    failed = _wait_for_state(str(run["id"]), "FAILED", timeout=45.0)

    assert failed["failure_code"] == "MIGRATION_FAILED"
    dynamic = _request("GET", f"/api/v1/runs/{run['id']}/dynamic-result")
    assert dynamic.status_code == 200
    assert dynamic.json()["executions"][-1]["result"]["error_code"] == "MIGRATION_FAILED"
    assert (
        _scalar(f"SELECT status FROM shadow_environments WHERE run_id = '{UUID(str(run['id']))}'")
        == "CLEANED"
    )
    assert (
        _scalar(
            "SELECT result->>'error_code' FROM runner_executions "
            f"WHERE run_id = '{UUID(str(run['id']))}' AND action = 'APPLY_TARGET'"
        )
        == "MIGRATION_FAILED"
    )
    assert _shadow_resource_count(str(run["id"])) == 0


def test_cancel_during_target_apply_finalizes_every_shadow_resource() -> None:
    run = _create(f"e2e-cancel-upgrade-{uuid4()}", "projects/slow-upgrade")
    runner_id = _wait_for_runner(str(run["id"]), "APPLY_TARGET")
    _assert_runner_is_hardened(runner_id)
    current = _request("GET", f"/api/v1/runs/{run['id']}").json()

    response = _request(
        "POST",
        f"/api/v1/runs/{run['id']}/cancel",
        json={"expected_version": current["version"]},
    )

    assert response.status_code == 202
    terminal = _wait_for_state(str(run["id"]), "CANCELLED", timeout=45.0)
    assert terminal["state"] == "CANCELLED"
    assert (
        _scalar(f"SELECT status FROM shadow_environments WHERE run_id = '{UUID(str(run['id']))}'")
        == "CLEANED"
    )
    assert _shadow_resource_count(str(run["id"])) == 0


def test_unique_fixture_conflict_is_structured_and_preserved_as_evidence() -> None:
    run = _create(f"e2e-unique-conflict-{uuid4()}", "projects/unique-conflict")

    failed = _wait_for_state(str(run["id"]), "FAILED", timeout=45.0)

    assert failed["failure_code"] == "SEED_CONSTRAINT_FAILED"
    dynamic = _request("GET", f"/api/v1/runs/{run['id']}/dynamic-result").json()
    seed = next(
        item for item in dynamic["executions"] if item["request"]["action"] == "LOAD_TEST_DATA"
    )
    assert seed["result"]["error_code"] == "SEED_CONSTRAINT_FAILED"
    assert "RUNNER_STDERR" in {item["kind"] for item in dynamic["evidence_items"]}
    assert _shadow_resource_count(str(run["id"])) == 0


def test_irreversible_downgrade_returns_rollback_failure_evidence() -> None:
    run = _create(f"e2e-irreversible-roundtrip-{uuid4()}", "projects/irreversible-roundtrip")

    failed = _wait_for_state(str(run["id"]), "FAILED", timeout=45.0)

    assert failed["failure_code"] == "ROLLBACK_FAILED"
    dynamic = _request("GET", f"/api/v1/runs/{run['id']}/dynamic-result").json()
    rollback = next(
        item
        for item in dynamic["executions"]
        if item["request"]["action"] == "VERIFY_ROLLBACK_ROUNDTRIP"
    )
    assert rollback["result"]["error_code"] == "ROLLBACK_FAILED"
    assert "RUNNER_STDERR" in {item["kind"] for item in dynamic["evidence_items"]}
    risk = _request("GET", f"/api/v1/runs/{run['id']}/risk-report").json()
    assert risk["final_risk"] == "HIGH"
    assert risk["requires_approval"] is True
    assert "dynamic_failure:ROLLBACK_FAILED" in risk["policy_reasons"]
    assert _shadow_resource_count(str(run["id"])) == 0


def test_type_conversion_failure_is_reported_by_target_upgrade() -> None:
    run = _create(f"e2e-type-conversion-{uuid4()}", "projects/type-conversion-failure")

    failed = _wait_for_state(str(run["id"]), "FAILED", timeout=45.0)

    assert failed["failure_code"] == "MIGRATION_FAILED"
    dynamic = _request("GET", f"/api/v1/runs/{run['id']}/dynamic-result").json()
    target = next(
        item for item in dynamic["executions"] if item["request"]["action"] == "APPLY_TARGET"
    )
    assert target["result"]["error_code"] == "MIGRATION_FAILED"
    assert "RUNNER_STDERR" in {item["kind"] for item in dynamic["evidence_items"]}
    assert _shadow_resource_count(str(run["id"])) == 0


def test_unsupported_seed_type_remains_an_explicit_coverage_gap() -> None:
    run = _create(f"e2e-unsupported-type-{uuid4()}", "projects/unsupported-type")

    _wait_for_state(str(run["id"]), "COMPLETED", timeout=45.0)

    dynamic = _request("GET", f"/api/v1/runs/{run['id']}/dynamic-result").json()
    seed = next(
        item for item in dynamic["executions"] if item["request"]["action"] == "LOAD_TEST_DATA"
    )
    assert any(
        gap.startswith("unsupported_type:users.payload:") for gap in seed["result"]["coverage_gaps"]
    )
    coverage = [item for item in dynamic["evidence_items"] if item["kind"] == "COVERAGE_GAPS"]
    assert coverage
    assert coverage[0]["observation_scope"] == "unknown_in_production"
    risk = _request("GET", f"/api/v1/runs/{run['id']}/risk-report").json()
    assert risk["final_risk"] == "MEDIUM"
    assert "shadow_coverage_gaps_present" in risk["policy_reasons"]
    assert _shadow_resource_count(str(run["id"])) == 0


def test_dangerous_changed_revision_produces_located_high_risk_findings() -> None:
    run = _create(f"e2e-dangerous-static-{uuid4()}", "projects/dangerous-drop")

    failed = _wait_for_state(str(run["id"]), "FAILED")
    response = _request("GET", f"/api/v1/runs/{run['id']}/static-report")

    assert failed["failure_code"] == "ROLLBACK_FAILED"
    assert response.status_code == 200
    report = response.json()
    assert report["risk_level"] == "HIGH"
    by_rule = {finding["rule_id"]: finding for finding in report["findings"]}
    assert {"SOPS001", "SOPS004"}.issubset(by_rule)
    assert by_rule["SOPS001"]["relative_path"].endswith("002_drop_legacy.py")
    assert by_rule["SOPS001"]["line"] == 10
    assert by_rule["SOPS001"]["evidence_ids"][0].startswith("evidence:")
    risk = _request("GET", f"/api/v1/runs/{run['id']}/risk-report").json()
    assert risk["final_risk"] == "HIGH"
    assert risk["requires_approval"] is True
    assert "static_risk:HIGH" in risk["policy_reasons"]
    assert _shadow_resource_count(str(run["id"])) == 0
