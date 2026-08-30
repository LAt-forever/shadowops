import hashlib

import pytest

from shadowops.evidence.store import LocalArtifactStore


def test_artifact_store_atomically_reuses_content_address(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)

    first = store.put_json({"table": "users", "rows": 1})
    second = store.put_json({"rows": 1, "table": "users"})

    assert first == second
    assert first.uri == f"artifact://sha256/{first.sha256}"
    stored = tmp_path / "evidence" / first.sha256[:2] / first.sha256
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == first.sha256
    assert list(stored.parent.glob(".tmp-*")) == []


def test_artifact_store_rejects_oversized_content(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, max_bytes=4)

    with pytest.raises(ValueError, match="budget"):
        store.put(b"12345")
