"""One-process online recovery, strictly synthetic clocks, keys and state files."""

import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import MappingProxyType
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_runtime import MASTER, NOW, read_clock
from test_runtime import installation as installation
from test_runtime import runtime as runtime

from license_sdk import ICP_DIAG
from license_sdk.recovery import (
    RecoveryError,
    canonical_json,
    parse_recovery_request,
    utc_text,
    verify_recovery,
)
from license_sdk.runtime import (
    MAX_COUNTER,
    BuildMode,
    ClockState,
    LicenseRuntime,
    RuntimeState,
    encode_clock,
)


@pytest.fixture
def recovery(runtime):
    instance, clock, root, install, publish = runtime
    signer = Ed25519PrivateKey.generate()
    instance.trusted_keys = MappingProxyType(
        dict(instance.trusted_keys)
        | {
            "recovery-test": signer.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        }
    )

    def sign(request, **changes):
        value = {
            "schema_version": 1,
            "kind": "clock-recovery",
            "key_id": "recovery-test",
            "product_id": "icp-diag",
            "installation_id": install,
            "service": "ai",
            "recovery_id": str(uuid4()),
            "target_id": request.target_id,
            "issued_at": utc_text(NOW),
            "window_start": utc_text(NOW),
            "window_end": utc_text(NOW + timedelta(days=7)),
        } | changes
        payload = canonical_json(value)
        return canonical_json(
            {
                "payload": base64.urlsafe_b64encode(payload).decode().rstrip("="),
                "signature": base64.urlsafe_b64encode(signer.sign(payload)).decode().rstrip("="),
            }
        )

    return instance, clock, root, install, publish, sign


def anomaly(recovery, kind):
    instance, clock, root, *_ = recovery
    instance.require_valid()
    if kind == "rollback":
        clock.wall += timedelta(days=400)
        instance.checkpoint()
        clock.wall = NOW
    elif kind == "missing":
        (root / "state/ai/clock.json").unlink()
    else:
        (root / "state/ai/clock.json").write_bytes(b"broken")
    assert instance.status(read_only=True).state in (
        RuntimeState.CLOCK_STATE_INVALID,
        RuntimeState.CLOCK_ROLLBACK,
    )
    return instance.get_or_create_recovery_request()


def restarted(instance, clock):
    return LicenseRuntime(
        profile=ICP_DIAG,
        service="ai",
        paths=instance.paths,
        trusted_keys=instance.trusted_keys,
        mode=BuildMode.CUSTOMER_REQUIRED,
        time_source=clock,
    )


@pytest.mark.parametrize("kind", ["rollback", "missing", "broken"])
def test_apply_refresh_same_runtime_and_stable_requests(recovery, kind):
    instance, clock, root, install, _, sign = recovery
    license_file = root / "shared/active.license.json"
    original = license_file.read_bytes()
    request = anomaly(recovery, kind)
    challenge = root / "state/ai/recovery-challenge.json"
    before = challenge.stat().st_mtime_ns
    assert parse_recovery_request(request.to_bytes(), ICP_DIAG) == request
    assert instance.get_or_create_recovery_request() == request
    assert challenge.stat().st_mtime_ns == before
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(
            result == request
            for result in pool.map(lambda _: instance.get_or_create_recovery_request(), range(16))
        )
    assert restarted(instance, clock).get_or_create_recovery_request() == request
    signed = sign(request)
    result = instance.apply_recovery(signed)
    assert result.action_status == "APPLIED"
    assert result.status.state == RuntimeState.ACTIVE
    assert read_clock(root, install).epoch == (request.base_epoch or 0) + 1
    assert read_clock(root, install).sequence == 0
    assert not challenge.exists()
    after = (root / "state/ai/clock.json").read_bytes()
    clock.wall += timedelta(days=8)
    assert instance.apply_recovery(signed).action_status == "ALREADY_APPLIED"
    assert (root / "state/ai/clock.json").read_bytes() == after
    assert restarted(instance, clock).apply_recovery(signed).action_status == "ALREADY_APPLIED"
    assert license_file.read_bytes() == original


