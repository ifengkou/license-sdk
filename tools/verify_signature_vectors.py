"""Check test-only signed fixtures with the JDK, without SDK or crypto packages."""

from __future__ import annotations

import base64
import json
import subprocess
import tempfile
from pathlib import Path

JAVA_PROBE = """
import java.nio.file.*;
import java.security.*;
import java.security.spec.*;
import java.util.*;

public class VerifyFixtures {
    public static void main(String[] args) throws Exception {
        List<String> lines = Files.readAllLines(Path.of(args[0]));
        PublicKey key = KeyFactory.getInstance("Ed25519").generatePublic(
            new X509EncodedKeySpec(Base64.getDecoder().decode(lines.get(0))));
        for (String line : lines.subList(1, lines.size())) {
            String[] pair = line.split(" ");
            byte[] payload = Base64.getUrlDecoder().decode(pair[0]);
            byte[] signature = Base64.getUrlDecoder().decode(pair[1]);
            Signature verifier = Signature.getInstance("Ed25519");
            verifier.initVerify(key);
            verifier.update(payload);
            if (!verifier.verify(signature)) throw new AssertionError("Invalid fixture signature");
            payload[0] ^= 1;
            verifier.initVerify(key);
            verifier.update(payload);
            if (verifier.verify(signature)) throw new AssertionError("Tampered payload accepted");
        }
        System.out.println("PASS: " + (lines.size() - 1) + " test signatures and tamper rejections");
    }
}
"""


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    fixtures = json.loads(
        (root / "protocol/test-fixtures/commercial-v1-test-vectors.json").read_text()
    )
    if fixtures["purpose"] != "synthetic-test-trust-only":
        raise ValueError("expected test-only fixtures")
    combinations: set[tuple[str, str]] = set()
    lines = [fixtures["public_key_spki_base64"]]
    for case in fixtures["cases"]:
        envelope = case["envelope"]
        raw = base64.urlsafe_b64decode(
            envelope["payload"] + "=" * (-len(envelope["payload"]) % 4)
        )
        payload = json.loads(raw)
        if (
            payload["key_id"] != "phase1-test-only"
            or payload["product_id"] != "icp-diag"
        ):
            raise ValueError("unexpected fixture identity")
        validity, binding = payload["validity_type"], payload["binding_type"]
        if (validity == "PERPETUAL") != (payload["expires_at"] is None):
            raise ValueError("invalid fixture validity")
        if (binding == "UNBOUND") != (
            payload["binding_scheme"] is None and payload["binding_id"] is None
        ):
            raise ValueError("invalid fixture binding")
        combinations.add((validity, binding))
        lines.append(envelope["payload"] + " " + envelope["signature"])
    expected = {
        (v, b) for v in ("TERM", "PERPETUAL") for b in ("HOST_INSTALL", "UNBOUND")
    }
    if len(lines) != 5 or combinations != expected:
        raise ValueError("expected exactly four license combinations")
    with tempfile.TemporaryDirectory(prefix="license-signature-probe-") as directory:
        temporary = Path(directory)
        source = temporary / "VerifyFixtures.java"
        source.write_text(JAVA_PROBE)
        data = temporary / "fixtures.txt"
        data.write_text("\n".join(lines) + "\n")
        subprocess.run(["javac", str(source)], check=True, cwd=temporary)
        subprocess.run(
            ["java", "-cp", str(temporary), "VerifyFixtures", str(data)],
            check=True,
            cwd=temporary,
        )


if __name__ == "__main__":
    main()
