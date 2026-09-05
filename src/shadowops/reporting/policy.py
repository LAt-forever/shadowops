"""Deterministic risk floor that model output cannot lower."""

from shadowops.reporting.contracts import PolicyDecisionV1
from shadowops.rules.contracts import Severity

_RANK: dict[Severity, int] = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}


class PolicyEngine:
    policy_version = "m6.policy.v1"
    required_evidence = frozenset(
        {"SEED_SUMMARY", "SCHEMA_FINGERPRINT", "SMOKE_SUMMARY", "ROLLBACK_ROUNDTRIP"}
    )

    def evaluate(
        self,
        *,
        static_risk: Severity,
        model_risk: Severity,
        execution_failures: tuple[str, ...],
        evidence_kinds: frozenset[str],
        coverage_gaps: tuple[str, ...],
    ) -> PolicyDecisionV1:
        risk = static_risk
        reasons = [f"static_risk:{static_risk}"]
        if execution_failures:
            risk = "HIGH"
            reasons.extend(f"dynamic_failure:{code}" for code in sorted(set(execution_failures)))
        missing = sorted(self.required_evidence - evidence_kinds)
        if missing:
            risk = "HIGH"
            reasons.append(f"missing_mandatory_evidence:{','.join(missing)}")
        if coverage_gaps:
            reasons.append("shadow_coverage_gaps_present")
            if _RANK[risk] < _RANK["MEDIUM"]:
                risk = "MEDIUM"
        if _RANK[model_risk] > _RANK[risk]:
            risk = model_risk
            reasons.append(f"model_raised_risk:{model_risk}")
        return PolicyDecisionV1(
            risk_level=risk,
            requires_approval=risk == "HIGH",
            reasons=tuple(reasons),
        )