def test_not_required_and_commercial_expiry_retained(recovery):
    instance, _, root, _, publish, sign = recovery
    publish({"not_before": "2026-10-19T00:00:00Z", "expires_at": utc_text(NOW)})
    with pytest.raises(RecoveryError, match="NOT_REQUIRED"):
        instance.get_or_create_recovery_request()
    assert not (root / "state/ai/recovery-challenge.json").exists()
    (root / "state/ai/clock.json").write_bytes(b"bad")
    request = instance.get_or_create_recovery_request()
    assert instance.apply_recovery(sign(request)).status.state == RuntimeState.EXPIRED


@pytest.mark.parametrize(
    "change,code",
    [
        ({"product_id": "other"}, "INVALID"),
        ({"installation_id": str(uuid4())}, "TARGET_CHANGED"),
        ({"service": "metor"}, "TARGET_CHANGED"),
        ({"target_id": "0" * 40}, "TARGET_CHANGED"),
        ({"kind": "commercial-license"}, "INVALID"),
        ({"recovery_id": "00000000-0000-0000-0000-000000000000"}, "INVALID"),
        ({"window_start": utc_text(NOW + timedelta(seconds=1))}, "WINDOW_INVALID"),
        ({"window_end": utc_text(NOW)}, "INVALID"),
        ({"window_end": utc_text(NOW + timedelta(days=7, seconds=1))}, "INVALID"),
    ],
)
def test_reject_without_modification(recovery, change, code):
    instance, _, root, _, _, sign = recovery
    request = anomaly(recovery, "broken")
    before = (root / "state/ai/clock.json").read_bytes()
    with pytest.raises(RecoveryError, match=code):
        instance.apply_recovery(sign(request, **change))
    assert (root / "state/ai/clock.json").read_bytes() == before
    assert instance.status(read_only=True).state == RuntimeState.CLOCK_STATE_INVALID


@pytest.mark.parametrize("offset,allowed", [(0, True), (604799, True), (604800, False)])
def test_recovery_exact_window_without_day_grace(recovery, offset, allowed):
    instance, clock, root, _, _, sign = recovery
    request = anomaly(recovery, "missing")
    clock.wall = NOW + timedelta(seconds=offset)
    if allowed:
        assert instance.apply_recovery(sign(request)).action_status == "APPLIED"
    else:
        with pytest.raises(RecoveryError, match="WINDOW_INVALID"):
            instance.apply_recovery(sign(request))
        assert not (root / "state/ai/clock.json").exists()


def test_changed_target_and_healthy_observation_invalidate(recovery):
    instance, clock, root, _, _, sign = recovery
    request = anomaly(recovery, "rollback")
    signed = sign(request)
    clock.wall += timedelta(days=400)
    instance.status(read_only=True)
    assert not (root / "state/ai/recovery-challenge.json").exists()
    clock.wall = NOW
    newer = instance.get_or_create_recovery_request()
    assert newer.nonce != request.nonce
    with pytest.raises(RecoveryError, match="TARGET_CHANGED"):
        instance.apply_recovery(signed)


@pytest.mark.parametrize("failure", ["before", "after"])
def test_commit_fault_and_same_file_retry(recovery, monkeypatch, failure):
    instance, _, root, install, _, sign = recovery
    request = anomaly(recovery, "rollback")
    signed = sign(request)
    before = (root / "state/ai/clock.json").read_bytes()
    with monkeypatch.context() as patch:
        if failure == "before":

            def fail(*args):
                raise OSError("injected before publication")

            patch.setattr(os, "replace", fail)
            with pytest.raises(RecoveryError, match="STORAGE_FAILED"):
                instance.apply_recovery(signed)
            assert (root / "state/ai/clock.json").read_bytes() == before
        else:
            original = os.fsync
            calls = 0

            def fail_directory(fd):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected directory sync failure")
                original(fd)

            patch.setattr(os, "fsync", fail_directory)
            result = instance.apply_recovery(signed)
            assert result.action_status == "OUTCOME_UNKNOWN"
            assert result.status.state == RuntimeState.CLOCK_STATE_INVALID
    result = instance.apply_recovery(signed)
    assert result.action_status == ("APPLIED" if failure == "before" else "ALREADY_APPLIED")
    assert result.status.state == RuntimeState.ACTIVE
    assert read_clock(root, install).epoch == request.base_epoch + 1


