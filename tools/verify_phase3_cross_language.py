"""Independent standard-library clock oracle versus the published Java/Python codecs."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import subprocess
import tempfile
from pathlib import Path


def verify_runtime(jar: Path, root: Path, canonical_seed: dict, master: bytes) -> None:
    from datetime import UTC, datetime, timedelta

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from license_sdk import ICP_DIAG
    from license_sdk.runtime import BuildMode, LicenseRuntime, RuntimePaths

    class Clock:
        wall = datetime(2026, 10, 21, tzinfo=UTC)
        mono = 0

        def wall_now(self):
            return self.wall

        def monotonic_ns(self):
            return self.mono

    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    (root / "public.der").write_bytes(public)
    java = r"""
import io.metor.license.core.*;
import java.nio.file.*;
import java.time.*;
import java.security.*;
import java.security.spec.*;
import java.io.*;
import java.util.*;
public class Phase3RuntimeProbe {
  static class Clock implements TimeSource {
    Instant wall; long mono;
    public Instant utcNow() { return wall; }
    public long monotonicNanos() { return mono; }
  }
  public static void main(String[] args) throws Exception {
    Path root = Path.of(args[0]);
    var key = KeyFactory.getInstance("Ed25519").generatePublic(
        new X509EncodedKeySpec(Files.readAllBytes(Path.of(args[1]))));
    var config = new LicenseRuntime.Config("icp-diag", "metor",
        "icp-diag/host-fingerprint/v1", "icp-diag/host-install-code/v1",
        "icp-diag/recovery-target/v1", root.resolve("identity/installation.json"),
        root.resolve("identity/service.key"), root.resolve("shared/active.license.json"),
        root.resolve("state/metor/clock.json"), root.resolve("no-machine-id"));
    Clock clock = new Clock();
    var runtime = new LicenseRuntime(config, Map.of("oracle", key), clock);
    var input = new BufferedReader(new InputStreamReader(System.in));
    for (String line; (line = input.readLine()) != null;) {
      String[] p = line.split("\t");
      clock.wall = Instant.parse(p[0]); clock.mono = Long.parseLong(p[1]);
      var result = p[2].equals("true") ? runtime.checkpoint() : runtime.inspect();
      System.out.println(result.state().name());
      System.out.flush();
    }
  }
}
"""
    (root / "Phase3RuntimeProbe.java").write_text(java)
    subprocess.run(["javac", "-cp", str(jar), str(root / "Phase3RuntimeProbe.java")], check=True)
    start = Clock.wall

    def signed(*, future=False, perpetual=False) -> bytes:
        data = dict(
            schema_version=1,
            key_id="oracle",
            kind="commercial-license",
            license_id="11223344-5566-4788-99aa-bbccddeeff00",
            customer_id="synthetic",
            customer_name="合成测试客户",
            product_id="icp-diag",
            binding_type="UNBOUND",
            binding_scheme=None,
            binding_id=None,
            validity_type="PERPETUAL" if perpetual else "TERM",
            issued_at="2026-10-21T00:00:00Z",
            not_before="2026-10-22T00:00:00Z" if future else "2026-10-21T00:00:00Z",
            expires_at=None if perpetual else "2026-10-21T00:00:20Z",
        )
        if future and not perpetual:
            data["expires_at"] = "2026-10-23T00:00:00Z"
        payload = canonical(data)
        return canonical(dict(payload=b64(payload), signature=b64(key.sign(payload))))

    count = 0

    def run_scenario(scenario):
        roots = [root / scenario / language for language in ("python", "java")]
        for target in roots:
            for directory in ("identity", "shared", "state/metor"):
                (target / directory).mkdir(parents=True, exist_ok=True)
            (target / "identity/installation.json").write_bytes(
                canonical(
                    dict(
                        schema_version=1,
                        product_id="icp-diag",
                        installation_id=canonical_seed["installation_id"],
                        initialized=True,
                    )
                )
            )
            (target / "identity/service.key").write_bytes(master)
            derived = hmac.digest(master, b"icp-diag/state/metor/v1", "sha256")
            raw = canonical(canonical_seed)
            (target / "state/metor/clock.json").write_bytes(
                canonical(dict(payload=b64(raw), mac=b64(hmac.digest(derived, raw, "sha256"))))
            )
            (target / "shared/active.license.json").write_bytes(
                signed(future=scenario == "future", perpetual=scenario != "expiry")
            )
        clock = Clock()
        runtime = LicenseRuntime(
            profile=ICP_DIAG,
            service="metor",
            paths=RuntimePaths(roots[0], roots[0] / "no-machine-id"),
            trusted_keys={"oracle": public},
            mode=BuildMode.CUSTOMER_REQUIRED,
            time_source=clock,
        )
        process = subprocess.Popen(
            [
                "java",
                "-cp",
                f"{jar}:{root}",
                "Phase3RuntimeProbe",
                str(roots[1]),
                str(root / "public.der"),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )

        def check(wall, mono, expected, force=False):
            nonlocal count
            clock.wall, clock.mono = start + timedelta(seconds=wall), int(mono * 1_000_000_000)
            actual = (runtime.checkpoint() if force else runtime.status(read_only=True)).state.value
            assert actual == expected, (scenario, "Python", actual, expected)
            assert process.stdin is not None and process.stdout is not None
            instant = clock.wall.isoformat().replace("+00:00", "Z")
            process.stdin.write(f"{instant}\t{clock.mono}\t{str(force).lower()}\n")
            process.stdin.flush()
            actual_java = process.stdout.readline().strip()
            assert actual_java == expected, (scenario, "Java", actual_java, expected)
            count += 1

        def mutate(relative, content):
            for target in roots:
                path = target / relative
                if content is None:
                    path.unlink()
                else:
                    path.write_bytes(content)

        try:
            check(0, 0, "NOT_YET_VALID" if scenario == "future" else "ACTIVE")
            if scenario == "expiry":
                check(19.999999, 19.999999, "ACTIVE")
                check(20, 20, "EXPIRED")
            elif scenario == "boundary":
                check(-86399, 0, "ACTIVE")
                check(-86400, 0, "ACTIVE")
                check(-86401, 0, "CLOCK_ROLLBACK")
                check(1, 1, "ACTIVE")
            elif scenario == "cumulative":
                check(-14_400, 14_400, "ACTIVE")
                check(-28_800, 28_800, "ACTIVE")
                check(-43_201, 43_200, "CLOCK_ROLLBACK")
            elif scenario in ("damage", "future"):
                mutate("state/metor/clock.json", b"{}")
                check(0, 1, "CLOCK_STATE_INVALID")
                if scenario == "future":
                    assert runtime.status().mode == "RESTRICTED"
                mutate("shared/active.license.json", None)
                check(0, 2, "UNLICENSED")
            elif scenario == "cache":
                mutate("shared/active.license.json", b"{}")
                check(0, 1, "INVALID")
                mutate("shared/active.license.json", None)
                check(0, 2, "UNLICENSED")
            else:
                check(40, 40, "ACTIVE", force=True)
                # A fresh process must use the durable watermark, not reconstruct an empty seed.
                for target in roots:
                    decoded = json.loads((target / "state/metor/clock.json").read_bytes())
                    raw = base64.urlsafe_b64decode(decoded["payload"] + "==")
                    assert json.loads(raw)["high_watermark"] == "2026-10-21T00:00:40Z"
                assert process.stdin is not None
                process.stdin.close()
                process.wait(timeout=10)
                assert process.returncode == 0
                process = subprocess.Popen(
                    [
                        "java",
                        "-cp",
                        f"{jar}:{root}",
                        "Phase3RuntimeProbe",
                        str(roots[1]),
                        str(root / "public.der"),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    text=True,
                )
                runtime = LicenseRuntime(
                    profile=ICP_DIAG,
                    service="metor",
                    paths=RuntimePaths(roots[0], roots[0] / "no-machine-id"),
                    trusted_keys={"oracle": public},
                    mode=BuildMode.CUSTOMER_REQUIRED,
                    time_source=clock,
                )
                check(-86_361, 0, "CLOCK_ROLLBACK")
        finally:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            process.wait(timeout=10)
            assert process.returncode == 0

    for scenario in ("expiry", "boundary", "cumulative", "damage", "future", "cache", "restart"):
        run_scenario(scenario)
    print(f"PASS: {count} independent runtime transitions agree in Java/Python without machine-id")


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def main() -> None:
    from license_sdk.runtime import decode_clock, encode_clock

    sdk = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--java-jar", type=Path, default=sdk / "java/license-core/build/libs/license-core-0.7.0.jar"
    )
    jar = parser.parse_args().java_jar.resolve()
    master = bytes(range(32))  # Synthetic installation key, never production trust.
    installation = "11223344-5566-4788-99aa-bbccddeeff00"
    cases: list[tuple[str, bytes, str, bool]] = []
    seed = dict(
        schema_version=1,
        product_id="icp-diag",
        installation_id=installation,
        service="metor",
        epoch=0,
        sequence=0,
        high_watermark="2026-10-21T00:00:00Z",
        last_recovery_id=None,
    )

    def envelope(payload: bytes, service: str = "metor") -> bytes:
        derived = hmac.digest(master, f"icp-diag/state/{service}/v1".encode(), "sha256")
        return canonical(
            dict(payload=b64(payload), mac=b64(hmac.digest(derived, payload, "sha256")))
        )

    def add(name: str, data: dict, *, valid: bool = False, service: str = "metor") -> None:
        cases.append((name, envelope(canonical(data), service), service, valid))

    for service in ("metor", "ai"):
        add(service + "-seed", {**seed, "service": service}, valid=True, service=service)
    add("signed-64-counter-limit", {**seed, "sequence": (1 << 63) - 1}, valid=True)
    add(
        "recovery-metadata",
        {**seed, "epoch": 4, "sequence": 12, "last_recovery_id": installation},
        valid=True,
    )
    cases.append(
        ("pretty-original-payload", envelope(json.dumps(seed, indent=2).encode()), "metor", True)
    )
    for field in seed:
        data = seed.copy()
        del data[field]
        add("missing-" + field, data)
    for name, patch in (
        ("schema-bool", {"schema_version": True}),
        ("schema-float", {"schema_version": 1.0}),
        ("schema-unknown", {"schema_version": 2}),
        ("wrong-product", {"product_id": "other-product"}),
        ("wrong-installation", {"installation_id": "11223344-5566-4788-99aa-bbccddeeff01"}),
        ("uppercase-installation", {"installation_id": installation.upper()}),
        ("wrong-service", {"service": "ai"}),
        ("negative-epoch", {"epoch": -1}),
        ("boolean-epoch", {"epoch": False}),
        ("float-sequence", {"sequence": 1.0}),
        ("counter-overflow", {"sequence": 1 << 63}),
        ("string-sequence", {"sequence": "1"}),
        ("null-sequence", {"sequence": None}),
        ("unknown-field", {"extra": 1}),
        ("bad-recovery-id", {"last_recovery_id": "unknown"}),
        ("bad-recovery-type", {"last_recovery_id": True}),
        ("bad-day", {"high_watermark": "2026-02-30T00:00:00Z"}),
        ("year-zero", {"high_watermark": "0000-01-01T00:00:00Z"}),
        ("offset", {"high_watermark": "2026-10-21T00:00:00+00:00"}),
        ("fractional", {"high_watermark": "2026-10-21T00:00:00.0Z"}),
    ):
        add(name, {**seed, **patch})
    raw = canonical(seed)
    cases.append(
        (
            "negative-zero-is-integer-zero",
            envelope(raw.replace(b'"sequence":0', b'"sequence":-0')),
            "metor",
            True,
        )
    )
    for name, value in (
        ("duplicate-payload", b'{"schema_version":1,' + raw[1:]),
        ("escaped-duplicate", b'{"schema_versi\\u006fn":1,' + raw[1:]),
        ("payload-bom", b"\xef\xbb\xbf" + raw),
        ("trailing-root", raw + b"{}"),
        ("payload-invalid-utf8", raw.replace(b"metor", b"\xff")),
        ("exponent-counter", raw.replace(b'"sequence":0', b'"sequence":0e0')),
        ("oversize-payload", raw + b" " * 4096),
    ):
        cases.append((name, envelope(value), "metor", False))
    good = envelope(raw)
    outer = json.loads(good)
    for name, value in (
        ("extra-envelope-field", canonical({**outer, "extra": 1})),
        ("duplicate-envelope", b'{"payload":"x",' + good[1:]),
        ("padded-payload", canonical({**outer, "payload": outer["payload"] + "="})),
        ("padded-mac", canonical({**outer, "mac": outer["mac"] + "="})),
        ("wrong-mac", canonical({**outer, "mac": b64(bytes(32))})),
        ("oversize-envelope", good + b" " * 65536),
        ("envelope-bom", b"\xef\xbb\xbf" + good),
    ):
        cases.append((name, value, "metor", False))

    java = r"""
