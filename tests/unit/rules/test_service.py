from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from shadowops.repository.contracts import (
    RepoSnapshotV1,
    RevisionGraphV1,
    UnsupportedReasonV1,
)
from shadowops.repository.snapshot import SnapshotReader
from shadowops.rules.service import StaticAuditService

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
SNAPSHOT_ID = UUID("22222222-2222-4222-8222-222222222222")
GRAPH_ID = UUID("33333333-3333-4333-8333-333333333333")
REPORT_ID = UUID("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 8, 28, tzinfo=UTC)


class UnusedReader:
    def read_text(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("unsupported structure should not require revision source")


def test_unsupported_revision_graph_becomes_a_high_risk_structural_finding() -> None:
    snapshot = RepoSnapshotV1(
        id=SNAPSHOT_ID,
        run_id=RUN_ID,
        source_path_hash="a" * 64,
        diff_mode="WORKING_TREE",
        head_commit="b" * 40,
        dirty_diff_hash="c" * 64,
        content_hash="d" * 64,
        artifact_uri=f"artifact://snapshots/{'d' * 64}",
        file_count=1,
        total_bytes=10,
        created_at=NOW,
    )
    graph = RevisionGraphV1(
        id=GRAPH_ID,
        run_id=RUN_ID,
        snapshot_id=SNAPSHOT_ID,
        diff_mode="WORKING_TREE",
        head_commit="b" * 40,
        nodes=(),
        heads=("one", "two"),
        target_chain=(),
        changed_revisions=(),
        supported=False,
        unsupported_reasons=(
            UnsupportedReasonV1(
                code="MULTIPLE_REVISION_HEADS",
                detail="Multiple Alembic revision heads are unsupported",
            ),
        ),
        created_at=NOW,
    )
    service = StaticAuditService(
        lambda snapshot_id: snapshot if snapshot_id == SNAPSHOT_ID else None,
        lambda run_id: graph if run_id == RUN_ID else None,
        cast(SnapshotReader, UnusedReader()),
        clock=lambda: NOW,
        uuid_factory=lambda: REPORT_ID,
    )

    report = service.analyze(RUN_ID, SNAPSHOT_ID)

    assert report.risk_level == "HIGH"
    assert report.revision_graph.supported is False
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "STRUCTURE"
    assert report.findings[0].evidence_ids[0].startswith("evidence:")
    assert report.unsupported_reasons[0].code == "MULTIPLE_REVISION_HEADS"
