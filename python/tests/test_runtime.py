"""Durable offline clock and runtime observation, using only synthetic installations."""

import base64
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from license_sdk import ICP_DIAG
from license_sdk.identity import binding_id, host_fingerprint
from license_sdk.runtime import (
    BuildMode,
    ClockState,
    CommitPhase,
    FileStateStore,
    LicenseRuntime,
    RuntimePaths,
    RuntimeState,
    StateStoreError,
    decode_clock,
    encode_clock,
)

NOW = datetime(2026, 10, 20, tzinfo=UTC)
MASTER = b"x" * 32


class FakeTime:
    def __init__(self):
        self.wall = NOW
        self.mono = 0

    def wall_now(self):
        return self.wall

    def monotonic_ns(self):
        return self.mono

    def advance(self, seconds):
        self.wall += timedelta(seconds=seconds)
        self.mono += int(seconds * 1_000_000_000)


@pytest.fixture
def installation(tmp_path):
    root = tmp_path / "license"
    for directory in ("identity", "shared", "state/ai"):
        (root / directory).mkdir(parents=True)
    install_id = str(uuid4())
    (root / "identity/installation.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "product_id": "icp-diag",
                "installation_id": install_id,
                "initialized": True,
            }
        )
    )
    (root / "identity/service.key").write_bytes(MASTER)
    clock = encode_clock(
        ClockState(0, 0, NOW, None),
        product_id="icp-diag",
        installation_id=install_id,
        service="ai",
        service_key=MASTER,
    )
    (root / "state/ai/clock.json").write_bytes(clock)
    return root, install_id


@pytest.fixture
def runtime(installation):
    root, install_id = installation
    clock = FakeTime()
    signer = Ed25519PrivateKey.generate()
    trust = {
        "test": signer.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    }
    data = {
        "schema_version": 1,
        "key_id": "test",
        "kind": "commercial-license",
        "license_id": str(uuid4()),
        "customer_id": "test",
        "customer_name": "测试",
        "product_id": "icp-diag",
        "binding_type": "UNBOUND",
        "binding_scheme": None,
        "binding_id": None,
        "validity_type": "TERM",
        "issued_at": "2026-10-20T00:00:00Z",
        "not_before": "2026-10-20T00:00:00Z",
        "expires_at": "2027-10-20T00:00:00Z",
    }
    instance = LicenseRuntime(
        profile=ICP_DIAG,
        service="ai",
        paths=RuntimePaths(root, root / "missing-machine-id"),
        trusted_keys=trust,
        mode=BuildMode.CUSTOMER_REQUIRED,
        time_source=clock,
    )

    def publish(changes=None):
        value = data | (changes or {})
        raw = json.dumps(value).encode()
        outer = {
            "payload": base64.urlsafe_b64encode(raw).decode().rstrip("="),
            "signature": base64.urlsafe_b64encode(signer.sign(raw)).decode().rstrip("="),
        }
        (root / "shared/active.license.json").write_text(json.dumps(outer))

    publish()
    return instance, clock, root, install_id, publish


def read_clock(root, install_id):
    return decode_clock(
        (root / "state/ai/clock.json").read_bytes(),
        product_id="icp-diag",
        installation_id=install_id,
        service="ai",
        service_key=MASTER,
    )


def test_unbound_no_machine_and_checkpoint(runtime):
    instance, clock, root, install_id, _ = runtime
    assert instance.status().state == RuntimeState.ACTIVE
    assert read_clock(root, install_id).sequence == 0
    clock.advance(29.9)
    assert instance.status().state == RuntimeState.ACTIVE
    assert read_clock(root, install_id).sequence == 0
    clock.advance(0.1)
    assert instance.status().state == RuntimeState.ACTIVE
    assert read_clock(root, install_id).sequence == 1
    assert read_clock(root, install_id).high_watermark == NOW + timedelta(seconds=30)
    assert instance.status(force_checkpoint=True).state == RuntimeState.ACTIVE
    assert read_clock(root, install_id).sequence == 2
    assert os.stat(root / "state/ai/clock.json").st_mode & 0o777 == 0o600


