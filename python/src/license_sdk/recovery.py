"""Strict single-service recovery wire formats; no file or terminal access."""

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from license_sdk.codes import decode_code, encode_code
from license_sdk.identity import installation_uuid, recovery_target
from license_sdk.profile import ProductProfile
from license_sdk.verification import _base64, _identifier, parse_json_object, parse_utc_timestamp

if TYPE_CHECKING:
    from license_sdk.runtime import RuntimeStatus

MAX_COUNTER = (1 << 63) - 1
_CHALLENGE_FIELDS = {
    "schema_version",
    "kind",
    "product_id",
    "installation_id",
    "service",
    "nonce",
    "reason",
    "state_identity",
    "base_epoch",
    "generated_at",
}
_RECOVERY_FIELDS = {
    "schema_version",
    "kind",
    "key_id",
    "product_id",
    "installation_id",
    "service",
    "recovery_id",
    "target_id",
    "issued_at",
    "window_start",
    "window_end",
}


class RecoveryError(ValueError):
    def __init__(self, code: str, http_status: int = 400) -> None:
        self.code, self.http_status = code, http_status
        super().__init__(code)


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware UTC required")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _hex(value: object, size: int) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{" + str(size) + "}", value) is None:
        raise ValueError("invalid lowercase digest")
    return value


@dataclass(frozen=True)
class RecoveryRequest:
    product_id: str
    installation_id: str
    service: str
    nonce: str
    reason: str
    state_identity: str
    base_epoch: int | None
    generated_at: datetime
    target_id: str
    recovery_code: str

    def challenge_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "kind": "recovery-challenge",
            "product_id": self.product_id,
            "installation_id": self.installation_id,
            "service": self.service,
            "nonce": self.nonce,
            "reason": self.reason,
            "state_identity": self.state_identity,
            "base_epoch": self.base_epoch,
            "generated_at": utc_text(self.generated_at),
        }

    def to_dict(self) -> dict[str, object]:
        return self.challenge_payload() | {
            "kind": "clock-recovery-request",
            "target_id": self.target_id,
            "recovery_code": self.recovery_code,
        }

    def to_bytes(self) -> bytes:
        return canonical_json(self.to_dict())


def _request(data: Mapping[str, object], profile: ProductProfile) -> RecoveryRequest:
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("invalid recovery schema")
    if data["product_id"] != profile.product_id:
        raise ValueError("incorrect recovery product")
    service = data["service"]
    if not isinstance(service, str) or service not in dict(profile.recovery_prefixes):
        raise ValueError("incorrect recovery service")
    install, nonce = data["installation_id"], data["nonce"]
    if not isinstance(install, str) or not isinstance(nonce, str):
        raise ValueError("invalid recovery identity")
    installation_uuid(install)
    installation_uuid(nonce)
    reason = data["reason"]
    if reason not in ("CLOCK_ROLLBACK", "CLOCK_STATE_INVALID"):
        raise ValueError("invalid recovery reason")
    assert isinstance(reason, str)
    state_identity = _hex(data["state_identity"], 64)
    epoch = data["base_epoch"]
    if epoch is not None and (type(epoch) is not int or not 0 <= epoch <= MAX_COUNTER):
        raise ValueError("invalid recovery base epoch")
    assert epoch is None or isinstance(epoch, int)
    target = recovery_target(profile, install, service, nonce, bytes.fromhex(state_identity))
    return RecoveryRequest(
        profile.product_id,
        install,
        service,
        nonce,
        reason,
        state_identity,
        epoch,
        parse_utc_timestamp(data["generated_at"]),
        target.hex(),
        encode_code(dict(profile.recovery_prefixes)[service], target),
    )


def parse_recovery_request(raw: bytes, profile: ProductProfile) -> RecoveryRequest:
    data = parse_json_object(raw, maximum=8192)
    if data.keys() != _CHALLENGE_FIELDS | {"target_id", "recovery_code"}:
        raise ValueError("invalid recovery request fields")
    if data["kind"] != "clock-recovery-request":
        raise ValueError("invalid recovery request purpose")
    result = _request(data, profile)
    if data["target_id"] != result.target_id or not isinstance(data["recovery_code"], str):
        raise ValueError("recovery request target differs")
    target = decode_code(dict(profile.recovery_prefixes)[result.service], data["recovery_code"])
    if target.hex() != result.target_id:
        raise ValueError("recovery request code differs")
    return result


