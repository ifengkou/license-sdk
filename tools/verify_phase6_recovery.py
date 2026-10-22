"""Real issuer CLI recovery files consumed by actual Java/Python SDK artifacts."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from license_sdk import ICP_DIAG
from license_sdk.recovery import RecoveryError, canonical_json, utc_text
from license_sdk.runtime import (
    BuildMode,
    ClockState,
    LicenseRuntime,
    RuntimePaths,
    encode_clock,
)

INSTALL = "11111111-1111-4111-8111-111111111111"
MASTER = bytes(range(32))


class Clock:
    def __init__(self, now):
        self.now = now

    def wall_now(self):
        return self.now

    def monotonic_ns(self):
        return 0


def cli(*args):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from license_issuer.cli import main; raise SystemExit(main())",
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def main():
    sdk = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--java-jar",
        type=Path,
        default=sdk / "java/license-core/build/libs/license-core-0.6.0.jar",
    )
    jar = parser.parse_args().java_jar.resolve()
    now = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=2)
    assert len(MASTER) == 32
    checks = 0
    with tempfile.TemporaryDirectory(prefix="license-phase6-") as temporary:
        root = Path(temporary).resolve()
        phrase = root / "passphrase"
        phrase.write_bytes(b"synthetic-public-conformance-passphrase")
        phrase.chmod(0o600)
        private, public = root / "private.pem", root / "public.pem"
        cli(
            "keygen",
            "--key-id",
            "recovery-test",
            "--private-key",
            str(private),
            "--public-key",
            str(public),
            "--passphrase-file",
            str(phrase),
        )
        from cryptography.hazmat.primitives import serialization

        key = serialization.load_pem_public_key(public.read_bytes())
        der = root / "public.der"
        der.write_bytes(
            key.public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        subprocess.run(
            [
                "javac",
                "-cp",
                str(jar),
                "-d",
                str(root),
                str(sdk / "java/examples/RecoveryProbe.java"),
            ],
            check=True,
        )

        def java(command, install_root, service, file=None, instant=now):
            args = [
                "java",
                "-cp",
                f"{jar}:{root}",
                "RecoveryProbe",
                command,
                str(install_root),
                str(der),
                service,
                dict(ICP_DIAG.recovery_prefixes)[service],
                utc_text(instant),
            ]
            if file is not None:
                args.append(str(file))
            result = subprocess.run(args, check=False, capture_output=True, text=True)
            assert result.returncode in (0, 2), result.stderr
            return json.loads(result.stdout)

        def python_runtime(install_root, service, instant=now):
            return LicenseRuntime(
                profile=ICP_DIAG,
                service=service,
                paths=RuntimePaths(install_root, install_root / "machine-id"),
                trusted_keys={"recovery-test": der.read_bytes()},
                mode=BuildMode.CUSTOMER_REQUIRED,
                time_source=Clock(instant),
            )

        for service in ("metor", "ai"):
            for damage in ("rollback", "missing", "broken"):
                for producer in ("java", "python"):
                    case = root / f"{service}-{damage}-{producer}"
                    original = case / "original"
                    for directory in ("identity", "shared", f"state/{service}"):
                        (original / directory).mkdir(parents=True)
                    (original / "identity/installation.json").write_bytes(
                        canonical_json(
                            dict(
                                schema_version=1,
                                product_id="icp-diag",
                                installation_id=INSTALL,
                                initialized=True,
                            )
                        )
                    )
                    (original / "identity/service.key").write_bytes(MASTER)
                    clock_path = original / f"state/{service}/clock.json"
                    if damage == "rollback":
                        clock_path.write_bytes(
                            encode_clock(
                                ClockState(2, 9, now + timedelta(days=400), None),
                                product_id="icp-diag",
                                installation_id=INSTALL,
                                service=service,
                                service_key=MASTER,
                            )
                        )
                    elif damage == "broken":
                        clock_path.write_bytes(b"broken")
                    license_file = original / "shared/active.license.json"
                    cli(
                        "sign-license",
                        "--product",
                        "icp-diag",
                        "--key-id",
                        "recovery-test",
                        "--private-key",
                        str(private),
                        "--passphrase-file",
                        str(phrase),
                        "--customer-id",
                        "synthetic",
                        "--customer-name",
                        "公开测试",
                        "--binding",
                        "UNBOUND",
                        "--validity",
                        "PERPETUAL",
                        "--not-before",
                        utc_text(now - timedelta(days=1)),
                        "--records-dir",
                        str(case),
                        "--output",
                        str(license_file),
                    )
                    original_license = license_file.read_bytes()
                    if producer == "java":
                        request = java("request", original, service)
                        assert java("request", original, service) == request
                    else:
                        runtime = python_runtime(original, service)
                        request = runtime.get_or_create_recovery_request().to_dict()
                        assert runtime.get_or_create_recovery_request().to_dict() == request
                    request_file = case / "request.json"
                    request_file.write_bytes(canonical_json(request))
                    recovery_file = case / "recovery.json"
                    issuance = str(uuid4())
                    common = [
                        "sign-recovery",
                        "--product",
                        "icp-diag",
                        "--key-id",
                        "recovery-test",
                        "--private-key",
                        str(private),
                        "--passphrase-file",
                        str(phrase),
                        "--window-start",
                        utc_text(now),
                        "--window-end",
                        utc_text(now + timedelta(days=6)),
                        "--records-dir",
                        str(case),
                        "--output",
                        str(recovery_file),
                        "--issuance-id",
                        issuance,
                    ]
                    cli(*common, "--request-file", str(request_file))
                    signed = recovery_file.read_bytes()
                    cli(
                        *common,
                        "--installation-id",
                        INSTALL,
                        "--service",
                        service,
                        "--recovery-code",
                        request["recovery_code"],
                    )
                    assert recovery_file.read_bytes() == signed
                    for consumer in ("java", "python"):
                        target = case / consumer
                        shutil.copytree(original, target)

                        def apply(
                            file=recovery_file,
                            instant=now,
                            consumer=consumer,
                            target=target,
                            service=service,
                        ):
                            if consumer == "java":
                                return java("apply", target, service, file, instant)
                            try:
                                result = python_runtime(target, service, instant).apply_recovery(
                                    file.read_bytes()
                                )
                                return {
                                    "actionStatus": result.action_status,
                                    "state": result.status.state.value,
                                    "recoveryId": result.recovery_id,
                                }
                            except RecoveryError as error:
                                return {
                                    "error": error.code,
                                    "httpStatus": error.http_status,
                                }

                        malformed = case / f"invalid-{consumer}.json"
                        outer = json.loads(signed)
                        malformed.write_bytes(canonical_json(outer | {"signature": "A" * 86}))
                        before = (
                            (target / f"state/{service}/clock.json").read_bytes()
                            if clock_path.exists()
                            else None
                        )
                        assert apply(malformed)["error"] == "LICENSE_RECOVERY_INVALID"
                        assert (
                            apply(instant=now + timedelta(days=6))["error"]
                            == "LICENSE_RECOVERY_WINDOW_INVALID"
                        )
                        current = target / f"state/{service}/clock.json"
                        assert (current.read_bytes() if current.exists() else None) == before
                        first = apply()
                        assert first["actionStatus"] == "APPLIED" and first["state"] == "ACTIVE", (
                            first
                        )
                        after = current.read_bytes()
                        second = apply(instant=now + timedelta(days=8))
                        assert second["actionStatus"] == "ALREADY_APPLIED", second
                        assert current.read_bytes() == after
                        assert (
                            target / "shared/active.license.json"
                        ).read_bytes() == original_license
                        checks += 1
        print(
            f"PASS: {checks} actual CLI recovery cross-language paths; "
            "both services, file/manual, rollback/missing/corrupt, signature/window negatives, "
            "restart/idempotency and unchanged commercial files"
        )


if __name__ == "__main__":
    main()
