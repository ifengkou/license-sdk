"""Initialization never recreates an established installation or uncertain draft."""

import json
import os
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from license_sdk.installation import (
    OnboardingError,
    initialize_installation,
    record_lock,
    validate_installation,
)
from license_sdk.runtime import ClockState, LicenseRuntime, RuntimeState, encode_clock


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_init_idempotent_and_private(product, tmp_path):
    config, _ = product
    root = tmp_path / "new"
    first = initialize_installation(config, root)
    assert first.action_status == "INITIALIZED"
    before = snapshot(root)
    second = initialize_installation(config, root)
    assert second.action_status == "ALREADY_INITIALIZED"
    assert second.installation_id == first.installation_id
    assert snapshot(root) == before
    for file in (root / "identity").iterdir():
        assert file.stat().st_mode & 0o777 == 0o600
    for service in ("backend", "worker"):
        runtime = LicenseRuntime.from_config(config.to_bytes(), root=root, service=service)
        assert runtime.status(read_only=True).state == RuntimeState.UNLICENSED


def test_root_creation_sync_failure_can_confirm_parent_on_retry(product, tmp_path, monkeypatch):
    import license_sdk.installation as installation

    root = tmp_path / "parent" / "customer"
    sync = installation.sync_directory
    synced = []

    def fail_once(path):
        synced.append(path)
        if path == tmp_path and synced.count(tmp_path) == 1:
            raise OSError("new parent directory entry not confirmed")
        sync(path)

    monkeypatch.setattr(installation, "sync_directory", fail_once)
    with pytest.raises(OnboardingError, match="initialization not confirmed"):
        initialize_installation(product[0], root)
    assert not (root / "identity").exists()
    assert initialize_installation(product[0], root).action_status == "INITIALIZED"
    assert synced.count(tmp_path) >= 2
    assert root.parent in synced


