"""Fixed entrypoint for one allowlisted Alembic action."""

import contextlib
import hashlib
import io
import json
import os
import time
import traceback
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DataError, IntegrityError

from shadowops_runner.checks import (
    observation,
    row_counts,
    schema_fingerprint,
    seed_database,
    smoke_checks,
)

_REPOSITORY = Path("/repository")


def _artifact(value: str, limit: int) -> dict[str, Any]:
    encoded = value.encode("utf-8", errors="replace")
    truncated = len(encoded) > limit
    bounded = encoded[:limit]
    while bounded:
        try:
            rendered = bounded.decode("utf-8")
            break
        except UnicodeDecodeError:
            bounded = bounded[:-1]
    else:
        rendered = ""
    return {
        "media_type": "text/plain",
        "byte_count": len(bounded),
        "sha256": hashlib.sha256(bounded).hexdigest(),
        "truncated": truncated,
        "text": rendered,
    }


def _database_url(request: dict[str, Any]) -> str:
    if request.get("database_alias") != "shadow-postgres":
        raise ValueError("database alias is not allowlisted")
    user = os.environ["SHADOWOPS_DB_USER"]
    password = os.environ["SHADOWOPS_DB_PASSWORD"]
    database = os.environ["SHADOWOPS_DB_NAME"]
    timeout = int(request["statement_timeout_ms"])
    return (
        f"postgresql+psycopg://{user}:{password}@shadow-postgres:5432/{database}"
        f"?options=-c%20statement_timeout%3D{timeout}"
    )


def _alembic_config(database_url: str) -> Config:
    config = Config(str(_REPOSITORY / "alembic.ini"))
    config.set_main_option("script_location", str(_REPOSITORY / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _current_revision(database_url: str) -> str:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
            return "base" if revision is None else str(revision)
    finally:
        engine.dispose()


def _error_code(action: object, error: Exception) -> str:
    if isinstance(error, ValueError):
        return "INVALID_INPUT"
    if action == "LOAD_TEST_DATA":
        if isinstance(error, IntegrityError):
            return "SEED_CONSTRAINT_FAILED"
        if isinstance(error, DataError):
            return "SEED_TYPE_FAILED"
        return "SEED_FAILED"
    if action == "RUN_SMOKE_CHECKS":
        return "SMOKE_CHECK_FAILED"
    if action == "VERIFY_ROLLBACK_ROUNDTRIP":
        return "ROLLBACK_FAILED"
    return "MIGRATION_FAILED"


def execute(payload: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    output_limit = int(payload.get("output_limit_bytes", 65_536))
    stdout = io.StringIO()
    stderr = io.StringIO()
    status = "SUCCEEDED"
    error_code = None
    error_detail = None
    current_revision = None
    coverage_gaps: list[str] = []
    observations: list[dict[str, Any]] = []
    config: Config | None = None
    database_url: str | None = None
    action = payload.get("action")
    try:
        if payload.get("schema_version") != "1.0":
            raise ValueError("unsupported request schema")
        if action not in {
            "UPGRADE_BASELINE",
            "APPLY_TARGET",
            "LOAD_TEST_DATA",
            "RUN_SMOKE_CHECKS",
            "VERIFY_ROLLBACK_ROUNDTRIP",
        }:
            raise ValueError("action is not allowlisted")
        revision = str(payload["revision"])
        database_url = _database_url(payload)
        config = _alembic_config(database_url)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            if action in {"UPGRADE_BASELINE", "APPLY_TARGET"}:
                command.upgrade(config, revision)
            elif action == "LOAD_TEST_DATA":
                engine = create_engine(database_url)
                try:
                    summary, coverage_gaps = seed_database(
                        engine, allow_synthetic=payload.get("baseline_revision") != "base"
                    )
                    observations.append(observation("SEED_SUMMARY", summary))
                finally:
                    engine.dispose()
            elif action == "RUN_SMOKE_CHECKS":
                engine = create_engine(database_url)
                try:
                    schema, smoke = smoke_checks(engine)
                    observations.extend(
                        [
                            observation("SCHEMA_FINGERPRINT", schema),
                            observation("SMOKE_SUMMARY", smoke),
                        ]
                    )
                finally:
                    engine.dispose()
            else:
                baseline = payload.get("baseline_revision")
                if not isinstance(baseline, str):
                    raise ValueError("rollback roundtrip requires baseline_revision")
                engine = create_engine(database_url)
                try:
                    schema_before = schema_fingerprint(engine)
                    rows_before = row_counts(engine)
                finally:
                    engine.dispose()
                command.downgrade(config, baseline)
                downgraded_revision = _current_revision(database_url)
                command.upgrade(config, revision)
                upgraded_revision = _current_revision(database_url)
                engine = create_engine(database_url)
                try:
                    schema_after = schema_fingerprint(engine)
                    rows_after = row_counts(engine)
                finally:
                    engine.dispose()
                restored = (
                    upgraded_revision == revision
                    and schema_before["fingerprint"] == schema_after["fingerprint"]
                    and rows_before == rows_after
                )
                observations.extend(
                    [
                        observation(
                            "ROLLBACK_ROUNDTRIP",
                            {
                                "baseline_revision": baseline,
                                "downgraded_revision": downgraded_revision,
                                "target_revision": revision,
                                "upgraded_revision": upgraded_revision,
                                "schema_before": schema_before["fingerprint"],
                                "schema_after": schema_after["fingerprint"],
                                "row_counts_before": rows_before,
                                "row_counts_after": rows_after,
                                "restored": restored,
                            },
                        ),
                        observation("SCHEMA_FINGERPRINT", schema_after),
                    ]
                )
                if not restored:
                    raise RuntimeError("schema or row-count fingerprint changed after roundtrip")
            current_revision = _current_revision(database_url)
    except Exception as exc:  # untrusted migration failures become structured evidence
        status = "FAILED"
        error_code = _error_code(action, exc)
        error_detail = f"{type(exc).__name__}: {exc}"[:500]
        traceback.print_exc(file=stderr)
        if (
            action == "VERIFY_ROLLBACK_ROUNDTRIP"
            and config is not None
            and database_url is not None
        ):
            with contextlib.suppress(Exception):
                command.upgrade(config, str(payload["revision"]))
                current_revision = _current_revision(database_url)
    return {
        "schema_version": "1.0",
        "action": payload.get("action", "APPLY_TARGET"),
        "status": status,
        "error_code": error_code,
        "error_detail": error_detail,
        "current_revision": current_revision,
        "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
        "coverage_gaps": coverage_gaps,
        "observations": observations,
        "stdout": _artifact(stdout.getvalue(), output_limit),
        "stderr": _artifact(stderr.getvalue(), output_limit),
    }


def main() -> None:
    try:
        payload = json.loads(os.environ["SHADOWOPS_RUNNER_REQUEST"])
        result = execute(payload)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        result = {
            "schema_version": "1.0",
            "action": "APPLY_TARGET",
            "status": "FAILED",
            "error_code": "INVALID_INPUT",
            "error_detail": message[:500],
            "current_revision": None,
            "duration_ms": 0,
            "coverage_gaps": [],
            "observations": [],
            "stdout": _artifact("", 65_536),
            "stderr": _artifact(message, 65_536),
        }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
