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


def _confirm(path: Path, digest: str, shared_group: int | None) -> bool:
    if current_file_digest(path) != digest:
        raise OnboardingError("LICENSE_STATE_CONFLICT", "commercial license changed")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise ValueError("commercial license must be a regular file")
        if stat.S_IMODE(details.st_mode) != (0o640 if shared_group is not None else 0o600):
            return False
        if shared_group is not None and details.st_gid != shared_group:
            return False
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    sync_directory(path.parent)
    return True


def import_license(
    runtime: "LicenseRuntime",
    raw: bytes,
    expected: str | None,
    *,
    shared_group: int | None = None,
) -> ImportResult:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 65536:
        raise OnboardingError("LICENSE_FILE_INVALID", "license must contain 1-65536 bytes")
    if expected is not None and (
        not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None
    ):
        raise OnboardingError("LICENSE_FILE_INVALID", "expected raw file SHA-256 required")
    if shared_group is not None and (
        type(shared_group) is not int or not 0 <= shared_group <= (1 << 31) - 1
    ):
        raise ValueError("shared_group must be a numeric GID between 0 and 2147483647")
    path = runtime.paths.root / "shared/active.license.json"
    digest = hashlib.sha256(raw).hexdigest()
    status: RuntimeStatus | None
    # Lock order: publication record lock -> Runtime mutex -> clock lock; never reverse.
    with record_lock(path.parent / "license-import.lock"):
        if current_file_digest(path) != expected:
            raise OnboardingError("LICENSE_STATE_CONFLICT", "commercial license changed")
        same_bytes = expected == digest
        if same_bytes:
            if runtime.preflight(raw).payload_digest is None:
                raise OnboardingError(
                    "LICENSE_CANDIDATE_REJECTED", "existing license cannot be verified"
                )
            try:
                if _confirm(path, digest, shared_group):
                    status = runtime.status(read_only=True)
                    return ImportResult("ALREADY_APPLIED", digest, status)
            except (OSError, ValueError):
                return ImportResult("OUTCOME_UNKNOWN", digest, None)
        else:
            candidate = runtime.preflight(raw)
            if not candidate.acceptable or candidate.state != RuntimeState.ACTIVE:
                raise OnboardingError(
                    "LICENSE_CANDIDATE_REJECTED", "candidate license is not ACTIVE"
                )
        temporary: str | None = None
        published = False
        try:
            _safe_path(path)
            descriptor, temporary = tempfile.mkstemp(prefix=".license-import-", dir=path.parent)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                if shared_group is not None:
                    os.fchown(stream.fileno(), -1, shared_group)
                mode = 0o640 if shared_group is not None else 0o600
                os.fchmod(stream.fileno(), mode)
                details = os.fstat(stream.fileno())
                if stat.S_IMODE(details.st_mode) != mode or (
                    shared_group is not None and details.st_gid != shared_group
                ):
                    raise OSError("commercial license attributes not confirmed")
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            published, temporary = True, None
            sync_directory(path.parent)
            action = "ALREADY_APPLIED" if same_bytes else "APPLIED"
            return ImportResult(action, digest, runtime.status(read_only=True))
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
