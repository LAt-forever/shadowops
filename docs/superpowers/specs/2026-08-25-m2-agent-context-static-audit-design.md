# M2 Agent Context and Static Audit Design

> Status: approved in conversation on 2026-08-25

## 1. Goal

M2 turns a local Alembic repository into trusted, immutable, versioned observations that a later bounded Agent can consume. It delivers a small real-value slice: securely snapshot one repository, statically discover a linear Alembic revision chain without importing repository code, run four deterministic safety rules, and expose one evidence-bearing JSON report.

M2 is intentionally not a general source scanner. The project is Agent-first: M2 provides the future Agent tools `discover_migrations`, `read_revision`, `run_static_rules`, and `get_static_report`; M3 will add the bounded `Plan -> Tool Call -> Observation -> Re-plan -> Report` loop.

## 2. Delivery Split

M2 is delivered as two reviewable pull requests.

### M2A: Secure Discovery

Branch: `codex/m2a-secure-discovery`

- Validate the repository against one configured allowed root.
- Resolve `WORKING_TREE` and `RANGE` Git selectors.
- Create a content-addressed immutable snapshot.
- Parse one Alembic script location and revision metadata with Python AST only.
- Persist `RepoSnapshotV1` metadata and `RevisionGraphV1`.
- Replace the M1 `DISCOVERING` no-op with a real discovery stage.
- Do not add a public discovery endpoint.

### M2B: Static Audit

Branch: `codex/m2b-static-audit`, created after M2A merges.

- Replace the M1 `STATIC_ANALYSIS` no-op with a deterministic rule stage.
- Implement four migration-content rules plus structural findings derived from discovery.
- Persist `StaticReportV1` as versioned JSON.
- Add `GET /api/v1/runs/{id}/static-report`.
- Add safe and dangerous fixture repositories and black-box E2E coverage.

## 3. Scope Constraints

- Python 3.12 and the existing FastAPI, Celery, PostgreSQL, Redis, SQLAlchemy, Alembic, Pydantic, and Docker Compose baseline remain unchanged.
- Only local Git repositories under one explicit allowed root are accepted.
- Only PostgreSQL/Alembic and a single Alembic script location are supported.
- Repository Python is never imported or executed on the host/API/worker.
- Repository input is read-only; ShadowOps never commits, checks out, or edits the source repository.
- Snapshot and rule results are authoritative in PostgreSQL; Redis remains delivery-only.
- No Web UI, CLI report view, LLM Provider, Agent runtime, Tool Gateway, Migration execution, or shadow database is added in M2.
- M2 contains four migration-content rules, not the original ten-rule plan.
- No performance, accuracy, or reliability-rate claims are made without later measurement.

## 4. Configuration and Mounts

The application settings add:

```text
SHADOWOPS_REPO_ROOT=/repositories
SHADOWOPS_ARTIFACT_ROOT=/var/lib/shadowops/artifacts
SHADOWOPS_SNAPSHOT_MAX_FILES=10000
SHADOWOPS_SNAPSHOT_MAX_FILE_BYTES=5242880
SHADOWOPS_SNAPSHOT_MAX_TOTAL_BYTES=104857600
SHADOWOPS_SNAPSHOT_READ_CHUNK_BYTES=1048576
```

Compose uses a host-only interpolation variable:

```text
SHADOWOPS_REPO_ROOT_HOST=<explicit host directory>
```

The host directory is mounted read-only at `/repositories` in API and worker containers. A named artifact volume is mounted at `/var/lib/shadowops/artifacts`. The default development Compose configuration mounts only the versioned fixture root; scanning another local root requires explicit configuration.

Clients cannot override repository roots, artifact roots, exclusions, or resource budgets.

## 5. Allowed-Root and File Policy

`repository_path` remains a relative API field. API validation provides an early error, but the worker repeats the complete validation immediately before snapshotting and is the authoritative security boundary.

Validation rules:

- Reject absolute paths, empty path components, `..`, and NUL.
- Resolve the configured allowed root and candidate repository with strict filesystem resolution.
- Require the candidate to remain beneath the allowed root.
- Require a Git worktree with a readable `.git` control path.
- Reject the repository entry or any included tree entry that is a symlink.
- Reject hard links with a link count greater than one, devices, FIFOs, sockets, and other non-regular files.
- Walk without following symlinks.
- Open files with `O_NOFOLLOW` where available and verify the opened descriptor with `fstat` before reading.
- Read in bounded chunks while enforcing per-file and total-byte limits.
- If a file identity or metadata changes between inspection and completion, abort the snapshot instead of publishing mixed content.

Default excluded content:

