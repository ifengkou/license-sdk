"""Commercial publication under one shared transaction lock, preserving original bytes."""

import hashlib
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from license_sdk.installation import OnboardingError, record_lock, sync_directory
from license_sdk.runtime import RuntimeState, RuntimeStatus, _safe_path

if TYPE_CHECKING:
    from license_sdk.runtime import LicenseRuntime


@dataclass(frozen=True)
class ImportResult:
    action_status: str
    file_digest: str
    status: RuntimeStatus | None


def current_file_digest(path: Path) -> str | None:
    _safe_path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("commercial license must be a regular file")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while chunk := stream.read(65536):
                digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _confirm(path: Path, digest: str) -> None:
    if current_file_digest(path) != digest:
        raise OnboardingError("LICENSE_STATE_CONFLICT", "commercial license changed")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    sync_directory(path.parent)


def import_license(runtime: "LicenseRuntime", raw: bytes, expected: str | None) -> ImportResult:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 65536:
        raise OnboardingError("LICENSE_FILE_INVALID", "license must contain 1-65536 bytes")
    if expected is not None and (
        not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None
    ):
        raise OnboardingError("LICENSE_FILE_INVALID", "expected raw file SHA-256 required")
    path = runtime.paths.root / "shared/active.license.json"
    digest = hashlib.sha256(raw).hexdigest()
    status: RuntimeStatus | None
    # Lock order: publication record lock -> Runtime mutex -> clock lock; never reverse.
    with record_lock(path.parent / "license-import.lock"):
        if current_file_digest(path) != expected:
            raise OnboardingError("LICENSE_STATE_CONFLICT", "commercial license changed")
        if expected == digest:
            if runtime.preflight(raw).payload_digest is None:
                raise OnboardingError(
                    "LICENSE_CANDIDATE_REJECTED", "existing license cannot be verified"
                )
            try:
                _confirm(path, digest)
                status = runtime.status(read_only=True)
                return ImportResult("ALREADY_APPLIED", digest, status)
            except (OSError, ValueError):
                return ImportResult("OUTCOME_UNKNOWN", digest, None)
        candidate = runtime.preflight(raw)
        if not candidate.acceptable or candidate.state != RuntimeState.ACTIVE:
            raise OnboardingError("LICENSE_CANDIDATE_REJECTED", "candidate license is not ACTIVE")
        temporary: str | None = None
        published = False
        try:
            _safe_path(path)
            descriptor, temporary = tempfile.mkstemp(prefix=".license-import-", dir=path.parent)
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            published, temporary = True, None
            sync_directory(path.parent)
            return ImportResult("APPLIED", digest, runtime.status(read_only=True))
        except (OSError, ValueError) as exc:
            if published:
                try:
                    status = runtime.status(read_only=True)
                except (OSError, ValueError):
                    status = None
                return ImportResult("OUTCOME_UNKNOWN", digest, status)
            raise OnboardingError(
                "LICENSE_STORAGE_FAILED", "license publication failed before commit"
            ) from exc
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