import io.metor.license.core.*;
import java.nio.file.*;
import java.util.*;
public class Phase3ClockProbe {
  public static void main(String[] args) throws Exception {
    byte[] key = new byte[32];
    for (int i = 0; i < 32; i++) key[i] = (byte)i;
    for (String line : Files.readAllLines(Path.of(args[0]))) {
      String[] part = line.split("\t");
      try {
        var value = ClockStateCodec.decode(Files.readAllBytes(Path.of(part[0])), key,
            "icp-diag", UUID.fromString("11223344-5566-4788-99aa-bbccddeeff00"), part[1]);
        System.out.println(Base64.getEncoder().encodeToString(ClockStateCodec.encode(value, key)));
      } catch (IllegalArgumentException e) { System.out.println("INVALID"); }
    }
  }
}
"""
    with tempfile.TemporaryDirectory(prefix="license-phase3-oracle-") as temporary:
        root = Path(temporary).resolve()
        (root / "Phase3ClockProbe.java").write_text(java)
        manifest = []
        for i, (_, encoded, service, _) in enumerate(cases):
            path = root / f"case-{i}.json"
            path.write_bytes(encoded)
            manifest.append(f"{path}\t{service}")
        (root / "cases.tsv").write_text("\n".join(manifest))
        subprocess.run(["javac", "-cp", str(jar), str(root / "Phase3ClockProbe.java")], check=True)
        output = subprocess.check_output(
            ["java", "-cp", f"{jar}:{root}", "Phase3ClockProbe", str(root / "cases.tsv")],
            text=True,
        ).splitlines()
        assert len(output) == len(cases)
        for (name, encoded, service, valid), java_result in zip(cases, output, strict=True):
            keywords = dict(
                product_id="icp-diag",
                installation_id=installation,
                service=service,
                service_key=master,
            )
            try:
                clock = decode_clock(encoded, **keywords)
                normalized = encode_clock(clock, **keywords)
                python_result = base64.b64encode(normalized).decode()
                assert valid, f"Python accepted {name}"
                # Independent stdlib oracle checks canonical output and authenticated payload bytes.
                parsed = json.loads(encoded)
                original = base64.urlsafe_b64decode(parsed["payload"] + "==")
                expected = envelope(canonical(json.loads(original)), service)
                assert normalized == expected, f"noncanonical Python output: {name}"
            except (ValueError, TypeError, OverflowError):
                python_result = "INVALID"
                assert not valid, f"Python rejected {name}"
            assert java_result == python_result, f"Java/Python disagree: {name}"
        print(
            f"PASS: {len(cases)} independent clock cases; "
            "Java/Python decisions and canonical bytes agree"
        )
        print(f"Seed SHA-256 (metor): {hashlib.sha256(cases[0][1]).hexdigest()}")
        verify_runtime(jar, root, seed, master)


if __name__ == "__main__":
    main()
