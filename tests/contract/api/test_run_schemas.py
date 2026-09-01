from uuid import uuid4

import pytest
from pydantic import ValidationError

from shadowops.api.schemas.runs import (
    AuditRunViewV1,
    CancelAuditRunRequestV1,
    CreateAuditRunRequestV1,
    DiffMode,
)
from shadowops.domain.runs import RunState
from shadowops.rules.contracts import StaticFindingV1


def test_create_run_defaults_to_working_tree_and_normalizes_path() -> None:
    request = CreateAuditRunRequestV1(repository_path="  projects/demo  ")

    assert request.repository_path == "projects/demo"
    assert request.diff_mode is DiffMode.WORKING_TREE
    assert request.base_ref is None
    assert request.head_ref is None


def test_range_diff_requires_both_refs() -> None:
    with pytest.raises(ValidationError):
        CreateAuditRunRequestV1(
            repository_path="projects/demo",
            diff_mode=DiffMode.RANGE,
            base_ref="main",
        )


def test_working_tree_rejects_range_refs() -> None:
    with pytest.raises(ValidationError):
        CreateAuditRunRequestV1(
            repository_path="projects/demo",
            base_ref="main",
            head_ref="feature",
        )


def test_create_run_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CreateAuditRunRequestV1(repository_path="projects/demo", shell_command="rm -rf repo")


@pytest.mark.parametrize(
    "repository_path", ["/projects/demo", "../demo", "projects//demo", "projects/./demo"]
)
def test_create_run_rejects_unsafe_repository_paths(repository_path: str) -> None:
    with pytest.raises(ValidationError):
        CreateAuditRunRequestV1(repository_path=repository_path)


def test_cancel_requires_a_positive_expected_version() -> None:
    with pytest.raises(ValidationError):
        CancelAuditRunRequestV1(expected_version=0)


def test_run_view_exposes_versioned_static_audit_identity() -> None:
    run_id = uuid4()

    view = AuditRunViewV1(id=run_id, state=RunState.QUEUED, version=1)

    assert view.model_dump(mode="json") == {
        "schema_version": "1.0",
        "id": str(run_id),
        "state": "QUEUED",
        "version": 1,
        "execution_profile": "m5.dynamic-evidence.v1",
        "failure_code": None,
        "cancel_requested_at": None,
        "created_at": None,
        "updated_at": None,
        "completed_at": None,
        "links": {},
    }


def test_static_finding_contract_rejects_unversioned_extra_fields() -> None:
    with pytest.raises(ValidationError):
        StaticFindingV1(
            rule_id="SOPS001",
            rule_version="1.0",
            severity="HIGH",
            confidence=1.0,
            relative_path="migrations/versions/002.py",
            line=3,
            column=4,
            message="Destructive operation",
            remediation="Use a phased rollout",
            evidence_ids=("evidence:abc",),
            shell_command="dropdb production",  # type: ignore[call-arg]
        )
