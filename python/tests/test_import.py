"""Publication CAS, byte identity and durable/unknown failure boundaries."""

import base64
import hashlib
import json
import os
import stat
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from license_sdk.installation import OnboardingError, record_lock
from license_sdk.runtime import LicenseRuntime, RuntimeState


def signed(product, **changes):
    config, key = product
    now = datetime.now(UTC).replace(microsecond=0)
    data = dict(
        schema_version=1,
        key_id="new-v1",
        kind="commercial-license",
        license_id=str(uuid4()),
        customer_id="test",
        customer_name="Test",
        product_id=config.profile.product_id,
        binding_type="UNBOUND",
        binding_scheme=None,
        binding_id=None,
        validity_type="PERPETUAL",
        issued_at=now.isoformat().replace("+00:00", "Z"),
        not_before=now.isoformat().replace("+00:00", "Z"),
        expires_at=None,
    )
    data.update(changes)
    payload = json.dumps(data).encode()
    return json.dumps(
        {
            "payload": base64.urlsafe_b64encode(payload).decode().rstrip("="),
            "signature": base64.urlsafe_b64encode(key.sign(payload)).decode().rstrip("="),
        }
    ).encode()


def test_first_import_cas_and_same_instance(prepared, product):
    raw = signed(product)
    assert prepared.status(read_only=True).state == RuntimeState.UNLICENSED
    result = prepared.import_license(raw, None)
    assert result.action_status == "APPLIED"
    assert result.status.state == RuntimeState.ACTIVE
    prepared.require_valid()
    path = prepared.paths.root / "shared/active.license.json"
    assert path.read_bytes() == raw
    assert prepared.license_file_digest() == hashlib.sha256(raw).hexdigest()
    with pytest.raises(OnboardingError, match="changed"):
        prepared.import_license(signed(product), None)
    result = prepared.import_license(raw, prepared.license_file_digest())
    assert result.action_status == "ALREADY_APPLIED"
    assert path.read_bytes() == raw


