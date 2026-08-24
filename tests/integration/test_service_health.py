import subprocess

import httpx


def test_compose_api_reports_database_and_redis_ready() -> None:
    with httpx.Client(trust_env=False) as client:
        response = client.get("http://127.0.0.1:8000/health/ready", timeout=5.0)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {"database": "ok", "redis": "ok"},
    }


def test_worker_runs_as_non_root_user() -> None:
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "worker", "id", "-u"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() != "0"
