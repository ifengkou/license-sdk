import importlib.resources
import json
from pathlib import Path

import pytest

from license_sdk import ICP_DIAG, CodeError, decode_code, encode_code
from license_sdk.identity import binding_id, host_fingerprint, recovery_target

VECTORS = json.loads(
    importlib.resources.files("license_sdk")
    .joinpath("data/request-code-v1-vectors.json")
    .read_text()
)


def test_published_resource_matches_authority() -> None:
    authority = Path(__file__).parents[2] / "protocol/request-code-v1-vectors.json"
    packaged = importlib.resources.files("license_sdk").joinpath(
        "data/request-code-v1-vectors.json"
    )
    assert packaged.read_bytes() == authority.read_bytes()


@pytest.mark.parametrize("vector", VECTORS["binding_vectors"])
def test_bindings(vector: dict[str, str]) -> None:
    fingerprint = host_fingerprint(ICP_DIAG, vector["machine_id"].encode())
    assert fingerprint.hex() == vector["host_fingerprint"]
    identifier = binding_id(ICP_DIAG, fingerprint, vector["installation_id"])
    assert identifier.hex() == vector["binding_id"]
    assert encode_code("IC1", identifier) == vector["request_code"]
    assert decode_code("IC1", vector["request_code"]) == identifier


@pytest.mark.parametrize("vector", VECTORS["manual_positive_inputs"])
def test_positive(vector: dict[str, str]) -> None:
    assert decode_code("IC1", vector["input"]).hex() == vector["expected_binding_id"]


@pytest.mark.parametrize("vector", VECTORS["manual_negative_inputs"])
def test_negative(vector: dict[str, str]) -> None:
    with pytest.raises(CodeError) as caught:
        decode_code("IC1", vector["input"])
    assert caught.value.reason == vector["expected_error"]


@pytest.mark.parametrize("vector", VECTORS["recovery_vectors"])
def test_recovery(vector: dict[str, str]) -> None:
    target = recovery_target(
        ICP_DIAG,
        vector["installation_id"],
        vector["service"],
        vector["nonce"],
        bytes.fromhex(vector["state_identity"]),
    )
    prefix = dict(ICP_DIAG.recovery_prefixes)[vector["service"]]
    assert target.hex() == vector["target_id"]
    assert encode_code(prefix, target) == vector["recovery_code"]
    assert decode_code(prefix, vector["recovery_code"]) == target
    with pytest.raises(CodeError):
        decode_code("IC1", vector["recovery_code"])
    other = "RC1A" if prefix == "RC1J" else "RC1J"
    with pytest.raises(CodeError):
        decode_code(other, vector["recovery_code"])


@pytest.mark.parametrize("bad", ["\u00a0", "\u2003", "\u212a", "\u017f", "\u200b", "\x00"])
def test_unicode_and_control_rejected(bad: str) -> None:
    with pytest.raises(CodeError):
        decode_code("IC1", VECTORS["manual_positive_inputs"][0]["input"] + bad)


def test_prefix_ambiguity_not_normalized() -> None:
    value = VECTORS["manual_positive_inputs"][0]["input"]
    with pytest.raises(CodeError):
        decode_code("IC1", "1C1" + value[3:])


@pytest.mark.parametrize(
    "bad",
    [
        b"0" * 32,
        b"A" * 32,
        b" " + b"1" * 32,
        b"1" * 32 + b" ",
        b"1" * 32 + b"\r\n",
        b"1" * 32 + b"\n\n",
    ],
)
def test_machine_id_is_not_whitespace_trimmed(bad: bytes) -> None:
    with pytest.raises(ValueError):
        host_fingerprint(ICP_DIAG, bad)


def test_machine_id_terminal_newline() -> None:
    assert host_fingerprint(ICP_DIAG, b"1" * 32 + b"\n") == host_fingerprint(ICP_DIAG, b"1" * 32)