@pytest.mark.parametrize(
    "name",
    [
        "identity/installation.json",
        "identity/service.key",
        "state/backend/clock.json",
        "state/worker/clock.json",
    ],
)
def test_complete_missing_files_not_rebuilt(product, tmp_path, name):
    config, _ = product
    initialize_installation(config, tmp_path)
    (tmp_path / name).unlink()
    before = snapshot(tmp_path)
    with pytest.raises((ValueError, OSError)):
        initialize_installation(config, tmp_path)
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("fail_at", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
def test_sync_failure_preserves_or_resumes(product, tmp_path, monkeypatch, fail_at):
    config, _ = product
    fsync = os.fsync
    calls = 0

    def fail(fd):
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise OSError("injected sync failure")
        fsync(fd)

    monkeypatch.setattr(os, "fsync", fail)
    try:
        initialize_installation(config, tmp_path)
    except OnboardingError as exc:
        assert exc.code == "LICENSE_STORAGE_OUTCOME_UNKNOWN"
    before = snapshot(tmp_path)
    monkeypatch.setattr(os, "fsync", fsync)
    old_key = before.get("identity/service.key", before.get(".identity-stage/service.key"))
    try:
        initialize_installation(config, tmp_path)
    except ValueError:
        assert snapshot(tmp_path) == before
    else:
        if old_key is not None:
            assert (tmp_path / "identity/service.key").read_bytes() == old_key


def test_draft_resume_identity_and_existing_clock(product, tmp_path):
    config, _ = product
    initialize_installation(config, tmp_path)
    identity = tmp_path / "identity/installation.json"
    data = json.loads(identity.read_bytes())
    data["initialized"] = False
    identity.write_text(json.dumps(data))
    key = (tmp_path / "identity/service.key").read_bytes()
    backend = (tmp_path / "state/backend/clock.json").read_bytes()
    (tmp_path / "state/worker/clock.json").unlink()
    result = initialize_installation(config, tmp_path)
    assert result.installation_id == data["installation_id"]
    assert (tmp_path / "identity/service.key").read_bytes() == key
    assert (tmp_path / "state/backend/clock.json").read_bytes() == backend


def test_record_lock_threads_and_symlink(product, tmp_path):
    config, _ = product
    with record_lock(tmp_path / ".init.lock"):
        with pytest.raises(OnboardingError, match="already running"):
            initialize_installation(config, tmp_path)
    (tmp_path / "state").symlink_to(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError):
        initialize_installation(config, tmp_path)
    assert snapshot(tmp_path) == before


def test_validate_installation_reads_only_without_runtime_lock_or_sync(
    product, tmp_path, monkeypatch
):
    config, _ = product
    result = initialize_installation(config, tmp_path)
    (tmp_path / ".init.lock").unlink()
    before = snapshot(tmp_path)
    entries = sorted(tmp_path.rglob("*"))
    original_open = os.open

    def read_only_open(path, flags, *args, **kwargs):
        assert not flags & (os.O_CREAT | os.O_WRONLY | os.O_RDWR | os.O_TRUNC)
        return original_open(path, flags, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("readonly validation must not mutate or create Runtime")

    monkeypatch.setattr(os, "open", read_only_open)
    monkeypatch.setattr(os, "fsync", forbidden)
    monkeypatch.setattr(LicenseRuntime, "__init__", forbidden)
    assert validate_installation(config, tmp_path) == result.installation_id
    assert snapshot(tmp_path) == before
    assert sorted(tmp_path.rglob("*")) == entries


def test_validate_future_clock_and_invalid_commercial_are_independent(product, tmp_path):
    config, _ = product
    result = initialize_installation(config, tmp_path)
    key = (tmp_path / "identity/service.key").read_bytes()
    future = encode_clock(
        ClockState(2, 33, datetime(2090, 1, 1, tzinfo=UTC), None),
        product_id=config.profile.product_id,
        installation_id=result.installation_id,
        service="backend",
        service_key=key,
    )
    clock = tmp_path / "state/backend/clock.json"
    clock.write_bytes(future)
    for commercial in (None, b"broken or expired license"):
        if commercial is not None:
            (tmp_path / "shared/active.license.json").write_bytes(commercial)
        before = snapshot(tmp_path)
        assert validate_installation(config, tmp_path) == result.installation_id
        assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_clock",
        "missing_directory",
        "corrupt_clock",
        "short_key",
        "invalid_uuid",
        "wrong_product",
        "incomplete",
        "identity_extra",
        "stage",
        "legacy_stage",
        "clock_symlink",
        "directory_symlink",
    ],
)
def test_validate_rejects_invalid_installation_without_repair(product, tmp_path, mutation):
    config, _ = product
    initialize_installation(config, tmp_path)
    identity = tmp_path / "identity/installation.json"
    data = json.loads(identity.read_bytes())
    clock = tmp_path / "state/worker/clock.json"
    if mutation == "missing_clock":
        clock.unlink()
    elif mutation == "missing_directory":
        (tmp_path / "shared").rmdir()
    elif mutation == "corrupt_clock":
        clock.write_bytes(b"invalid HMAC")
    elif mutation == "short_key":
        (tmp_path / "identity/service.key").write_bytes(b"short")
    elif mutation in ("invalid_uuid", "wrong_product", "incomplete"):
        field, value = {
            "invalid_uuid": ("installation_id", "wrong"),
            "wrong_product": ("product_id", "wrong"),
            "incomplete": ("initialized", False),
        }[mutation]
        data[field] = value
        identity.write_text(json.dumps(data))
    elif mutation == "identity_extra":
        (tmp_path / "identity/unknown").write_bytes(b"extra")
    elif mutation in ("stage", "legacy_stage"):
        name = ".identity-stage" if mutation == "stage" else ".identity-stage-previous"
        (tmp_path / name).mkdir()
    elif mutation == "clock_symlink":
        clock.unlink()
        clock.symlink_to(tmp_path / "state/backend/clock.json")
    else:
        (tmp_path / "shared").rmdir()
        (tmp_path / "shared").symlink_to(tmp_path / "identity", target_is_directory=True)
    before = snapshot(tmp_path)
    entries = sorted(tmp_path.rglob("*"))
    with pytest.raises(OnboardingError) as exc:
        validate_installation(config, tmp_path)
    assert exc.value.code == "LICENSE_INSTALLATION_INVALID"
    assert snapshot(tmp_path) == before
    assert sorted(tmp_path.rglob("*")) == entries


def test_validate_missing_root_does_not_create_it(product, tmp_path):
    root = tmp_path / "missing"
    with pytest.raises(OnboardingError):
        validate_installation(product[0], root)
    assert not root.exists()


def test_validate_checks_all_configured_services(product, tmp_path):
    config, _ = product
    initialize_installation(config, tmp_path)
    expanded = replace(
        config,
        profile=replace(
            config.profile,
            recovery_prefixes=(*config.profile.recovery_prefixes, ("extra", "NP1E")),
        ),
    )
    with pytest.raises(OnboardingError):
        validate_installation(expanded, tmp_path)
