import pytest
from pydantic import ValidationError

from shadowops.agent.contracts import ReadOnlyToolName
from shadowops.agent.gateway import ReadOnlyToolGateway
from shadowops.domain.errors import RepositoryInputError


def _gateway() -> ReadOnlyToolGateway:
    return ReadOnlyToolGateway(
        lambda: None,  # type: ignore[arg-type,return-value]
        None,  # type: ignore[arg-type]
    )


def test_gateway_rejects_arguments_outside_the_versioned_tool_schema() -> None:
    with pytest.raises(ValidationError):
        _gateway().call(
            ReadOnlyToolName.DESCRIBE_SHADOW_CAPABILITIES,
            {"command": "docker run", "host_path": "/", "network": "host"},
        )


def test_gateway_rejects_non_allowlisted_tool_names() -> None:
    with pytest.raises(RepositoryInputError) as error:
        _gateway().call("run_shell", {})  # type: ignore[arg-type]

    assert error.value.code == "TOOL_NOT_ALLOWED"
