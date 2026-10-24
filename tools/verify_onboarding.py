"""Installed SDK/Issuer onboarding checks, using synthetic keys and real CLI processes."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import selectors
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from license_issuer.adapters import LocalKeyManager
from license_issuer.config import load_config
from license_issuer.web import create_app
from license_sdk import ICP_DIAG, ProductProfile
from license_sdk.runtime import LicenseRuntime
from verify_phase2_cross_language import run_cli, write_issuer_config

LOCK_PROBE = r"""
package io.metor.license.core;
import java.nio.file.Path;
public class OnboardingLockProbe {
    public static void main(String[] args) throws Exception {
        if (args[0].equals("draft")) {
            var files = new OnboardingFiles() {
                @Override void move(Path source, Path target, boolean replace) throws java.io.IOException {
                    if (source.getFileName().toString().equals(".identity-stage")) throw new java.io.IOException("Injected interruption");
                    super.move(source, target, replace);
                }
            };
            try { LicenseInstallation.initialize(ProductConfig.parse(java.nio.file.Files.readAllBytes(Path.of(args[1]))), Path.of(args[2]), files); }
            catch (OnboardingException expected) { if (expected.code() != OnboardingException.Code.OUTCOME_UNKNOWN) throw expected; return; }
            throw new AssertionError("Expected interrupted draft");
        }
        new OnboardingFiles().withLock(Path.of(args[0]), () -> {
            System.out.println("LOCKED"); System.out.flush(); System.in.read(); return null;
        });
    }
}
"""
PYTHON_LOCK = """
import sys
from pathlib import Path
from license_sdk.installation import record_lock
with record_lock(Path(sys.argv[1])):
    print('LOCKED', flush=True)
    sys.stdin.read(1)
"""
PYTHON_DRAFT = """
import sys
from pathlib import Path
import license_sdk.installation as installation
from license_sdk import ProductConfig
def interrupted(*args):
    raise OSError('Injected interruption')
installation.os.rename = interrupted
try:
    installation.initialize_installation(ProductConfig.from_bytes(Path(sys.argv[1]).read_bytes()), Path(sys.argv[2]))
except installation.OnboardingError as expected:
    assert expected.code == 'LICENSE_STORAGE_OUTCOME_UNKNOWN', expected.code
else:
    raise AssertionError('Expected interrupted draft')
