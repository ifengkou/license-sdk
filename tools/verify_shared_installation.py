"""Synthetic Linux multi-UID publication and read-only installation checks.

Run as root with an installed Python SDK, JDK 21 and a built core JAR. No Issuer,
business application, network or customer data is used. Re-run --check-only with
the generated work directory bind-mounted read-only to verify a real RO mount.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import select
import shutil
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from license_sdk import ProductConfig, ProductProfile
from license_sdk.installation import initialize_installation

PUBLISHER, READER, GROUP = 21001, 21002, 21000
JAVA_FAULT = """
package io.metor.license.core;
import java.nio.file.*;
public final class SharedPublishFault {
    public static void main(String[] args) throws Exception {
        if (args[0].equals("lock")) {
            new OnboardingFiles().withLock(Path.of(args[1]), () -> {
                System.out.println("READY"); System.out.flush(); System.in.read(); return null;
            });
            return;
        }
        var runtime = LicenseRuntime.fromConfig(Files.readAllBytes(Path.of(args[0])),
            Path.of(args[1]), "publisher");
        var files = new OnboardingFiles() {
            @Override void move(Path source, Path target, boolean replace)
                    throws java.io.IOException {
                if (!target.getFileName().toString().equals("active.license.json"))
                    throw new AssertionError("Unexpected publish target");
                if ((int)Files.getAttribute(source,"unix:gid") != 21000
                    || !Files.getPosixFilePermissions(source).equals(
                        java.nio.file.attribute.PosixFilePermissions.fromString("rw-r-----")))
                    throw new AssertionError("Temporary file lacks shared attributes");
                if (args[3].equals("before")) java.lang.Runtime.getRuntime().halt(71);
                super.move(source, target, replace);
                java.lang.Runtime.getRuntime().halt(72);
            }
        };
        runtime.importLicense(Files.readAllBytes(Path.of(args[2])),
            runtime.licenseFileDigest(), "21000", files);
        throw new AssertionError("Expected process interruption");
    }
}
"""
PYTHON_FAULT = """
import os, stat, sys
from pathlib import Path
from license_sdk.runtime import LicenseRuntime
runtime = LicenseRuntime.from_config(Path(sys.argv[1]).read_bytes(),
    root=Path(sys.argv[2]), service='publisher')
replace = os.replace
def interrupted(source, target):
    details = os.stat(source, follow_symlinks=False)
    assert details.st_gid == 21000 and stat.S_IMODE(details.st_mode) == 0o640
    if sys.argv[4] == 'before': os._exit(71)
    replace(source, target)
    os._exit(72)
os.replace = interrupted
runtime.import_license(Path(sys.argv[3]).read_bytes(), runtime.license_file_digest(),
    shared_group=21000)
raise AssertionError('Expected process interruption')
"""
PYTHON_LOCK = """
import sys
from pathlib import Path
from license_sdk.installation import record_lock
with record_lock(Path(sys.argv[1])):
    print('READY', flush=True)
    sys.stdin.read(1)
"""
READER_LOOP = """
import hashlib, json, os, sys, time
from pathlib import Path
path, allowed, stop = map(Path, sys.argv[1:])
digests = set(json.loads(allowed.read_bytes()))
assert os.geteuid() == 21002 and 21000 in os.getgroups()
print('READY', flush=True)
count = 0
while not stop.exists():
    with path.open('rb') as stream:
        raw = stream.read(65537)
        details = os.fstat(stream.fileno())
        assert details.st_gid == 21000 and details.st_mode & 0o777 == 0o640
    assert 1 <= len(raw) <= 65536
    assert hashlib.sha256(raw).hexdigest() in digests
    count += 1
    time.sleep(0.002)
