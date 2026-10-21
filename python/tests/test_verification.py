"""Commercial verification conformance and hostile input regression tests."""

import base64
import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from license_sdk.verification import (
    LicenseErrorReason,
    LicenseStatus,
    LicenseValidationError,
    parse_json_object,
    parse_utc_timestamp,
    validate_commercial_payload,
    verify_license,
)

FIXTURES = Path(__file__).parents[2] / "protocol/test-fixtures/commercial-license-v1.json"
NOW = datetime(2026, 10, 20, tzinfo=UTC)
BINDING = "6e557d24d92ba076121accaa712ec769bbc085df"


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


@pytest.fixture
def signing():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private, {"test": public}


def payload() -> dict:
    return {
        "schema_version": 1,
        "key_id": "test",
        "kind": "commercial-license",
        "license_id": "00000000-0000-4000-8000-000000000001",
        "customer_id": "test",
        "customer_name": "合成客户😀",
        "product_id": "icp-diag",
        "binding_type": "HOST_INSTALL",
        "binding_scheme": "host-install-code-v1",
        "binding_id": BINDING,
        "validity_type": "TERM",
        "issued_at": "2026-10-20T00:00:00Z",
        "not_before": "2026-10-20T00:00:00Z",
        "expires_at": "2027-10-20T00:00:00Z",
    }


def envelope(signing, data: dict | bytes) -> bytes:
    raw = data if isinstance(data, bytes) else json.dumps(data).encode()
    return json.dumps({"payload": b64(raw), "signature": b64(signing[0].sign(raw))}).encode()


def verify(signing, data: bytes, **kwargs):
    options = dict(
        product_id="icp-diag", trusted_keys=signing[1], binding_id=BINDING, effective_now=NOW
    )
    options.update(kwargs)
    return verify_license(data, **options)


@pytest.mark.parametrize("validity", ["TERM", "PERPETUAL"])
@pytest.mark.parametrize("binding", ["HOST_INSTALL", "UNBOUND"])
def test_four_modes(signing, validity, binding):
    data = payload()
    data["validity_type"] = validity
    if validity == "PERPETUAL":
        data["expires_at"] = None
    data["binding_type"] = binding
    if binding == "UNBOUND":
        data["binding_scheme"] = data["binding_id"] = None
    raw = envelope(signing, data)
    result = verify(signing, raw, binding_id=BINDING if binding == "HOST_INSTALL" else None)
    assert result.status == LicenseStatus.ACTIVE
    assert result.file_digest == hashlib.sha256(raw).hexdigest()
    with pytest.raises(FrozenInstanceError):
        result.status = LicenseStatus.EXPIRED


def test_exact_time_boundaries(signing):
    raw = envelope(signing, payload())
    assert verify(signing, raw, effective_now=NOW - timedelta(microseconds=1)).status == (
        LicenseStatus.NOT_YET_VALID
    )
    end = datetime(2027, 10, 20, tzinfo=UTC)
    assert verify(signing, raw, effective_now=end - timedelta(microseconds=1)).status == (
        LicenseStatus.ACTIVE
    )
    assert verify(signing, raw, effective_now=end).status == LicenseStatus.EXPIRED
    with pytest.raises(LicenseValidationError):
        verify(signing, raw, effective_now=NOW.replace(tzinfo=None))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("schema_version", 1.0),
        ("schema_version", 2),
        ("kind", "recovery"),
        ("key_id", "bad key"),
        ("key_id", 2),
        ("license_id", "00000000000040008000000000000001"),
        ("license_id", "00000000-0000-4000-8000-00000000000A"),
        ("customer_name", ""),
        ("customer_name", "x" * 201),
        ("customer_name", "bad\x80"),
        ("customer_name", "\ud800"),
        ("product_id", "other"),
        ("binding_type", None),
        ("binding_scheme", None),
        ("binding_id", BINDING.upper()),
        ("validity_type", "FOREVER"),
        ("expires_at", None),
        ("issued_at", "2028-10-20T00:00:00Z"),
        ("not_before", "2027-10-20T00:00:00Z"),
        ("not_before", "2026-10-20T00:00:00.0Z"),
        ("not_before", "2026-10-20T00:00:00+00:00"),
        ("not_before", "2026-02-30T00:00:00Z"),
        ("not_before", "2026-10-20T00:00:60Z"),
    ],
)
def test_invalid_payloads(signing, field, value):
    data = payload()
    data[field] = value
    with pytest.raises(LicenseValidationError):
        verify(signing, envelope(signing, data))


