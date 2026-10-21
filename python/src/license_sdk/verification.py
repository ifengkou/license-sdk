"""Strict, side-effect-free commercial license verification.

Only trusted product assembly supplies public keys and local binding identity. This
module neither loads private keys nor changes clock or installation state.
"""

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, NoReturn, cast
from uuid import UUID

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MAX_FILE_BYTES = 64 * 1024
MAX_PAYLOAD_BYTES = 32 * 1024
MAX_JSON_DEPTH = 8
_IDENTIFIER = re.compile(r"[A-Za-z0-9._-]{1,64}", re.ASCII)
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", re.ASCII)
_FIELDS = frozenset(
    {
        "schema_version",
        "key_id",
        "kind",
        "license_id",
        "customer_id",
        "customer_name",
        "product_id",
        "binding_type",
        "binding_scheme",
        "binding_id",
        "validity_type",
        "issued_at",
        "not_before",
        "expires_at",
    }
)


class LicenseErrorReason(StrEnum):
    FORMAT_INVALID = "FORMAT_INVALID"
    PAYLOAD_INVALID = "PAYLOAD_INVALID"
    PRODUCT_MISMATCH = "PRODUCT_MISMATCH"
    KEY_UNTRUSTED = "KEY_UNTRUSTED"
    TRUST_INVALID = "TRUST_INVALID"
    SIGNATURE_INVALID = "SIGNATURE_INVALID"
    BINDING_MISMATCH = "BINDING_MISMATCH"
    CONTEXT_INVALID = "CONTEXT_INVALID"


class LicenseValidationError(ValueError):
    def __init__(self, reason: LicenseErrorReason, message: str) -> None:
        self.reason = reason
        super().__init__(message)


class LicenseStatus(StrEnum):
    ACTIVE = "ACTIVE"
    NOT_YET_VALID = "NOT_YET_VALID"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class CommercialLicense:
    schema_version: int
    key_id: str
    kind: str
    license_id: str
    customer_id: str
    customer_name: str
    product_id: str
    binding_type: str
    binding_scheme: str | None
    binding_id: str | None
    validity_type: str
    issued_at: datetime
    not_before: datetime
    expires_at: datetime | None


@dataclass(frozen=True)
class VerifiedLicense:
    payload: CommercialLicense
    payload_digest: str
    file_digest: str
    status: LicenseStatus


def _fail(message: str) -> NoReturn:
    raise LicenseValidationError(LicenseErrorReason.PAYLOAD_INVALID, message)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    raise ValueError("nonstandard JSON number")


def _check_json(value: Any, depth: int = 1) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ValueError("JSON nesting too deep")
    if isinstance(value, str):
        if any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("JSON string contains controls or surrogates")
    elif isinstance(value, dict):
        for key, item in value.items():
            _check_json(key, depth)
            _check_json(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_json(item, depth + 1)
    elif isinstance(value, float):
        # The v1 schema has integer fields only. Reject even integral 1.0 / 1e0.
        raise ValueError("floating point JSON numbers are forbidden")


def _object(data: bytes, limit: int, reason: LicenseErrorReason) -> dict[str, Any]:
    if not isinstance(data, bytes) or not data or len(data) > limit:
        raise LicenseValidationError(reason, "JSON byte size is invalid")
    try:
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant
        )
        _check_json(value)
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value
    except (ValueError, RecursionError) as exc:
        raise LicenseValidationError(reason, "invalid strict UTF-8 JSON object") from exc


def parse_json_object(data: bytes, *, maximum: int = MAX_PAYLOAD_BYTES) -> dict[str, object]:
    """Parse a bounded strict object, shared with vendor-side request validation."""
    if type(maximum) is not int or not 1 <= maximum <= MAX_FILE_BYTES:
        raise ValueError("maximum must be between 1 and 65536 bytes")
    return cast(dict[str, object], _object(data, maximum, LicenseErrorReason.PAYLOAD_INVALID))


def parse_utc_timestamp(value: object) -> datetime:
    """Parse the protocol's exact UTC-second timestamp, rejecting offsets/leap seconds."""
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        _fail("timestamp must use YYYY-MM-DDTHH:mm:ssZ")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise LicenseValidationError(
            LicenseErrorReason.PAYLOAD_INVALID, "timestamp date is invalid"
        ) from exc