def encode_challenge(request: RecoveryRequest, service_key: bytes) -> bytes:
    raw = canonical_json(request.challenge_payload())
    key = hmac.digest(
        service_key,
        f"{request.product_id}/recovery-challenge/{request.service}/v1".encode(),
        "sha256",
    )
    return canonical_json(
        {
            "payload": base64.urlsafe_b64encode(raw).decode().rstrip("="),
            "mac": base64.urlsafe_b64encode(hmac.digest(key, raw, "sha256")).decode().rstrip("="),
        }
    )


def decode_challenge(
    raw: bytes, profile: ProductProfile, service: str, service_key: bytes
) -> RecoveryRequest:
    outer = parse_json_object(raw, maximum=8192)
    if outer.keys() != {"payload", "mac"}:
        raise ValueError("invalid recovery challenge envelope")
    payload, mac = _base64(outer["payload"], 4096), _base64(outer["mac"], 32)
    key = hmac.digest(
        service_key, f"{profile.product_id}/recovery-challenge/{service}/v1".encode(), "sha256"
    )
    if len(mac) != 32 or not hmac.compare_digest(mac, hmac.digest(key, payload, "sha256")):
        raise ValueError("recovery challenge MAC rejected")
    data = parse_json_object(payload, maximum=4096)
    if data.keys() != _CHALLENGE_FIELDS or data["kind"] != "recovery-challenge":
        raise ValueError("invalid recovery challenge fields")
    result = _request(data, profile)
    if result.service != service:
        raise ValueError("recovery challenge service differs")
    return result


@dataclass(frozen=True)
class SignedRecovery:
    key_id: str
    product_id: str
    installation_id: str
    service: str
    recovery_id: str
    target_id: str
    issued_at: datetime
    window_start: datetime
    window_end: datetime


def validate_recovery_payload(raw: bytes, profile: ProductProfile) -> SignedRecovery:
    data = parse_json_object(raw)
    if data.keys() != _RECOVERY_FIELDS:
        raise ValueError("invalid recovery payload fields")
    if (
        type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or data["kind"] != "clock-recovery"
    ):
        raise ValueError("invalid recovery payload purpose")
    if data["product_id"] != profile.product_id:
        raise ValueError("invalid recovery product")
    service = data["service"]
    if not isinstance(service, str) or service not in dict(profile.recovery_prefixes):
        raise ValueError("invalid recovery service")
    install, recovery_id = data["installation_id"], data["recovery_id"]
    if not isinstance(install, str) or not isinstance(recovery_id, str):
        raise ValueError("invalid recovery identity")
    installation_uuid(install)
    installation_uuid(recovery_id)
    issued, start, end = (
        parse_utc_timestamp(data[key]) for key in ("issued_at", "window_start", "window_end")
    )
    try:
        if not issued <= start < end <= issued + timedelta(days=7):
            raise ValueError("invalid recovery window")
    except OverflowError as exc:
        raise ValueError("invalid recovery window") from exc
    return SignedRecovery(
        _identifier(data["key_id"]),
        profile.product_id,
        install,
        service,
        recovery_id,
        _hex(data["target_id"], 40),
        issued,
        start,
        end,
    )


def verify_recovery(
    raw: bytes, *, profile: ProductProfile, trusted_keys: Mapping[str, bytes]
) -> SignedRecovery:
    try:
        outer = parse_json_object(raw, maximum=65536)
        if outer.keys() != {"payload", "signature"}:
            raise ValueError("invalid recovery envelope")
        payload, signature = _base64(outer["payload"], 32768), _base64(outer["signature"], 64)
        parsed = validate_recovery_payload(payload, profile)
        key_data = trusted_keys[parsed.key_id]
        key = (
            serialization.load_pem_public_key(key_data)
            if key_data.startswith(b"-----BEGIN")
            else serialization.load_der_public_key(key_data)
        )
        if not isinstance(key, Ed25519PublicKey) or len(signature) != 64:
            raise ValueError("invalid recovery signature key")
        if (
            not key_data.startswith(b"-----BEGIN")
            and key.public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            != key_data
        ):
            raise ValueError("noncanonical trusted SPKI")
        key.verify(signature, payload)
        return parsed
    except (
        KeyError,
        ValueError,
        TypeError,
        InvalidSignature,
        UnsupportedAlgorithm,
        OverflowError,
    ) as exc:
        raise RecoveryError("LICENSE_RECOVERY_INVALID") from exc


@dataclass(frozen=True)
class RecoveryResult:
    action_status: str
    status: "RuntimeStatus"
    recovery_id: str


def state_identity(raw: bytes | None) -> str:
    return hashlib.sha256(b"MISSING" if raw is None else raw).hexdigest()