def test_required_unknown_duplicate_and_unbound_fields(signing):
    for field in payload():
        data = payload()
        del data[field]
        with pytest.raises(LicenseValidationError):
            verify(signing, envelope(signing, data))
    data = payload()
    data["unknown"] = 1
    with pytest.raises(LicenseValidationError):
        verify(signing, envelope(signing, data))
    raw = json.dumps(payload()).encode()
    raw = raw[:-1] + b',"schema_versi\\u006fn":1}'
    with pytest.raises(LicenseValidationError):
        verify(signing, envelope(signing, raw))
    data = payload()
    data["binding_type"] = "UNBOUND"
    with pytest.raises(LicenseValidationError):
        verify(signing, envelope(signing, data))
    data = payload()
    data["validity_type"] = "PERPETUAL"
    with pytest.raises(LicenseValidationError):
        verify(signing, envelope(signing, data))


def test_raw_signature_and_product_isolation(signing):
    raw = envelope(signing, payload())
    for option in (
        {"product_id": "second-product"},
        {"binding_id": None},
        {"binding_id": "0" * 40},
        {"trusted_keys": {}},
    ):
        with pytest.raises(LicenseValidationError):
            verify(signing, raw, **option)
    decoded = json.loads(raw)
    original = base64.urlsafe_b64decode(decoded["payload"] + "==")
    decoded["payload"] = b64(original + b" ")
    with pytest.raises(LicenseValidationError) as error:
        verify(signing, json.dumps(decoded).encode())
    assert error.value.reason == LicenseErrorReason.SIGNATURE_INVALID
    decoded = json.loads(raw)
    decoded["signature"] = b64(b"x" * 64)
    with pytest.raises(LicenseValidationError):
        verify(signing, json.dumps(decoded).encode())


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\xef\xbb\xbf{}",
        b"\xff",
        b"[]",
        b'{"payload":"a","payload":"b"}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":1e0}',
        b'{"x":1.0}',
        b'{"\\ud800":1}',
        b'{"x":"\\u009f"}',
        b'{"x":' + b"[" * 20 + b"0" + b"]" * 20 + b"}",
        b" " * 65537,
    ],
)
def test_strict_bounded_json(raw):
    with pytest.raises(LicenseValidationError):
        parse_json_object(raw)


def test_envelope_encoding(signing):
    valid = json.loads(envelope(signing, payload()))
    for field in ("payload", "signature"):
        for value in ("", True, valid[field] + "=", "*", "A", "_____", "x" * 50000):
            data = dict(valid)
            data[field] = value
            with pytest.raises(LicenseValidationError):
                verify(signing, json.dumps(data).encode())
    for change in ({"unknown": "x"}, {"payload": valid["payload"]}):
        data = dict(valid) if "unknown" in change else {}
        data.update(change)
        with pytest.raises(LicenseValidationError):
            verify(signing, json.dumps(data).encode())


def test_public_synthetic_vectors():
    path = next((Path(__file__).parents[2] / "protocol/test-fixtures").glob("*.json"))
    fixtures = json.loads(path.read_text())
    trust = {fixtures["key_id"]: base64.b64decode(fixtures["public_key_spki_base64"])}
    for case in fixtures["cases"]:
        result = verify_license(
            json.dumps(case["envelope"]).encode(),
            product_id="icp-diag",
            trusted_keys=trust,
            binding_id=BINDING,
            effective_now=NOW,
        )
        assert result.status == LicenseStatus.ACTIVE


def test_validator_and_timestamp_are_terminal_free():
    result = validate_commercial_payload(json.dumps(payload()).encode(), "icp-diag")
    assert result.customer_name == "合成客户😀"
    assert parse_utc_timestamp("0001-01-01T00:00:00Z").year == 1


def test_raw_payload_preserved_and_padding_bits_rejected(signing):
    raw = (" \n" + json.dumps(payload(), ensure_ascii=False, indent=2) + " \n").encode()
    file_bytes = envelope(signing, raw)
    result = verify(signing, file_bytes)
    assert result.payload_digest == hashlib.sha256(raw).hexdigest()
    value = json.loads(file_bytes)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    original = value["signature"]
    # A 64-byte Ed25519 signature has four unused bits in its last base64 digit.
    index = alphabet.index(original[-1])
    value["signature"] = original[:-1] + alphabet[index + 1]
    with pytest.raises(LicenseValidationError) as error:
        verify(signing, json.dumps(value).encode())
    assert error.value.reason == LicenseErrorReason.FORMAT_INVALID


def test_payload_limit_and_invalid_trust(signing):
    with pytest.raises(LicenseValidationError):
        validate_commercial_payload(b" " * 32769, "icp-diag")
    for invalid in (
        b"not-a-key",
        b"",
        b"-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----",
    ):
        with pytest.raises(LicenseValidationError) as error:
            verify(signing, envelope(signing, payload()), trusted_keys={"test": invalid})
        assert error.value.reason == LicenseErrorReason.TRUST_INVALID
    with pytest.raises(ValueError):
        parse_json_object(b"{}", maximum=65537)
