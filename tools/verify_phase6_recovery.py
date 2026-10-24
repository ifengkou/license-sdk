"""Real issuer TOML/CLI recovery consumed by actual Java/Python SDK artifacts."""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from license_sdk import ICP_DIAG, ProductProfile
from license_sdk.codes import encode_code
from license_sdk.identity import binding_id, host_fingerprint
from license_sdk.recovery import RecoveryError, canonical_json, utc_text
from license_sdk.runtime import (
    BuildMode,
    ClockState,
    LicenseRuntime,
    RuntimePaths,
    encode_clock,
)
from verify_phase2_cross_language import write_issuer_config

INSTALL = "11111111-1111-4111-8111-111111111111"
MASTER = bytes(range(32))
MACHINE = b"0123456789abcdef0123456789abcdef\n"
OTHER_PRODUCT = ProductProfile(
    "other-product",
    b"other-product/host-fingerprint/v1",
    b"other-product/host-install-code/v1",
    b"other-product/recovery-target/v1",
    "IC1",
    (("gateway", "RC101"), ("worker", "RC102")),
)


class Clock:
    def __init__(self, now):
        self.now = now

    def wall_now(self):
        return self.now

    def monotonic_ns(self):
        return 0


def cli(*args, success=True):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from license_issuer.cli import main; raise SystemExit(main())",
            *map(str, args),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if success:
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)
    assert result.returncode != 0, "invalid issuer input unexpectedly succeeded"
    return None


