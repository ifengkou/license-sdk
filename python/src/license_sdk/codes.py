"""Version 1 manual code codec. CRC detects transcription errors, not authenticity."""

import binascii
import re
from enum import StrEnum

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ASCII_IGNORED = frozenset("- \t\n\r\v\f")
_ALIASES = str.maketrans({"O": "0", "I": "1", "L": "1"})


class CodeErrorReason(StrEnum):
    PREFIX = "PREFIX"
    CHARACTER = "CHARACTER"
    LENGTH = "LENGTH"
    PADDING = "PADDING"
    CHECKSUM = "CHECKSUM"


class CodeError(ValueError):
    def __init__(self, reason: CodeErrorReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def _validate_prefix(prefix: str) -> None:
    if re.fullmatch(r"[A-Z][A-Z0-9]{1,7}", prefix) is None:
        raise ValueError("prefix must be uppercase ASCII alphanumeric")


def encode_code(prefix: str, identifier: bytes) -> str:
    _validate_prefix(prefix)
    if len(identifier) != 20:
        raise ValueError("identifier must be exactly 20 bytes")
    crc = binascii.crc_hqx(prefix.encode("ascii") + identifier, 0)
    packed = int.from_bytes(identifier + crc.to_bytes(2, "big"), "big") << 4
    body = "".join(ALPHABET[(packed >> shift) & 31] for shift in range(175, -1, -5))
    return prefix + "-" + "-".join(body[i : i + 6] for i in range(0, 36, 6))


def decode_code(prefix: str, value: str) -> bytes:
    _validate_prefix(prefix)
    if len(value) > 256:
        raise CodeError(CodeErrorReason.LENGTH)
    # Reject before case conversion: Unicode case mappings must never become ASCII.
    if not value.isascii():
        raise CodeError(CodeErrorReason.CHARACTER)
    compact = "".join(char for char in value if char not in _ASCII_IGNORED).upper()
    if not compact.startswith(prefix):
        raise CodeError(CodeErrorReason.PREFIX)
    body = compact[len(prefix) :]
    if len(body) != 36:
        raise CodeError(CodeErrorReason.LENGTH)
    body = body.translate(_ALIASES)
    number = 0
    for char in body:
        if char not in ALPHABET:
            raise CodeError(CodeErrorReason.CHARACTER)
        number = (number << 5) | ALPHABET.index(char)
    if number & 15:
        raise CodeError(CodeErrorReason.PADDING)
    raw = (number >> 4).to_bytes(22, "big")
    identifier, checksum = raw[:20], int.from_bytes(raw[20:], "big")
    if binascii.crc_hqx(prefix.encode("ascii") + identifier, 0) != checksum:
        raise CodeError(CodeErrorReason.CHECKSUM)
    return identifier
