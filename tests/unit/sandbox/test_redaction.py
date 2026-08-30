import hashlib
import json

from shadowops.sandbox.docker_manager import DockerResourceManager


def test_runner_result_redacts_secret_and_recomputes_artifact_hash() -> None:
    secret = "temporary-password"
    text = f"password={secret}"
    raw = json.dumps(
        {
            "schema_version": "1.0",
            "action": "APPLY_TARGET",
            "status": "FAILED",
            "error_code": "MIGRATION_FAILED",
            "error_detail": text,
            "current_revision": None,
            "duration_ms": 1,
            "stdout": {
                "media_type": "text/plain",
                "byte_count": 0,
                "sha256": hashlib.sha256(b"").hexdigest(),
                "truncated": False,
                "text": "",
            },
            "stderr": {
                "media_type": "text/plain",
                "byte_count": len(text),
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "truncated": False,
                "text": text,
            },
        }
    )

    result = DockerResourceManager._redacted_result(raw, secret)

    assert secret not in result.model_dump_json()
    assert result.error_detail == "password=[REDACTED]"
    assert result.stderr.sha256 == hashlib.sha256(b"password=[REDACTED]").hexdigest()
