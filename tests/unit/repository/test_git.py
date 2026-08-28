import subprocess
from pathlib import Path

from pytest import MonkeyPatch

from shadowops.repository.git import GitRepository


def test_git_commands_trust_only_the_validated_repository(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    repository = Path("/repositories/projects/demo")

    GitRepository(repository)._run_result("status", check=False)

    command = captured["command"]
    assert isinstance(command, list)
    assert "safe.directory=*" not in command
    assert command[-5:] == [
        "-c",
        f"safe.directory={repository}",
        "-C",
        str(repository),
        "status",
    ]
