"""Explicit installation and shared POSIX transaction primitives."""

import fcntl
import os
import stat
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from license_sdk.identity import installation_uuid
from license_sdk.product import ProductConfig
from license_sdk.recovery import canonical_json
from license_sdk.runtime import ClockState, _safe_path, decode_clock, encode_clock, read_bounded
from license_sdk.verification import parse_json_object


class OnboardingError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class InstallationResult:
    action_status: str
    installation_id: str


_MUTEX_GUARD = threading.Lock()
_MUTEXES: dict[Path, threading.Lock] = {}


def absolute_path(path: Path) -> Path:
    # abspath normalizes relative spelling without following symlinks.
    value = Path(os.path.abspath(path))
    _safe_path(value)
    return value


def sync_directory(path: Path) -> None:
    _safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def directory(path: Path) -> None:
    _safe_path(path)
    if not path.exists():
        directory(path.parent)
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    if not path.is_dir():
        raise ValueError("installation directory required")
    # Also confirms a directory left by a previous interrupted creation attempt.
    if path.parent != path:
        sync_directory(path.parent)


@contextmanager
def record_lock(path: Path) -> Iterator[None]:
    _safe_path(path)
    with _MUTEX_GUARD:
        mutex = _MUTEXES.setdefault(path, threading.Lock())
    if not mutex.acquire(blocking=False):
        raise OnboardingError("LICENSE_STATE_CONFLICT", "installation operation already running")
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("lock must be a regular file")
        try:
            fcntl.lockf(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 0, os.SEEK_SET)
        except BlockingIOError as exc:
            raise OnboardingError(
                "LICENSE_STATE_CONFLICT", "installation operation already running"
            ) from exc
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        mutex.release()


def write_new(path: Path, raw: bytes) -> None:
    _safe_path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def replace_file(path: Path, raw: bytes) -> None:
    _safe_path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".installation-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _identity(path: Path, product: ProductConfig) -> tuple[dict[str, object], bytes]:
    data = parse_json_object(read_bounded(path / "installation.json", 4096))
    if (
        data.keys() != {"schema_version", "product_id", "installation_id", "initialized"}
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or data["product_id"] != product.profile.product_id
        or type(data["initialized"]) is not bool
        or not isinstance(data["installation_id"], str)
    ):
        raise ValueError("invalid installation identity; preserve installation and investigate")
    installation_uuid(data["installation_id"])
    master = read_bounded(path / "service.key", 32)
    if len(master) != 32:
        raise ValueError("invalid installation key; preserve installation and investigate")
    return data, master


def _initialize_installation(product: ProductConfig, root: Path) -> InstallationResult:
    """Initialize all services once; resume a valid draft without replacing its identity."""
    root = absolute_path(root)
    directory(root)
    with record_lock(root / ".init.lock"):
        identity, stage = root / "identity", root / ".identity-stage"
        _safe_path(identity)
        _safe_path(stage)
        if identity.exists() and stage.exists():
            raise ValueError("ambiguous identity stage; preserve installation and investigate")
        if not identity.exists():
            allowed = {".init.lock", ".identity-stage"}
            if any(path.name not in allowed for path in root.iterdir()):
                raise ValueError("initialization requires a clean root; preserve existing files")
            if not stage.exists():
                stage.mkdir(mode=0o700)
                data = {
                    "schema_version": 1,
                    "product_id": product.profile.product_id,
                    "installation_id": str(uuid4()),
                    "initialized": False,
                }
                write_new(stage / "installation.json", canonical_json(data))
                write_new(stage / "service.key", os.urandom(32))
            data, master = _identity(stage, product)
            if data["initialized"] is not False or {p.name for p in stage.iterdir()} != {
                "installation.json",
                "service.key",
            }:
                raise ValueError("invalid identity stage; preserve files and investigate")
            # Confirm resumed stage data before publishing its directory.
            for name in ("installation.json", "service.key"):
                descriptor = os.open(stage / name, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            sync_directory(stage)
            os.rename(stage, identity)
            sync_directory(root)
        data, master = _identity(identity, product)
        installation_id = str(data["installation_id"])
        completed = data["initialized"] is True
        if completed:
            for subdirectory in (root / "shared", root / "state"):
                _safe_path(subdirectory)
                if not subdirectory.is_dir():
                    raise ValueError(
                        "completed installation missing directory; do not reinitialize"
                    )
        else:
            directory(root / "shared")
            directory(root / "state")
        for service, _ in product.profile.recovery_prefixes:
            target = root / "state" / service / "clock.json"
            _safe_path(target)
            if completed and not target.exists():
                raise ValueError(
                    "completed installation missing clock; use migration or signed recovery"
                )
            if not completed:
                directory(target.parent)
            try:
                raw = read_bounded(target, 8192)
            except FileNotFoundError:
                if completed:
                    raise
                raw = encode_clock(
                    ClockState(0, 0, datetime.now(UTC).replace(microsecond=0), None),
                    product_id=product.profile.product_id,
                    installation_id=installation_id,
                    service=service,
                    service_key=master,
                )
                write_new(target, raw)
            decode_clock(
                raw,
                product_id=product.profile.product_id,
                installation_id=installation_id,
                service=service,
                service_key=master,
            )
            if not completed:
                descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                sync_directory(target.parent)
        if completed:
            for file in (
                identity / "installation.json",
                identity / "service.key",
                *(
                    root / "state" / service / "clock.json"
                    for service, _ in product.profile.recovery_prefixes
                ),
            ):
                descriptor = os.open(file, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            for folder in (
                identity,
                root / "shared",
                root / "state",
                *(root / "state" / service for service, _ in product.profile.recovery_prefixes),
                root,
            ):
                sync_directory(folder)
            return InstallationResult("ALREADY_INITIALIZED", installation_id)
        sync_directory(root / "state")
        sync_directory(root / "shared")
        replace_file(identity / "installation.json", canonical_json(data | {"initialized": True}))
        sync_directory(root)
        return InstallationResult("INITIALIZED", installation_id)


def initialize_installation(product: ProductConfig, root: Path) -> InstallationResult:
    """Storage failures preserve the draft for inspection or a safe resume."""
    try:
        return _initialize_installation(product, root)
    except OSError as exc:
        raise OnboardingError(
            "LICENSE_STORAGE_OUTCOME_UNKNOWN",
            "initialization not confirmed; preserve files and retry",
        ) from exc