print(count, flush=True)
"""


def credentials(uid: int) -> None:
    os.setgroups([GROUP])
    os.setgid(uid)
    os.setuid(uid)


def run(
    command: list[str], uid: int | None = None, expected: int = 0
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=60,
        preexec_fn=None if uid is None else lambda: credentials(uid),
    )
    assert result.returncode == expected, (command, result.returncode, result.stdout, result.stderr)
    return result


def operation(language: str, jar: Path, action: str, config: Path, root: Path) -> list[str]:
    base = (
        [sys.executable, "-m", "license_sdk.cli"]
        if language == "python"
        else [java_tool("java"), "-jar", str(jar)]
    )
    return [*base, action, "--config", str(config), "--root", str(root)]


def java_tool(name: str) -> str:
    java_home = os.environ.get("JAVA_HOME")
    return str(Path(java_home) / "bin" / name) if java_home else name


def snapshot(root: Path) -> dict[str, tuple]:
    result = {}
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        result[str(path.relative_to(root))] = (
            info.st_uid,
            info.st_gid,
            stat.S_IMODE(info.st_mode),
            info.st_ino,
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
        )
    return result


def check_readonly(work: Path, jar: Path) -> None:
    root, config = work / "readonly-installation", work / "product.json"
    assert root.is_dir() and not list(root.rglob("*.lock"))
    before = snapshot(root)
    ids = []
    for language in ("python", "java"):
        result = run(operation(language, jar, "check", config, root))
        value = json.loads(result.stdout)
        ids.append(value["installationId"])
        assert snapshot(root) == before
    assert len(set(ids)) == 1
    print(json.dumps({"readonlyCheck": "passed", "installationId": ids[0]}))


def make_license(key: Ed25519PrivateKey) -> bytes:
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = json.dumps(
        dict(
            schema_version=1,
            key_id="synthetic",
            kind="commercial-license",
            license_id=str(uuid4()),
            customer_id="synthetic",
            customer_name="Synthetic",
            product_id="shared-test",
            binding_type="UNBOUND",
            binding_scheme=None,
            binding_id=None,
            validity_type="PERPETUAL",
            issued_at=now,
            not_before=now,
            expires_at=None,
        )
    ).encode()
    return json.dumps(
        {
            "payload": base64.urlsafe_b64encode(payload).decode().rstrip("="),
            "signature": base64.urlsafe_b64encode(key.sign(payload)).decode().rstrip("="),
        }
    ).encode()


def exercise(work: Path, jar: Path) -> None:
    assert sys.platform == "linux" and os.geteuid() == 0, "Linux root required to test real UIDs"
    assert not work.exists() or not any(work.iterdir()), "work directory must be empty"
    work.mkdir(parents=True, exist_ok=True)
    work.chmod(0o755)
    key = Ed25519PrivateKey.generate()
    config = ProductConfig(
        ProductProfile(
            "shared-test",
            b"shared-test/fp/v1",
            b"shared-test/binding/v1",
            b"shared-test/recovery/v1",
            "ST1",
            (("publisher", "ST1P"), ("reader", "ST1R")),
        ),
        {
            "synthetic": key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        },
    )
    config_file = work / "product.json"
    config_file.write_bytes(config.to_bytes())
    config_file.chmod(0o644)
    root = work / "installation"
    initialize_installation(config, root)
    for path in (root, root / "identity", root / "state"):
        os.chown(path, 0, GROUP)
        path.chmod(0o750)
    for path in (root / "identity").iterdir():
        os.chown(path, 0, GROUP)
        path.chmod(0o640)
    os.chown(root / "shared", PUBLISHER, GROUP)
    (root / "shared").chmod(0o750)
    for service, uid in (("publisher", PUBLISHER), ("reader", READER)):
        directory = root / "state" / service
        os.chown(directory, uid, uid)
        directory.chmod(0o700)
        os.chown(directory / "clock.json", uid, uid)
    candidates = []
    for index in range(10):
        path = work / f"license-{index}.json"
        path.write_bytes(make_license(key))
        path.chmod(0o644)
        candidates.append(path)
    allowed = work / "allowed.json"
    allowed.write_text(json.dumps([hashlib.sha256(p.read_bytes()).hexdigest() for p in candidates]))
    source = work / "SharedPublishFault.java"
    source.write_text(JAVA_FAULT)
    classes = work / "classes"
    classes.mkdir()
    run([java_tool("javac"), "-cp", str(jar), "-d", str(classes), str(source)])

    def publish(language: str, candidate: Path, group: str = str(GROUP), expected: int = 0):
        command = operation(language, jar, "import", config_file, root)
        return run(
            [*command, "--service", "publisher", "--file", str(candidate), "--shared-group", group],
            PUBLISHER,
            expected,
        )

    publish("python", candidates[0])
    target = root / "shared/active.license.json"
    stop = work / "stop"
    reader = subprocess.Popen(
        [sys.executable, "-c", READER_LOOP, str(target), str(allowed), str(stop)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=lambda: credentials(READER),
    )
    try:
        assert reader.stdout is not None and select.select([reader.stdout], [], [], 10)[0]
        assert reader.stdout.readline().strip() == "READY", "reader failed to start"
        for holder_language, contender in (("python", "java"), ("java", "python")):
            lock = str(root / "shared/license-import.lock")
            holder_command = (
                [sys.executable, "-c", PYTHON_LOCK, lock]
                if holder_language == "python"
                else [
                    java_tool("java"),
                    "-cp",
                    os.pathsep.join((str(jar), str(classes))),
                    "io.metor.license.core.SharedPublishFault",
                    "lock",
                    lock,
                ]
            )
            holder = subprocess.Popen(
                holder_command,
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=lambda: credentials(PUBLISHER),
            )
            try:
                assert holder.stdout is not None and select.select([holder.stdout], [], [], 10)[0]
                assert holder.stdout.readline().strip() == "READY", "lock holder failed to start"
                previous = target.read_bytes()
                result = publish(contender, candidates[1], expected=1)
                assert (
                    json.loads(result.stdout or result.stderr)["code"] == "LICENSE_STATE_CONFLICT"
                )
                assert target.read_bytes() == previous
            finally:
                output, errors = holder.communicate(input="x", timeout=10)
            assert holder.returncode == 0, (output, errors)
            released = json.loads(publish(contender, candidates[1]).stdout)
            assert released["actionStatus"] in ("APPLIED", "ALREADY_APPLIED")
        for index, language in enumerate(("java", "python", "java", "python"), 1):
            publish(language, candidates[index])
        for index, (language, stage) in enumerate(
            (("python", "before"), ("python", "after"), ("java", "before"), ("java", "after")), 5
        ):
            previous = target.read_bytes()
            args = [str(config_file), str(root), str(candidates[index]), stage]
            command = (
                [sys.executable, "-c", PYTHON_FAULT, *args]
                if language == "python"
                else [
                    java_tool("java"),
                    "-cp",
                    os.pathsep.join((str(jar), str(classes))),
                    "io.metor.license.core.SharedPublishFault",
                    *args,
                ]
            )
            run(command, PUBLISHER, 71 if stage == "before" else 72)
            assert target.read_bytes() == (
                previous if stage == "before" else candidates[index].read_bytes()
            )
            assert target.stat().st_gid == GROUP and stat.S_IMODE(target.stat().st_mode) == 0o640
        for language in ("python", "java"):
            previous = (target.read_bytes(), target.stat().st_mode, target.stat().st_gid)
            result = publish(language, candidates[9], "21099", expected=1)
            failure = json.loads(result.stdout or result.stderr)
            assert failure["code"] in ("LICENSE_STORAGE_FAILED", "STORAGE_FAILED")
            assert (target.read_bytes(), target.stat().st_mode, target.stat().st_gid) == previous
    finally:
        stop.touch()
        output, errors = reader.communicate(timeout=10)
    assert reader.returncode == 0 and int(output.strip()) > 0, (output, errors)

    readonly = work / "readonly-installation"
    shutil.copytree(root, readonly)
    for path in readonly.rglob("*.lock"):
        path.unlink()
    # Crash leftovers are irrelevant to a completed installation but are not backup input.
    for path in (readonly / "shared").glob(".license*"):
        path.unlink()
    for path in [*readonly.rglob("*"), readonly]:
        os.chown(path, 0, GROUP)
        path.chmod(0o550 if path.is_dir() else 0o440)
    check_readonly(work, jar)
    before = snapshot(readonly)
    for language in ("python", "java"):
        run(operation(language, jar, "check", config_file, readonly), READER)
    assert snapshot(readonly) == before
    print(
        json.dumps(
            {
                "linuxMultiUid": "passed",
                "publishers": ["python", "java"],
                "readerUid": READER,
                "sharedGid": GROUP,
                "reads": int(output.strip()),
                "processInterruptions": 4,
                "crossLanguageLockChecks": 2,
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java-jar", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    work, jar = args.work_dir.resolve(), args.java_jar.resolve()
    if args.check_only:
        assert os.statvfs(work).f_flag & os.ST_RDONLY, "--check-only requires a read-only mount"
        check_readonly(work, jar)
    else:
        exercise(work, jar)


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, OSError, subprocess.SubprocessError) as error:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            message = (
                str(error)[:8000].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            )
            print(f"::error title=SDK POSIX verification::{message}", flush=True)
        raise
