"""Public configuration parses strictly and never changes caller trust."""

import json
from pathlib import Path

import pytest

from license_sdk import ProductConfig, ProductProfile
from license_sdk.runtime import BuildMode, LicenseRuntime, RuntimeState


def test_roundtrip_and_immutable_trust(product):
    config, _ = product
    assert ProductConfig.from_bytes(config.to_bytes()) == config
    with pytest.raises(TypeError):
        config.trusted_keys["x"] = b"bad"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(schema_version=True),
        lambda d: d.update(schema_version=2),
        lambda d: d.update(unknown=True),
        lambda d: d.pop("product_id"),
        lambda d: d.update(services=[]),
        lambda d: d.update(trusted_keys={}),
        lambda d: d["services"].append(d["services"][0]),
        lambda d: d["services"][0].update(recovery_prefix="NP1"),
        lambda d: d.update(product_id="bad/identifier"),
        lambda d: d.update(trusted_keys={"bad": "-----BEGIN PRIVATE KEY-----\nabc\n"}),
        lambda d: d.update(fingerprint_domain=123),
    ],
)
def test_invalid_config(product, mutation):
    data = json.loads(product[0].to_bytes())
    mutation(data)
    with pytest.raises(ValueError):
        ProductConfig.from_bytes(json.dumps(data).encode())


def test_duplicate_size_utf8_and_factory(product, tmp_path):
    config, _ = product
    for raw in [
        b"",
        b"x" * 65537,
        b"\xff",
        config.to_bytes().replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1'),
    ]:
        with pytest.raises(ValueError):
            ProductConfig.from_bytes(raw)
    with pytest.raises(ValueError):
        LicenseRuntime.from_config(config.to_bytes(), root=tmp_path)
    with pytest.raises(ValueError):
        LicenseRuntime.from_config(config.to_bytes(), root=tmp_path, service="missing")
    runtime = LicenseRuntime.from_config(config.to_bytes(), root=tmp_path, service="backend")
    assert runtime.mode == BuildMode.CUSTOMER_REQUIRED
    assert runtime.status(read_only=True).state == RuntimeState.CONFIG_INVALID
    assert list(tmp_path.iterdir()) == []
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        LicenseRuntime.from_config(config.to_bytes(), root=link / "child", service="backend")
    single = ProductConfig(
        ProductProfile("one", b"f", b"b", b"r", "OP1", (("api", "OP1A"),)), config.trusted_keys
    )
    assert LicenseRuntime.from_config(single.to_bytes(), root=Path("relative")).service == "api"