- `.git`
- `.venv`, `venv`, `env`
- `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`
- `node_modules`, `dist`, `build`
- `.env`, `.env.*` except `.env.example`
- common private-key and credential filenames such as `id_rsa`, `id_ed25519`, `*.pem`, `*.key`, and cloud credential directories

Budget exhaustion fails the entire operation; partial snapshots are never exposed.

## 6. Git Selector Semantics

Git is invoked with argument arrays and never through a shell. Repository-controlled external execution is disabled with explicit Git configuration and flags, including hooks, filesystem monitor, external diff, and text conversion.

### WORKING_TREE

- Resolve the current `HEAD` to an immutable commit SHA.
- Snapshot tracked files in their current working-tree form plus non-ignored untracked files.
- Deleted tracked files are absent from the snapshot and represented in the canonical change manifest.
- Record the resolved `HEAD`, a canonical tracked diff hash, and untracked file content hashes.
- The dirty-diff hash is derived from sorted canonical change records, not terminal-formatted output.

### RANGE

- Resolve `base_ref` and `head_ref` with `--end-of-options` to immutable commit SHAs.
- Require base to be an ancestor of head.
- Snapshot only the resolved head commit contents; live working-tree modifications are ignored.
- Read the Git tree without checking it out into the source repository.
- Reject symlink, submodule, traversal, device, and over-budget entries before artifact publication.
- Record changed paths and statuses from the resolved base/head commit pair.

The selector records whether each migration is new or modifies an existing revision. Non-ancestor ranges, invalid refs, and corrupted object data fail with stable errors.

## 7. Immutable Snapshot Artifact

Artifact layout:

```text
SHADOWOPS_ARTIFACT_ROOT/
└── snapshots/
    └── <content-sha256>/
        ├── manifest.json
        └── tree/
```

The manifest contains sorted relative POSIX paths, byte sizes, normalized regular-file modes, and per-file SHA-256 values. Snapshot `content_hash` is the SHA-256 of the canonical JSON manifest. Absolute source paths are never included.

Publication protocol:

1. Create a sibling temporary directory with an unpredictable identifier.
2. Copy bounded verified content into `tree/` while calculating hashes.
3. Write and re-read canonical `manifest.json`.
4. Verify every file and the aggregate content hash.
5. Atomically rename the temporary directory to the content-hash path.
6. If the target already exists, verify its manifest and reuse it; a mismatch is `SNAPSHOT_INTEGRITY_FAILED`.
7. Remove temporary content in `finally` on failure or cancellation.

Database rollback does not delete a valid published content-addressed artifact. M2 does not add a general garbage collector; only stale temporary directories are eligible for simple maintenance cleanup.

The database stores an internal `artifact://snapshots/<content-sha256>` URI, never a host absolute path.

## 8. Persistence

### `repo_snapshots`

- `id` UUID primary key
- `run_id` UUID unique foreign key to `audit_runs`
- `schema_version` string
- `source_path_hash` SHA-256 string
- `diff_mode` string
- `base_commit` nullable commit SHA
- `head_commit` commit SHA
- `dirty_diff_hash` nullable SHA-256 string
- `content_hash` SHA-256 string
- `artifact_uri` string
- `file_count` positive integer
- `total_bytes` non-negative integer
- `created_at` timezone-aware timestamp

### `revision_graphs`

- `id` UUID primary key
- `run_id` UUID unique foreign key to `audit_runs`
- `snapshot_id` UUID foreign key to `repo_snapshots`
- `schema_version` string
- `supported` boolean
- `nodes` JSON
- `target_chain` JSON
- `changed_revisions` JSON
- `unsupported_reasons` JSON
- `created_at` timezone-aware timestamp

M2B adds `static_reports` with a unique `run_id`, `snapshot_id`, `schema_version`, `ruleset_version`, `risk_level`, full versioned report JSON, and timestamp. Individual findings are deliberately not normalized into a separate M2 table.

Unique constraints make retries converge on the existing row. Repository adapters expose create-or-get operations and reject an existing row whose immutable identity differs.

## 9. Agent-Ready Application Interfaces

```python
class SnapshotService:
    def create(self, request: SnapshotRequestV1) -> RepoSnapshotV1: ...


class SnapshotReader:
    def read_text(
        self,
        snapshot_id: UUID,
        relative_path: str,
        *,
        max_bytes: int,
    ) -> SnapshotTextV1: ...


class AlembicDiscoveryService:
    def discover(self, snapshot_id: UUID) -> RevisionGraphV1: ...


class StaticAuditService:
    def analyze(self, run_id: UUID, snapshot_id: UUID) -> StaticReportV1: ...
```

The services do not depend on Celery or HTTP. M3 can wrap them as strongly typed tools without exposing the repository root or artifact filesystem.