def test_rollback_and_automatic_catchup(runtime):
    instance, clock, root, _, _ = runtime
    assert instance.status().state == RuntimeState.ACTIVE
    clock.advance(86_401)
    clock.wall = NOW
    assert instance.status().state == RuntimeState.CLOCK_ROLLBACK
    before = (root / "state/ai/clock.json").read_bytes()
    assert instance.status(force_checkpoint=True).state == RuntimeState.CLOCK_ROLLBACK
    assert (root / "state/ai/clock.json").read_bytes() == before
    clock.wall = NOW + timedelta(seconds=1)
    assert instance.status().state == RuntimeState.ACTIVE  # exact one-day tolerance
    assert instance.status().effective_now == NOW + timedelta(seconds=86_401)


def test_forward_jump_retained_without_checkpoint(runtime):
    instance, clock, _, _, _ = runtime
    instance.status()
    clock.wall += timedelta(days=400)
    assert instance.status().state == RuntimeState.EXPIRED
    clock.wall = NOW
    result = instance.status()
    assert result.state == RuntimeState.CLOCK_ROLLBACK
    assert RuntimeState.EXPIRED in result.reasons
    assert result.effective_now == NOW + timedelta(days=400)


def test_exact_subsecond_expiry(runtime):
    instance, clock, _, _, publish = runtime
    publish({"expires_at": "2026-10-20T00:00:01Z"})
    assert instance.status().state == RuntimeState.ACTIVE
    clock.advance(0.999999)
    assert instance.status().state == RuntimeState.ACTIVE
    clock.advance(0.000001)
    assert instance.status().state == RuntimeState.EXPIRED


@pytest.mark.parametrize(
    "lag,expected", [(86_399, "ACTIVE"), (86_400, "ACTIVE"), (86_401, "CLOCK_ROLLBACK")]
)
def test_one_day_rollback_boundary(runtime, lag, expected):
    instance, clock, _, _, _ = runtime
    instance.require_valid()
    clock.wall = NOW - timedelta(seconds=lag)
    result = instance.status(read_only=True)
    assert result.state.value == expected
    assert result.effective_now == NOW


def test_guard_never_writes_clock_even_after_checkpoint_interval(runtime):
    instance, clock, root, install_id, _ = runtime
    before = (root / "state/ai/clock.json").read_bytes()
    instance.require_valid()
    for _ in range(3):
        clock.advance(31)
        instance.require_valid()
        assert (root / "state/ai/clock.json").read_bytes() == before
    assert not (root / "state/ai/clock.lock").exists()
    assert instance.checkpoint().state == RuntimeState.ACTIVE
    assert read_clock(root, install_id).sequence == 1


def test_commercial_boundaries_have_no_rollback_grace(runtime):
    from license_sdk.guard import LicenseDeniedError

    instance, clock, root, _, publish = runtime
    publish({"not_before": "2026-10-20T00:00:01Z", "expires_at": "2026-10-20T00:00:03Z"})
    candidate = (root / "shared/active.license.json").read_bytes()
    for offset, expected in enumerate(("NOT_YET_VALID", "ACTIVE", "ACTIVE", "EXPIRED", "EXPIRED")):
        clock.wall = NOW + timedelta(seconds=offset)
        clock.mono = offset * 1_000_000_000
        assert instance.status(read_only=True).state.value == expected
        fresh = LicenseRuntime(
            profile=ICP_DIAG,
            service="ai",
            paths=instance.paths,
            trusted_keys=instance.trusted_keys,
            mode=BuildMode.CUSTOMER_REQUIRED,
            time_source=clock,
        )
        assert fresh.status(read_only=True).state.value == expected
        assert instance.preflight(candidate).state.value == expected
        if expected == "ACTIVE":
            instance.require_valid()
        else:
            with pytest.raises(LicenseDeniedError) as caught:
                instance.require_valid()
            assert caught.value.state == expected