@pytest.mark.parametrize(
    "changes",
    [
        {"product_id": "wrong"},
        {"key_id": "wrong"},
        {
            "binding_type": "HOST_INSTALL",
            "binding_scheme": "host-install-code-v1",
            "binding_id": "f" * 40,
        },
        {"not_before": "2090-01-01T00:00:00Z"},
        {
            "validity_type": "TERM",
            "issued_at": "2020-01-01T00:00:00Z",
            "not_before": "2020-01-01T00:00:00Z",
            "expires_at": "2021-01-01T00:00:00Z",
        },
    ],
)
def test_invalid_candidate_preserves_installation(prepared, product, changes):
    prepared.import_license(signed(product), None)
    root = prepared.paths.root
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(OnboardingError):
        prepared.import_license(signed(product, **changes), prepared.license_file_digest())
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("stage", ["file_sync", "rename", "directory_sync", "status"])
def test_import_failure_classification(prepared, product, monkeypatch, stage):
    raw = signed(product)
    path = prepared.paths.root / "shared/active.license.json"
    fsync, replace, status = os.fsync, os.replace, prepared.status

    def fail(*args, **kwargs):
        raise OSError("injected")

    if stage == "file_sync":
        monkeypatch.setattr(os, "fsync", fail)
    elif stage == "rename":
        monkeypatch.setattr(os, "replace", fail)
    elif stage == "directory_sync":
        calls = 0

        def sync(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected directory fsync")
            fsync(fd)

        monkeypatch.setattr(os, "fsync", sync)
    else:

        def fail_after_publish(*args, **kwargs):
            if path.exists():
                raise OSError("injected status observation")
            return status(*args, **kwargs)

        monkeypatch.setattr(prepared, "status", fail_after_publish)
    if stage in ("file_sync", "rename"):
        with pytest.raises(OnboardingError) as exc:
            prepared.import_license(raw, None)
        assert exc.value.code == "LICENSE_STORAGE_FAILED"
        assert not path.exists()
    else:
        result = prepared.import_license(raw, None)
        assert result.action_status == "OUTCOME_UNKNOWN"
        assert path.read_bytes() == raw
    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(prepared, "status", status)
    if path.exists():
        assert (
            prepared.import_license(raw, prepared.license_file_digest()).action_status
            == "ALREADY_APPLIED"
        )


def test_same_bytes_expired_does_not_lie_active(prepared, product):
    raw = signed(product)
    prepared.import_license(raw, None)

    class Later:
        def wall_now(self):
            return datetime.now(UTC) + timedelta(days=3)

        def monotonic_ns(self):
            return prepared._monotonic + 1

    raw = signed(
        product,
        validity_type="TERM",
        expires_at=(datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    prepared.import_license(raw, prepared.license_file_digest())
    prepared.time = Later()
    result = prepared.import_license(raw, prepared.license_file_digest())
    assert result.action_status == "ALREADY_APPLIED"
    assert result.status.state == RuntimeState.EXPIRED


def test_corrupt_old_file_digest_full_and_identical_reject(prepared):
    path = prepared.paths.root / "shared/active.license.json"
    raw = b"broken"
    path.write_bytes(raw)
    assert prepared.license_file_digest() == hashlib.sha256(raw).hexdigest()
    with pytest.raises(OnboardingError):
        prepared.import_license(raw, prepared.license_file_digest())
    path.write_bytes(b"x" * 100000)
    assert prepared.license_file_digest() == hashlib.sha256(b"x" * 100000).hexdigest()
    path.unlink()
    path.symlink_to(prepared.paths.root / "identity/service.key")
    with pytest.raises(ValueError):
        prepared.license_file_digest()


def test_busy_and_candidate_bounds(prepared, product):
    for raw in (b"", b"x" * 65537, b"bad"):
        with pytest.raises(OnboardingError):
            prepared.import_license(raw, None)
    with record_lock(prepared.paths.root / "shared/license-import.lock"):
        with pytest.raises(OnboardingError) as exc:
            prepared.import_license(signed(product), None)
        assert exc.value.code == "LICENSE_STATE_CONFLICT"


def test_same_factory_runtime_recovery_and_restart_preserve_other_service(prepared, product):
    from license_sdk.recovery import canonical_json, utc_text

    prepared.import_license(signed(product), None)
    root = prepared.paths.root
    commercial = (root / "shared/active.license.json").read_bytes()
    worker = (root / "state/worker/clock.json").read_bytes()
    (root / "state/backend/clock.json").write_bytes(b"injected corrupt clock")
    request = prepared.get_or_create_recovery_request()
    now = datetime.now(UTC)
    data = dict(
        schema_version=1,
        kind="clock-recovery",
        key_id="new-v1",
        product_id=product[0].profile.product_id,
        installation_id=request.installation_id,
        service="backend",
        recovery_id=str(uuid4()),
        target_id=request.target_id,
        issued_at=utc_text(now),
        window_start=utc_text(now),
        window_end=utc_text(now + timedelta(days=1)),
    )
    payload = canonical_json(data)
    recovery = canonical_json(
        {
            "payload": base64.urlsafe_b64encode(payload).decode().rstrip("="),
            "signature": base64.urlsafe_b64encode(product[1].sign(payload)).decode().rstrip("="),
        }
    )
    assert prepared.apply_recovery(recovery).action_status == "APPLIED"
    assert prepared.status(read_only=True).state == RuntimeState.ACTIVE
    assert prepared.apply_recovery(recovery).action_status == "ALREADY_APPLIED"
    prepared.close()
    restarted = LicenseRuntime.from_config(product[0].to_bytes(), root=root, service="backend")
    assert restarted.apply_recovery(recovery).action_status == "ALREADY_APPLIED"
    assert (root / "state/worker/clock.json").read_bytes() == worker
    assert (root / "shared/active.license.json").read_bytes() == commercial


def test_two_runtime_threads_cas_at_most_one_publish(prepared, product):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    barrier = threading.Barrier(2)

    def attempt(_):
        runtime = LicenseRuntime.from_config(
            product[0].to_bytes(), root=prepared.paths.root, service="backend"
        )
        raw = signed(product)
        barrier.wait(timeout=5)
        try:
            return runtime.import_license(raw, None).action_status
        except OnboardingError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert results.count("APPLIED") == 1
    assert results.count("LICENSE_STATE_CONFLICT") == 1


def test_shared_attributes_ready_before_replace_and_default_stays_private(
    prepared, product, monkeypatch
):
    root = prepared.paths.root
    path = root / "shared/active.license.json"
    unchanged = {
        file: (file.read_bytes(), stat.S_IMODE(file.stat().st_mode))
        for file in root.rglob("*")
        if file.is_file()
    }
    first = signed(product)
    prepared.import_license(first, None)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    raw = signed(product)
    replace = os.replace
    observed = []

    def check_before_replace(source, target):
        details = os.stat(source, follow_symlinks=False)
        assert details.st_gid == os.getgid()
        assert stat.S_IMODE(details.st_mode) == 0o640
        assert path.read_bytes() == first
        observed.append(source)
        replace(source, target)

    monkeypatch.setattr(os, "replace", check_before_replace)
    result = prepared.import_license(raw, prepared.license_file_digest(), shared_group=os.getgid())
    assert result.action_status == "APPLIED"
    assert observed and path.read_bytes() == raw
    assert path.stat().st_gid == os.getgid()
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    for file, before in unchanged.items():
        assert (file.read_bytes(), stat.S_IMODE(file.stat().st_mode)) == before


def test_same_bytes_shared_attributes_repaired_atomically_then_confirmed(
    prepared, product, monkeypatch
):
    raw = signed(product)
    prepared.import_license(raw, None)
    path = prepared.paths.root / "shared/active.license.json"
    old_inode = path.stat().st_ino
    digest = prepared.license_file_digest()
    result = prepared.import_license(raw, digest, shared_group=os.getgid())
    assert result.action_status == "ALREADY_APPLIED"
    assert result.status.state == RuntimeState.ACTIVE
    assert path.read_bytes() == raw and path.stat().st_ino != old_inode
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    inode = path.stat().st_ino

    def unexpected_replace(*args):
        raise AssertionError("matching attributes must not republish")

    monkeypatch.setattr(os, "replace", unexpected_replace)
    assert prepared.import_license(raw, digest, shared_group=os.getgid()).action_status == (
        "ALREADY_APPLIED"
    )
    assert path.stat().st_ino == inode


@pytest.mark.parametrize("failure", ["group", "mode", "unconfirmed"])
@pytest.mark.parametrize("same_bytes", [False, True])
def test_shared_attribute_failure_preserves_old_bytes_and_attributes(
    prepared, product, monkeypatch, failure, same_bytes
):
    raw = signed(product)
    prepared.import_license(raw, None)
    path = prepared.paths.root / "shared/active.license.json"
    before = path.stat()
    candidate = raw if same_bytes else signed(product)

    def fail(*args):
        raise PermissionError("injected attribute failure")

    group = os.getgid()
    if failure == "group":
        monkeypatch.setattr(os, "fchown", fail)
    elif failure == "mode":
        monkeypatch.setattr(os, "fchmod", fail)
    else:
        group += 1
        monkeypatch.setattr(os, "fchown", lambda *args: None)
    with pytest.raises(OnboardingError) as exc:
        prepared.import_license(candidate, prepared.license_file_digest(), shared_group=group)
    assert exc.value.code == "LICENSE_STORAGE_FAILED"
    after = path.stat()
    assert path.read_bytes() == raw
    assert (after.st_ino, after.st_gid, after.st_mode) == (
        before.st_ino,
        before.st_gid,
        before.st_mode,
    )
    assert not list(path.parent.glob(".license-import-*"))


def test_expired_same_bytes_can_repair_shared_attributes(prepared, product):
    raw = signed(
        product,
        validity_type="TERM",
        expires_at=(datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    prepared.import_license(raw, None)

    class Later:
        def wall_now(self):
            return datetime.now(UTC) + timedelta(days=3)

        def monotonic_ns(self):
            return prepared._monotonic + 1

    prepared.time = Later()
    result = prepared.import_license(raw, prepared.license_file_digest(), shared_group=os.getgid())
    assert result.action_status == "ALREADY_APPLIED"
    assert result.status.state == RuntimeState.EXPIRED
    assert (
        stat.S_IMODE((prepared.paths.root / "shared/active.license.json").stat().st_mode) == 0o640
    )


@pytest.mark.parametrize("group", [-1, True, "staff", 1.0, 1 << 31])
def test_invalid_shared_group_rejected_before_lock_or_publish(prepared, product, group):
    root = prepared.paths.root
    before = sorted(root.rglob("*"))
    with pytest.raises(ValueError, match="numeric GID"):
        prepared.import_license(signed(product), None, shared_group=group)
    assert sorted(root.rglob("*")) == before