`SnapshotReader` accepts only snapshot-relative paths present in the verified manifest. It has an independent response-size limit and returns source text plus its content hash and evidence identity.

## 10. Alembic Discovery

### Configuration

- Locate one `alembic.ini` in the repository root.
- Parse without code execution or general interpolation.
- Support a literal relative `script_location` and the explicit `%(here)s/<relative-path>` form.
- Require the resolved script location to remain within the snapshot.
- Package locations, multiple locations, environment expansion, and dynamic configuration produce unsupported reasons.
- Never import or run `env.py`.

### Revision AST

Parse only top-level assignments for:

- `revision`
- `down_revision`
- `branch_labels`
- `depends_on`

Values must be safely representable as literal string, tuple/list of strings, or `None`. Any name lookup, call, formatted string, attribute access, or expression is `DYNAMIC_REVISION_METADATA`. Function bodies are retained as AST only to locate operations and determine whether `upgrade()` and `downgrade()` exist.

`RevisionNodeV1` contains revision ID, parent IDs, relative source location, changed/new/modified classification, upgrade/downgrade presence, file content hash, and a stable evidence ID.

`RevisionGraphV1` contains selector metadata, nodes, heads, baseline revision, ordered target chain, changed revisions, `supported`, and structured unsupported reasons.

`supported=true` requires:

- one script location
- literal unique revision IDs and parents
- no missing parent, duplicate ID, cycle, merge node, or multiple target head
- changed target revisions forming one continuous linear chain

The parser returns all trustworthy partial data when unsupported. It never guesses dynamic values.

## 11. Static Rules

M2B implements four deterministic migration-content rules.

### `SOPS001` Destructive DDL

Detect `op.drop_table`, `op.drop_column`, and `op.drop_index` in `upgrade()`, plus the equivalent `DROP TABLE/COLUMN/INDEX` in literal-string `op.execute()` calls. Severity is `HIGH`. Destructive calls in a valid `downgrade()` are not findings for this rule.

### `SOPS002` Direct NOT NULL Addition

Detect `op.add_column(..., sa.Column(..., nullable=False, ...))` in `upgrade()`.

- Without `server_default`: `HIGH`
- With a non-null server default: `MEDIUM`, with table rewrite/lock behavior explicitly marked unknown for production
- Nullable additions do not match

M2 does not claim to prove arbitrary multi-revision backfill strategies safe.

### `SOPS003` Non-Concurrent Index

Detect `op.create_index()` without `postgresql_concurrently=True`. Severity is `MEDIUM`; the report describes potential write blocking and marks production table size and duration unknown.

### `SOPS004` Missing or Explicitly Irreversible Downgrade

Detect a missing `downgrade()`, a body containing only `pass`, or an explicit `raise NotImplementedError`. Severity is `HIGH`. M2 does not attempt general semantic inversion proof.

Dynamic `op.execute()` content is not executed and is never reported as a known destructive statement. It may add an unknown observation under the applicable rule without claiming a specific DDL fact.

Discovery unsupported reasons produce one separate structural `HIGH` finding. This is a trust-boundary finding and is not counted as a fifth migration-content rule.

## 12. Findings and Static Report

`StaticFindingV1` contains:

- `rule_id` and `rule_version`
- `severity`: `INFO | LOW | MEDIUM | HIGH`
- `confidence`
- relative file path, line, and column
- factual message
- remediation guidance
- evidence IDs
- `observation_scope="static_source"`
- explicit unknowns

Source evidence IDs are deterministic from snapshot content hash, relative path, source location, and rule ID. Findings are sorted by severity, path, line, column, and rule ID before persistence.

`StaticReportV1` contains schema version, run ID, snapshot hash, selector/commit metadata, revision graph summary, findings, unsupported reasons, deterministic maximum risk, ruleset version, and generation timestamp.

The only new M2 public query is:

```text
GET /api/v1/runs/{run_id}/static-report
```

- Unknown run: `404 RUN_NOT_FOUND`
- Existing run without a committed report: `409 STATIC_REPORT_NOT_READY`
- Completed report: `200 StaticReportV1`

`AuditRunViewV1.links` adds `static_report`.

## 13. Worker Integration

M2 adds a deliberately small explicit handler map:

```python
handlers = {
    RunState.DISCOVERING: DiscoveryStageHandler(...),
    RunState.STATIC_ANALYSIS: StaticAnalysisStageHandler(...),
}
```

It is not a general plugin framework. Other M1 stages continue using `m1.noop.v1`.

Execution sequence:

