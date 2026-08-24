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

    monkeypatch.setattr("shadowops.cli.app.httpx.get", lambda *args, **kwargs: Response())

    result = runner.invoke(app, ["ping", "--api-url", "http://127.0.0.1:8000"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "ready"
