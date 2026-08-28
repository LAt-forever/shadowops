from shadowops.repository.contracts import RevisionNodeV1
from shadowops.rules.engine import StaticRuleEngine

SNAPSHOT_HASH = "a" * 64


def _node() -> RevisionNodeV1:
    return RevisionNodeV1(
        revision="002",
        parent_ids=("001",),
        relative_path="migrations/versions/002_change.py",
        change_kind="NEW",
        has_upgrade=True,
        has_downgrade=True,
        content_hash="b" * 64,
        evidence_id="evidence:revision",
    )


def _findings(source: str):
    return StaticRuleEngine().evaluate(SNAPSHOT_HASH, _node(), source)


def test_sops001_detects_destructive_calls_and_literal_sql_only() -> None:
    findings = _findings(
        """
def upgrade():
    op.drop_column("users", "legacy")
    op.execute("ALTER TABLE accounts DROP COLUMN old_name")
    op.execute(build_sql())

def downgrade():
    op.drop_table("temporary")
"""
    )

    destructive = [finding for finding in findings if finding.rule_id == "SOPS001"]
    assert len(destructive) == 2
    assert all(finding.severity == "HIGH" for finding in destructive)
    assert [finding.line for finding in destructive] == [3, 4]
    assert all(finding.relative_path == _node().relative_path for finding in destructive)
    assert all(finding.evidence_ids[0].startswith("evidence:") for finding in destructive)


def test_sops002_distinguishes_missing_and_present_server_defaults() -> None:
    findings = _findings(
        """
def upgrade():
    op.add_column("users", sa.Column("required", sa.Text(), nullable=False))
    op.add_column(
        "users", sa.Column("defaulted", sa.Text(), nullable=False, server_default="ready")
    )
    op.add_column("users", sa.Column("optional", sa.Text(), nullable=True))

def downgrade():
    op.drop_column("users", "optional")
"""
    )

    not_null = [finding for finding in findings if finding.rule_id == "SOPS002"]
    assert [finding.severity for finding in not_null] == ["HIGH", "MEDIUM"]
    assert all("nullable" in finding.remediation.lower() for finding in not_null)
    assert all(finding.unknowns for finding in not_null)


def test_sops003_matches_only_known_non_concurrent_index_creation() -> None:
    findings = _findings(
        """
def upgrade():
    op.create_index("ix_blocking", "users", ["email"])
    op.create_index("ix_safe", "users", ["name"], postgresql_concurrently=True)
    op.create_index("ix_dynamic", "users", ["age"], postgresql_concurrently=setting)

def downgrade():
    op.drop_index("ix_blocking")
"""
    )

    indexes = [finding for finding in findings if finding.rule_id == "SOPS003"]
    assert len(indexes) == 1
    assert indexes[0].severity == "MEDIUM"
    assert indexes[0].line == 3


def test_sops004_detects_missing_pass_and_explicit_irreversible_downgrades() -> None:
    sources = (
        "def upgrade():\n    pass\n",
        "def upgrade():\n    pass\ndef downgrade():\n    pass\n",
        "def upgrade():\n    pass\ndef downgrade():\n    raise NotImplementedError()\n",
    )

    for source in sources:
        findings = _findings(source)
        irreversible = [finding for finding in findings if finding.rule_id == "SOPS004"]
        assert len(irreversible) == 1
        assert irreversible[0].severity == "HIGH"


def test_safe_nullable_column_with_reversible_downgrade_has_no_findings() -> None:
    findings = _findings(
        """
def upgrade():
    op.add_column("users", sa.Column("nickname", sa.Text(), nullable=True))

def downgrade():
    op.drop_column("users", "nickname")
"""
    )

    assert findings == ()
