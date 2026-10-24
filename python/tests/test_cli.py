"""Installed command implementation has structured outcomes and no secret traceback."""

import hashlib
import json

import pytest
from test_import import signed

from license_sdk.cli import main
from license_sdk.identity import binding_id, host_fingerprint
from license_sdk.runtime import LicenseRuntime


def test_cli_complete_path(product, tmp_path, capsys):
    config, _ = product
    config_path = tmp_path / "product.json"
    config_path.write_bytes(config.to_bytes())
    root = tmp_path / "installation"
    common = ["--config", str(config_path), "--root", str(root)]
    assert main(["init", *common]) == 0
    assert json.loads(capsys.readouterr().out)["actionStatus"] == "INITIALIZED"
    assert main(["init", *common]) == 0
    assert json.loads(capsys.readouterr().out)["actionStatus"] == "ALREADY_INITIALIZED"
    args = [*common, "--service", "backend"]
    assert main(["status", *args]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "UNLICENSED"
    machine = tmp_path / "machine-id"
    machine.write_bytes(b"0123456789abcdef0123456789abcdef\n")
    output = tmp_path / "request.json"
    assert main(["request", *args, "--machine-id", str(machine), "--output", str(output)]) == 0
    request = json.loads(capsys.readouterr().out)
    assert json.loads(output.read_bytes()) == request
    install = json.loads((root / "identity/installation.json").read_bytes())
    assert (
        request["binding_id"]
        == binding_id(
            config.profile,
            host_fingerprint(config.profile, machine.read_bytes()),
            install["installation_id"],
        ).hex()
    )
    assert b"service.key" not in output.read_bytes()
    assert machine.read_bytes().strip() not in output.read_bytes()
    candidate = tmp_path / "license.json"
    candidate.write_bytes(
        signed(
            product,
            binding_type="HOST_INSTALL",
            binding_scheme="host-install-code-v1",
            binding_id=request["binding_id"],
        )
    )
    assert main(["import", *args, "--machine-id", str(machine), "--file", str(candidate)]) == 0
    assert json.loads(capsys.readouterr().out)["status"]["state"] == "ACTIVE"
    assert main(["import", *args, "--machine-id", str(machine), "--file", str(candidate)]) == 0
    assert json.loads(capsys.readouterr().out)["actionStatus"] == "ALREADY_APPLIED"
    runtime = LicenseRuntime.from_config(
        config.to_bytes(), root=root, service="backend", machine_id_file=machine
    )
    runtime.require_valid()
    assert main(["request", *args, "--machine-id", str(machine), "--output", str(output)]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["code"] == "LICENSE_OUTPUT_EXISTS"
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("command", ["init", "status", "request", "import"])
def test_help(command, capsys):
    with pytest.raises(SystemExit) as exc:
        main([command, "--help"])
    assert exc.value.code == 0
    assert "--config" in capsys.readouterr().out


def test_status_reports_full_digest_of_oversized_invalid_file(product, tmp_path, capsys):
    from license_sdk.installation import initialize_installation

    config, _ = product
    config_path = tmp_path / "product.json"
    config_path.write_bytes(config.to_bytes())
    root = tmp_path / "customer"
    initialize_installation(config, root)
    raw = b"broken" * 20000
    (root / "shared/active.license.json").write_bytes(raw)
    assert (
        main(["status", "--config", str(config_path), "--root", str(root), "--service", "backend"])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "INVALID"
    assert result["fileDigest"] == hashlib.sha256(raw).hexdigest()
