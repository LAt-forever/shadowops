"""Deterministic, bounded seed and database observations for the fixed Runner."""

import hashlib
import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Uuid,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Engine

_MANIFEST = Path("/repository/shadowops-fixture.json")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_MAX_TABLES = 64
_MAX_COLUMNS = 256
_MAX_ROWS = 100


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def observation(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    encoded = canonical_json(data)
    if len(encoded) > 65_536:
        raise ValueError("structured observation exceeds its fixed output budget")
    return {
        "kind": kind,
        "scope": "observed_in_shadow",
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "data": data,
    }


def _table_names(engine: Engine) -> list[str]:
    names = sorted(
        name
        for name in inspect(engine).get_table_names(schema="public")
        if name != "alembic_version"
    )
    if len(names) > _MAX_TABLES:
        raise ValueError("schema exceeds the fixed table observation budget")
    return names


def schema_fingerprint(engine: Engine) -> dict[str, Any]:
    inspector = inspect(engine)
    tables: list[dict[str, Any]] = []
    column_count = 0
    for table_name in _table_names(engine):
        columns = []
        for column in inspector.get_columns(table_name, schema="public"):
            column_count += 1
            if column_count > _MAX_COLUMNS:
                raise ValueError("schema exceeds the fixed column observation budget")
            columns.append(
                {
                    "name": column["name"],
                    "type": str(column["type"]),
                    "nullable": bool(column["nullable"]),
                    "default": None if column.get("default") is None else str(column["default"]),
                }
            )
        primary_key = inspector.get_pk_constraint(table_name, schema="public")
        unique = sorted(
            sorted(item.get("column_names") or [])
            for item in inspector.get_unique_constraints(table_name, schema="public")
        )
        foreign_keys = [
            {
                "columns": item.get("constrained_columns") or [],
                "referred_table": item.get("referred_table"),
                "referred_columns": item.get("referred_columns") or [],
            }
            for item in inspector.get_foreign_keys(table_name, schema="public")
        ]
        foreign_keys.sort(key=lambda item: canonical_json(item))
        tables.append(
            {
                "name": table_name,
                "columns": columns,
                "primary_key": primary_key.get("constrained_columns") or [],
                "unique": unique,
                "foreign_keys": foreign_keys,
            }
        )
    shape = {"tables": tables}
    return {
        "fingerprint": hashlib.sha256(canonical_json(shape)).hexdigest(),
        "table_count": len(tables),
        "column_count": column_count,
        "schema": shape,
    }


def row_counts(engine: Engine) -> dict[str, int]:
    counts: dict[str, int] = {}
    metadata = MetaData()
    with engine.connect() as connection:
        for table_name in _table_names(engine):
            table = Table(table_name, metadata, schema="public", autoload_with=connection)
            counts[table_name] = int(
                connection.execute(select(func.count()).select_from(table)).scalar_one()
            )
    return counts


def _manifest_rows(engine: Engine) -> tuple[list[tuple[Table, list[dict[str, Any]]]], list[str]]:
    payload = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    if set(payload) != {"schema_version", "tables"} or payload["schema_version"] != "1.0":
        raise ValueError("fixture manifest must use the fixed 1.0 schema")
    tables = payload["tables"]
    if not isinstance(tables, list) or len(tables) > _MAX_TABLES:
        raise ValueError("fixture manifest table budget exceeded")
    available = set(_table_names(engine))
    metadata = MetaData()
    prepared: list[tuple[Table, list[dict[str, Any]]]] = []
    summaries: list[str] = []
    total_rows = 0
    for item in tables:
        if not isinstance(item, dict) or set(item) != {"name", "rows"}:
            raise ValueError("fixture manifest table entry is invalid")
        name = item["name"]
        rows = item["rows"]
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name) or name not in available:
            raise ValueError("fixture manifest references an unavailable table")
        if not isinstance(rows, list):
            raise ValueError("fixture manifest rows must be a list")
        total_rows += len(rows)
        if total_rows > _MAX_ROWS:
            raise ValueError("fixture manifest row budget exceeded")
        table = Table(name, metadata, schema="public", autoload_with=engine)
        columns = set(table.columns.keys())
        validated: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict) or not set(row).issubset(columns):
                raise ValueError("fixture manifest row contains an unknown column")
            clean: dict[str, Any] = {}
            for key, value in row.items():
                if not isinstance(key, str) or not _IDENTIFIER.fullmatch(key):
                    raise ValueError("fixture manifest column name is invalid")
                if value is not None and not isinstance(value, str | int | float | bool):
                    raise ValueError("fixture manifest values must be scalar JSON values")
                if isinstance(value, str) and len(value.encode()) > 4_096:
                    raise ValueError("fixture manifest value budget exceeded")
                clean[key] = value
            validated.append(clean)
        prepared.append((table, validated))
        summaries.append(name)
    return prepared, summaries