@pytest.mark.parametrize("damage", ["missing", "bytes", "mac", "symlink"])
def test_clock_integrity_sticky_no_recreation(runtime, damage):
    instance, _, root, _, _ = runtime
    assert instance.status().state == RuntimeState.ACTIVE
    path = root / "state/ai/clock.json"
    original = path.read_bytes()
    if damage == "missing":
        path.unlink()
    elif damage == "bytes":
        path.write_bytes(b"broken")
    elif damage == "mac":
        data = json.loads(original)
        data["mac"] = "A" * 43
        path.write_text(json.dumps(data))
    else:
        path.unlink()
        path.symlink_to(root / "identity/installation.json")
    assert instance.status().state == RuntimeState.CLOCK_STATE_INVALID
    if damage == "missing":
        assert not path.exists()
    if path.is_symlink():
        path.unlink()
    path.write_bytes(original)
    assert instance.status().state == RuntimeState.CLOCK_STATE_INVALID
    assert instance.status().mode == "RESTRICTED"


def test_license_observed_each_time_and_priority(runtime):
    instance, _, root, _, publish = runtime
    first = instance.status()
    path = root / "shared/active.license.json"
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    assert instance.status().file_digest != first.file_digest
    path.write_bytes(b"broken")
    assert instance.status().state == RuntimeState.INVALID
    assert instance.status().mode == "RESTRICTED"
    path.unlink()
    (root / "state/ai/clock.json").unlink()
    result = instance.status()
    assert result.state == RuntimeState.UNLICENSED
    assert RuntimeState.CLOCK_STATE_INVALID in result.reasons
    publish()
    assert instance.status().state == RuntimeState.CLOCK_STATE_INVALID


def test_future_license_remains_restricted_on_bad_clock(runtime):
    instance, _, root, _, publish = runtime
    publish({"not_before": "2027-01-01T00:00:00Z"})
    (root / "state/ai/clock.json").unlink()
    result = instance.status()
    assert result.state == RuntimeState.CLOCK_STATE_INVALID
    assert RuntimeState.NOT_YET_VALID in result.reasons
    assert result.mode == "RESTRICTED"


def test_identity_missing_incomplete_changed(runtime):
    instance, _, root, _, publish = runtime
    path = root / "identity/installation.json"
    original = path.read_text()
    data = json.loads(original)
    data["initialized"] = False
    path.write_text(json.dumps(data))
    assert instance.status().state == RuntimeState.CONFIG_INVALID
    path.write_text(original)
    assert instance.status().state == RuntimeState.CONFIG_INVALID
    # Binding cannot raise an AssertionError if installation is unavailable.
    publish(
        {
            "binding_type": "HOST_INSTALL",
            "binding_scheme": "host-install-code-v1",
            "binding_id": "a" * 40,
        }
    )
    assert instance.status().state == RuntimeState.CONFIG_INVALID


def test_bound_requires_real_machine_id(runtime):
    instance, _, root, install_id, publish = runtime
    machine = b"00112233445566778899aabbccddeeff\n"
    bound = binding_id(ICP_DIAG, host_fingerprint(ICP_DIAG, machine), install_id).hex()
    publish(
        {
            "binding_type": "HOST_INSTALL",
            "binding_scheme": "host-install-code-v1",
            "binding_id": bound,
        }
    )
    assert instance.status().state == RuntimeState.INVALID
    instance.paths.machine_id_file.write_bytes(machine)
    assert instance.status().state == RuntimeState.ACTIVE


def test_restart_keeps_persisted_reference(runtime):
    instance, clock, root, _, _ = runtime
    instance.status()
    clock.advance(86_401)
    instance.status(force_checkpoint=True)
    clock.wall = NOW
    restarted = LicenseRuntime(
        profile=ICP_DIAG,
        service="ai",
        paths=instance.paths,
        trusted_keys=instance.trusted_keys,
        mode=BuildMode.CUSTOMER_REQUIRED,
        time_source=clock,
    )
    assert restarted.status().state == RuntimeState.CLOCK_ROLLBACK
    assert restarted.status().effective_now >= NOW + timedelta(seconds=86_401)
    assert (root / "state/ai/clock.json").exists()