"""


def snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.name.endswith(".lock")
    }


def cli(
    language,
    jar,
    action,
    config,
    root,
    *,
    service=None,
    machine=None,
    extra=(),
    ok=True,
):
    command = (
        [sys.executable, "-m", "license_sdk.cli"]
        if language == "python"
        else ["java", "-jar", str(jar)]
    )
    command += [action, "--config", str(config), "--root", str(root)]
    if service is not None:
        command += ["--service", service]
    if machine is not None:
        command += ["--machine-id", str(machine)]
    result = subprocess.run(
        [*command, *map(str, extra)],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert (result.returncode == 0) == ok, (command, result.stdout, result.stderr)
    return json.loads(result.stdout or result.stderr)


def download(directory, profile):
    directory.mkdir()
    private, public = directory / "private.pem", directory / "public.pem"
    password = directory / "passphrase.txt"
    password.write_bytes(b"synthetic-onboarding-passphrase")
    password.chmod(0o600)
    LocalKeyManager(private, public).generate(password.read_bytes())
    records, output = directory / "records", directory / "output"
    records.mkdir()
    output.mkdir()
    toml = write_issuer_config(
        directory / "issuer.toml",
        profile,
        key_id="synthetic-2026",
        private=private,
        public=public,
        records=records,
        output=output,
    )
    settings = load_config(toml)
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        assert (
            client.get("/sdk-config/download", follow_redirects=False).status_code
            == 303
        )
        csrf = client.get("/api/session").json()["data"]["csrf"]
        response = client.post(
            "/api/unlock",
            json={"passphrase": password.read_text()},
            headers={"Origin": settings.origin, "X-CSRF-Token": csrf},
        )
        assert response.status_code == 200, response.text
        response = client.get("/sdk-config/download")
        assert response.status_code == 200
        config = directory / "license-product.json"
        config.write_bytes(response.content)
    return config, toml, password


def locked(holder, contender, jar, classes, path, command):
    if holder == "python":
        invocation = [sys.executable, "-c", PYTHON_LOCK, str(path)]
    else:
        invocation = [
            "java",
            "-cp",
            os.pathsep.join((str(jar), str(classes))),
            "io.metor.license.core.OnboardingLockProbe",
            str(path),
        ]
    process = subprocess.Popen(
        invocation,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=15), "lock holder did not start"
        assert process.stdout.readline().strip() == "LOCKED", process.stderr.read()
        rejected = command(contender, False)
        assert rejected["code"] == "LICENSE_STATE_CONFLICT", rejected
    finally:
        if process.poll() is None:
            process.communicate("x", timeout=15)
        assert process.returncode == 0


def check_init(directory, config, jar, service, machine, classes):
    for first, second in (("python", "java"), ("java", "python")):
        draft_root = directory / ("draft-" + first)
        if first == "python":
            invocation = [
                sys.executable,
                "-c",
                PYTHON_DRAFT,
                str(config),
                str(draft_root),
            ]
        else:
            invocation = [
                "java",
                "-cp",
                os.pathsep.join((str(jar), str(classes))),
                "io.metor.license.core.OnboardingLockProbe",
                "draft",
                str(config),
                str(draft_root),
            ]
        subprocess.run(
            invocation, check=True, capture_output=True, text=True, timeout=20
        )
        draft = draft_root / ".identity-stage"
        identity = json.loads((draft / "installation.json").read_bytes())
        key = (draft / "service.key").read_bytes()
        resumed = cli(second, jar, "init", config, draft_root)
        assert resumed["actionStatus"] == "INITIALIZED", resumed
        assert resumed["installationId"] == identity["installation_id"]
        assert (draft_root / "identity/service.key").read_bytes() == key
        root = directory / ("init-" + first)
        result = cli(first, jar, "init", config, root)
        assert result["actionStatus"] == "INITIALIZED", result
        before = snapshot(root)
        result = cli(second, jar, "init", config, root)
        assert result["actionStatus"] == "ALREADY_INITIALIZED", result
        assert snapshot(root) == before
        for language in (first, second):
            state = cli(
                language, jar, "status", config, root, service=service, machine=machine
            )
            assert state["state"] == "UNLICENSED", state
            cli(language, jar, "status", config, root, machine=machine, ok=False)
        locked(
            first,
            second,
            jar,
            classes,
            root / ".init.lock",
            lambda lang, ok, root=root: cli(lang, jar, "init", config, root, ok=ok),
        )
        assert snapshot(root) == before
        # Deliberate damaged completed installation must never get repaired or reset.
        (root / "identity/service.key").unlink()
        damaged = snapshot(root)
        cli(second, jar, "init", config, root, ok=False)
        assert snapshot(root) == damaged


def sign(toml, password, output, request, binding, validity):
    flags = [
        "sign-license",
        "--config",
        toml,
        "--passphrase-file",
        password,
        "--customer-id",
        "synthetic-onboarding",
        "--customer-name",
        "Synthetic onboarding",
        "--binding",
        binding,
        "--validity",
        validity,
        "--not-before",
        "2026-01-01T00:00:00Z",
        "--output",
        output,
    ]
    if binding == "HOST_INSTALL":
        flags += ["--request-file", request]
    if validity == "TERM":
        flags += ["--expires-at", "2099-01-01T00:00:00Z"]
    run_cli(sys.executable, *flags)


def check_commercial(directory, config, toml, password, jar, service, machine, classes):
    count = 0
    combinations = itertools.product(("python", "java"), repeat=3)
    for init_lang, request_lang, import_lang in combinations:
        root = directory / f"commercial-{init_lang}-{request_lang}-{import_lang}"
        cli(init_lang, jar, "init", config, root)
        runtime = LicenseRuntime.from_config(
            config.read_bytes(), root=root, service=service, machine_id_file=machine
        )
        assert runtime.status(read_only=True).state.value == "UNLICENSED"
        request = directory / f"{root.name}-request.json"
        cli(
            request_lang,
            jar,
            "request",
            config,
            root,
            service=service,
            machine=machine,
            extra=("--output", request),
        )
        cli(
            request_lang,
            jar,
            "request",
            config,
            root,
            service=service,
            machine=machine,
            extra=("--output", request),
            ok=False,
        )
        for binding, validity in itertools.product(
            ("HOST_INSTALL", "UNBOUND"), ("TERM", "PERPETUAL")
        ):
            output = directory / f"{root.name}-{binding}-{validity}.json"
            sign(toml, password, output, request, binding, validity)
            before = snapshot(root)
            result = cli(
                import_lang,
                jar,
                "import",
                config,
                root,
                service=service,
                machine=machine,
                extra=("--file", output),
            )
            assert result["actionStatus"] == "APPLIED", result
            assert result["status"]["state"] == "ACTIVE", result
            assert (
                root / "shared/active.license.json"
            ).read_bytes() == output.read_bytes()
            assert (
                result["fileDigest"] == hashlib.sha256(output.read_bytes()).hexdigest()
            )
            runtime.require_valid()  # Original instance sees the CLI publication.
            after = snapshot(root)
            assert all(
                after[name] == raw
                for name, raw in before.items()
                if name != "shared/active.license.json"
            )
            repeat = cli(
                "java" if import_lang == "python" else "python",
                jar,
                "import",
                config,
                root,
                service=service,
                machine=machine,
                extra=("--file", output),
            )
            assert repeat["actionStatus"] == "ALREADY_APPLIED", repeat
            assert snapshot(root) == after
            broken = directory / "broken.json"
            broken.write_bytes(b'{"payload":"broken","signature":"broken"}')
            cli(
                import_lang,
                jar,
                "import",
                config,
                root,
                service=service,
                machine=machine,
                extra=("--file", broken),
                ok=False,
            )
            assert snapshot(root) == after
            count += 1
        for holder, contender in (("python", "java"), ("java", "python")):
            locked(
                holder,
                contender,
                jar,
                classes,
                root / "shared/license-import.lock",
                lambda lang, ok, root=root, output=output: cli(
                    lang,
                    jar,
                    "import",
                    config,
                    root,
                    service=service,
                    machine=machine,
                    extra=("--file", output),
                    ok=ok,
                ),
            )
            assert snapshot(root) == after
        runtime.close()
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("init", "commercial", "all"), default="all")
    parser.add_argument("--java-jar", type=Path, required=True)
    args = parser.parse_args()
    jar = args.java_jar.resolve()
    with tempfile.TemporaryDirectory(prefix="license-onboarding-") as temporary:
        directory = Path(temporary).resolve()
        source = directory / "OnboardingLockProbe.java"
        source.write_text(LOCK_PROBE)
        subprocess.run(
            ["javac", "-cp", str(jar), "-d", str(directory), str(source)], check=True
        )
        machine = directory / "machine-id"
        machine.write_text("0123456789abcdef0123456789abcdef\n")
        other = ProductProfile(
            "other-product",
            b"other/fingerprint/v1",
            b"other/binding/v1",
            b"other/recovery/v1",
            "OP1",
            (("gateway", "OP1G"), ("worker", "OP1W")),
        )
        count = 0
        for profile in (ICP_DIAG, other):
            product_dir = directory / profile.product_id
            config, toml, password = download(product_dir, profile)
            service = profile.recovery_prefixes[0][0]
            if args.stage in ("init", "all"):
                check_init(product_dir, config, jar, service, machine, directory)
            if args.stage in ("commercial", "all"):
                count += check_commercial(
                    product_dir,
                    config,
                    toml,
                    password,
                    jar,
                    service,
                    machine,
                    directory,
                )
        print(
            json.dumps(
                {
                    "stage": args.stage,
                    "platform": sys.platform,
                    "products": 2,
                    "commercial_combinations": count,
                    "cross_process_locks": "passed",
                }
            )
        )


if __name__ == "__main__":
    main()
