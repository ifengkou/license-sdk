"""Immutable public product configuration, loaded only from trusted product resources."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from license_sdk.profile import ProductProfile
from license_sdk.verification import _pairs, _reject_constant


@dataclass(frozen=True)
class ProductConfig:
    profile: ProductProfile
    trusted_keys: Mapping[str, bytes]

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ProductProfile) or not self.profile.recovery_prefixes:
            raise ValueError("at least one service required")
        profile = self.profile
        object.__setattr__(
            self,
            "profile",
            ProductProfile(
                profile.product_id,
                bytes(profile.fingerprint_domain),
                bytes(profile.binding_domain),
                bytes(profile.recovery_domain),
                profile.request_prefix,
                tuple((service, prefix) for service, prefix in profile.recovery_prefixes),
            ),
        )
        keys: dict[str, bytes] = {}
        if not isinstance(self.trusted_keys, Mapping) or not self.trusted_keys:
            raise ValueError("trusted_keys must be nonempty")
        for key_id, pem in self.trusted_keys.items():
            if not isinstance(key_id, str) or re.fullmatch(r"[A-Za-z0-9._-]{1,64}", key_id) is None:
                raise ValueError("invalid key_id")
            if not isinstance(pem, bytes) or len(pem) > 4096:
                raise ValueError("public PEM required")
            try:
                key = serialization.load_pem_public_key(pem)
            except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
                raise ValueError("Ed25519 public key required") from exc
            if not isinstance(key, Ed25519PublicKey):
                raise ValueError("Ed25519 public key required")
            canonical = key.public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            if pem != canonical:
                raise ValueError("canonical Ed25519 SPKI PEM required")
            keys[key_id] = pem
        object.__setattr__(self, "trusted_keys", MappingProxyType(keys))

    @classmethod
    def from_bytes(cls, raw: bytes) -> "ProductConfig":
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= 65536:
            raise ValueError("config must contain 1-65536 bytes")
        try:
            data = json.loads(
                raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant
            )
            if (
                not isinstance(data, dict)
                or data.keys()
                != {
                    "schema_version",
                    "product_id",
                    "fingerprint_domain",
                    "binding_domain",
                    "recovery_domain",
                    "request_prefix",
                    "services",
                    "trusted_keys",
                }
                or type(data["schema_version"]) is not int
                or data["schema_version"] != 1
            ):
                raise ValueError("invalid product config fields or version")
            strings = (
                "product_id",
                "fingerprint_domain",
                "binding_domain",
                "recovery_domain",
                "request_prefix",
            )
            if any(not isinstance(data[name], str) for name in strings):
                raise ValueError("product config strings required")
            services = data["services"]
            if not isinstance(services, list) or not services:
                raise ValueError("services must be nonempty")
            pairs = []
            for service in services:
                if not isinstance(service, dict) or service.keys() != {"id", "recovery_prefix"}:
                    raise ValueError("invalid service fields")
                if any(not isinstance(service[name], str) for name in service):
                    raise ValueError("service strings required")
                pairs.append((service["id"], service["recovery_prefix"]))
            trust = data["trusted_keys"]
            if not isinstance(trust, dict) or any(not isinstance(v, str) for v in trust.values()):
                raise ValueError("trusted_keys must contain PEM strings")
            profile = ProductProfile(
                data["product_id"],
                data["fingerprint_domain"].encode("ascii"),
                data["binding_domain"].encode("ascii"),
                data["recovery_domain"].encode("ascii"),
                data["request_prefix"],
                tuple(pairs),
            )
            return cls(profile, {key: value.encode("ascii") for key, value in trust.items()})
        except (ValueError, TypeError, RecursionError, KeyError) as exc:
            raise ValueError("invalid public product configuration") from exc

    def to_bytes(self) -> bytes:
        profile = self.profile
        raw = json.dumps(
            {
                "schema_version": 1,
                "product_id": profile.product_id,
                "fingerprint_domain": profile.fingerprint_domain.decode("ascii"),
                "binding_domain": profile.binding_domain.decode("ascii"),
                "recovery_domain": profile.recovery_domain.decode("ascii"),
                "request_prefix": profile.request_prefix,
                "services": [
                    {"id": service, "recovery_prefix": prefix}
                    for service, prefix in profile.recovery_prefixes
                ],
                "trusted_keys": {
                    key: value.decode("ascii") for key, value in self.trusted_keys.items()
                },
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if len(raw) > 65536:
            raise ValueError("public product configuration too large")
        return raw

    def select_service(self, service: str | None) -> str:
        services = dict(self.profile.recovery_prefixes)
        if service is None and len(services) == 1:
            return next(iter(services))
        if service not in services:
            raise ValueError("select a configured service explicitly for multi-service products")
        return service