def test_challenge_unknown_commit_does_not_change_nonce_on_retry(recovery, monkeypatch):
    instance, clock, root, *_ = recovery
    (root / "state/ai/clock.json").write_bytes(b"broken")
    original = os.fsync
    calls = 0

    def fail(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("directory sync")
        original(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail)
        with pytest.raises(RecoveryError) as caught:
            instance.get_or_create_recovery_request()
        assert caught.value.http_status == 202
    existing = (root / "state/ai/recovery-challenge.json").read_bytes()
    request = instance.get_or_create_recovery_request()
    assert (root / "state/ai/recovery-challenge.json").read_bytes() == existing
    assert restarted(instance, clock).get_or_create_recovery_request() == request


@pytest.mark.parametrize("damage", ["symlink", "oversize", "directory"])
def test_unsafe_clock_is_not_recoverable(recovery, damage):
    instance, _, root, *_ = recovery
    path = root / "state/ai/clock.json"
    path.unlink()
    if damage == "symlink":
        path.symlink_to(root / "identity/service.key")
    elif damage == "oversize":
        path.write_bytes(b"x" * 8193)
    else:
        path.mkdir()
    with pytest.raises(RecoveryError, match="STORAGE_FAILED"):
        instance.get_or_create_recovery_request()
    assert not (root / "state/ai/recovery-challenge.json").exists()


def test_counter_exhaustion_never_wraps(recovery):
    instance, clock, root, install, _, sign = recovery
    (root / "state/ai/clock.json").write_bytes(
        encode_clock(
            ClockState(MAX_COUNTER, 0, NOW + timedelta(days=400), None),
            product_id="icp-diag",
            installation_id=install,
            service="ai",
            service_key=MASTER,
        )
    )
    request = instance.get_or_create_recovery_request()
    with pytest.raises(RecoveryError, match="INVALID"):
        instance.apply_recovery(sign(request))
    assert read_clock(root, install).epoch == MAX_COUNTER


def test_strict_signature_and_duplicate_unknown_fields(recovery):
    instance, _, _, _, _, sign = recovery
    request = anomaly(recovery, "broken")
    valid = sign(request)
    outer = json.loads(valid)
    for candidate in (
        b"{}",
        valid + b"x",
        canonical_json(outer | {"unknown": 1}),
        valid.replace(b'"payload":', b'"payload":"bad","payload":'),
        canonical_json(outer | {"signature": "A" * 86}),
    ):
        with pytest.raises(RecoveryError, match="INVALID"):
            verify_recovery(candidate, profile=ICP_DIAG, trusted_keys=instance.trusted_keys)


def test_consumed_challenge_cleanup_failure_does_not_undo_recovery(recovery, monkeypatch):
    from pathlib import Path

    instance, _, root, _, _, sign = recovery
    request = anomaly(recovery, "broken")
    original = Path.unlink

    def fail(path, *args, **kwargs):
        if path.name == "recovery-challenge.json":
            raise OSError("cleanup failure")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        signed = sign(request)
        assert instance.apply_recovery(signed).status.state == RuntimeState.ACTIVE
        assert instance.apply_recovery(signed).action_status == "ALREADY_APPLIED"
        assert instance.status(read_only=True).state == RuntimeState.ACTIVE
    assert (root / "state/ai/recovery-challenge.json").exists()


def test_apply_serializes_same_file_and_rejects_other_recovery_id(recovery):
    instance, _, root, install, _, sign = recovery
    request = anomaly(recovery, "missing")
    signed = sign(request)
    other = sign(request)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: instance.apply_recovery(signed), range(8)))
    assert sum(result.action_status == "APPLIED" for result in results) == 1
    assert sum(result.action_status == "ALREADY_APPLIED" for result in results) == 7
    before = (root / "state/ai/clock.json").read_bytes()
    with pytest.raises(RecoveryError, match="TARGET_CHANGED"):
        instance.apply_recovery(other)
    assert (root / "state/ai/clock.json").read_bytes() == before
    assert read_clock(root, install).epoch == (request.base_epoch or 0) + 1


def test_old_recovery_cannot_lower_reference_again(recovery):
    instance, clock, root, _, _, sign = recovery
    request = anomaly(recovery, "broken")
    signed = sign(request)
    instance.apply_recovery(signed)
    clock.wall += timedelta(days=400)
    instance.status(read_only=True)
    clock.wall = NOW
    result = instance.apply_recovery(signed)
    assert result.action_status == "ALREADY_APPLIED"
    assert result.status.state == RuntimeState.CLOCK_ROLLBACK
    assert result.status.effective_now >= NOW + timedelta(days=400)


