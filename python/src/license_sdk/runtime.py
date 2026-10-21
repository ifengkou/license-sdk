"""Offline runtime observation and durable clock protection, independent of HTTP."""

import base64
import fcntl
import hashlib
import hmac
import json
import logging
import os
import stat
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from license_sdk.identity import binding_id, host_fingerprint, installation_uuid
from license_sdk.profile import ProductProfile
from license_sdk.verification import (
    CommercialLicense,
    LicenseErrorReason,
    LicenseStatus,
    LicenseValidationError,
    VerifiedLicense,
    _base64,
    parse_json_object,
    parse_utc_timestamp,
    verify_license,
)

MAX_COUNTER = (1 << 63) - 1


class BuildMode(StrEnum):
    DEVELOPMENT = "DEVELOPMENT"
    CUSTOMER_REQUIRED = "CUSTOMER_REQUIRED"
    INVALID = "INVALID"


class RuntimeState(StrEnum):
    DEVELOPMENT = "DEVELOPMENT"
    CONFIG_INVALID = "CONFIG_INVALID"
    CLOCK_STATE_INVALID = "CLOCK_STATE_INVALID"
    INVALID = "INVALID"
    UNLICENSED = "UNLICENSED"
    CLOCK_ROLLBACK = "CLOCK_ROLLBACK"
    NOT_YET_VALID = "NOT_YET_VALID"
    EXPIRED = "EXPIRED"
    ACTIVE = "ACTIVE"


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    machine_id_file: Path = Path("/etc/machine-id")

    def __post_init__(self) -> None:
        if not self.root.is_absolute() or not self.machine_id_file.is_absolute():
            raise ValueError("license paths must be absolute")


@dataclass(frozen=True)
class RuntimeStatus:
    state: RuntimeState
    reasons: tuple[RuntimeState, ...]
    mode: str
    effective_now: datetime
    license: CommercialLicense | None = None
    payload_digest: str | None = None
    file_digest: str | None = None


class TimeSource(Protocol):
    def wall_now(self) -> datetime: ...
    def monotonic_ns(self) -> int: ...


class SystemTimeSource:
    def wall_now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


class CommitPhase(StrEnum):
    NOT_COMMITTED = "NOT_COMMITTED"
    COMMITTED = "COMMITTED"
    COMMIT_DURABILITY_UNKNOWN = "COMMIT_DURABILITY_UNKNOWN"


class StateStoreError(OSError):
    def __init__(self, phase: CommitPhase) -> None:
        self.phase = phase
        super().__init__(f"clock persistence failed: {phase}")


def _safe_path(path: Path) -> None:
    if not path.is_absolute():
        raise ValueError("absolute path required")
    for entry in (*reversed(path.parents), path):
        if entry.is_symlink():
            raise ValueError("symlink in license path")