1. Claim the versioned step using the existing fencing token.
2. Check cancellation.
3. Execute the handler outside a long database transaction.
4. Renew the step lease after Git selection, snapshot publication, discovery, and rule evaluation boundaries.
5. Persist the immutable result with create-or-get semantics.
6. Re-check cancellation and claim ownership.
7. Optimistically transition the run and enqueue the next outbox event.

The execution service gains an explicit failed-step path. Trusted-input failures mark the step and run `FAILED`, persist a stable code, and do not enqueue another event. Unsupported but trustworthy discovery succeeds and later becomes a high-risk static finding.

M2 does not add a heartbeat thread. Bounded input sizes and phase checkpoints keep the implementation small; later dynamic tools may introduce a reusable periodic heartbeat if measured execution duration requires it.

## 14. Idempotency and Recovery

- Snapshot content hashes make artifact publication idempotent.
- One run converges on one snapshot, one revision graph, and one static report.
- A crash after artifact rename but before database commit reuses the verified artifact.
- A crash after database commit is rejected by the existing run version and step key.
- A reclaimed expired step gets a new claim token; the old worker cannot finalize.
- Cancellation is cooperative at phase boundaries and removes unpublished temporary directories in `finally`.
- PostgreSQL is authoritative; Redis duplicates cannot duplicate durable logical results.

## 15. Stable Failure Codes

- `REPOSITORY_OUTSIDE_ALLOWED_ROOT`
- `REPOSITORY_NOT_FOUND`
- `REPOSITORY_NOT_GIT`
- `REPOSITORY_SYMLINK_REJECTED`
- `REPOSITORY_FILE_UNSUPPORTED`
- `REPOSITORY_CHANGED_DURING_SNAPSHOT`
- `SNAPSHOT_LIMIT_EXCEEDED`
- `SNAPSHOT_INTEGRITY_FAILED`
- `GIT_SELECTOR_INVALID`
- `GIT_RANGE_NOT_LINEAR`
- `GIT_OBJECT_INVALID`
- `ALEMBIC_CONFIG_NOT_FOUND`
- `STATIC_REPORT_NOT_READY`

Raw exceptions and host absolute paths remain internal and are not returned in API details.

## 16. Test Strategy

### M2A Unit and Security Tests

- absolute path, `..`, and resolved-root escape
- repository and internal symlinks
- hard links, devices, FIFO, and socket entries
- file count, per-file, and total-byte budgets
- file replacement during snapshot
- excluded VCS, cache, build, and credential content
- deterministic snapshot and dirty-diff hashes
- valid/invalid refs, non-ancestor range, submodule, and corrupt object data
- archive traversal and over-budget Git objects
- literal and dynamic Alembic metadata
- linear chain, missing parent, duplicate ID, cycle, multiple heads, and merge node
- a fixture module with import-time side effects proving no import occurs

### M2B Rule Tests

Each rule has at least one match and one non-match. Tests assert location, evidence ID, severity, confidence, remediation, and unknowns. Dynamic expressions must not become known facts.

### Fixtures

- `safe_add_column`: add a nullable column with a reversible downgrade
- `dangerous_drop`: drop a column in upgrade and omit or reject downgrade

Versioned fixture source is copied to a temporary directory where tests create deterministic Git history. Nested `.git` directories and network dependencies are not committed.

### Integration and E2E

M2A verifies PostgreSQL persistence, duplicate delivery convergence, worker recovery using the same artifact, and reliable `FAILED` transitions for security violations.

M2B verifies `POST /runs -> discovery -> static analysis -> static report -> COMPLETED`, safe/dangerous fixture outcomes, API/worker restart queryability, report idempotency, and stable report-not-ready errors.

CI adds repository security tests to the normal quality lane. PostgreSQL/Redis/Compose behavior stays in the integration lane. M2 has no live LLM test.

## 17. Acceptance Criteria

M2A is complete when a permitted fixture produces a deterministic persisted snapshot and linear revision graph, repeated messages converge, worker restart recovers, and traversal/symlink/oversize/dynamic metadata cases have the specified safe outcomes without importing repository code.

M2B is complete when the safe fixture produces no high-risk content finding, the dangerous fixture produces a located evidence-bearing `HIGH` finding, the report remains queryable after restart, unsupported graph structure becomes a high-risk report rather than a guessed chain, and all current quality/integration/E2E gates pass from clean test state.

## 18. Deferred Work

- the other six original static rules
- general Python/SQL data-flow analysis
- execution of `env.py` or migrations
- multiple script locations and branch/merge support
- complex artifact garbage collection
- CLI or Web report rendering
- Agent/LLM/Tool Gateway implementation
- shadow PostgreSQL dynamic execution
- production repositories, remote Git, multiple databases, Kubernetes, OAuth, or multi-tenancy