def test_clock_bytes_change_invalidates_existing_request(recovery):
    instance, _, root, _, _, sign = recovery
    first = anomaly(recovery, "broken")
    old_signed = sign(first)
    (root / "state/ai/clock.json").write_bytes(b"different broken clock")
    with pytest.raises(RecoveryError, match="TARGET_CHANGED"):
        instance.apply_recovery(old_signed)
    second = instance.get_or_create_recovery_request()
    assert second.target_id != first.target_id
    assert second.nonce != first.nonce


def test_public_protocol_recovery_vectors():
    from pathlib import Path

    from license_sdk.recovery import decode_challenge

    values = json.loads(
        (Path(__file__).parents[2] / "protocol/recovery-v1-vectors.json").read_bytes()
    )
    for value in values["vectors"]:
        signed = verify_recovery(
            value["recovery"].encode(),
            profile=ICP_DIAG,
            trusted_keys={"recovery-test": base64.b64decode(values["public_key_der_base64"])},
        )
        parsed = parse_recovery_request(canonical_json(value["request"]), ICP_DIAG)
        assert parsed.target_id == signed.target_id
        assert (
            decode_challenge(
                value["challenge"].encode(),
                ICP_DIAG,
                value["service"],
                bytes.fromhex(values["service_key_hex"]),
            )
            == parsed
        )


def test_repeated_apply_needs_confirmed_durability(recovery, monkeypatch):
    instance, _, root, _, _, sign = recovery
    request = anomaly(recovery, "broken")
    signed = sign(request)
    instance.apply_recovery(signed)
    before = (root / "state/ai/clock.json").read_bytes()
    with monkeypatch.context() as patch:

        def fail(*args):
            raise OSError("durability confirmation failure")

        patch.setattr(os, "fsync", fail)
        result = instance.apply_recovery(signed)
        assert result.action_status == "OUTCOME_UNKNOWN"
        assert result.status.state == RuntimeState.CLOCK_STATE_INVALID
    assert instance.apply_recovery(signed).status.state == RuntimeState.ACTIVE
    assert (root / "state/ai/clock.json").read_bytes() == before


def test_public_negative_protocol_vectors():
    from pathlib import Path

    values = json.loads(
        (Path(__file__).parents[2] / "protocol/recovery-v1-vectors.json").read_bytes()
    )
    for value in values["negative_vectors"]:
        with pytest.raises(RecoveryError, match=value["error"]):
            verify_recovery(
                value["file"].encode(),
                profile=ICP_DIAG,
                trusted_keys={"recovery-test": base64.b64decode(values["public_key_der_base64"])},
            )


def test_healthy_cleanup_failure_requires_new_challenge(recovery, monkeypatch):
    from pathlib import Path

    instance, clock, root, _, _, sign = recovery
    old = anomaly(recovery, "rollback")
    original = Path.unlink

    def fail(path, *args, **kwargs):
        if path.name == "recovery-challenge.json":
            raise OSError("cannot invalidate challenge")
        return original(path, *args, **kwargs)

    clock.wall += timedelta(days=400)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        assert instance.status(read_only=True).state == RuntimeState.CLOCK_STATE_INVALID
    clock.wall = NOW
    with pytest.raises(RecoveryError, match="TARGET_CHANGED"):
        instance.apply_recovery(sign(old))
    newer = instance.get_or_create_recovery_request()
    assert newer.nonce != old.nonce
    assert instance.apply_recovery(sign(newer)).status.state == RuntimeState.ACTIVE


@pytest.mark.parametrize("year", [1, 999, 1000, 9999])
def test_request_time_has_zero_padded_canonical_year(year):
    from datetime import UTC, datetime

    from license_sdk.verification import parse_utc_timestamp

    value = datetime(year, 1, 1, tzinfo=UTC)
    assert parse_utc_timestamp(utc_text(value)) == value


def test_normal_challenge_invalidation_confirms_directory(recovery, monkeypatch):
    instance, clock, root, *_ = recovery
    anomaly(recovery, "rollback")
    clock.wall += timedelta(days=400)
    with monkeypatch.context() as patch:

        def fail(*args):
            raise OSError("failed directory persistence")

        patch.setattr(os, "fsync", fail)
        assert instance.status(read_only=True).state == RuntimeState.CLOCK_STATE_INVALID
    assert not (root / "state/ai/recovery-challenge.json").exists()
