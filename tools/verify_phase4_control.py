"""Independent CONTROL JWS oracle and cross-language conformance probe; synthetic keys only."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def encoded(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode()


def main() -> None:
    from license_sdk.proofs import ICP_CONTROL, ControlProof, ControlProofError

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--classpath-file",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "java/license-spring/build/runtime-classpath.txt",
    )
    classpath = parser.parse_args().classpath_file.read_text().strip()
    master = bytes(range(32))
    derived = hmac.digest(master, b"icp-diag/control/v1", "sha256")
    installation = "11223344-5566-4788-99aa-bbccddeeff00"
    start = datetime(2026, 10, 21, tzinfo=UTC)
    epoch = int(start.timestamp())
    body = b'{"synthetic":true}'
    scope, method, path = "LICENSE_VALIDATE", "POST", "/internal/license/validate"
    header = {"alg": "HS256", "typ": "JWT"}
    base = dict(
        iss="icp-diag/metor",
        aud="icp-diag/ai-internal",
        installation_id=installation,
        purpose="CONTROL",
        scope=scope,
        iat=epoch,
        exp=epoch + 60,
        jti="22334455-6677-4899-aabb-ccddeeff0011",
        http_method=method,
        http_path=path,
        body_sha256=hashlib.sha256(body).hexdigest(),
    )

    def signed(claims=None, *, raw=None, head=None, key=derived):
        parts = (
            b64(head if head is not None else encoded(header)),
            b64(raw if raw is not None else encoded(claims if claims is not None else base)),
        )
        signing = ".".join(parts)
        return signing + "." + b64(hmac.digest(key, signing.encode(), "sha256"))

    cases = []

    def add(name, *, valid=False, patch=None, token=None, offset=10, actual_body=body):
        cases.append((name, token or signed({**base, **(patch or {})}), offset, actual_body, valid))

    add("valid-control", valid=True)
    add("same-body-network-retry", valid=True)
    add("clock-skew-lower-bound", valid=True, offset=-5)
    add("before-lower-bound", offset=-5.000001)
    add("before-expiry-plus-skew", valid=True, offset=64.999999)
    add("exact-expiry-plus-skew", offset=65)
    add("after-expiry-plus-skew", offset=66)
    add("one-second-window", valid=True, patch={"exp": epoch + 1}, offset=0)
    add("negative-time-claims", patch={"iat": -1, "exp": 59}, offset=-epoch)
    add(
        "payload-whitespace-within-limit", valid=True, token=signed(raw=encoded(base) + b" " * 3000)
    )
    add("header-size-limit", token=signed(head=encoded(header) + b" " * 512))
    add("payload-size-limit", token=signed(raw=encoded(base) + b" " * 4096))
    too_large_body = b"x" * 65537
    add(
        "actual-body-size-limit",
        patch={"body_sha256": hashlib.sha256(too_large_body).hexdigest()},
        actual_body=too_large_body,
    )
    for field in base:
        value = base.copy()
        del value[field]
        add("missing-" + field, token=signed(value))
    for name, patch in (
        ("wrong-issuer", {"iss": "other/metor"}),
        ("wrong-audience", {"aud": "other/ai-internal"}),
        ("audience-array", {"aud": [base["aud"]]}),
        ("wrong-installation", {"installation_id": "11223344-5566-4788-99aa-bbccddeeff01"}),
        ("wrong-purpose", {"purpose": "TASK"}),
        ("wrong-scope", {"scope": "LICENSE_STATUS"}),
        ("wrong-method", {"http_method": "GET"}),
        ("wrong-path", {"http_path": "/internal/license/status"}),
        ("wrong-body-digest", {"body_sha256": "0" * 64}),
        ("uppercase-digest", {"body_sha256": base["body_sha256"].upper()}),
        ("noncanonical-jti", {"jti": "22334455-6677-4899-AABB-CCDDEEFF0011"}),
        ("wrong-version-jti", {"jti": "22334455-6677-1899-aabb-ccddeeff0011"}),
        ("wrong-variant-jti", {"jti": "22334455-6677-4899-7abb-ccddeeff0011"}),
        ("null-jti", {"jti": None}),
        ("boolean-iat", {"iat": True}),
        ("float-exp", {"exp": float(epoch + 60)}),
        ("string-iat", {"iat": str(epoch)}),
        ("window-too-long", {"exp": epoch + 61}),
        ("empty-window", {"exp": epoch}),
        ("negative-window", {"exp": epoch - 1}),
        ("oversized-integer", {"exp": 1 << 64}),
        ("unknown-field", {"unexpected": 1}),
    ):
        add(name, patch=patch)
    for name, value in (
        ("algorithm-none", {"alg": "none", "typ": "JWT"}),
        ("algorithm-hs384", {"alg": "HS384", "typ": "JWT"}),
        ("wrong-type-header", {"alg": "HS256", "typ": "JWS"}),
        ("external-key-header", {**header, "jku": "https://invalid.example/key"}),
        ("key-selector-header", {**header, "kid": "other"}),
    ):
        add(name, token=signed(head=encoded(value)))
    raw = encoded(base)
    add("duplicate-claim", token=signed(raw=b'{"iss":"icp-diag/metor",' + raw[1:]))
    add("escaped-duplicate-claim", token=signed(raw=b'{"i\\u0073s":"icp-diag/metor",' + raw[1:]))
    add("duplicate-header", token=signed(head=b'{"alg":"HS256","alg":"HS256","typ":"JWT"}'))
    add("payload-bom", token=signed(raw=b"\xef\xbb\xbf" + raw))
    add("payload-invalid-utf8", token=signed(raw=raw.replace(b"CONTROL", b"\xff")))
    add("foreign-product-key", token=signed(key=hmac.digest(master, b"other/control/v1", "sha256")))
    add("task-purpose-key", token=signed(key=hmac.digest(master, b"icp-diag/task/v1", "sha256")))
    add("actual-body-modified", actual_body=body + b" ")
    good = signed()
    parts = good.split(".")
    add("padded-signature", token=good + "=")
    add("padded-payload", token=parts[0] + "." + parts[1] + "=." + parts[2])
    add("signature-tampered", token=good[:-4] + ("A" if good[-4] != "A" else "B") + good[-3:])
    add("token-too-large", token="a" * 8193)

    java = r"""
