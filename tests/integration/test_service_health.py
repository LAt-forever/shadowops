import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).parents[2]
API_BASE = os.environ.get("SHADOWOPS_API_BASE", "http://127.0.0.1:8000")


def _run(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        cwd=PROJECT_ROOT,
        text=True,
    )


def _compose(*arguments: str) -> subprocess.CompletedProcess[str]:
    return _run("docker", "compose", *arguments)


def _readiness() -> httpx.Response:
    with httpx.Client(trust_env=False) as client:
        return client.get(f"{API_BASE}/health/ready", timeout=5.0)


def _wait_until_ready(timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if _readiness().status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise AssertionError("API did not return to ready state before timeout")


def _celery_ping() -> subprocess.CompletedProcess[str]:
    return _compose(
        "exec",
        "-T",
        "worker",
        "celery",
        "-A",
        "shadowops.worker.celery_app:celery_app",
        "inspect",
        "ping",
        "--timeout=5",
    )


def _wait_until_worker_recovers(timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    container_id = _compose("ps", "--quiet", "worker").stdout.strip()
    while time.monotonic() < deadline:
        health = _run(
            "docker",
            "inspect",
            "--format={{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}",
            container_id,
        ).stdout.strip()
        if health == "healthy":
            try:
                if "pong" in _celery_ping().stdout:
                    return
            except subprocess.CalledProcessError:
                pass
        time.sleep(0.5)
    raise AssertionError("Celery worker did not recover its broker roundtrip before timeout")


def test_compose_api_reports_database_and_redis_ready() -> None:
    response = _readiness()

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {"database": "ok", "redis": "ok"},
    }


def test_worker_runs_as_non_root_user() -> None:
    result = _compose("exec", "-T", "worker", "id", "-u")

    assert result.stdout.strip() != "0"


def test_worker_healthcheck_verifies_broker_roundtrip() -> None:
    container_id = _compose("ps", "--quiet", "worker").stdout.strip()
    result = _run(
        "docker",
        "inspect",
        "--format={{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}",
        container_id,
    )

    assert result.stdout.strip() == "healthy"
    ping = _celery_ping()
    assert "pong" in ping.stdout


@pytest.mark.parametrize(
    ("service", "failed_dependency"),
    [("control-postgres", "database"), ("redis", "redis")],
)
def test_readiness_identifies_real_dependency_outage(
    service: str,
    failed_dependency: str,
) -> None:
    _compose("stop", service)
    started = time.monotonic()
    try:
        response = _readiness()
        elapsed = time.monotonic() - started

        expected_dependencies = {"database": "ok", "redis": "ok"}
        expected_dependencies[failed_dependency] = "unavailable"
        assert elapsed < 5.0
        assert response.status_code == 503
        assert response.json() == {
            "status": "not_ready",
            "dependencies": expected_dependencies,
        }
    finally:
        _compose("start", service)
        _wait_until_ready()
        if service == "redis":
            _wait_until_worker_recovers()


def test_api_runtime_logs_are_json() -> None:
    _readiness()
    result = _compose("logs", "--no-color", "--no-log-prefix", "api")
    log_lines = [line for line in result.stdout.splitlines() if line]

    assert log_lines
    assert all(isinstance(json.loads(line), dict) for line in log_lines)
