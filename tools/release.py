"""Build verified release inputs; never publishes or changes Git tags."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*command: str, cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def version(tag: str | None = None) -> str:
    core = tomllib.loads((ROOT / "python/pyproject.toml").read_text())["project"]["version"]
    adapter = tomllib.loads((ROOT / "python/fastapi/pyproject.toml").read_text())["project"]
    java = re.search(r"version = '([^']+)'", (ROOT / "java/build.gradle").read_text())
    protocol = json.loads((ROOT / "protocol/compatibility.json").read_bytes())["sdk_version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", core):
        raise ValueError("release version must be MAJOR.MINOR.PATCH")
    if java is None or {core, adapter["version"], java[1], protocol} != {core}:
        raise ValueError("Python core/adapter, Java and protocol versions must match")
    if f"metor-license-core=={core}" not in adapter["dependencies"]:
        raise ValueError("adapter must require the same core version")
    if tag is not None and tag != f"v{core}":
        raise ValueError(f"tag must be v{core}")
    return core


def checksums(directory: Path) -> None:
    files = sorted(p for p in directory.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
    (directory / "SHA256SUMS").write_text(
        "".join(f"{sha256(p)}  {p.relative_to(directory).as_posix()}\n" for p in files)
    )


def maven_paths(release: str) -> list[Path]:
    return [
        Path("io/metor/license")
        / f"license-{module}"
        / release
        / f"license-{module}-{release}{suffix}"
        for module in ("core", "spring")
        for suffix in (".jar", "-sources.jar", "-javadoc.jar", ".pom", ".module")
    ]


def validate_maven(files: dict[Path, bytes], release: str) -> None:
    if set(files) != set(maven_paths(release)):
        raise ValueError("Maven repository must contain both complete SDK publications")
    ns = {"m": "http://maven.apache.org/POM/4.0.0"}
    for module in ("core", "spring"):
        artifact = f"license-{module}"
        base = Path("io/metor/license") / artifact / release / f"{artifact}-{release}"
        pom = ET.fromstring(files[Path(f"{base}.pom")])
        if [
            pom.findtext(f"m:{key}", namespaces=ns) for key in ("groupId", "artifactId", "version")
        ] != ["io.metor.license", artifact, release]:
            raise ValueError("Maven POM coordinates do not match the release")
        if module == "spring" and not any(
            dependency.findtext("m:groupId", namespaces=ns) == "io.metor.license"
            and dependency.findtext("m:artifactId", namespaces=ns) == "license-core"
            and dependency.findtext("m:version", namespaces=ns) == release
            for dependency in pom.findall("m:dependencies/m:dependency", ns)
        ):
            raise ValueError("Spring POM must require the matching core version")
        metadata = json.loads(files[Path(f"{base}.module")])
        component = metadata["component"]
        if (component["group"], component["module"], component["version"]) != (
            "io.metor.license",
            artifact,
            release,
        ):
            raise ValueError("Gradle module coordinates do not match the release")
        if module == "spring":
            for name in ("apiElements", "runtimeElements"):
                variants = [variant for variant in metadata["variants"] if variant["name"] == name]
                dependencies = [
                    dependency
                    for variant in variants
                    for dependency in variant.get("dependencies", [])
                    if dependency.get("group") == "io.metor.license"
                    and dependency.get("module") == "license-core"
                ]
                if (
                    len(variants) != 1
                    or len(dependencies) != 1
                    or dependencies[0].get("version") != {"requires": release}
                ):
                    raise ValueError(
                        "Spring Gradle metadata must require the matching core version"
                    )
        for variant in metadata["variants"]:
            for entry in variant.get("files", []):
                path = base.parent / entry["name"]
                if path not in files or entry["url"] != entry["name"]:
                    raise ValueError("Gradle module references an unexpected file")
                raw = files[path]
                if entry["size"] != len(raw) or entry["sha256"] != hashlib.sha256(raw).hexdigest():
                    raise ValueError("Gradle module file hash does not match its JAR")


def maven_bundle(repository: Path, dist: Path, output: Path) -> None:
    release = version()
    name = f"license-sdk-java-maven-{release}"
    output.mkdir(parents=True, exist_ok=True)
    if (output / f"{name}.tar.gz").exists():
        raise ValueError("refusing to replace an existing Maven bundle")
    files = {}
    for relative in maven_paths(release):
        path = repository / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"missing regular Maven publication file: {relative}")
        files[relative] = path.read_bytes()
    validate_maven(files, release)
    for relative, raw in files.items():
        if relative.suffix == ".jar":
            matches = list(dist.glob(f"*/build/libs/{relative.name}"))
            if len(matches) != 1 or matches[0].read_bytes() != raw:
                raise ValueError(f"Maven and standalone JAR differ: {relative.name}")
    with tempfile.TemporaryDirectory(prefix="license-maven-") as temporary:
        bundle = Path(temporary) / name
        for relative, raw in files.items():
            target = bundle / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        (bundle / "README.txt").write_text(
            f"License SDK {release} local Maven repository. JDK 21 required.\n"
            "Use this extracted directory as your Maven/Gradle repository URL.\n"
            f"Core: io.metor.license:license-core:{release}\n"
            f"Optional CONTROL adapter: io.metor.license:license-spring:{release}\n"
            "Only SDK artifacts are bundled; prepare third-party dependencies separately.\n"
            "Verify SHA256SUMS before building. Product configuration comes from your Issuer.\n"
        )
        checksums(bundle)
        shutil.make_archive(str(output / name), "gztar", root_dir=temporary, base_dir=name)


def verify_maven_bundle(archive: Path, assets: Path, release: str) -> None:
    prefix = f"license-sdk-java-maven-{release}/"
    files: dict[Path, bytes] = {}
    with tarfile.open(archive, "r:gz") as stream:
        for member in stream:
            if member.isdir():
                continue
            if not member.isfile() or not member.name.startswith(prefix) or member.size > 16777216:
                raise ValueError("unexpected Maven archive entry")
            relative = Path(member.name[len(prefix) :])
            if relative in files or relative not in {
                *maven_paths(release),
                Path("README.txt"),
                Path("SHA256SUMS"),
            }:
                raise ValueError("unexpected or duplicate Maven archive file")
            content = stream.extractfile(member)
            if content is None:
                raise ValueError("missing Maven archive content")
            files[relative] = content.read()
    manifest = files.pop(Path("SHA256SUMS"))
    expected = "".join(
        f"{hashlib.sha256(raw).hexdigest()}  {path.as_posix()}\n"
        for path, raw in sorted(files.items())
    ).encode()
    if manifest != expected:
        raise ValueError("Maven archive internal checksums do not match")
    files.pop(Path("README.txt"))
    validate_maven(files, release)
    for path, raw in files.items():
        if path.suffix == ".jar" and (assets / path.name).read_bytes() != raw:
            raise ValueError(f"Maven and standalone JAR differ: {path.name}")


def wheelhouse(dist: Path, output: Path, offline: bool) -> None:
    release = version()
    if sys.version_info[:2] != (3, 12):
        raise ValueError("offline bundles currently target CPython 3.12")
    machine = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64"}.get(platform.machine())
    system = {"linux": "linux", "darwin": "macos"}.get(sys.platform)
    if (
        machine is None
        or system is None
        or (system == "linux" and platform.libc_ver()[0] != "glibc")
    ):
        raise ValueError("supported bundles: Linux glibc x86_64/aarch64 and macOS aarch64")
    if system == "macos" and machine != "aarch64":
        raise ValueError("macOS offline bundle currently targets aarch64")
    name = f"license-sdk-python-{release}-cp312-{system}-{machine}"
    output.mkdir(parents=True, exist_ok=True)
    if (output / f"{name}.tar.gz").exists():
        raise ValueError("refusing to replace an existing bundle; use a clean output directory")
    with tempfile.TemporaryDirectory(prefix="license-release-") as temporary:
        bundle = Path(temporary) / name
        wheels = bundle / "wheelhouse"
        wheels.mkdir(parents=True)
        core = dist / f"metor_license_core-{release}-py3-none-any.whl"
        adapter = dist / f"metor_license_fastapi-{release}-py3-none-any.whl"
        for wheel in (core, adapter):
            shutil.copy2(wheel, wheels / wheel.name)
        for package, suffix in (
            ("metor-license-core", "core"),
            ("metor-license-fastapi", "fastapi"),
        ):
            requirements = bundle / f"requirements-{suffix}.txt"
            command = [
                "uv",
                "export",
                "--quiet",
                "--locked",
                "--no-dev",
                "--package",
                package,
                "--no-emit-workspace",
                "--no-header",
                "--no-annotate",
                "--output-file",
                str(requirements),
            ]
            if offline:
                command.append("--offline")
            run(*command, cwd=ROOT / "python")
            with requirements.open("a") as stream:
                stream.write(f"metor-license-core=={release} --hash=sha256:{sha256(core)}\n")
                if suffix == "fastapi":
                    stream.write(
                        f"metor-license-fastapi=={release} --hash=sha256:{sha256(adapter)}\n"
                    )
            download = [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--only-binary=:all:",
                "--require-hashes",
                "--find-links",
                str(wheels),
                "--dest",
                str(wheels),
                "--requirement",
                str(requirements),
            ]
            if offline:
                # A local complete wheelhouse can be supplied via PIP_FIND_LINKS.
                download.append("--no-index")
            run(*download)
        (bundle / "BUILD-INFO.json").write_text(
            json.dumps(
                {
                    "sdk_version": release,
                    "python": platform.python_version(),
                    "platform": system,
                    "architecture": machine,
                    "libc": platform.libc_ver(),
                    "source_commit": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                    ).strip(),
                    "source_dirty": bool(
                        subprocess.check_output(
                            ["git", "status", "--porcelain"], cwd=ROOT, text=True
                        ).strip()
                    ),
                    "lock_sha256": sha256(ROOT / "python/uv.lock"),
                },
                indent=2,
            )
            + "\n"
        )
        (bundle / "README.txt").write_text(
            "Requires preinstalled CPython 3.12 and venv/pip for this OS/architecture.\n"
            "Run inside this extracted directory:\n"
            "  python3.12 -m venv .venv\n"
            "  .venv/bin/python -m pip install --no-index --find-links=wheelhouse "
            "--require-hashes -r requirements-core.txt\n"
            "For FastAPI integration, use requirements-fastapi.txt instead.\n"
            "  .venv/bin/python -m pip check\n"
            "  .venv/bin/license-sdk --help\n"
            "Build/install once; application startup does not install packages.\n"
            "Download your product config separately from your own Issuer.\n"
        )
        shutil.copy2(ROOT / "docs/integration-cn.md", bundle / "integration-cn.md")
        shutil.copy2(ROOT / "python/examples/quick_start.py", bundle / "quick_start.py")
        checksums(bundle)
        # Verify both dependency selections in a fresh environment, without network or sources.
        venv = Path(temporary) / "smoke-venv"
        run(sys.executable, "-m", "venv", str(venv))
        python = str(venv / "bin/python")
        run(
            python,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(wheels),
            "--require-hashes",
            "-r",
            str(bundle / "requirements-core.txt"),
            cwd=Path(temporary),
        )
        run(str(venv / "bin/license-sdk"), "--help", cwd=Path(temporary))
        run(
            python,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(wheels),
            "--require-hashes",
            "-r",
            str(bundle / "requirements-fastapi.txt"),
            cwd=Path(temporary),
        )
        run(python, "-m", "pip", "check", cwd=Path(temporary))
        run(
            python,
            "-I",
            "-c",
            "import license_sdk, license_sdk_fastapi; "
            "from license_sdk.runtime import LicenseRuntime",
            cwd=Path(temporary),
        )
        shutil.make_archive(str(output / name), "gztar", root_dir=bundle.parent, base_dir=name)


def assemble(inputs: Path, output: Path) -> None:
    release = version()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("release output directory must be empty")
    # An allowlist prevents unrelated build output or product configuration entering a release.
    expected = {
        f"license-sdk-java-maven-{release}.tar.gz",
        *(
            f"license-{module}-{release}{classifier}.jar"
            for module in ("core", "spring")
            for classifier in ("", "-sources", "-javadoc")
        ),
        *(
            f"metor_license_{module}-{release}{extension}"
            for module in ("core", "fastapi")
            for extension in ("-py3-none-any.whl", ".tar.gz")
        ),
        *(
            f"license-sdk-python-{release}-cp312-{system}-{arch}.tar.gz"
            for system, arch in (
                ("linux", "x86_64"),
                ("linux", "aarch64"),
                ("macos", "aarch64"),
            )
        ),
    }
    for name in sorted(expected):
        matches = list(inputs.rglob(name))
        if not matches:
            raise ValueError(f"missing release asset: {name}")
        if len({sha256(path) for path in matches}) != 1:
            raise ValueError(f"different builds for the same asset: {name}")
        shutil.copy2(matches[0], output / name)
    verify_maven_bundle(output / f"license-sdk-java-maven-{release}.tar.gz", output, release)
    checksums(output)
    print(f"Ready: {len(expected)} assets + SHA256SUMS for v{release}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-version")
    check.add_argument("--tag")
    maven = sub.add_parser("maven")
    maven.add_argument("--repository", type=Path, required=True)
    maven.add_argument("--dist", type=Path, required=True)
    maven.add_argument("--output", type=Path, required=True)
    bundle = sub.add_parser("wheelhouse")
    bundle.add_argument("--dist", type=Path, required=True)
    bundle.add_argument("--output", type=Path, required=True)
    bundle.add_argument("--offline", action="store_true")
    collect = sub.add_parser("assemble")
    collect.add_argument("--inputs", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "check-version":
        print(version(args.tag))
    elif args.command == "maven":
        maven_bundle(args.repository.resolve(), args.dist.resolve(), args.output.resolve())
    elif args.command == "wheelhouse":
        wheelhouse(args.dist.resolve(), args.output.resolve(), args.offline)
    else:
        assemble(args.inputs.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
