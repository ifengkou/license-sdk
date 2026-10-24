"""Synthetic CLI and independent adversarial corpus; temporary test keys only."""

from __future__ import annotations

import base64
import copy
import json
import pathlib
import subprocess
import tempfile
from datetime import UTC

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def make_corpus(directory: pathlib.Path) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    (directory / "public.pem").write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )

    def b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    base = dict(
        schema_version=1,
        key_id="independent-test",
        kind="commercial-license",
        license_id="11223344-5566-4788-99aa-bbccddeeff00",
        customer_id="synthetic-test",
        customer_name="合成测试客户",
        product_id="icp-diag",
        binding_type="HOST_INSTALL",
        binding_scheme="host-install-code-v1",
        binding_id="6e557d24d92ba076121accaa712ec769bbc085df",
        validity_type="TERM",
        issued_at="2026-10-20T00:00:00Z",
        not_before="2026-10-20T00:00:00Z",
        expires_at="2027-10-20T00:00:00Z",
    )
    cases = []

    def add(
        name,
        valid=True,
        changes=None,
        remove=None,
        raw=None,
        outer=None,
        now="2026-10-20T00:00:00Z",
    ):
        payload = copy.deepcopy(base)
        payload.update(changes or {})
        if remove:
            payload.pop(remove)
        data = (
            raw
            if raw is not None
            else json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
        )
        env = {"payload": b64(data), "signature": b64(key.sign(data))}
        encoded = json.dumps(env, separators=(",", ":")).encode()
        if outer:
            encoded = outer(env, encoded)
        filename = f"{len(cases):03d}-{name}.json"
        (directory / filename).write_bytes(encoded)
        cases.append(dict(name=name, file=filename, accept=valid, now=now))

    for validity in ["TERM", "PERPETUAL"]:
        for binding in ["HOST_INSTALL", "UNBOUND"]:
            add(
                validity + "-" + binding,
                changes={
                    "validity_type": validity,
                    "expires_at": None if validity == "PERPETUAL" else base["expires_at"],
                    "binding_type": binding,
                    "binding_scheme": None if binding == "UNBOUND" else base["binding_scheme"],
                    "binding_id": None if binding == "UNBOUND" else base["binding_id"],
                },
            )
    add(
        "pretty-reordered-original-bytes",
        raw=json.dumps(dict(reversed(list(base.items()))), ensure_ascii=False, indent=2).encode(),
    )
    add("astral-200-codepoints", changes={"customer_name": "😀" * 200})
    add("exact-expiry", False, now="2027-10-20T00:00:00Z")
    add("after-expiry-subsecond", False, now="2027-10-20T00:00:00.000001Z")
    add("before-expiry-subsecond", now="2027-10-19T23:59:59.999999Z")
    add("before-start-subsecond", False, now="2026-10-19T23:59:59.999999Z")
    add("before-start", False, now="2026-10-19T23:59:59Z")
    for name, changes in [
        ("schema-bool", {"schema_version": True}),
        ("schema-float", {"schema_version": 1.0}),
        ("schema-unknown", {"schema_version": 2}),
        ("unknown-kind", {"kind": "clock-recovery"}),
        ("wrong-product", {"product_id": "other-product"}),
        ("unknown-key", {"key_id": "unknown"}),
        ("unknown-field", {"unused": 1}),
        ("uppercase-uuid", {"license_id": "11223344-5566-4788-99AA-BBCCDDEEFF00"}),
        ("invalid-uuid", {"license_id": "x"}),
        ("customer-empty", {"customer_name": ""}),
        ("customer-201", {"customer_name": "😀" * 201}),
        ("customer-surrogate", {"customer_name": "\ud800"}),
        ("customer-control", {"customer_name": "a\x85b"}),
        ("customer-null", {"customer_name": None}),
        ("id-nonascii", {"customer_id": "客户"}),
        ("term-null", {"expires_at": None}),
        ("perpetual-date", {"validity_type": "PERPETUAL"}),
        ("unknown-validity", {"validity_type": "FOREVER"}),
        ("binding-null", {"binding_id": None}),
        ("binding-uppercase", {"binding_id": base["binding_id"].upper()}),
        ("binding-bad", {"binding_id": "0" * 40}),
        ("unknown-binding", {"binding_type": "OTHER"}),
        ("unbound-with-id", {"binding_type": "UNBOUND"}),
        ("binding-scheme", {"binding_scheme": "v0"}),
        ("empty-interval", {"not_before": base["expires_at"]}),
        ("issued-after-expiry", {"issued_at": "2028-01-01T00:00:00Z"}),
        ("utc-offset", {"not_before": "2026-10-20T00:00:00+00:00"}),
        ("utc-fraction", {"not_before": "2026-10-20T00:00:00.0Z"}),
        ("utc-leapsecond", {"not_before": "2026-10-20T00:00:60Z"}),
        ("utc-bad-day", {"not_before": "2026-02-30T00:00:00Z"}),
        ("utc-short", {"not_before": "2026-1-1T00:00:00Z"}),
        ("utc-lowercase", {"not_before": "2026-10-20t00:00:00z"}),
        ("year-zero", {"not_before": "0000-01-01T00:00:00Z"}),
    ]:
        add(name, False, changes=changes)
    for field in base:
        add("missing-" + field, False, remove=field)
    raw = json.dumps(base, separators=(",", ":")).encode()
    add("duplicate-payload-key", False, raw=b'{"schema_version":1,' + raw[1:])
    add(
        "escaped-duplicate-payload-key",
        False,
        raw=b'{"schema_versi\\u006fn":1,' + raw[1:],
    )
    add(
        "payload-exponent",
        False,
        raw=raw.replace(b'"schema_version":1', b'"schema_version":1e0'),
    )
    add(
        "payload-NaN",
        False,
        raw=raw.replace(b'"schema_version":1', b'"schema_version":NaN'),
    )
    add("payload-invalid-utf8", False, raw=raw.replace(b'"synthetic-test"', b'"\xff"'))
    add("payload-bom", False, raw=b"\xef\xbb\xbf" + raw)
    add("payload-oversize", False, raw=b" " * 32769 + raw)
    add("outer-unknown", False, outer=lambda e, b: b[:-1] + b',"extra":null}')
    add(
        "outer-duplicate",
        False,
        outer=lambda e, b: b'{"payload":' + json.dumps(e["payload"]).encode() + b"," + b[1:],
    )
    add("outer-bom", False, outer=lambda e, b: b"\xef\xbb\xbf" + b)
    add("outer-trailing-json", False, outer=lambda e, b: b + b" {}")
    add("outer-over-limit", False, outer=lambda e, b: b + b" " * 65537)
    add(
        "base64-padding",
        False,
        outer=lambda e, b: json.dumps(dict(e, payload=e["payload"] + "=")).encode(),
    )
    add(
        "base64-whitespace",
        False,
        outer=lambda e, b: json.dumps(dict(e, payload=" " + e["payload"])).encode(),
    )
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

    def noncanonical(e, b):
        sig = e["signature"]
        i = alphabet.index(sig[-1])
        return json.dumps(dict(e, signature=sig[:-1] + alphabet[i | 1])).encode()

    add("noncanonical-signature-pad-bits", False, outer=noncanonical)
    add(
        "signature-tamper",
        False,
        outer=lambda e, b: json.dumps(
            dict(
                e,
                signature=("A" if e["signature"][0] != "A" else "B") + e["signature"][1:],
            )
        ).encode(),
    )
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "public_key": "public.pem",
                "key_id": base["key_id"],
                "binding_id": base["binding_id"],
                "cases": cases,
            },
            indent=2,
        )
    )
    return directory / "manifest.json"