def read_bounded(path: Path, maximum: int) -> bytes:
    _safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        details = os.fstat(fd)
        if not stat.S_ISREG(details.st_mode) or details.st_size > maximum:
            raise ValueError("expected bounded regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("file too large")
        return data
    finally:
        os.close(fd)


class FileStateStore:
    """Existing-directory store; OS lock and local mutex serialize compare-and-replace."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._mutex = threading.RLock()

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self._mutex:
            _safe_path(self.path)
            lock_path = self.path.with_name("clock.lock")
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ValueError("lock must be regular")
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)

    def read(self) -> bytes:
        return read_bounded(self.path, 8192)

    def replace(self, expected: bytes, replacement: bytes) -> CommitPhase:
        # Caller holds locked(). Never create an absent state or overwrite changed state.
        temporary: str | None = None
        published = False
        try:
            if self.read() != expected:
                raise ValueError("clock changed before commit")
            fd, temporary = tempfile.mkstemp(prefix=".clock-", dir=self.path.parent)
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(replacement)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            published = True
            temporary = None
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return CommitPhase.COMMITTED
        except (OSError, ValueError) as exc:
            phase = (
                CommitPhase.COMMIT_DURABILITY_UNKNOWN if published else CommitPhase.NOT_COMMITTED
            )
            raise StateStoreError(phase) from exc
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    logging.getLogger(__name__).warning("clock temporary-file cleanup failed")


@dataclass(frozen=True)
class ClockState:
    epoch: int
    sequence: int
    high_watermark: datetime
    last_recovery_id: str | None


def _counter(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_COUNTER:
        raise ValueError("counter must be nonnegative signed 64-bit integer")
    return value


def _uuid(value: object) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("canonical UUID required")
    return value


def decode_clock(
    raw: bytes, *, product_id: str, installation_id: str, service: str, service_key: bytes
) -> ClockState:
    if len(service_key) != 32:
        raise ValueError("service key must contain 32 bytes")
    installation_uuid(installation_id)
    outer = parse_json_object(raw, maximum=8192)
    if outer.keys() != {"payload", "mac"}:
        raise ValueError("invalid clock envelope fields")
    payload = _base64(outer["payload"], 4096)
    mac = _base64(outer["mac"], 32)
    key = hmac.digest(service_key, f"{product_id}/state/{service}/v1".encode(), "sha256")
    if len(mac) != 32 or not hmac.compare_digest(mac, hmac.digest(key, payload, "sha256")):
        raise ValueError("clock MAC rejected")
    data = parse_json_object(payload, maximum=4096)
    if data.keys() != {
        "schema_version",
        "product_id",
        "installation_id",
        "service",
        "epoch",
        "sequence",
        "high_watermark",
        "last_recovery_id",
    }:
        raise ValueError("invalid clock fields")
    if (
        type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or data["product_id"] != product_id
        or data["installation_id"] != installation_id
        or data["service"] != service
    ):
        raise ValueError("clock identity mismatch")
    recovery = data["last_recovery_id"]
    if recovery is not None:
        recovery = _uuid(recovery)
    return ClockState(
        _counter(data["epoch"]),
        _counter(data["sequence"]),
        parse_utc_timestamp(data["high_watermark"]),
        recovery,
    )


def encode_clock(
    clock: ClockState, *, product_id: str, installation_id: str, service: str, service_key: bytes
) -> bytes:
    # Shared v1 canonical bytes are also emitted by the standalone deployment initializer.
    if len(service_key) != 32:
        raise ValueError("service key must contain 32 bytes")
    installation_uuid(installation_id)
    _counter(clock.epoch)
    _counter(clock.sequence)
    if clock.last_recovery_id is not None:
        _uuid(clock.last_recovery_id)
    if clock.high_watermark.tzinfo is None or clock.high_watermark.utcoffset() is None:
        raise ValueError("aware high watermark required")
    data = {
        "schema_version": 1,
        "product_id": product_id,
        "installation_id": installation_id,
        "service": service,
        "epoch": clock.epoch,
        "sequence": clock.sequence,
        "high_watermark": clock.high_watermark.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "last_recovery_id": clock.last_recovery_id,
    }
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    key = hmac.digest(service_key, f"{product_id}/state/{service}/v1".encode(), "sha256")
    envelope = {
        "payload": base64.urlsafe_b64encode(payload).decode().rstrip("="),
        "mac": base64.urlsafe_b64encode(hmac.digest(key, payload, "sha256")).decode().rstrip("="),
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()


class LicenseRuntime:
    def __init__(
        self,
        *,
        profile: ProductProfile,
        service: str,
        paths: RuntimePaths,
        trusted_keys: Mapping[str, bytes],
        mode: BuildMode,
        time_source: TimeSource | None = None,
    ) -> None:
        self.profile, self.service, self.paths = profile, service, paths
        self.mode, self.trusted_keys = mode, MappingProxyType(dict(trusted_keys))
        self.time = time_source or SystemTimeSource()
        self.store = FileStateStore(paths.root / "state" / service / "clock.json")
        self._lock = threading.RLock()
        self._config_invalid = (
            service not in dict(profile.recovery_prefixes)
            or mode not in (BuildMode.DEVELOPMENT, BuildMode.CUSTOMER_REQUIRED)
            or (mode == BuildMode.CUSTOMER_REQUIRED and not trusted_keys)
        )
        self._clock_invalid = False
        self._clock: ClockState | None = None
        self._raw_clock: bytes | None = None
        self._anchor: datetime | None = None
        self._monotonic: int | None = None
        self._last_effective: datetime | None = None
        self._checkpoint_ns: int | None = None
        self._installation: str | None = None
        self._master: bytes | None = None

    def _identity(self) -> None:
        data = parse_json_object(read_bounded(self.paths.root / "identity/installation.json", 4096))
        if (
            data.keys() != {"schema_version", "product_id", "installation_id", "initialized"}
            or type(data["schema_version"]) is not int
            or data["schema_version"] != 1
            or data["product_id"] != self.profile.product_id
            or data["initialized"] is not True
            or not isinstance(data["installation_id"], str)
        ):
            raise ValueError("invalid installation identity")
        installation_uuid(data["installation_id"])
        master = read_bounded(self.paths.root / "identity/service.key", 32)
        if len(master) != 32:
            raise ValueError("service key must contain 32 bytes")
        if self._installation is not None and (
            self._installation != data["installation_id"] or self._master != master
        ):
            raise ValueError("installation changed while running")
        self._installation, self._master = data["installation_id"], master

    def _clock_observe(self, wall: datetime, mono: int, force: bool) -> tuple[datetime, bool]:
        if self._clock_invalid:
            return wall, False
        try:
            assert self._installation is not None and self._master is not None
            with self.store.locked():
                raw = self.store.read()
                clock = decode_clock(
                    raw,
                    product_id=self.profile.product_id,
                    installation_id=self._installation,
                    service=self.service,
                    service_key=self._master,
                )
                if self._clock is None:
                    self._anchor = max(wall, clock.high_watermark)
                    self._monotonic = self._checkpoint_ns = mono
                elif (
                    clock.epoch != self._clock.epoch
                    or clock.sequence < self._clock.sequence
                    or clock.high_watermark < self._clock.high_watermark
                    or (clock.sequence == self._clock.sequence and raw != self._raw_clock)
                ):
                    raise ValueError("clock regressed or changed at the same sequence")
                assert self._anchor is not None and self._monotonic is not None
                if mono < self._monotonic:
                    raise ValueError("monotonic source regressed")
                elapsed = timedelta(microseconds=(mono - self._monotonic) // 1000)
                reference = max(
                    clock.high_watermark, self._anchor + elapsed, self._last_effective or wall
                )
                effective = max(wall, reference, self._last_effective or wall)
                rollback = wall + timedelta(seconds=300) < reference
                self._clock, self._raw_clock = clock, raw
                self._last_effective = effective
                if not rollback and (
                    force
                    or self._checkpoint_ns is None
                    or mono - self._checkpoint_ns >= 30_000_000_000
                ):
                    if clock.sequence == MAX_COUNTER:
                        raise ValueError("clock sequence exhausted")
                    updated = replace(
                        clock,
                        sequence=clock.sequence + 1,
                        high_watermark=effective.replace(microsecond=0),
                    )
                    encoded = encode_clock(
                        updated,
                        product_id=self.profile.product_id,
                        installation_id=self._installation,
                        service=self.service,
                        service_key=self._master,
                    )
                    self.store.replace(raw, encoded)
                    self._clock, self._raw_clock = updated, encoded
                    self._checkpoint_ns = mono
                return effective, rollback
        except (OSError, ValueError, OverflowError):
            self._clock_invalid = True
            return wall, False

    def _verify(self, raw: bytes, effective: datetime) -> VerifiedLicense:
        try:
            return verify_license(
                raw,
                product_id=self.profile.product_id,
                trusted_keys=self.trusted_keys,
                binding_id=None,
                effective_now=effective,
            )
        except LicenseValidationError as exc:
            if exc.reason != LicenseErrorReason.BINDING_MISMATCH:
                raise
        # Only an otherwise valid HOST_INSTALL signature can reach machine-id access.
        if self._installation is None:
            raise ValueError("local installation unavailable for binding")
        fingerprint = host_fingerprint(self.profile, read_bounded(self.paths.machine_id_file, 33))
        local = binding_id(self.profile, fingerprint, self._installation).hex()
        return verify_license(
            raw,
            product_id=self.profile.product_id,
            trusted_keys=self.trusted_keys,
            binding_id=local,
            effective_now=effective,
        )

    def status(self, *, force_checkpoint: bool = False) -> RuntimeStatus:
        with self._lock:
            wall, mono = self.time.wall_now(), self.time.monotonic_ns()
            if wall.tzinfo is None or wall.utcoffset() is None or type(mono) is not int or mono < 0:
                self._config_invalid = True
                wall = datetime.now(UTC)
            wall = wall.astimezone(UTC)
            if self.mode == BuildMode.DEVELOPMENT and not self._config_invalid:
                return RuntimeStatus(RuntimeState.DEVELOPMENT, (), "DEVELOPMENT", wall)
            reasons: list[RuntimeState] = []
            try:
                self._identity()
            except (OSError, ValueError):
                self._config_invalid = True
            if self._config_invalid:
                reasons.append(RuntimeState.CONFIG_INVALID)
            effective, rollback = wall, False
            if (
                not self._config_invalid
                and self._installation is not None
                and self._master is not None
            ):
                effective, rollback = self._clock_observe(wall, mono, force_checkpoint)
            if self._clock_invalid:
                reasons.append(RuntimeState.CLOCK_STATE_INVALID)
            verified: VerifiedLicense | None = None
            file_digest: str | None = None
            try:
                raw = read_bounded(self.paths.root / "shared/active.license.json", 65536)
                file_digest = hashlib.sha256(raw).hexdigest()
            except FileNotFoundError:
                reasons.append(RuntimeState.UNLICENSED)
            except (OSError, ValueError):
                reasons.append(RuntimeState.INVALID)
            else:
                try:
                    verified = self._verify(raw, effective)
                except (OSError, ValueError):
                    reasons.append(RuntimeState.INVALID)
            if rollback:
                reasons.append(RuntimeState.CLOCK_ROLLBACK)
            if verified is not None and verified.status != LicenseStatus.ACTIVE:
                reasons.append(RuntimeState(verified.status.value))
            priority = (
                RuntimeState.CONFIG_INVALID,
                RuntimeState.UNLICENSED,
                RuntimeState.INVALID,
                RuntimeState.CLOCK_STATE_INVALID,
                RuntimeState.CLOCK_ROLLBACK,
                RuntimeState.NOT_YET_VALID,
                RuntimeState.EXPIRED,
            )
            reasons.sort(key=priority.index)
            state = reasons[0] if reasons else RuntimeState.ACTIVE
            restricted_license = verified is None or verified.status == LicenseStatus.NOT_YET_VALID
            run_mode = (
                "MANAGEMENT_ONLY"
                if self._config_invalid or restricted_license
                else "READ_ONLY"
                if reasons
                else "NORMAL"
            )
            return RuntimeStatus(
                state,
                tuple(reasons),
                run_mode,
                effective,
                verified.payload if verified else None,
                verified.payload_digest if verified else None,
                file_digest,
            )
