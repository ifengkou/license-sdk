"""Synthetic products and SDK-initialized installations for onboarding tests."""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from license_sdk import ProductConfig, ProductProfile
from license_sdk.installation import initialize_installation
from license_sdk.runtime import LicenseRuntime


@pytest.fixture
def product():
    signer = Ed25519PrivateKey.generate()
    pem = signer.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    profile = ProductProfile(
        "new-product",
        b"new/fp/v1",
        b"new/bind/v1",
        b"new/rec/v1",
        "NP1",
        (("backend", "NP1B"), ("worker", "NP1W")),
    )
    return ProductConfig(profile, {"new-v1": pem}), signer


@pytest.fixture
def prepared(product, tmp_path):
    config, _ = product
    initialize_installation(config, tmp_path)
    return LicenseRuntime.from_config(config.to_bytes(), root=tmp_path, service="backend")
