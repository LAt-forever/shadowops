from copy import deepcopy

import pytest
from pydantic import ValidationError

from shadowops.agent.catalog import CAPABILITY_CATALOG
from shadowops.agent.contracts import AuditPlanV1, PlanStepV1
from shadowops.agent.validator import PlanValidationError, PlanValidator


def _plan() -> AuditPlanV1:
    prior: str | None = None
    steps = []
    for item in CAPABILITY_CATALOG:
        step_id = item.name.value
        steps.append(
            PlanStepV1(
                id=step_id,
                capability=item.name,
                depends_on=() if prior is None else (prior,),
                timeout_seconds=item.max_timeout_seconds,
                required=True,
                reason="Required deterministic check",
                evidence_refs=("evidence:abc",),
            )
        )
        prior = step_id
    return AuditPlanV1(objective="Audit migrations", steps=tuple(steps))


def test_reference_plan_is_valid_and_forms_an_acyclic_capability_dag() -> None:
    plan = PlanValidator().validate(_plan())

    assert len(plan.steps) == len(CAPABILITY_CATALOG)
    assert plan.steps[-1].capability.value == "cleanup_shadow_environment"


def test_validator_rejects_missing_mandatory_steps_and_excessive_timeout() -> None:
    payload = _plan().model_dump(mode="python")
    payload["steps"] = payload["steps"][:-1]
    payload["steps"][0]["timeout_seconds"] = 600

    with pytest.raises(PlanValidationError) as error:
        PlanValidator().validate(AuditPlanV1.model_validate(payload))

    assert "MANDATORY_CAPABILITY_MISSING:cleanup_shadow_environment" in error.value.errors
    assert "TIMEOUT_EXCEEDS_CAPABILITY_LIMIT:provision_shadow_db" in error.value.errors


def test_plan_contract_has_no_command_image_network_or_path_escape_hatch() -> None:
    payload = _plan().model_dump(mode="python")
    payload["steps"][0]["command"] = "docker run --network host"
    payload["steps"][0]["image"] = "untrusted:latest"
    payload["steps"][0]["host_path"] = "/"

    with pytest.raises(ValidationError):
        AuditPlanV1.model_validate(payload)


def test_validator_rejects_dependency_cycles() -> None:
    payload = deepcopy(_plan().model_dump(mode="python"))
    payload["steps"][0]["depends_on"] = (payload["steps"][-1]["id"],)

    with pytest.raises(PlanValidationError) as error:
        PlanValidator().validate(AuditPlanV1.model_validate(payload))

    assert "PLAN_CYCLE" in error.value.errors