@pytest.mark.parametrize("stage", ["before_replace", "directory_sync"])
def test_checkpoint_commit_failure_sticky(runtime, monkeypatch, stage):
    instance, _, root, _, _ = runtime
    instance.status()
    old = (root / "state/ai/clock.json").read_bytes()
    if stage == "before_replace":

        def fail_replace(*args):
            raise OSError("injected replace error")

        monkeypatch.setattr(os, "replace", fail_replace)
    else:
        original = os.fsync
        calls = 0

        def fail_directory(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected directory sync error")
            original(fd)

        monkeypatch.setattr(os, "fsync", fail_directory)
    assert instance.status(force_checkpoint=True).state == RuntimeState.CLOCK_STATE_INVALID
    if stage == "before_replace":
        assert (root / "state/ai/clock.json").read_bytes() == old
    assert instance.status().state == RuntimeState.CLOCK_STATE_INVALID


def test_store_commit_classification(installation, monkeypatch):
    root, _ = installation
    store = FileStateStore(root / "state/ai/clock.json")
    old = store.read()
    with store.locked(), pytest.raises(StateStoreError) as error:
        store.replace(b"wrong expected", b"data")
    assert error.value.phase == CommitPhase.NOT_COMMITTED
    assert store.read() == old


def test_development_and_invalid_customer_no_filecreation(tmp_path):
    paths = RuntimePaths(tmp_path / "absent")
    dev = LicenseRuntime(
        profile=ICP_DIAG, service="ai", paths=paths, trusted_keys={}, mode=BuildMode.DEVELOPMENT
    )
    assert dev.status().state == RuntimeState.DEVELOPMENT
    assert not paths.root.exists()
    customer = LicenseRuntime(
        profile=ICP_DIAG,
        service="ai",
        paths=paths,
        trusted_keys={},
        mode=BuildMode.CUSTOMER_REQUIRED,
    )
    assert customer.status().state == RuntimeState.CONFIG_INVALID
    assert not paths.root.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("epoch", True),
        ("sequence", -1),
        ("sequence", 1.0),
        ("sequence", 2**63),
        ("schema_version", True),
        ("service", "metor"),
        ("product_id", "other"),
        ("installation_id", str(uuid4())),
        ("last_recovery_id", "broken"),
        ("high_watermark", "2026-10-20T00:00:00.1Z"),
    ],
)
def test_signed_clock_schema_rejection(installation, field, value):
    root, install_id = installation
    outer = json.loads((root / "state/ai/clock.json").read_bytes())
    raw = base64.urlsafe_b64decode(outer["payload"] + "==")
    data = json.loads(raw)
    data[field] = value
    raw = json.dumps(data).encode()
    key = hmac.digest(MASTER, b"icp-diag/state/ai/v1", "sha256")
    envelope = {
        "payload": base64.urlsafe_b64encode(raw).decode().rstrip("="),
        "mac": base64.urlsafe_b64encode(hmac.digest(key, raw, "sha256")).decode().rstrip("="),
    }
    with pytest.raises(ValueError):
        decode_clock(
            json.dumps(envelope).encode(),
            product_id="icp-diag",
            installation_id=install_id,
            service="ai",
            service_key=MASTER,
        )


def test_regressed_valid_clock_is_sticky(runtime):
    instance, clock, root, _, _ = runtime
    path = root / "state/ai/clock.json"
    old = path.read_bytes()
    instance.status()
    clock.advance(30)
    instance.status()
    path.write_bytes(old)
    assert instance.status().state == RuntimeState.CLOCK_STATE_INVALID


def test_mac_covers_raw_bytes(installation):
    root, install_id = installation
    outer = json.loads((root / "state/ai/clock.json").read_bytes())
    raw = base64.urlsafe_b64decode(outer["payload"] + "==")
    outer["payload"] = base64.urlsafe_b64encode(raw + b" ").decode().rstrip("=")
    with pytest.raises(ValueError):
        decode_clock(
            json.dumps(outer).encode(),
            product_id="icp-diag",
            installation_id=install_id,
            service="ai",
            service_key=MASTER,
        )
    with pytest.raises(ValueError):
        decode_clock(
            b"{}",
            product_id="icp-diag",
            installation_id=install_id,
            service="ai",
            service_key=b"short",
        )


