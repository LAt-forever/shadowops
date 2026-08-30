"""Deterministic safety validation for Agent-produced plans."""

from shadowops.agent.catalog import CAPABILITIES_BY_NAME, CAPABILITY_CATALOG
from shadowops.agent.contracts import AuditPlanV1, CapabilityName, PlanStepV1

_EVIDENCE_PREFIXES = ("evidence:", "revision:", "finding:", "static-report:")


class PlanValidationError(ValueError):
    def __init__(self, errors: tuple[str, ...]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class PlanValidator:
    def validate(self, plan: AuditPlanV1) -> AuditPlanV1:
        errors: list[str] = []
        by_id = {step.id: step for step in plan.steps}
        if len(by_id) != len(plan.steps):
            errors.append("DUPLICATE_STEP_ID")
        by_capability = {step.capability: step for step in plan.steps}
        if len(by_capability) != len(plan.steps):
            errors.append("DUPLICATE_CAPABILITY")
        for specification in CAPABILITY_CATALOG:
            step = by_capability.get(specification.name)
            if specification.mandatory and step is None:
                errors.append(f"MANDATORY_CAPABILITY_MISSING:{specification.name.value}")
                continue
            if step is None:
                continue
            if not step.required:
                errors.append(f"MANDATORY_STEP_NOT_REQUIRED:{step.id}")
            if step.timeout_seconds > specification.max_timeout_seconds:
                errors.append(f"TIMEOUT_EXCEEDS_CAPABILITY_LIMIT:{step.id}")
            if any(not ref.startswith(_EVIDENCE_PREFIXES) for ref in step.evidence_refs):
                errors.append(f"EVIDENCE_REF_INVALID:{step.id}")
        for step in plan.steps:
            if step.id in step.depends_on or any(item not in by_id for item in step.depends_on):
                errors.append(f"DEPENDENCY_INVALID:{step.id}")
        if self._has_cycle(by_id):
            errors.append("PLAN_CYCLE")
        for capability, step in by_capability.items():
            specification = CAPABILITIES_BY_NAME[capability]
            ancestors = self._ancestor_capabilities(step.id, by_id)
            for prerequisite in specification.prerequisites:
                if prerequisite not in ancestors:
                    errors.append(
                        f"CAPABILITY_PREREQUISITE_MISSING:{capability.value}:{prerequisite.value}"
                    )
        if errors:
            raise PlanValidationError(tuple(sorted(set(errors))))
        return plan

    @staticmethod
    def _has_cycle(by_id: dict[str, PlanStepV1]) -> bool:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> bool:
            if step_id in visiting:
                return True
            if step_id in visited or step_id not in by_id:
                return False
            visiting.add(step_id)
            step = by_id[step_id]
            cyclic = any(visit(item) for item in step.depends_on)
            visiting.remove(step_id)
            visited.add(step_id)
            return cyclic

        return any(visit(step_id) for step_id in by_id)

    @staticmethod
    def _ancestor_capabilities(step_id: str, by_id: dict[str, PlanStepV1]) -> set[CapabilityName]:
        result: set[CapabilityName] = set()
        pending = list(by_id[step_id].depends_on)
        while pending:
            dependency = pending.pop()
            step = by_id.get(dependency)
            if step is None or step.capability in result:
                continue
            result.add(step.capability)
            pending.extend(step.depends_on)
        return result
