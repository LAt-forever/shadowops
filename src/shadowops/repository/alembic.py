"""Static Alembic revision discovery that never imports repository code."""

import ast
import configparser
import hashlib
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import UUID, uuid4

from shadowops.domain.errors import RepositoryInputError
from shadowops.repository.contracts import (
    RepoSnapshotV1,
    RevisionGraphV1,
    RevisionNodeV1,
    SnapshotManifestV1,
    UnsupportedReasonV1,
)
from shadowops.repository.security import safe_relative_path


class AlembicDiscoveryService:
    def __init__(
        self,
        artifact_root: Path,
        snapshot_lookup: Callable[[UUID], RepoSnapshotV1 | None],
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._artifact_root = artifact_root
        self._snapshot_lookup = snapshot_lookup
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory or uuid4

    def discover(self, snapshot_id: UUID) -> RevisionGraphV1:
        snapshot = self._snapshot_lookup(snapshot_id)
        if snapshot is None:
            raise RepositoryInputError("SNAPSHOT_INTEGRITY_FAILED", "Snapshot was not found")
        artifact = self._artifact_root / "snapshots" / snapshot.content_hash
        manifest = self._read_manifest(artifact, snapshot.content_hash)
        entries = {item.path: item for item in manifest.files}
        if "alembic.ini" not in entries:
            raise RepositoryInputError(
                "ALEMBIC_CONFIG_NOT_FOUND", "The repository root has no alembic.ini"
            )
        reasons: list[UnsupportedReasonV1] = []
        script_location = self._script_location(artifact, entries["alembic.ini"].sha256, reasons)
        nodes: list[RevisionNodeV1] = []
        if script_location is not None:
            prefix = f"{script_location.as_posix().rstrip('/')}/versions/"
            changed = {item.path: item.status for item in snapshot.changed_paths}
            for relative_path, entry in sorted(entries.items()):
                if not relative_path.startswith(prefix) or not relative_path.endswith(".py"):
                    continue
                node = self._parse_revision(
                    artifact / "tree" / relative_path,
                    relative_path,
                    entry.sha256,
                    snapshot.content_hash,
                    changed.get(relative_path),
                    reasons,
                )
                if node is not None:
                    nodes.append(node)
            for relative_path, status in sorted(changed.items()):
                if (
                    relative_path.startswith(prefix)
                    and relative_path.endswith(".py")
                    and status.startswith("D")
                ):
                    reasons.append(
                        UnsupportedReasonV1(
                            code="CHANGED_REVISION_SOURCE_MISSING",
                            detail="A changed revision is absent from the target snapshot",
                            relative_path=relative_path,
                        )
                    )
        return self._build_graph(snapshot, tuple(nodes), reasons)

    @staticmethod
    def _read_manifest(artifact: Path, expected_hash: str) -> SnapshotManifestV1:
        try:
            data = (artifact / "manifest.json").read_bytes()
            if hashlib.sha256(data).hexdigest() != expected_hash:
                raise ValueError
            return SnapshotManifestV1.model_validate_json(data)
        except (OSError, ValueError) as exc:
            raise RepositoryInputError(
                "SNAPSHOT_INTEGRITY_FAILED", "Snapshot artifact could not be read"
            ) from exc

    def _script_location(
        self,
        artifact: Path,
        expected_hash: str,
        reasons: list[UnsupportedReasonV1],
    ) -> PurePosixPath | None:
        parser = configparser.ConfigParser(interpolation=None)
        try:
            data = (artifact / "tree" / "alembic.ini").read_bytes()
            if hashlib.sha256(data).hexdigest() != expected_hash:
                raise ValueError
            parser.read_string(data.decode("utf-8", errors="strict"))
            value = parser.get("alembic", "script_location").strip()
        except ValueError as exc:
            raise RepositoryInputError(
                "SNAPSHOT_INTEGRITY_FAILED", "Snapshot content is invalid"
            ) from exc
        except (OSError, UnicodeError, configparser.Error, KeyError):
            reasons.append(
                UnsupportedReasonV1(
                    code="UNSUPPORTED_SCRIPT_LOCATION",
                    detail="alembic.ini has no supported script_location",
                    relative_path="alembic.ini",
                )
            )
            return None
        if value.startswith("%(here)s/"):
            value = value[len("%(here)s/") :]
        if not value or "%" in value or "$" in value or ":" in value or " " in value:
            reasons.append(
                UnsupportedReasonV1(
                    code="UNSUPPORTED_SCRIPT_LOCATION",
                    detail="Only one literal relative script_location is supported",
                    relative_path="alembic.ini",
                )
            )
            return None
        try:
            return safe_relative_path(value)
        except RepositoryInputError:
            reasons.append(
                UnsupportedReasonV1(
                    code="UNSUPPORTED_SCRIPT_LOCATION",
                    detail="script_location must remain within the snapshot",
                    relative_path="alembic.ini",
                )
            )
            return None

    def _parse_revision(
        self,
        path: Path,
        relative_path: str,
        content_hash: str,
        snapshot_hash: str,
        status: str | None,
        reasons: list[UnsupportedReasonV1],
    ) -> RevisionNodeV1 | None:
        try:
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != content_hash:
                raise ValueError
            source = data.decode("utf-8", errors="strict")
            module = ast.parse(source, filename=relative_path)
        except ValueError as exc:
            raise RepositoryInputError(
                "SNAPSHOT_INTEGRITY_FAILED", "Snapshot content is invalid"
            ) from exc
        except (OSError, UnicodeError, SyntaxError):
            reasons.append(
                UnsupportedReasonV1(
                    code="INVALID_REVISION_SOURCE",
                    detail="Revision source could not be parsed safely",
                    relative_path=relative_path,
                )
            )
            return None
        assignments: dict[str, ast.expr] = {}
        functions: set[str] = set()
        for statement in module.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.add(statement.name)
            elif isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        assignments[target.id] = statement.value
            elif (
                isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and statement.value is not None
            ):
                assignments[statement.target.id] = statement.value
        values: dict[str, str | tuple[str, ...] | None] = {}
        dynamic = False
        for name in ("revision", "down_revision"):
            try:
                values[name] = self._literal_metadata(assignments[name])
            except (KeyError, ValueError):
                dynamic = True
                reasons.append(
                    UnsupportedReasonV1(
                        code="DYNAMIC_REVISION_METADATA",
                        detail=f"{name} must be a literal string, string sequence, or null",
                        relative_path=relative_path,
                    )
                )
        for name in ("branch_labels", "depends_on"):
            if name not in assignments:
                values[name] = None
                continue
            try:
                values[name] = self._literal_metadata(assignments[name])
            except ValueError:
                dynamic = True
                reasons.append(
                    UnsupportedReasonV1(
                        code="DYNAMIC_REVISION_METADATA",
                        detail=f"{name} must be a literal string, string sequence, or null",
                        relative_path=relative_path,
                    )
                )
        revision = values.get("revision")
        down_revision = values.get("down_revision")
        for name, code in (
            ("branch_labels", "BRANCH_LABELS_UNSUPPORTED"),
            ("depends_on", "REVISION_DEPENDENCY_UNSUPPORTED"),
        ):
            if values.get(name) not in {None, ()}:
                reasons.append(
                    UnsupportedReasonV1(
                        code=code,
                        detail=f"Non-empty {name} is unsupported",
                        relative_path=relative_path,
                    )
                )
        if dynamic or not isinstance(revision, str):
            return None
        if (
            not revision
            or (isinstance(down_revision, str) and not down_revision)
            or (isinstance(down_revision, tuple) and any(not parent for parent in down_revision))
        ):
            reasons.append(
                UnsupportedReasonV1(
                    code="INVALID_REVISION_METADATA",
                    detail="Revision identifiers must be non-empty literal strings",
                    relative_path=relative_path,
                )
            )
        if isinstance(down_revision, str):
            parent_ids: tuple[str, ...] = (down_revision,)
        elif isinstance(down_revision, tuple):
            parent_ids = down_revision
        else:
            parent_ids = ()
        if status is None:
            change_kind: Literal["UNCHANGED", "NEW", "MODIFIED"] = "UNCHANGED"
        elif status == "??" or status.startswith("A"):
            change_kind = "NEW"
        else:
            change_kind = "MODIFIED"
        evidence = hashlib.sha256(
            f"{snapshot_hash}:{relative_path}:{content_hash}:revision".encode()
        ).hexdigest()
        return RevisionNodeV1(
            revision=revision,
            parent_ids=parent_ids,
            relative_path=relative_path,
            change_kind=change_kind,
            has_upgrade="upgrade" in functions,
            has_downgrade="downgrade" in functions,
            content_hash=content_hash,
            evidence_id=f"evidence:{evidence}",
        )

    @staticmethod
    def _literal_metadata(expression: ast.expr) -> str | tuple[str, ...] | None:
        if isinstance(expression, ast.Constant) and (
            expression.value is None or isinstance(expression.value, str)
        ):
            return expression.value
        if isinstance(expression, (ast.Tuple, ast.List)):
            values: list[str] = []
            for element in expression.elts:
                if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
                    raise ValueError
                values.append(element.value)
            return tuple(values)
        raise ValueError

    def _build_graph(
        self,
        snapshot: RepoSnapshotV1,
        nodes: tuple[RevisionNodeV1, ...],
        reasons: list[UnsupportedReasonV1],
    ) -> RevisionGraphV1:
        counts = Counter(node.revision for node in nodes)
        duplicates = sorted(revision for revision, count in counts.items() if count > 1)
        for revision in duplicates:
            reasons.append(
                UnsupportedReasonV1(
                    code="DUPLICATE_REVISION_ID", detail=f"Duplicate revision ID: {revision}"
                )
            )
        by_id = {node.revision: node for node in nodes if counts[node.revision] == 1}
        for node in nodes:
            if len(node.parent_ids) > 1:
                reasons.append(
                    UnsupportedReasonV1(
                        code="MERGE_REVISION_UNSUPPORTED",
                        detail=f"Merge revision is unsupported: {node.revision}",
                        relative_path=node.relative_path,
                    )
                )
            for parent in node.parent_ids:
                if parent not in by_id:
                    reasons.append(
                        UnsupportedReasonV1(
                            code="MISSING_REVISION_PARENT",
                            detail=f"Revision parent is missing: {parent}",
                            relative_path=node.relative_path,
                        )
                    )
        parents = {parent for node in nodes for parent in node.parent_ids}
        heads = tuple(sorted(node.revision for node in nodes if node.revision not in parents))
        if nodes and self._has_cycle(by_id):
            reasons.append(
                UnsupportedReasonV1(
                    code="REVISION_CYCLE", detail="Alembic revision graph contains a cycle"
                )
            )
        if len(heads) > 1:
            reasons.append(
                UnsupportedReasonV1(
                    code="MULTIPLE_REVISION_HEADS",
                    detail="Multiple Alembic revision heads are unsupported",
                )
            )
        chain: list[str] = []
        if len(heads) == 1:
            current: str | None = heads[0]
            seen: set[str] = set()
            reverse_chain: list[str] = []
            while current is not None and current in by_id:
                if current in seen:
                    reasons.append(
                        UnsupportedReasonV1(
                            code="REVISION_CYCLE", detail="Alembic revision graph contains a cycle"
                        )
                    )
                    break
                seen.add(current)
                reverse_chain.append(current)
                parent_ids = by_id[current].parent_ids
                current = parent_ids[0] if len(parent_ids) == 1 else None
            chain = list(reversed(reverse_chain))
            if len(seen) != len(by_id):
                reasons.append(
                    UnsupportedReasonV1(
                        code="DISCONNECTED_REVISION_GRAPH",
                        detail="Alembic revisions do not form one connected chain",
                    )
                )
        changed = tuple(node.revision for node in nodes if node.change_kind != "UNCHANGED")
        ordered_changed = tuple(revision for revision in chain if revision in set(changed))
        positions = [chain.index(revision) for revision in ordered_changed]
        changed_is_contiguous = not positions or positions == list(
            range(positions[0], positions[0] + len(positions))
        )
        if changed and (set(ordered_changed) != set(changed) or not changed_is_contiguous):
            reasons.append(
                UnsupportedReasonV1(
                    code="CHANGED_REVISIONS_NOT_LINEAR",
                    detail="Changed revisions do not form one target chain",
                )
            )
        baseline = None
        if ordered_changed:
            first = by_id.get(ordered_changed[0])
            baseline = (
                first.parent_ids[0] if first is not None and len(first.parent_ids) == 1 else None
            )
        if not nodes and not reasons:
            reasons.append(
                UnsupportedReasonV1(
                    code="NO_REVISIONS_FOUND", detail="No Alembic revisions were found"
                )
            )
        unique_reasons = tuple(
            {
                (reason.code, reason.detail, reason.relative_path): reason for reason in reasons
            }.values()
        )
        return RevisionGraphV1(
            id=self._uuid_factory(),
            run_id=snapshot.run_id,
            snapshot_id=snapshot.id,
            diff_mode=snapshot.diff_mode,
            base_commit=snapshot.base_commit,
            head_commit=snapshot.head_commit,
            nodes=tuple(sorted(nodes, key=lambda node: (node.relative_path, node.revision))),
            heads=heads,
            baseline_revision=baseline,
            target_chain=tuple(chain),
            changed_revisions=ordered_changed,
            supported=not unique_reasons and bool(nodes),
            unsupported_reasons=unique_reasons,
            created_at=self._clock(),
        )

    @staticmethod
    def _has_cycle(by_id: dict[str, RevisionNodeV1]) -> bool:
        visited: set[str] = set()
        active: set[str] = set()

        def visit(revision: str) -> bool:
            if revision in active:
                return True
            if revision in visited:
                return False
            visited.add(revision)
            active.add(revision)
            node = by_id[revision]
            cyclic = any(parent in by_id and visit(parent) for parent in node.parent_ids)
            active.remove(revision)
            return cyclic

        return any(visit(revision) for revision in by_id)
