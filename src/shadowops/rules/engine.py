"""AST-only rules for changed Alembic revision source."""

import ast
import hashlib
import re

from shadowops.repository.contracts import RevisionNodeV1
from shadowops.rules.contracts import Severity, StaticFindingV1

_DESTRUCTIVE_SQL = re.compile(r"\bDROP\s+(TABLE|COLUMN|INDEX)\b", re.IGNORECASE)
_DESTRUCTIVE_CALLS = {"drop_table", "drop_column", "drop_index"}


class StaticRuleEngine:
    """Evaluate the four M2 content rules without executing repository code."""

    def evaluate(
        self, snapshot_hash: str, node: RevisionNodeV1, source: str
    ) -> tuple[StaticFindingV1, ...]:
        try:
            module = ast.parse(source, filename=node.relative_path)
        except SyntaxError:
            return ()
        upgrade = self._function(module, "upgrade")
        downgrade = self._function(module, "downgrade")
        findings: list[StaticFindingV1] = []
        if upgrade is not None:
            findings.extend(self._destructive_ddl(snapshot_hash, node, upgrade))
            findings.extend(self._direct_not_null(snapshot_hash, node, upgrade))
            findings.extend(self._non_concurrent_index(snapshot_hash, node, upgrade))
        irreversible = self._irreversible_downgrade(snapshot_hash, node, downgrade)
        if irreversible is not None:
            findings.append(irreversible)
        return tuple(findings)

    @staticmethod
    def _function(module: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        for statement in module.body:
            if (
                isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
                and statement.name == name
            ):
                return statement
        return None

    def _destructive_ddl(
        self,
        snapshot_hash: str,
        node: RevisionNodeV1,
        upgrade: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> list[StaticFindingV1]:
        findings: list[StaticFindingV1] = []
        for expression in ast.walk(upgrade):
            if not isinstance(expression, ast.Call):
                continue
            call = self._op_call(expression)
            operation: str | None = None
            confidence = 1.0
            if call in _DESTRUCTIVE_CALLS:
                operation = call
            elif call == "execute" and expression.args:
                sql = expression.args[0]
                if isinstance(sql, ast.Constant) and isinstance(sql.value, str):
                    match = _DESTRUCTIVE_SQL.search(sql.value)
                    if match is not None:
                        operation = f"literal DROP {match.group(1).upper()} SQL"
                        confidence = 0.95
            if operation is None:
                continue
            findings.append(
                self._finding(
                    snapshot_hash,
                    node,
                    expression,
                    rule_id="SOPS001",
                    severity="HIGH",
                    confidence=confidence,
                    message=f"Upgrade performs destructive operation: {operation}",
                    remediation=(
                        "Use an expand/migrate/contract rollout and retain compatibility until "
                        "dependent code and data have been migrated."
                    ),
                    unknowns=("Production dependencies and retained data are unknown.",),
                )
            )
        return findings

    def _direct_not_null(
        self,
        snapshot_hash: str,
        node: RevisionNodeV1,
        upgrade: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> list[StaticFindingV1]:
        findings: list[StaticFindingV1] = []
        for expression in ast.walk(upgrade):
            if not isinstance(expression, ast.Call) or self._op_call(expression) != "add_column":
                continue
            column = self._column_argument(expression)
            if column is None or not self._keyword_is(column, "nullable", False):
                continue
            server_default = self._keyword(column, "server_default")
            has_non_null_default = server_default is not None and not (
                isinstance(server_default, ast.Constant) and server_default.value is None
            )
            findings.append(
                self._finding(
                    snapshot_hash,
                    node,
                    expression,
                    rule_id="SOPS002",
                    severity="MEDIUM" if has_non_null_default else "HIGH",
                    confidence=1.0,
                    message=(
                        "Upgrade directly adds a NOT NULL column with a server default."
                        if has_non_null_default
                        else "Upgrade directly adds a NOT NULL column without a server default."
                    ),
                    remediation=(
                        "Add the column as nullable, backfill in bounded batches, then enforce "
                        "NOT NULL in a later migration."
                    ),
                    unknowns=(
                        "Production table size, PostgreSQL rewrite behavior, and lock duration "
                        "are unknown.",
                    ),
                )
            )
        return findings

    def _non_concurrent_index(
        self,
        snapshot_hash: str,
        node: RevisionNodeV1,
        upgrade: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> list[StaticFindingV1]:
        findings: list[StaticFindingV1] = []
        for expression in ast.walk(upgrade):
            if not isinstance(expression, ast.Call) or self._op_call(expression) != "create_index":
                continue
            concurrent = self._keyword(expression, "postgresql_concurrently")
            if concurrent is not None and not isinstance(concurrent, ast.Constant):
                continue
            if isinstance(concurrent, ast.Constant) and concurrent.value is True:
                continue
            findings.append(
                self._finding(
                    snapshot_hash,
                    node,
                    expression,
                    rule_id="SOPS003",
                    severity="MEDIUM",
                    confidence=1.0,
                    message="Upgrade creates a PostgreSQL index without concurrent mode.",
                    remediation=(
                        "Use PostgreSQL concurrent index creation with the required Alembic "
                        "autocommit handling."
                    ),
                    unknowns=("Production table size and write-blocking duration are unknown.",),
                )
            )
        return findings

    def _irreversible_downgrade(
        self,
        snapshot_hash: str,
        node: RevisionNodeV1,
        downgrade: ast.FunctionDef | ast.AsyncFunctionDef | None,
    ) -> StaticFindingV1 | None:
        target: ast.AST
        message: str
        if downgrade is None:
            target = ast.parse("pass").body[0]
            message = "Revision has no downgrade function."
        else:
            body = [
                statement
                for statement in downgrade.body
                if not (
                    isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)
                )
            ]
            if body and all(isinstance(statement, ast.Pass) for statement in body):
                target = downgrade
                message = "Revision downgrade contains only pass."
            else:
                irreversible = next(
                    (
                        expression
                        for expression in ast.walk(downgrade)
                        if isinstance(expression, ast.Raise)
                        and self._is_not_implemented(expression.exc)
                    ),
                    None,
                )
                if irreversible is None:
                    return None
                target = irreversible
                message = "Revision downgrade explicitly raises NotImplementedError."
        return self._finding(
            snapshot_hash,
            node,
            target,
            rule_id="SOPS004",
            severity="HIGH",
            confidence=1.0,
            message=message,
            remediation="Implement and test a deterministic downgrade for this revision.",
            unknowns=("Rollback behavior has not been dynamically executed.",),
        )

    @staticmethod
    def _op_call(expression: ast.Call) -> str | None:
        function = expression.func
        if (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "op"
        ):
            return function.attr
        return None

    @staticmethod
    def _column_argument(expression: ast.Call) -> ast.Call | None:
        candidate: ast.expr | None = expression.args[1] if len(expression.args) > 1 else None
        if candidate is None:
            candidate = StaticRuleEngine._keyword(expression, "column")
        if not isinstance(candidate, ast.Call):
            return None
        function = candidate.func
        if (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "sa"
            and function.attr == "Column"
        ):
            return candidate
        return None

    @staticmethod
    def _keyword(expression: ast.Call, name: str) -> ast.expr | None:
        return next((item.value for item in expression.keywords if item.arg == name), None)

    @staticmethod
    def _keyword_is(expression: ast.Call, name: str, value: object) -> bool:
        candidate = StaticRuleEngine._keyword(expression, name)
        return isinstance(candidate, ast.Constant) and candidate.value is value

    @staticmethod
    def _is_not_implemented(expression: ast.expr | None) -> bool:
        if isinstance(expression, ast.Name):
            return expression.id == "NotImplementedError"
        return (
            isinstance(expression, ast.Call)
            and isinstance(expression.func, ast.Name)
            and expression.func.id == "NotImplementedError"
        )

    @staticmethod
    def _finding(
        snapshot_hash: str,
        node: RevisionNodeV1,
        location: ast.AST,
        *,
        rule_id: str,
        severity: Severity,
        confidence: float,
        message: str,
        remediation: str,
        unknowns: tuple[str, ...],
    ) -> StaticFindingV1:
        line = max(1, getattr(location, "lineno", 1))
        column = max(0, getattr(location, "col_offset", 0))
        digest = hashlib.sha256(
            f"{snapshot_hash}:{node.relative_path}:{line}:{column}:{rule_id}".encode()
        ).hexdigest()
        return StaticFindingV1(
            rule_id=rule_id,
            rule_version="1.0",
            severity=severity,
            confidence=confidence,
            relative_path=node.relative_path,
            line=line,
            column=column,
            message=message,
            remediation=remediation,
            evidence_ids=(f"evidence:{digest}",),
            unknowns=unknowns,
        )
