"""Private object storage for uploaded originals and derived artifacts.

Design constraints:
  * Keys are **app-generated** (document id + a random component). User input
    never reaches a filesystem path, so traversal and injection are impossible
    by construction, not by sanitisation.
  * The store is private: there are no public URLs, no CDN, and no bucket
    listing. Access always goes through an authorized API path.
  * Originals are immutable. ``put`` refuses to overwrite, so a document's
    bytes can never be silently replaced after ingestion.
  * Integrity is verified by content hash on read (``get_verified``), so silent
    corruption or tampering is detected rather than propagated.

The local backend is the default and works without any cloud credentials. A
future S3-compatible backend only needs to implement the same ``ObjectStore``
surface; nothing in the domain layer knows which backend is in use.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import Settings, get_settings
from ..errors import ConflictError, NotFoundError, ValidationError

# Characters permitted in a storage key. Deliberately restrictive: a key is a
# private internal identifier, not a user-facing filename.
_KEY_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"


@dataclass(frozen=True)
class ObjectStat:
    size_bytes: int
    content_hash: str


class ObjectStoreError(Exception):
    """Backend failure (I/O, permissions). Never contains user content."""


class ObjectStore(ABC):
    """Minimal, auditable surface for private object storage."""

    @abstractmethod
    def put(self, key: str, data: bytes, *, expected_hash: Optional[str] = None) -> None:
        """Store bytes immutably. Raises if the key already exists."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Return object bytes, or raise ``NotFoundError``."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        """Permanently delete. Raises ``NotFoundError`` if absent."""

    @abstractmethod
    def stat(self, key: str) -> ObjectStat:
        ...

    def get_verified(self, key: str, expected_hash: str) -> bytes:
        """Return bytes after verifying the stored SHA-256 matches."""
        data = self.get(key)
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_hash:
            raise ObjectStoreError(
                f"Integrity check failed for object '{key}': stored hash does not match"
            )
        return data


def _validate_key(key: str) -> str:
    """Reject any key that is not a clean, relative, app-generated path."""
    if not key or key.startswith("/") or key.startswith("\\"):
        raise ValidationError("Storage key must be a relative path", code="invalid_key")
    if "\x00" in key or "\\" in key:
        raise ValidationError("Storage key contains illegal characters", code="invalid_key")
    if ".." in key.split("/"):
        raise ValidationError("Storage key must not traverse directories", code="invalid_key")
    # Each segment must match the allowed pattern; no leading dots, so hidden
    # files and dot-directories can never appear under the private root.
    for segment in key.split("/"):
        if not re.fullmatch(_KEY_SEGMENT, segment):
            raise ValidationError("Storage key contains an illegal segment", code="invalid_key")
    return key


def generate_document_key(document_id: str, *, kind: str = "originals", suffix: str = "") -> str:
    """Build an opaque, app-generated key for a document artifact.

    The user's filename is never part of the key; it is kept only as display
    metadata on the document row.
    """
    if kind not in {"originals", "pages", "derived"}:
        raise ValidationError(f"Unknown storage kind '{kind}'", code="invalid_key")
    token = secrets.token_hex(16)
    safe_id = _validate_key(document_id)
    tail = f"-{suffix}" if suffix else ""
    key = f"documents/{safe_id}/{kind}/{token}{tail}"
    return _validate_key(key)


class LocalObjectStore(ObjectStore):
    """Filesystem-backed store rooted at a single private directory."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        _validate_key(key)
        candidate = (self.root / key).resolve()
        # Defence in depth: even with a validated key, confirm containment.
        if not candidate.is_relative_to(self.root):
            raise ValidationError("Storage key escapes the storage root", code="invalid_key")
        return candidate

    def put(self, key: str, data: bytes, *, expected_hash: Optional[str] = None) -> None:
        if expected_hash is not None:
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected_hash:
                raise ObjectStoreError("Refusing to store bytes that fail the hash check")
        path = self._path(key)
        if path.exists():
            raise ConflictError("Storage object already exists", code="object_exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a private temp file then atomically rename, so a crash never
        # leaves a partially-written object visible under the final key.
        tmp = path.with_name(path.name + f".tmp{os.getpid()}{secrets.token_hex(4)}")
        try:
            with open(tmp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        os.chmod(path, 0o600)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise NotFoundError("Storage object not found", code="object_not_found")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ObjectStoreError(f"Failed to read object '{key}'") from exc

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if not path.is_file():
            raise NotFoundError("Storage object not found", code="object_not_found")
        try:
            path.unlink()
        except OSError as exc:
            raise ObjectStoreError(f"Failed to delete object '{key}'") from exc

    def stat(self, key: str) -> ObjectStat:
        path = self._path(key)
        if not path.is_file():
            raise NotFoundError("Storage object not found", code="object_not_found")
        data = path.read_bytes()
        return ObjectStat(size_bytes=len(data), content_hash=hashlib.sha256(data).hexdigest())


_store: Optional[ObjectStore] = None


def get_object_store(settings: Optional[Settings] = None) -> ObjectStore:
    """Return the configured store, constructing it on first use."""
    global _store
    if _store is not None:
        return _store
    resolved = settings or get_settings()
    backend = resolved.storage_backend.lower()
    if backend != "local":
        # Fail loudly rather than silently writing to an unexpected location.
        raise ObjectStoreError(f"Unsupported storage backend '{resolved.storage_backend}'")
    root = Path(resolved.storage_local_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    _store = LocalObjectStore(root)
    return _store


def reset_object_store_for_tests() -> None:
    global _store
    _store = None