def _identifier(value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _fail("identifier must be 1-64 ASCII identifier characters")
    return value


def validate_commercial_payload(payload_bytes: bytes, product_id: str) -> CommercialLicense:
    """Validate unsigned schema bytes for issuer construction; this does NOT verify trust."""
    _identifier(product_id)
    data = _object(payload_bytes, MAX_PAYLOAD_BYTES, LicenseErrorReason.PAYLOAD_INVALID)
    if data.keys() != _FIELDS:
        _fail("commercial payload fields differ from schema v1")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        _fail("schema_version must be JSON integer 1")
    if data["kind"] != "commercial-license":
        _fail("incorrect payload kind")
    key_id = _identifier(data["key_id"])
    customer_id = _identifier(data["customer_id"])
    actual_product = _identifier(data["product_id"])
    if actual_product != product_id:
        raise LicenseValidationError(LicenseErrorReason.PRODUCT_MISMATCH, "product does not match")
    license_id = data["license_id"]
    try:
        if not isinstance(license_id, str) or str(UUID(license_id)) != license_id:
            _fail("license_id must be canonical lowercase UUID")
    except ValueError as exc:
        raise LicenseValidationError(
            LicenseErrorReason.PAYLOAD_INVALID, "license_id must be canonical lowercase UUID"
        ) from exc
    name = data["customer_name"]
    if not isinstance(name, str) or not 1 <= len(name) <= 200:
        _fail("customer_name must contain 1-200 Unicode code points")
    binding_type, scheme, binding = data["binding_type"], data["binding_scheme"], data["binding_id"]
    if binding_type == "HOST_INSTALL":
        if (
            scheme != "host-install-code-v1"
            or not isinstance(binding, str)
            or re.fullmatch(r"[0-9a-f]{40}", binding) is None
        ):
            _fail("HOST_INSTALL requires scheme and lowercase binding digest")
    elif binding_type == "UNBOUND":
        if scheme is not None or binding is not None:
            _fail("UNBOUND requires explicit null binding fields")
    else:
        _fail("unknown binding_type")
    issued = parse_utc_timestamp(data["issued_at"])
    start = parse_utc_timestamp(data["not_before"])
    validity = data["validity_type"]
    if validity == "TERM":
        end = parse_utc_timestamp(data["expires_at"])
        if issued > end or start >= end:
            _fail("invalid TERM chronology")
    elif validity == "PERPETUAL":
        if data["expires_at"] is not None:
            _fail("PERPETUAL requires explicit null expires_at")
        end = None
    else:
        _fail("unknown validity_type")
    return CommercialLicense(
        1,
        key_id,
        "commercial-license",
        license_id,
        customer_id,
        name,
        actual_product,
        binding_type,
        scheme,
        binding,
        validity,
        issued,
        start,
        end,
    )


def _base64(value: object, limit: int) -> bytes:
    if not isinstance(value, str) or not value or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise LicenseValidationError(LicenseErrorReason.FORMAT_INVALID, "invalid Base64URL")
    if len(value) > (limit * 4 + 2) // 3:
        raise LicenseValidationError(LicenseErrorReason.FORMAT_INVALID, "Base64URL exceeds limit")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error) as exc:
        raise LicenseValidationError(
            LicenseErrorReason.FORMAT_INVALID, "invalid Base64URL"
        ) from exc
    if len(decoded) > limit or base64.urlsafe_b64encode(decoded).decode().rstrip("=") != value:
        raise LicenseValidationError(LicenseErrorReason.FORMAT_INVALID, "noncanonical Base64URL")
    return decoded


def verify_license(
    file_bytes: bytes,
    *,
    product_id: str,
    trusted_keys: Mapping[str, bytes],
    binding_id: str | None,
    effective_now: datetime,
) -> VerifiedLicense:
    """Verify raw signed bytes using explicitly assembled trust and current local context."""
    if (
        not isinstance(effective_now, datetime)
        or effective_now.tzinfo is None
        or effective_now.utcoffset() is None
    ):
        raise LicenseValidationError(
            LicenseErrorReason.CONTEXT_INVALID, "aware current UTC required"
        )
    envelope = _object(file_bytes, MAX_FILE_BYTES, LicenseErrorReason.FORMAT_INVALID)
    if envelope.keys() != {"payload", "signature"}:
        raise LicenseValidationError(LicenseErrorReason.FORMAT_INVALID, "invalid envelope fields")
    raw = _base64(envelope["payload"], MAX_PAYLOAD_BYTES)
    signature = _base64(envelope["signature"], 64)
    if len(signature) != 64:
        raise LicenseValidationError(LicenseErrorReason.SIGNATURE_INVALID, "invalid signature size")
    payload = validate_commercial_payload(raw, product_id)
    key_data = trusted_keys.get(payload.key_id)
    if key_data is None:
        raise LicenseValidationError(LicenseErrorReason.KEY_UNTRUSTED, "untrusted signing key")
    try:
        key = (
            serialization.load_pem_public_key(key_data)
            if key_data.startswith(b"-----BEGIN")
            else serialization.load_der_public_key(key_data)
        )
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("Ed25519 key required")
        # DER must be a canonical SPKI, with no trailing objects or bytes.
        if (
            not key_data.startswith(b"-----BEGIN")
            and key.public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            != key_data
        ):
            raise ValueError("canonical SPKI required")
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        raise LicenseValidationError(
            LicenseErrorReason.TRUST_INVALID, "invalid trusted SPKI"
        ) from exc
    try:
        key.verify(signature, raw)
    except InvalidSignature as exc:
        raise LicenseValidationError(
            LicenseErrorReason.SIGNATURE_INVALID, "license signature rejected"
        ) from exc
    if payload.binding_type == "HOST_INSTALL" and (
        binding_id is None or payload.binding_id != binding_id
    ):
        raise LicenseValidationError(
            LicenseErrorReason.BINDING_MISMATCH, "local binding does not match"
        )
    now = effective_now.astimezone(UTC)
    status = LicenseStatus.ACTIVE
    if now < payload.not_before:
        status = LicenseStatus.NOT_YET_VALID
    elif payload.expires_at is not None and now >= payload.expires_at:
        status = LicenseStatus.EXPIRED
    return VerifiedLicense(
        payload, hashlib.sha256(raw).hexdigest(), hashlib.sha256(file_bytes).hexdigest(), status
    )
