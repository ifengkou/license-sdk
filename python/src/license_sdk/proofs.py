"""Purpose-separated, request-bound internal CONTROL proofs, never user authentication."""

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

import jwt

from license_sdk.identity import installation_uuid
from license_sdk.verification import _base64, parse_json_object

_FIELDS = {
    "iss",
    "aud",
    "installation_id",
    "purpose",
    "scope",
    "iat",
    "exp",
    "jti",
    "http_method",
    "http_path",
    "body_sha256",
}


class ControlProofError(ValueError):
    """The internal credential or its exact request binding was rejected."""


@dataclass(frozen=True)
class ControlProofConfig:
    domain: bytes
    issuer: str
    audience: str

    def __post_init__(self) -> None:
        if not self.domain or not self.issuer or not self.audience:
            raise ValueError("explicit product proof domains required")


ICP_CONTROL = ControlProofConfig(b"icp-diag/control/v1", "icp-diag/metor", "icp-diag/ai-internal")


class ControlProof:
    def __init__(
        self, *, config: ControlProofConfig, service_key: bytes, installation_id: str
    ) -> None:
        installation_uuid(installation_id)
        if len(service_key) != 32:
            raise ValueError("32-byte service key and canonical installation UUID required")
        self.config, self.installation_id = config, installation_id
        self._key = hmac.digest(service_key, config.domain, "sha256")

    def issue(
        self,
        *,
        scope: str,
        method: str,
        path: str,
        body: bytes,
        now: datetime,
        lifetime_seconds: int = 60,
    ) -> str:
        if type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 60:
            raise ValueError("proof lifetime must be 1-60 seconds")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("aware real UTC time required")
        _request(scope, method, path, body)
        iat = int(now.timestamp())
        if not 0 <= iat < iat + lifetime_seconds <= (1 << 63) - 6:
            raise ValueError("proof time outside range")
        return jwt.encode(
            {
                "iss": self.config.issuer,
                "aud": self.config.audience,
                "installation_id": self.installation_id,
                "purpose": "CONTROL",
                "scope": scope,
                "iat": iat,
                "exp": iat + lifetime_seconds,
                "jti": str(uuid4()),
                "http_method": method,
                "http_path": path,
                "body_sha256": hashlib.sha256(body).hexdigest(),
            },
            self._key,
            algorithm="HS256",
            headers={"typ": "JWT"},
        )

    def verify(
        self, token: str, *, scope: str, method: str, path: str, body: bytes, now: datetime
    ) -> None:
        try:
            _request(scope, method, path, body)
            if now.tzinfo is None or now.utcoffset() is None or len(token) > 8192:
                raise ValueError("invalid time or oversized proof")
            pieces = token.split(".")
            if len(pieces) != 3:
                raise ValueError("invalid compact proof")
            header = parse_json_object(_base64(pieces[0], 512), maximum=512)
            claims = parse_json_object(_base64(pieces[1], 4096), maximum=4096)
            if header != {"alg": "HS256", "typ": "JWT"} or claims.keys() != _FIELDS:
                raise ValueError("invalid proof fields")
            if len(_base64(pieces[2], 32)) != 32:
                raise ValueError("invalid proof signature")
            jwt.decode(
                token,
                self._key,
                algorithms=["HS256"],
                options={
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_aud": False,
                    "verify_iss": False,
                    "verify_nbf": False,
                },
            )
            if (
                claims["iss"] != self.config.issuer
                or claims["aud"] != self.config.audience
                or claims["installation_id"] != self.installation_id
                or claims["purpose"] != "CONTROL"
                or claims["scope"] != scope
                or claims["http_method"] != method
                or claims["http_path"] != path
                or claims["body_sha256"] != hashlib.sha256(body).hexdigest()
                or not isinstance(claims["jti"], str)
                or str(installation_uuid(claims["jti"])) != claims["jti"]
            ):
                raise ValueError("proof binding mismatch")
            iat, exp = claims["iat"], claims["exp"]
            if (
                type(iat) is not int
                or type(exp) is not int
                or not 0 <= iat < exp <= (1 << 63) - 6
                or not 0 < exp - iat <= 60
            ):
                raise ValueError("invalid proof window")
            timestamp = now.timestamp()
            if timestamp < iat - 5 or timestamp >= exp + 5:
                raise ValueError("proof outside transport window")
        except (ValueError, TypeError, OverflowError, jwt.PyJWTError) as exc:
            raise ControlProofError("internal control proof rejected") from exc


def _request(scope: str, method: str, path: str, body: bytes) -> None:
    if (
        re.fullmatch(r"[A-Z][A-Z_]{0,63}", scope) is None
        or re.fullmatch(r"[A-Z]{1,16}", method) is None
        or not path.startswith("/")
        or "?" in path
        or "#" in path
        or len(path) > 2048
        or re.fullmatch(r"[\x21-\x7e]+", path) is None
        or len(body) > 65536
    ):
        raise ValueError("invalid request binding")