def test_public_seed_vectors_are_packaged_and_match():
    from importlib.resources import files

    authority = Path(__file__).parents[2] / "protocol/clock-state-v1-vectors.json"
    packaged = files("license_sdk").joinpath("data/clock-state-v1-vectors.json").read_bytes()
    assert packaged == authority.read_bytes()
    vectors = json.loads(packaged)
    for vector in vectors["vectors"]:
        raw = vector["envelope"].encode()
        assert hashlib.sha256(raw).hexdigest() == vector["sha256"]
        params = dict(
            product_id=vectors["product_id"],
            installation_id=vectors["installation_id"],
            service=vector["service"],
            service_key=bytes.fromhex(vectors["service_key_hex"]),
        )
        clock = decode_clock(raw, **params)
        assert encode_clock(clock, **params) == raw


def test_preflight_never_persists_and_allows_initial_activation(runtime):
    instance, clock, root, _, _ = runtime
    active = root / "shared/active.license.json"
    raw = active.read_bytes()
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    instance.status(read_only=True)
    clock.advance(60)
    assert instance.preflight(raw).acceptable
    assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    assert not (root / "state/ai/clock.lock").exists()
    active.unlink()
    assert instance.preflight(raw).acceptable
    assert not active.exists()


def test_preflight_real_utc_ignores_polluted_reference_retains_restriction(runtime):
    instance, clock, root, _, _ = runtime
    raw = (root / "shared/active.license.json").read_bytes()
    instance.status(read_only=True)
    clock.wall += timedelta(days=500)
    instance.status(force_checkpoint=True)
    clock.wall = NOW
    before = (root / "state/ai/clock.json").read_bytes()
    result = instance.preflight(raw)
    assert result.acceptable
    assert RuntimeState.CLOCK_ROLLBACK in result.reasons
    assert (root / "state/ai/clock.json").read_bytes() == before
    assert instance.status(read_only=True).mode == "RESTRICTED"


def test_preflight_future_expired_invalid_and_wrong_host(runtime):
    instance, _, root, _, publish = runtime
    for changes, state in [
        ({"not_before": "2027-01-01T00:00:00Z"}, RuntimeState.NOT_YET_VALID),
        (
            {"not_before": "2026-10-19T00:00:00Z", "expires_at": "2026-10-20T00:00:00Z"},
            RuntimeState.EXPIRED,
        ),
        (
            {
                "binding_type": "HOST_INSTALL",
                "binding_scheme": "host-install-code-v1",
                "binding_id": "a" * 40,
            },
            RuntimeState.INVALID,
        ),
    ]:
        publish(changes)
        result = instance.preflight((root / "shared/active.license.json").read_bytes())
        assert not result.acceptable
        assert result.state == state
    assert not instance.preflight(b"invalid").acceptable


def test_request_guard_observes_file_each_time(runtime):
    from license_sdk.guard import LicenseDeniedError

    instance, _, root, _, publish = runtime
    instance.require_valid()
    publish({"not_before": "2026-10-19T00:00:00Z", "expires_at": "2026-10-20T00:00:00Z"})
    with pytest.raises(LicenseDeniedError) as caught:
        instance.require_valid()
    assert caught.value.state == "EXPIRED"
    assert caught.value.reasons == ("EXPIRED",)
    (root / "shared/active.license.json").unlink()
    with pytest.raises(LicenseDeniedError) as caught:
        instance.require_valid()
    assert caught.value.state == "UNLICENSED"


def test_internal_auth_always_needs_identity_in_development(tmp_path):
    instance = LicenseRuntime(
        profile=ICP_DIAG,
        service="ai",
        paths=RuntimePaths(tmp_path),
        trusted_keys={},
        mode=BuildMode.DEVELOPMENT,
    )
    assert instance.status().state == RuntimeState.DEVELOPMENT
    with pytest.raises(OSError):
        from license_sdk.proofs import ICP_CONTROL

        instance.control_proof(config=ICP_CONTROL)
