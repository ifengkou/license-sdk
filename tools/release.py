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
import tempfile
import tomllib
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
    checksums(output)
    print(f"Ready: {len(expected)} assets + SHA256SUMS for v{release}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-version")
    check.add_argument("--tag")
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
    elif args.command == "wheelhouse":
        wheelhouse(args.dist.resolve(), args.output.resolve(), args.offline)
    else:
        assemble(args.inputs.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
