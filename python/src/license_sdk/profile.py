"""Explicit product protocol domains; callers select a trusted built-in profile."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ProductProfile:
    product_id: str
    fingerprint_domain: bytes
    binding_domain: bytes
    recovery_domain: bytes
    request_prefix: str
    recovery_prefixes: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", self.product_id) is None:
            raise ValueError("product_id must be 1-64 ASCII identifier characters")
        for domain in (self.fingerprint_domain, self.binding_domain, self.recovery_domain):
            if re.fullmatch(rb"[a-zA-Z0-9/._-]{1,128}", domain) is None:
                raise ValueError("protocol domains must be nonempty ASCII without NUL")
        prefixes = [self.request_prefix, *(prefix for _, prefix in self.recovery_prefixes)]
        if len(set(prefixes)) != len(prefixes):
            raise ValueError("code prefixes must be distinct")
        for prefix in prefixes:
            if re.fullmatch(r"[A-Z][A-Z0-9]{1,7}", prefix) is None:
                raise ValueError("prefix must be uppercase ASCII alphanumeric")
        services = [service for service, _ in self.recovery_prefixes]
        if len(set(services)) != len(services):
            raise ValueError("recovery services must be distinct")
        for service in services:
            if re.fullmatch(r"[a-z][a-z0-9-]{0,31}", service) is None:
                raise ValueError("service must be ASCII alphanumeric")


ICP_DIAG = ProductProfile(
    product_id="icp-diag",
    fingerprint_domain=b"icp-diag/host-fingerprint/v1",
    binding_domain=b"icp-diag/host-install-code/v1",
    recovery_domain=b"icp-diag/recovery-target/v1",
    request_prefix="IC1",
    recovery_prefixes=(("metor", "RC1J"), ("ai", "RC1A")),
)
