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


def execute(payload: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    output_limit = int(payload.get("output_limit_bytes", 65_536))
    stdout = io.StringIO()
    stderr = io.StringIO()
    status = "SUCCEEDED"
    error_code = None
    error_detail = None
    current_revision = None
    try:
        if payload.get("schema_version") != "1.0":
            raise ValueError("unsupported request schema")
        action = payload.get("action")
        if action not in {"UPGRADE_BASELINE", "APPLY_TARGET"}:
            raise ValueError("action is not allowlisted")
        revision = str(payload["revision"])
        database_url = _database_url(payload)
        config = Config(str(_REPOSITORY / "alembic.ini"))
        config.set_main_option("script_location", str(_REPOSITORY / "migrations"))
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            command.upgrade(config, revision)
            engine = create_engine(database_url)
            try:
                with engine.connect() as connection:
                    current_revision = connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one_or_none()
                    if current_revision is None:
                        current_revision = "base"
            finally:
                engine.dispose()
    except Exception as exc:  # untrusted migration failures become structured evidence
        status = "FAILED"
        error_code = "MIGRATION_FAILED" if not isinstance(exc, ValueError) else "INVALID_INPUT"
        error_detail = f"{type(exc).__name__}: {exc}"[:500]
        traceback.print_exc(file=stderr)
    return {
        "schema_version": "1.0",
        "action": payload.get("action", "APPLY_TARGET"),
        "status": status,
        "error_code": error_code,
        "error_detail": error_detail,
        "current_revision": current_revision,
        "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
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
            "stdout": _artifact("", 65_536),
            "stderr": _artifact(message, 65_536),
        }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
