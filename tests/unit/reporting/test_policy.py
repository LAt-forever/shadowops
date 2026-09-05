from shadowops.reporting.policy import PolicyEngine

REQUIRED = frozenset({"SEED_SUMMARY", "SCHEMA_FINGERPRINT", "SMOKE_SUMMARY", "ROLLBACK_ROUNDTRIP"})


def test_model_cannot_lower_static_or_dynamic_risk_floor() -> None:
    decision = PolicyEngine().evaluate(
        static_risk="HIGH",
        model_risk="INFO",
        execution_failures=("ROLLBACK_FAILED",),
        evidence_kinds=REQUIRED,
        coverage_gaps=(),
    )

    assert decision.risk_level == "HIGH"
    assert decision.requires_approval is True
    assert "dynamic_failure:ROLLBACK_FAILED" in decision.reasons


def test_missing_mandatory_evidence_is_high_and_coverage_gap_is_not_success() -> None:
    missing = PolicyEngine().evaluate(
        static_risk="INFO",
        model_risk="INFO",
        execution_failures=(),
        evidence_kinds=frozenset(),
        coverage_gaps=(),
    )
    covered_with_gap = PolicyEngine().evaluate(
        static_risk="INFO",
        model_risk="LOW",
        execution_failures=(),
        evidence_kinds=REQUIRED,
        coverage_gaps=("unknown_production_distribution",),
    )

    assert missing.risk_level == "HIGH"
    assert covered_with_gap.risk_level == "MEDIUM"