def main():
    sdk = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--java-jar",
        type=Path,
        default=sdk / "java/license-core/build/libs/license-core-0.7.0.jar",
    )
    jar = parser.parse_args().java_jar.resolve()
    now = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=10)
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
            private,
            "--public-key",
            public,
            "--passphrase-file",
            phrase,
        )
        from cryptography.hazmat.primitives import serialization

        key = serialization.load_pem_public_key(public.read_bytes())
        signing_key = serialization.load_pem_private_key(private.read_bytes(), phrase.read_bytes())
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

        def java(command, install_root, service, profile, file=None, instant=now):
            args = [
                "java",
                "-cp",
                f"{jar}:{root}",
                "RecoveryProbe",
                command,
                str(install_root),
                str(der),
                service,
                dict(profile.recovery_prefixes)[service],
                utc_text(instant),
                profile.product_id,
                profile.fingerprint_domain.decode("ascii"),
                profile.binding_domain.decode("ascii"),
                profile.recovery_domain.decode("ascii"),
            ]
            if file is not None:
                args.append(str(file))
            result = subprocess.run(args, check=False, capture_output=True, text=True)
            assert result.returncode in (0, 2), result.stderr
            return json.loads(result.stdout)

        def python_runtime(install_root, service, profile, instant=now):
            return LicenseRuntime(
                profile=profile,
                service=service,
                paths=RuntimePaths(install_root, install_root / "machine-id"),
                trusted_keys={"recovery-test": der.read_bytes()},
                mode=BuildMode.CUSTOMER_REQUIRED,
                time_source=Clock(instant),
            )

        def altered_file(case, signed, name, changes):
            outer = json.loads(signed)
            payload = json.loads(
                base64.urlsafe_b64decode(outer["payload"] + "=" * (-len(outer["payload"]) % 4))
            )
            raw = canonical_json(payload | changes)
            outer = {
                "payload": base64.urlsafe_b64encode(raw).decode().rstrip("="),
                "signature": base64.urlsafe_b64encode(signing_key.sign(raw)).decode().rstrip("="),
            }
            path = case / f"{name}.json"
            path.write_bytes(canonical_json(outer))
            return path

        for profile in (ICP_DIAG, OTHER_PRODUCT):
            for service, _prefix in profile.recovery_prefixes:
                other_service = next(s for s, _ in profile.recovery_prefixes if s != service)
                for damage in ("rollback", "missing", "broken"):
                    for producer in ("java", "python"):
                        case = root / f"{profile.product_id}-{service}-{damage}-{producer}"
                        original = case / "original"
                        for directory in (
                            "identity",
                            "shared",
                            f"state/{service}",
                            f"state/{other_service}",
                        ):
                            (original / directory).mkdir(parents=True)
                        (original / "machine-id").write_bytes(MACHINE)
                        (original / "identity/installation.json").write_bytes(
                            canonical_json(
                                dict(
                                    schema_version=1,
                                    product_id=profile.product_id,
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
                                    product_id=profile.product_id,
                                    installation_id=INSTALL,
                                    service=service,
                                    service_key=MASTER,
                                )
                            )
                        elif damage == "broken":
                            clock_path.write_bytes(b"broken")
                        other_clock = original / f"state/{other_service}/clock.json"
                        other_clock.write_bytes(
                            encode_clock(
                                ClockState(1, 0, now, None),
                                product_id=profile.product_id,
                                installation_id=INSTALL,
                                service=other_service,
                                service_key=MASTER,
                            )
                        )
                        original_other = other_clock.read_bytes()
                        config = write_issuer_config(
                            case / "issuer.toml",
                            profile,
                            key_id="recovery-test",
                            private=private,
                            public=public,
                            records=case,
                            output=case,
                        )
                        license_file = original / "shared/active.license.json"
                        # Preserve ICP UNBOUND; the new Runtime derives its real host/install binding.
                        bound = profile is OTHER_PRODUCT
                        binding = binding_id(profile, host_fingerprint(profile, MACHINE), INSTALL)
                        cli(
                            "sign-license",
                            "--config",
                            config,
                            "--passphrase-file",
                            phrase,
                            "--customer-id",
                            "synthetic",
                            "--customer-name",
                            "公开测试",
                            "--binding",
                            "HOST_INSTALL" if bound else "UNBOUND",
                            "--validity",
                            "PERPETUAL",
                            "--not-before",
                            utc_text(now - timedelta(days=1)),
                            "--output",
                            license_file,
                            *(
                                [
                                    "--request-code",
                                    encode_code(profile.request_prefix, binding),
                                ]
                                if bound
                                else []
                            ),
                        )
                        original_license = license_file.read_bytes()
                        if producer == "java":
                            request = java("request", original, service, profile)
                            assert java("request", original, service, profile) == request
                        else:
                            runtime = python_runtime(original, service, profile)
                            request = runtime.get_or_create_recovery_request().to_dict()
                            assert runtime.get_or_create_recovery_request().to_dict() == request
                        request_file = case / "request.json"
                        request_file.write_bytes(canonical_json(request))
                        recovery_file = case / "recovery.json"
                        common = [
                            "sign-recovery",
                            "--config",
                            config,
                            "--passphrase-file",
                            phrase,
                            "--window-start",
                            utc_text(now),
                            "--window-end",
                            utc_text(now + timedelta(days=6)),
                            "--output",
                            recovery_file,
                            "--issuance-id",
                            str(uuid4()),
                        ]
                        cli(*common, "--request-file", request_file)
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

                        # Reject mismatched request prefix/domain before signing.
                        for label, wrong in (
                            (
                                "prefix",
                                replace(
                                    profile,
                                    recovery_prefixes=tuple(
                                        (s, "WRONG1" if s == service else p)
                                        for s, p in profile.recovery_prefixes
                                    ),
                                ),
                            ),
                            (
                                "domain",
                                replace(profile, recovery_domain=b"wrong/recovery/v1"),
                            ),
                        ):
                            wrong_config = write_issuer_config(
                                case / f"wrong-{label}.toml",
                                wrong,
                                key_id="recovery-test",
                                private=private,
                                public=public,
                                records=case,
                                output=case,
                            )
                            rejected_output = case / f"rejected-{label}.json"
                            cli(
                                "sign-recovery",
                                "--config",
                                wrong_config,
                                "--passphrase-file",
                                phrase,
                                "--window-start",
                                utc_text(now),
                                "--window-end",
                                utc_text(now + timedelta(days=6)),
                                "--request-file",
                                request_file,
                                "--output",
                                rejected_output,
                                success=False,
                            )
                            assert not rejected_output.exists()

                        for consumer in ("java", "python"):
                            target = case / consumer
                            shutil.copytree(original, target)

                            def apply(
                                file=recovery_file,
                                instant=now,
                                selected=profile,
                                consumer=consumer,
                                target=target,
                                service=service,
                            ):
                                if consumer == "java":
                                    return java(
                                        "apply",
                                        target,
                                        service,
                                        selected,
                                        file,
                                        instant,
                                    )
                                try:
                                    result = python_runtime(
                                        target, service, selected, instant
                                    ).apply_recovery(file.read_bytes())
                                    return {
                                        "actionStatus": result.action_status,
                                        "state": result.status.state.value,
                                    }
                                except RecoveryError as error:
                                    return {
                                        "error": error.code,
                                        "httpStatus": error.http_status,
                                    }

                            malformed = case / f"invalid-{consumer}.json"
                            malformed.write_bytes(
                                canonical_json(json.loads(signed) | {"signature": "A" * 86})
                            )
                            current = target / f"state/{service}/clock.json"
                            before = current.read_bytes() if current.exists() else None
                            assert apply(malformed)["error"] == "LICENSE_RECOVERY_INVALID"
                            assert (
                                apply(instant=now + timedelta(days=6))["error"]
                                == "LICENSE_RECOVERY_WINDOW_INVALID"
                            )
                            for label, changes, expected_error in (
                                (
                                    "product",
                                    {"product_id": "wrong-product"},
                                    "LICENSE_RECOVERY_INVALID",
                                ),
                                (
                                    "service",
                                    {"service": other_service},
                                    "LICENSE_RECOVERY_TARGET_CHANGED",
                                ),
                                (
                                    "key",
                                    {"key_id": "unknown-key"},
                                    "LICENSE_RECOVERY_INVALID",
                                ),
                                (
                                    "target",
                                    {"target_id": "0" * 40},
                                    "LICENSE_RECOVERY_TARGET_CHANGED",
                                ),
                            ):
                                invalid = altered_file(case, signed, label, changes)
                                assert apply(invalid)["error"] == expected_error, (
                                    label,
                                    consumer,
                                )
                            assert (
                                apply(
                                    selected=replace(profile, recovery_domain=b"wrong/recovery/v1")
                                )["error"]
                                == "LICENSE_RECOVERY_TARGET_CHANGED"
                            )
                            assert (current.read_bytes() if current.exists() else None) == before
                            if bound:
                                for wrong in (
                                    replace(profile, fingerprint_domain=b"wrong/fingerprint/v1"),
                                    replace(profile, binding_domain=b"wrong/binding/v1"),
                                ):
                                    if consumer == "java":
                                        invalid_status = java("status", target, service, wrong)[
                                            "state"
                                        ]
                                    else:
                                        invalid_status = (
                                            python_runtime(target, service, wrong)
                                            .status(read_only=True)
                                            .state.value
                                        )
                                    assert invalid_status == "INVALID", invalid_status
                                assert (
                                    current.read_bytes() if current.exists() else None
                                ) == before

                            if consumer == "java":
                                first = java("roundtrip", target, service, profile, recovery_file)
                            else:
                                runtime = python_runtime(target, service, profile)
                                requested = runtime.get_or_create_recovery_request()
                                result = runtime.apply_recovery(signed)
                                refreshed = runtime.status(read_only=True)
                                after_first = current.read_bytes()
                                repeated = runtime.apply_recovery(signed)
                                assert current.read_bytes() == after_first
                                first = {
                                    "targetId": requested.target_id,
                                    "actionStatus": result.action_status,
                                    "state": result.status.state.value,
                                    "refreshedState": refreshed.state.value,
                                    "repeatStatus": repeated.action_status,
                                }
                            assert first == {
                                "targetId": request["target_id"],
                                "actionStatus": "APPLIED",
                                "state": "ACTIVE",
                                "refreshedState": "ACTIVE",
                                "repeatStatus": "ALREADY_APPLIED",
                            }, first
                            after = current.read_bytes()
                            second = apply(instant=now + timedelta(days=8))
                            assert second["actionStatus"] == "ALREADY_APPLIED", second
                            assert current.read_bytes() == after
                            assert (
                                target / "shared/active.license.json"
                            ).read_bytes() == original_license
                            assert (
                                target / f"state/{other_service}/clock.json"
                            ).read_bytes() == original_other
                            checks += 1
        print(
            f"PASS: {checks} actual TOML/CLI recovery cross-language paths; ICP/new product, "
            "two services each, file/manual, rollback/missing/corrupt, same-instance "
            "request/apply/status/repeat, restart idempotency, product/service/domain/prefix/key/"
            "signature/window/target negatives, unchanged commercial and other-service clock files"
        )


if __name__ == "__main__":
    main()
