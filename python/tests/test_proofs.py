"""Strict request-bound CONTROL proofs use independent keys from user and TASK tokens."""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from license_sdk.proofs import ICP_CONTROL, ControlProof, ControlProofConfig, ControlProofError

NOW = datetime(2026, 10, 21, tzinfo=UTC)
MASTER = b"control-test".ljust(32, b"x")
INSTALLATION = str(uuid4())
REQUEST = dict(
    scope="LICENSE_VALIDATE", method="POST", path="/internal/license/validate", body=b"raw"
)


def proof():
    return ControlProof(config=ICP_CONTROL, service_key=MASTER, installation_id=INSTALLATION)


def claims():
    iat = int(NOW.timestamp())
    return dict(
        iss=ICP_CONTROL.issuer,
        aud=ICP_CONTROL.audience,
        installation_id=INSTALLATION,
        purpose="CONTROL",
        scope="LICENSE_VALIDATE",
        iat=iat,
        exp=iat + 60,
        jti=str(uuid4()),
        http_method="POST",
        http_path=REQUEST["path"],
        body_sha256=hashlib.sha256(REQUEST["body"]).hexdigest(),
    )


def token(data, domain=b"icp-diag/control/v1", headers=None):
    return jwt.encode(
        data, hmac.digest(MASTER, domain, "sha256"), algorithm="HS256", headers=headers
    )


def test_valid_boundary_subsecond_and_network_retry():
    signed = proof().issue(**REQUEST, now=NOW)
    for delta in (-5, 0, 64.999999):
        proof().verify(signed, **REQUEST, now=NOW + timedelta(seconds=delta))
        proof().verify(signed, **REQUEST, now=NOW + timedelta(seconds=delta))
    for delta in (-5.000001, 65):
        with pytest.raises(ControlProofError):
            proof().verify(signed, **REQUEST, now=NOW + timedelta(seconds=delta))


@pytest.mark.parametrize(
    "field,value",
    [
        ("iss", "user"),
        ("aud", [ICP_CONTROL.audience]),
        ("purpose", "TASK"),
        ("installation_id", str(uuid4())),
        ("jti", "bad"),
        ("jti", "00000000-0000-1000-8000-000000000000"),
        ("scope", "LICENSE_STATUS"),
        ("http_method", "GET"),
        ("http_path", "/other"),
        ("body_sha256", "0" * 64),
        ("iat", True),
        ("iat", 0.0),
        ("iat", -2),
        ("exp", None),
        ("exp", int(NOW.timestamp()) + 61),
        ("exp", int(NOW.timestamp())),
        ("exp", 2**63),
        ("unknown", "x"),
    ],
)
def test_wrong_claims_rejected(field, value):
    with pytest.raises(ControlProofError):
        proof().verify(token(claims() | {field: value}), **REQUEST, now=NOW)


def test_all_claims_required_and_no_header_key_sources():
    for field in claims():
        data = claims()
        del data[field]
        with pytest.raises(ControlProofError):
            proof().verify(token(data), **REQUEST, now=NOW)
    for header in ({"kid": "user"}, {"jku": "https://example.invalid"}, {"typ": "TASK"}):
        with pytest.raises(ControlProofError):
            proof().verify(token(claims(), headers=header), **REQUEST, now=NOW)


def test_cross_product_installation_and_purpose_keys():
    signed = token(claims())
    for instance in (
        ControlProof(config=ICP_CONTROL, service_key=b"z" * 32, installation_id=INSTALLATION),
        ControlProof(config=ICP_CONTROL, service_key=MASTER, installation_id=str(uuid4())),
        ControlProof(
            config=ControlProofConfig(b"other/control/v1", "other/metor", "other/ai"),
            service_key=MASTER,
            installation_id=INSTALLATION,
        ),
    ):
        with pytest.raises(ControlProofError):
            instance.verify(signed, **REQUEST, now=NOW)
    for domain in (b"icp-diag/task/v1", b"user-secret"):
        with pytest.raises(ControlProofError):
            proof().verify(token(claims(), domain), **REQUEST, now=NOW)


def test_raw_bytes_duplicates_and_canonical_encoding():
    signed = token(claims())
    with pytest.raises(ControlProofError):
        proof().verify(signed, **(REQUEST | {"body": b"raw "}), now=NOW)
    header, payload, signature = signed.split(".")
    for corrupted in (header + "=." + payload + "." + signature, signed + " ", "x" * 8193):
        with pytest.raises(ControlProofError):
            proof().verify(corrupted, **REQUEST, now=NOW)
    duplicate = (
        json.dumps(claims())
        .replace('"purpose": "CONTROL"', '"purpose":"CONTROL","purpose":"CONTROL"')
        .encode()
    )

    def encode(value):
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    unsigned = header + "." + encode(duplicate)
    malformed = (
        unsigned
        + "."
        + encode(
            hmac.digest(
                hmac.digest(MASTER, ICP_CONTROL.domain, "sha256"), unsigned.encode(), "sha256"
            )
        )
    )
    with pytest.raises(ControlProofError):
        proof().verify(malformed, **REQUEST, now=NOW)


@pytest.mark.parametrize("lifetime", [0, 61, True, 1.0])
def test_invalid_issue_window(lifetime):
    with pytest.raises(ValueError):
        proof().issue(**REQUEST, now=NOW, lifetime_seconds=lifetime)
