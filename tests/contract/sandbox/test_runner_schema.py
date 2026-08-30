import hashlib

import pytest
from pydantic import ValidationError

from shadowops.sandbox.contracts import (
    BoundedArtifactV1,
    RunnerAction,
    RunnerRequestV1,
    RunnerResultV1,
    RunnerStatus,
)


def _artifact(text: str = "") -> BoundedArtifactV1:
    data = text.encode()
    return BoundedArtifactV1(
        byte_count=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        truncated=False,
        text=text,
    )


def test_runner_request_only_accepts_a_fixed_action_alias_revision_and_budgets() -> None:
    request = RunnerRequestV1(
        action=RunnerAction.APPLY_TARGET,
        revision="002_add_nickname",
        statement_timeout_ms=30_000,
        output_limit_bytes=65_536,
    )

    assert request.database_alias == "shadow-postgres"
    assert "command" not in RunnerRequestV1.model_json_schema()["properties"]
    assert "image" not in RunnerRequestV1.model_json_schema()["properties"]
    assert "network" not in RunnerRequestV1.model_json_schema()["properties"]
    assert "host_path" not in RunnerRequestV1.model_json_schema()["properties"]


@pytest.mark.parametrize("field", ["command", "image", "network", "host_path"])
def test_runner_request_rejects_agent_escape_fields(field: str) -> None:
    payload = {
        "action": "APPLY_TARGET",
        "revision": "002",
        "statement_timeout_ms": 30_000,
        "output_limit_bytes": 65_536,
        field: "attacker-controlled",
    }

    with pytest.raises(ValidationError):
        RunnerRequestV1.model_validate(payload)


def test_runner_result_is_versioned_and_structured() -> None:
    result = RunnerResultV1(
        action=RunnerAction.UPGRADE_BASELINE,
        status=RunnerStatus.FAILED,
        error_code="MIGRATION_FAILED",
        error_detail="syntax error",
        duration_ms=12,
        stdout=_artifact(),
        stderr=_artifact("syntax error"),
    )

    assert result.schema_version == "1.0"
    assert result.stderr.sha256 == hashlib.sha256(b"syntax error").hexdigest()


def test_runner_contract_rejects_invalid_revision_and_unbounded_output() -> None:
    with pytest.raises(ValidationError):
        RunnerRequestV1(
            action=RunnerAction.APPLY_TARGET,
            revision="head; rm -rf /",
            statement_timeout_ms=30_000,
            output_limit_bytes=1_000_000,
        )
