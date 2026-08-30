import pytest
from pydantic import ValidationError

from shadowops.agent.catalog import CAPABILITY_CATALOG
from shadowops.agent.contracts import AuditPlanV1, PlanStepV1


def test_audit_plan_json_schema_is_strict_and_capability_only() -> None:
    schema = AuditPlanV1.model_json_schema()
    step_schema = schema["$defs"]["PlanStepV1"]

    assert schema["additionalProperties"] is False
    assert step_schema["additionalProperties"] is False
    assert set(step_schema["properties"]) == {
        "id",
        "capability",
        "depends_on",
        "timeout_seconds",
        "required",
        "reason",
        "evidence_refs",
    }
    serialized = str(schema).lower()
    assert all(name not in serialized for name in ("command", "image", "network", "host_path"))


def test_audit_plan_rejects_unknown_capabilities() -> None:
    specification = CAPABILITY_CATALOG[0]
    valid = PlanStepV1(
        id=specification.name.value,
        capability=specification.name,
        timeout_seconds=specification.max_timeout_seconds,
        required=True,
        reason="Use a fixed capability.",
    ).model_dump(mode="json")
    valid["capability"] = "run_arbitrary_command"

    with pytest.raises(ValidationError):
        AuditPlanV1.model_validate({"objective": "Audit migrations", "steps": [valid]})
