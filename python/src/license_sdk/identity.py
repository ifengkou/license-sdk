"""Pure host/install and recovery digest functions, without filesystem access."""

import hashlib
import hmac
import re
from uuid import UUID

from license_sdk.profile import ProductProfile


def installation_uuid(value: str) -> UUID:
    parsed = UUID(value)
    if str(parsed) != value or parsed.version != 4:
        raise ValueError("installation UUID must be lowercase canonical UUID v4")
    return parsed


def host_fingerprint(profile: ProductProfile, machine_id: bytes) -> bytes:
    # systemd file convention: accept one terminal LF, never trim spaces or CR.
    value = machine_id.removesuffix(b"\n")
    if re.fullmatch(rb"[0-9a-f]{32}", value) is None or value == b"0" * 32:
        raise ValueError("invalid machine-id")
    return hmac.digest(profile.fingerprint_domain, bytes.fromhex(value.decode("ascii")), "sha256")


def binding_id(profile: ProductProfile, fingerprint: bytes, installation_id: str) -> bytes:
    if len(fingerprint) != 32:
        raise ValueError("fingerprint must be 32 bytes")
    return hashlib.sha256(
        profile.binding_domain + b"\x00" + fingerprint + installation_uuid(installation_id).bytes
    ).digest()[:20]


def recovery_target(
    profile: ProductProfile,
    installation_id: str,
    service: str,
    nonce: str,
    state_identity: bytes,
) -> bytes:
    if service not in dict(profile.recovery_prefixes):
        raise ValueError("unknown recovery service")
    if len(state_identity) != 32:
        raise ValueError("state identity must be 32 bytes")
    return hashlib.sha256(
        profile.recovery_domain
        + b"\x00"
        + installation_uuid(installation_id).bytes
        + service.encode("ascii")
        + b"\x00"
        + installation_uuid(nonce).bytes
        + state_identity
    ).digest()[:20]
