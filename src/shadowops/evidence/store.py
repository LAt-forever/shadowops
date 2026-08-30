"""Atomic content-addressed local artifact store."""

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoredArtifact:
    uri: str
    sha256: str
    byte_count: int


class LocalArtifactStore:
    def __init__(self, root: Path, *, max_bytes: int = 1_048_576) -> None:
        self._root = root / "evidence"
        self._max_bytes = max_bytes

    def put_json(self, value: object) -> StoredArtifact:
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        return self.put(data)

    def put_text(self, value: str) -> StoredArtifact:
        return self.put(value.encode())

    def put(self, data: bytes) -> StoredArtifact:
        if len(data) > self._max_bytes:
            raise ValueError("artifact exceeds the fixed store budget")
        digest = hashlib.sha256(data).hexdigest()
        parent = self._root / digest[:2]
        destination = parent / digest
        parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise RuntimeError("content-addressed artifact is corrupt")
            return StoredArtifact(f"artifact://sha256/{digest}", digest, len(data))
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=parent, prefix=".tmp-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
            directory_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return StoredArtifact(f"artifact://sha256/{digest}", digest, len(data))