def _synthetic_value(table: str, column: str, column_type: object) -> object | None:
    if isinstance(column_type, Integer):
        return 1
    if isinstance(column_type, String):
        return f"shadowops_{table}_{column}_1"
    if isinstance(column_type, Boolean):
        return True
    if isinstance(column_type, DateTime):
        return datetime(2000, 1, 1, tzinfo=UTC)
    if isinstance(column_type, Date):
        return date(2000, 1, 1)
    if isinstance(column_type, Numeric):
        return Decimal("1")
    if isinstance(column_type, Uuid):
        return uuid5(NAMESPACE_URL, f"shadowops:{table}:{column}:1")
    return None


def _synthetic_rows(engine: Engine) -> tuple[list[tuple[Table, list[dict[str, Any]]]], list[str]]:
    inspector = inspect(engine)
    metadata = MetaData()
    prepared: list[tuple[Table, list[dict[str, Any]]]] = []
    gaps: list[str] = []
    for table_name in _table_names(engine):
        if inspector.get_foreign_keys(table_name, schema="public"):
            gaps.append(f"unsupported_foreign_key:{table_name}")
            continue
        table = Table(table_name, metadata, schema="public", autoload_with=engine)
        row: dict[str, Any] = {}
        required_unsupported = False
        for column in table.columns:
            if column.autoincrement is True and column.primary_key:
                continue
            value = _synthetic_value(table_name, column.name, column.type)
            if value is not None:
                row[column.name] = value
                continue
            gaps.append(f"unsupported_type:{table_name}.{column.name}:{column.type}")
            if not column.nullable and column.default is None and column.server_default is None:
                required_unsupported = True
        if required_unsupported:
            gaps.append(f"table_not_seeded:{table_name}")
            continue
        prepared.append((table, [row]))
    return prepared, gaps


def seed_database(
    engine: Engine, *, allow_synthetic: bool = True
) -> tuple[dict[str, Any], list[str]]:
    if _MANIFEST.is_file():
        prepared, table_names = _manifest_rows(engine)
        gaps: list[str] = []
        source = "fixture_manifest"
    elif not allow_synthetic:
        return (
            {
                "source": "synthetic",
                "tables_considered": len(_table_names(engine)),
                "tables_seeded": [],
                "rows_inserted": {},
                "coverage_complete": False,
            },
            ["no_baseline_schema:synthetic_seed_skipped"],
        )
    else:
        prepared, gaps = _synthetic_rows(engine)
        table_names = [table.name for table, _ in prepared]
        source = "synthetic"
    inserted: dict[str, int] = {}
    with engine.begin() as connection:
        for table, rows in prepared:
            if rows:
                connection.execute(table.insert(), rows)
            inserted[table.name] = len(rows)
    return (
        {
            "source": source,
            "tables_considered": len(_table_names(engine)),
            "tables_seeded": table_names,
            "rows_inserted": inserted,
            "coverage_complete": not gaps,
        },
        gaps,
    )


def smoke_checks(engine: Engine) -> tuple[dict[str, Any], dict[str, Any]]:
    schema = schema_fingerprint(engine)
    counts = row_counts(engine)
    with engine.begin() as connection:
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        constraint_rows = connection.execute(
            text(
                "SELECT COUNT(*) AS total, "
                "COUNT(*) FILTER (WHERE NOT convalidated) AS unvalidated "
                "FROM pg_constraint c "
                "JOIN pg_namespace n ON n.oid = c.connamespace "
                "WHERE n.nspname = 'public'"
            )
        ).one()
    smoke = {
        "schema_fingerprint": schema["fingerprint"],
        "row_counts": counts,
        "constraint_count": int(constraint_rows.total),
        "unvalidated_constraint_count": int(constraint_rows.unvalidated),
        "constraints_immediate": True,
    }
    return schema, smoke