import io.metor.license.spring.ControlProofCodec;
import java.nio.file.*;
import java.time.*;
import java.util.*;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
public class Phase4ControlProbe {
  public static void main(String[] args) throws Exception {
    byte[] master = new byte[32];
    for (int i=0; i<32; i++) master[i]=(byte)i;
    Mac mac=Mac.getInstance("HmacSHA256"); mac.init(new SecretKeySpec(master,"HmacSHA256"));
    byte[] key=mac.doFinal("icp-diag/control/v1".getBytes(java.nio.charset.StandardCharsets.UTF_8));
    var codec=new ControlProofCodec("icp-diag/metor","icp-diag/ai-internal");
    UUID installation=UUID.fromString("11223344-5566-4788-99aa-bbccddeeff00");
    for (String line : Files.readAllLines(Path.of(args[0]))) {
      String[] p=line.split("\t");
      try {
        codec.verify(Files.readString(Path.of(p[0])),key,installation,"LICENSE_VALIDATE",
          "POST","/internal/license/validate",Files.readAllBytes(Path.of(p[1])),Instant.parse(p[2]));
        System.out.println("VALID");
      } catch (IllegalArgumentException e) { System.out.println("INVALID"); }
    }
    String token=codec.sign(key,installation,"LICENSE_VALIDATE","POST","/internal/license/validate",
      "{\"synthetic\":true}".getBytes(),Instant.parse("2026-10-21T00:00:00Z"),
      UUID.fromString("22334455-6677-4899-aabb-ccddeeff0011"));
    Files.writeString(Path.of(args[1]),token);
  }
}
"""
    proof = ControlProof(config=ICP_CONTROL, service_key=master, installation_id=installation)
    python_token = proof.issue(scope=scope, method=method, path=path, body=body, now=start)
    add("python-issued", valid=True, token=python_token)
    with tempfile.TemporaryDirectory(prefix="phase4-control-oracle-") as temporary:
        root = Path(temporary).resolve()
        (root / "Phase4ControlProbe.java").write_text(java)
        manifest = []
        for i, (name, token, offset, actual_body, expected) in enumerate(cases):
            when = start + timedelta(seconds=offset)
            (root / f"{i}.token").write_text(token)
            (root / f"{i}.body").write_bytes(actual_body)
            instant = when.isoformat().replace("+00:00", "Z")
            manifest.append(f"{root}/{i}.token\t{root}/{i}.body\t{instant}")
            try:
                proof.verify(
                    token, scope=scope, method=method, path=path, body=actual_body, now=when
                )
                accepted = True
            except ControlProofError:
                accepted = False
            assert accepted == expected, ("Python", name, accepted, expected)
        (root / "cases.tsv").write_text("\n".join(manifest))
        subprocess.run(
            ["javac", "-cp", classpath, str(root / "Phase4ControlProbe.java")], check=True
        )
        results = subprocess.check_output(
            [
                "java",
                "-cp",
                f"{classpath}:{root}",
                "Phase4ControlProbe",
                str(root / "cases.tsv"),
                str(root / "java.token"),
            ],
            text=True,
        ).splitlines()
        assert len(results) == len(cases)
        for (name, _, _, _, expected), result in zip(cases, results, strict=True):
            assert result == ("VALID" if expected else "INVALID"), ("Java", name, result, expected)
        java_token = (root / "java.token").read_text()
        proof.verify(java_token, scope=scope, method=method, path=path, body=body, now=start)
        for language, token in (("Java", java_token), ("Python", python_token)):
            first, second, signature = token.split(".")
            assert (
                b64(hmac.digest(derived, (first + "." + second).encode(), "sha256")) == signature
            ), language
        print(f"PASS: {len(cases)} independent CONTROL proof cases agree in Java/Python")
        print("PASS: actual Java/Python issuance interoperates and matches standard-library HMAC")


if __name__ == "__main__":
    main()
