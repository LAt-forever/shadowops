"""Build one deterministic, evidence-bearing static report per run."""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from shadowops.domain.errors import RepositoryInputError
from shadowops.repository.contracts import RepoSnapshotV1, RevisionGraphV1
from shadowops.repository.snapshot import SnapshotReader
from shadowops.rules.contracts import (
    RevisionGraphSummaryV1,
    Severity,
    StaticFindingV1,
    StaticReportV1,
)
from shadowops.rules.engine import StaticRuleEngine

_SEVERITY_ORDER: dict[Severity, int] = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}


class StaticAuditService:
    def __init__(
        self,
        snapshot_lookup: Callable[[UUID], RepoSnapshotV1 | None],
        graph_lookup: Callable[[UUID], RevisionGraphV1 | None],
        snapshot_reader: SnapshotReader,
        *,
        max_source_bytes: int = 5 * 1024 * 1024,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
        engine: StaticRuleEngine | None = None,
    ) -> None:
        self._snapshot_lookup = snapshot_lookup
        self._graph_lookup = graph_lookup
        self._snapshot_reader = snapshot_reader
        self._max_source_bytes = max_source_bytes
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory or uuid4
        self._engine = engine or StaticRuleEngine()

    def analyze(self, run_id: UUID, snapshot_id: UUID) -> StaticReportV1:
        snapshot = self._snapshot_lookup(snapshot_id)
        graph = self._graph_lookup(run_id)
        if (
            snapshot is None
            or snapshot.run_id != run_id
            or graph is None
            or graph.snapshot_id != snapshot_id
        ):
            raise RepositoryInputError(
                "SNAPSHOT_INTEGRITY_FAILED", "Static analysis inputs are incomplete"
            )
        changed = set(graph.changed_revisions)
        findings: list[StaticFindingV1] = []
        for node in graph.nodes:
            if node.revision not in changed:
                continue
            source = self._snapshot_reader.read_text(
                snapshot_id, node.relative_path, max_bytes=self._max_source_bytes
            )
            findings.extend(self._engine.evaluate(snapshot.content_hash, node, source.text))
        findings.extend(self._structural_findings(snapshot.content_hash, graph))
        findings.sort(
            key=lambda finding: (
                -_SEVERITY_ORDER[finding.severity],
                finding.relative_path or "",
                finding.line,
                finding.column,
                finding.rule_id,
            )
        )
        risk_level: Severity = (
            max(
                (finding.severity for finding in findings),
                key=_SEVERITY_ORDER.__getitem__,
            )
            if findings
            else "INFO"
        )
        return StaticReportV1(
            id=self._uuid_factory(),
            run_id=run_id,
            snapshot_id=snapshot_id,
            snapshot_hash=snapshot.content_hash,
            diff_mode=snapshot.diff_mode,
            base_commit=snapshot.base_commit,
            head_commit=snapshot.head_commit,
            revision_graph=RevisionGraphSummaryV1(
                supported=graph.supported,
                heads=graph.heads,
                baseline_revision=graph.baseline_revision,
                target_chain=graph.target_chain,
                changed_revisions=graph.changed_revisions,
            ),
            findings=tuple(findings),
            unsupported_reasons=graph.unsupported_reasons,
            risk_level=risk_level,
            created_at=self._clock(),
        )

    @staticmethod
    def _structural_findings(snapshot_hash: str, graph: RevisionGraphV1) -> list[StaticFindingV1]:
        findings: list[StaticFindingV1] = []
        for reason in graph.unsupported_reasons:
            relative_path = reason.relative_path
            digest = hashlib.sha256(
                f"{snapshot_hash}:{relative_path or ''}:1:0:STRUCTURE:{reason.code}".encode()
            ).hexdigest()
            findings.append(
                StaticFindingV1(
                    rule_id="STRUCTURE",
                    rule_version="1.0",
                    severity="HIGH",
                    confidence=1.0,
                    relative_path=relative_path,
                    line=1,
                    column=0,
                    message=f"Repository structure is unsupported: {reason.detail}",
                    remediation=(
                        "Resolve the revision graph ambiguity before relying on dynamic audit."
                    ),
                    evidence_ids=(f"evidence:{digest}",),
                    unknowns=("No migration execution plan can be trusted for this structure.",),
                )
            )
        return findings
