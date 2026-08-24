from typer.testing import CliRunner

from shadowops.cli.app import app

runner = CliRunner()


def test_version_prints_package_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_ping_reports_ready_api(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"status": "ready"}

    class Client:
        def __init__(self, *, trust_env: bool) -> None:
            assert trust_env is False

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *args) -> None:
            return None

        def get(self, *args, **kwargs) -> Response:
            return Response()

    monkeypatch.setattr("shadowops.cli.app.httpx.Client", Client)

    result = runner.invoke(app, ["ping", "--api-url", "http://127.0.0.1:8000"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "ready"