# This probe uses the public API and the compiled runtime classpath only.
JAVA_PROBE = r"""
import io.metor.license.core.CommercialLicenseVerifier;
import io.metor.license.core.LicenseException;
import java.nio.file.*;
import java.security.*;
import java.security.spec.*;
import java.time.*;
import java.util.*;
public class Phase2Probe {
 public static void main(String[] args) throws Exception {
  String pem=Files.readString(Path.of(args[0]));
  String body=pem.replace("-----BEGIN PUBLIC KEY-----","")
   .replace("-----END PUBLIC KEY-----","").replaceAll("\\s","");
  PublicKey key=KeyFactory.getInstance("Ed25519").generatePublic(
   new X509EncodedKeySpec(Base64.getDecoder().decode(body)));
  var verifier=new CommercialLicenseVerifier(args[1],Map.of(args[2],key));
  for(String line:Files.readAllLines(Path.of(args[3]))) {
   String[] parts=line.split("\t");
   try { var result=verifier.verify(Files.readAllBytes(Path.of(parts[1])),
     args[4],Instant.parse(parts[2]));
    System.out.println(parts[0]+"\t"+result.status().name());
   } catch(LicenseException error) {System.out.println(parts[0]+"\tINVALID");}
  }
 }
}
"""


def check_both(directory, manifest, classpath, *, product="icp-diag"):
    from datetime import datetime

    from license_sdk.verification import LicenseValidationError, verify_license

    cases = manifest["cases"]
    public = (directory / manifest["public_key"]).read_bytes()
    requests = []
    python_results = {}
    for case in cases:
        path = directory / case["file"]
        requests.append(case["name"] + "\t" + str(path) + "\t" + case["now"])
        try:
            result = verify_license(
                path.read_bytes(),
                product_id=product,
                trusted_keys={manifest["key_id"]: public},
                binding_id=manifest["binding_id"],
                effective_now=datetime.fromisoformat(case["now"].replace("Z", "+00:00")),
            )
            status = getattr(result.status, "value", str(result.status))
        except LicenseValidationError:
            status = "INVALID"
        python_results[case["name"]] = status
    request_file = directory / "batch.tsv"
    request_file.write_text("\n".join(requests) + "\n")
    result = subprocess.run(
        [
            "java",
            "-cp",
            classpath + ":" + str(directory),
            "Phase2Probe",
            str(directory / manifest["public_key"]),
            product,
            manifest["key_id"],
            str(request_file),
            manifest["binding_id"],
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    java_results = dict(line.split("\t") for line in result.stdout.splitlines())
    for case in cases:
        expected = (
            "ACTIVE"
            if case["accept"]
            else {
                "exact-expiry": "EXPIRED",
                "after-expiry-subsecond": "EXPIRED",
                "before-start": "NOT_YET_VALID",
                "before-start-subsecond": "NOT_YET_VALID",
            }.get(case["name"], "INVALID")
        )
        actual = (python_results.get(case["name"]), java_results.get(case["name"]))
        if actual != (expected, expected):
            raise AssertionError(f"{case['name']}: expected {expected}, got Python/Java {actual}")
    return len(cases)


def run_cli(python, *args, success=True):
    command = [
        str(python),
        "-c",
        "from license_issuer.cli import main; raise SystemExit(main())",
        *map(str, args),
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if success and result.returncode != 0:
        raise AssertionError(f"CLI {args[0]} failed: {result.stderr} {result.stdout}")
    if not success and result.returncode == 0:
        raise AssertionError("invalid CLI input unexpectedly succeeded")
    return result


def write_issuer_config(path, profile, *, key_id, private, public, records, output):
    """Write a complete, synthetic single-product issuer configuration."""
    values = {
        "product_id": profile.product_id,
        "fingerprint_domain": profile.fingerprint_domain.decode("ascii"),
        "binding_domain": profile.binding_domain.decode("ascii"),
        "recovery_domain": profile.recovery_domain.decode("ascii"),
        "request_prefix": profile.request_prefix,
        "key_id": key_id,
        "private_key": str(private),
        "public_key": str(public),
        "records_dir": str(records),
        "output_dir": str(output),
    }
    lines = [f"{name} = {json.dumps(value)}" for name, value in values.items()]
    lines.append("port = 18200")
    for service, prefix in profile.recovery_prefixes:
        lines.extend(
            [
                "",
                "[[services]]",
                f"id = {json.dumps(service)}",
                f"recovery_prefix = {json.dumps(prefix)}",
            ]
        )
    path.write_text("\n".join(lines) + "\n")
    return path


def main():
    import argparse
    import subprocess
    import sys
    from datetime import datetime

    parser = argparse.ArgumentParser(
        description="Synthetic CLI and independent cross-language validation; no customer data"
    )
    parser.add_argument("--issuer-python", type=pathlib.Path, default=pathlib.Path(sys.executable))
    parser.add_argument("--classpath-file", type=pathlib.Path)
    args = parser.parse_args()
    root = pathlib.Path(__file__).resolve().parents[1]
    classpath_file = args.classpath_file or root / "java/license-core/build/runtime-classpath.txt"
    classpath = classpath_file.read_text().strip()
    if not classpath:
        raise ValueError("Build Java SDK and writeRuntimeClasspath first")
    with tempfile.TemporaryDirectory(prefix="license-phase2-cross-language-") as tmp:
        directory = pathlib.Path(tmp).resolve()
        make_corpus(directory)
        (directory / "Phase2Probe.java").write_text(JAVA_PROBE)
        subprocess.run(
            ["javac", "-cp", classpath, str(directory / "Phase2Probe.java")],
            check=True,
            cwd=directory,
        )
        manifest = json.loads((directory / "manifest.json").read_text())
        count = check_both(directory, manifest, classpath)
        print(
            f"PASS: {count} independently signed valid/adversarial cases agree in Java and Python"
        )
        private = directory / "issuer-test-private.pem"
        public = directory / "issuer-test-public.pem"
        password = directory / "passphrase"
        password.write_bytes(b"synthetic-test-only-passphrase")
        password.chmod(0o600)
        run_cli(
            args.issuer_python,
            "keygen",
            "--key-id",
            "cli-test",
            "--private-key",
            private,
            "--public-key",
            public,
            "--passphrase-file",
            password,
        )
        from license_sdk import ICP_DIAG, ProductProfile
        from license_sdk.codes import encode_code
        from license_sdk.identity import binding_id, host_fingerprint

        records = directory / "records"
        records.mkdir(mode=0o700)
        config = write_issuer_config(
            directory / "issuer.toml",
            ICP_DIAG,
            key_id="cli-test",
            private=private,
            public=public,
            records=records,
            output=directory,
        )
        key_args = ["--config", config, "--passphrase-file", password]
        customer_args = [
            "--customer-id",
            "synthetic-cli",
            "--customer-name",
            "Synthetic CLI test",
        ]
        now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        cases = []
        request_code = json.loads((root / "protocol/request-code-v1-vectors.json").read_text())[
            "binding_vectors"
        ][0]["request_code"]
        binding = "6e557d24d92ba076121accaa712ec769bbc085df"
        for validity in ["TERM", "PERPETUAL"]:
            for strategy in ["HOST_INSTALL", "UNBOUND"]:
                output = directory / f"cli-{validity}-{strategy}.json"
                flags = [
                    "--binding",
                    strategy,
                    "--validity",
                    validity,
                    "--not-before",
                    "2026-01-01T00:00:00Z",
                    "--output",
                    output,
                ]
                if validity == "TERM":
                    flags += ["--expires-at", "2099-01-01T00:00:00Z"]
                if strategy == "HOST_INSTALL":
                    flags += ["--request-code", request_code]
                first = run_cli(
                    args.issuer_python,
                    "sign-license",
                    *key_args,
                    *customer_args,
                    *flags,
                )
                original = output.read_bytes()
                issuance_id = json.loads(first.stdout)["issuance_id"]
                run_cli(
                    args.issuer_python,
                    "sign-license",
                    *key_args,
                    *customer_args,
                    *flags,
                    "--issuance-id",
                    issuance_id,
                )
                if output.read_bytes() != original:
                    raise AssertionError("retry changed the issued artifact")
                run_cli(
                    args.issuer_python,
                    "verify",
                    "--config",
                    config,
                    "--license",
                    output,
                    "--binding-id",
                    binding,
                    "--now",
                    now,
                )
                cases.append(
                    dict(
                        name=f"cli-{validity}-{strategy}",
                        file=output.name,
                        accept=True,
                        now=now,
                    )
                )
        request_file = directory / "request.json"
        request_file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "license-request",
                    "product_id": "icp-diag",
                    "binding_type": "HOST_INSTALL",
                    "binding_scheme": "host-install-code-v1",
                    "binding_id": binding,
                    "request_code": request_code,
                    "request_id": "11223344-5566-4788-99aa-bbccddeeff00",
                    "generated_at": now,
                }
            )
        )
        file_output = directory / "file-request-license.json"
        run_cli(
            args.issuer_python,
            "sign-license",
            *key_args,
            *customer_args,
            "--binding",
            "HOST_INSTALL",
            "--validity",
            "PERPETUAL",
            "--not-before",
            "2026-01-01T00:00:00Z",
            "--output",
            file_output,
            "--request-file",
            request_file,
        )

        def payload_of(path):
            envelope = json.loads(path.read_bytes())
            return json.loads(
                base64.urlsafe_b64decode(
                    envelope["payload"] + "=" * (-len(envelope["payload"]) % 4)
                )
            )

        if (
            payload_of(file_output)["binding_id"]
            != payload_of(directory / "cli-PERPETUAL-HOST_INSTALL.json")["binding_id"]
        ):
            raise AssertionError("file/manual requests produced different bindings")
        cases.append(dict(name="file-request", file=file_output.name, accept=True, now=now))
        protected_output = directory / "cli-PERPETUAL-UNBOUND.json"
        protected_bytes = protected_output.read_bytes()
        run_cli(
            args.issuer_python,
            "sign-license",
            *key_args,
            *customer_args,
            "--customer-name",
            "Different synthetic customer",
            "--binding",
            "UNBOUND",
            "--validity",
            "PERPETUAL",
            "--not-before",
            "2026-01-01T00:00:00Z",
            "--output",
            protected_output,
            success=False,
        )
        if protected_output.read_bytes() != protected_bytes:
            raise AssertionError("existing output was overwritten")
        cli_manifest = dict(
            public_key=public.name, key_id="cli-test", binding_id=binding, cases=cases
        )
        count = check_both(directory, cli_manifest, classpath)
        from license_issuer.adapters import (
            AtomicArtifactStore,
            EncryptedPemSigner,
            LocalIssueRecordStore,
        )
        from license_issuer.application import IssueLicenseRequest, IssuerApplication
        from license_issuer.ports import KeyReference

        second_profile = ProductProfile(
            "other-product",
            b"other-product/host-fingerprint/v1",
            b"other-product/host-install-code/v1",
            b"other-product/recovery-target/v1",
            "IC1",
            (("gateway", "RC101"), ("worker", "RC102")),
        )
        second_config = write_issuer_config(
            directory / "other-issuer.toml",
            second_profile,
            key_id="cli-test",
            private=private,
            public=public,
            records=records,
            output=directory,
        )
        actual_binding = binding_id(
            second_profile,
            host_fingerprint(second_profile, b"0123456789abcdef0123456789abcdef\n"),
            "11111111-1111-4111-8111-111111111111",
        )
        second_cases = []
        for validity in ("TERM", "PERPETUAL"):
            for strategy in ("HOST_INSTALL", "UNBOUND"):
                second_output = directory / f"other-{validity}-{strategy}.json"
                flags = []
                if validity == "TERM":
                    flags += ["--expires-at", "2099-01-01T00:00:00Z"]
                if strategy == "HOST_INSTALL":
                    flags += [
                        "--request-code",
                        encode_code(second_profile.request_prefix, actual_binding),
                    ]
                run_cli(
                    args.issuer_python,
                    "sign-license",
                    "--config",
                    second_config,
                    "--passphrase-file",
                    password,
                    *customer_args,
                    "--binding",
                    strategy,
                    "--validity",
                    validity,
                    "--not-before",
                    "2026-01-01T00:00:00Z",
                    "--output",
                    second_output,
                    *flags,
                )
                second_cases.append(
                    dict(
                        name=f"other-{validity}-{strategy}",
                        file=second_output.name,
                        accept=True,
                        now=now,
                    )
                )
        second_manifest = dict(
            public_key=public.name,
            key_id="cli-test",
            binding_id=actual_binding.hex(),
            cases=second_cases,
        )
        check_both(directory, second_manifest, classpath, product=second_profile.product_id)
        wrong_product = copy.deepcopy(second_manifest)
        for case in wrong_product["cases"]:
            case["accept"] = False
        check_both(directory, wrong_product, classpath, product="icp-diag")
        wrong_binding = copy.deepcopy(second_manifest)
        wrong_binding["binding_id"] = binding
        for case in wrong_binding["cases"]:
            case["accept"] = "UNBOUND" in case["name"]
        check_both(directory, wrong_binding, classpath, product=second_profile.product_id)
        print(
            "PASS: both SDKs accept all four other-product CLI combinations with real "
            "domain-derived binding, and reject cross-product/wrong-binding use"
        )
        direct_cases = []
        for validity in ("TERM", "PERPETUAL"):
            for strategy in ("HOST_INSTALL", "UNBOUND"):
                direct_output = directory / f"direct-{validity}-{strategy}.json"
                direct_app = IssuerApplication(
                    ICP_DIAG,
                    signer=EncryptedPemSigner("cli-test", private, password.read_bytes()),
                    records=LocalIssueRecordStore(records),
                    output=AtomicArtifactStore(direct_output),
                )
                direct_app.sign_license(
                    IssueLicenseRequest(
                        key_ref=KeyReference("cli-test"),
                        customer_id="synthetic-direct",
                        customer_name="Direct service test",
                        binding_type=strategy,
                        validity_type=validity,
                        not_before="2026-01-01T00:00:00Z",
                        expires_at="2099-01-01T00:00:00Z" if validity == "TERM" else None,
                        request_code=request_code if strategy == "HOST_INSTALL" else None,
                    )
                )
                direct_cases.append(
                    dict(
                        name=f"direct-{validity}-{strategy}",
                        file=direct_output.name,
                        accept=True,
                        now=now,
                    )
                )
        direct_manifest = dict(
            public_key=public.name,
            key_id="cli-test",
            binding_id=binding,
            cases=direct_cases,
        )
        direct_count = check_both(directory, direct_manifest, classpath)
        print(f"PASS: {direct_count} directly service-issued combinations accepted by both SDKs")
        invalid = directory / "invalid-output.json"
        run_cli(
            args.issuer_python,
            "sign-license",
            *key_args,
            *customer_args,
            "--binding",
            "UNBOUND",
            "--validity",
            "PERPETUAL",
            "--not-before",
            "2026-01-01T00:00:00Z",
            "--request-code",
            request_code,
            "--output",
            invalid,
            success=False,
        )
        if invalid.exists():
            raise AssertionError("invalid request created a license output")
        print(
            f"PASS: {count} CLI-signed combinations accepted by both SDKs; "
            "file/manual equivalence, retry stability and existing-output protection passed"
        )


if __name__ == "__main__":
    main()
