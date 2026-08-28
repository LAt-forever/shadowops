"""Provider boundary and deterministic Fake implementation used before M6."""

import json
from collections.abc import Iterable
from typing import Protocol

from shadowops.agent.catalog import CAPABILITY_CATALOG
from shadowops.agent.contracts import (
    AuditPlanV1,
    PlannerRequestV1,
    PlanStepV1,
    ProviderResponseV1,
)


class AgentProvider(Protocol):
    provider_name: str
    model_name: str

    def invoke(self, request: PlannerRequestV1) -> ProviderResponseV1: ...


class FakeAgentProvider:
    provider_name = "fake"
    model_name = "shadowops-reference-planner-v1"

    def __init__(self, outputs: Iterable[str] | None = None) -> None:
        self._outputs = iter(outputs) if outputs is not None else None

    def invoke(self, request: PlannerRequestV1) -> ProviderResponseV1:
        if self._outputs is not None:
            try:
                return ProviderResponseV1(text=next(self._outputs))
            except StopIteration:
                pass
        evidence = tuple(
            sorted(
                {item for observation in request.observations for item in observation.evidence_ids}
            )
        )
        prior: str | None = None
        steps: list[PlanStepV1] = []
        for specification in CAPABILITY_CATALOG:
            step_id = specification.name.value
            steps.append(
                PlanStepV1(
                    id=step_id,
                    capability=specification.name,
                    depends_on=() if prior is None else (prior,),
                    timeout_seconds=specification.max_timeout_seconds,
                    required=True,
                    reason=f"Execute fixed capability {specification.name.value} safely.",
                    evidence_refs=evidence[:4],
                )
            )
            prior = step_id
        plan = AuditPlanV1(
            objective=f"Audit migration evidence for run {request.run_id}",
            steps=tuple(steps),
            coverage_gaps=("M3 plans capabilities but does not execute a shadow database.",),
            assumptions=("PostgreSQL 16 is the only declared compatibility profile.",),
        )
        return ProviderResponseV1(
            text=json.dumps(plan.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        )
