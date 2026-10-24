"""Release packaging rejects incomplete, conflicting or inconsistent publications."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import release


class MavenReleaseTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.dist = self.root / "java"
        self.output = self.root / "output"
        self.version = release.version()
        for module in ("core", "spring"):
            artifact = f"license-{module}"
            folder = self.repository / "io/metor/license" / artifact / self.version
            folder.mkdir(parents=True)
            libs = self.dist / artifact / "build/libs"
            libs.mkdir(parents=True)
            variants = []
            for suffix in ("", "-sources", "-javadoc"):
                name = f"{artifact}-{self.version}{suffix}.jar"
                raw = name.encode()
                (folder / name).write_bytes(raw)
                (libs / name).write_bytes(raw)
                variants.append(
                    {
                        "name": {
                            "": "apiElements",
                            "-sources": "sourcesElements",
                            "-javadoc": "javadocElements",
                        }[suffix],
                        "files": [
                            {
                                "name": name,
                                "url": name,
                                "size": len(raw),
                                "sha256": hashlib.sha256(raw).hexdigest(),
                            }
                        ],
                    }
                )
            variants.append({**variants[0], "name": "runtimeElements"})
            if module == "spring":
                for variant in variants:
                    if variant["name"] in ("apiElements", "runtimeElements"):
                        variant["dependencies"] = [
                            {
                                "group": "io.metor.license",
                                "module": "license-core",
                                "version": {"requires": self.version},
                            }
                        ]
            dependency = (
                "<dependencies><dependency><groupId>io.metor.license</groupId>"
                f"<artifactId>license-core</artifactId><version>{self.version}</version>"
                "</dependency></dependencies>"
                if module == "spring"
                else ""
            )
            base = folder / f"{artifact}-{self.version}"
            Path(f"{base}.pom").write_text(
                '<project xmlns="http://maven.apache.org/POM/4.0.0">'
                f"<groupId>io.metor.license</groupId><artifactId>{artifact}</artifactId>"
                f"<version>{self.version}</version>{dependency}</project>"
            )
            Path(f"{base}.module").write_text(
                json.dumps(
                    {
                        "component": {
                            "group": "io.metor.license",
                            "module": artifact,
                            "version": self.version,
                        },
                        "variants": variants,
                    }
                )
            )

    def bundle(self):
        release.maven_bundle(self.repository, self.dist, self.output)
        return self.output / f"license-sdk-java-maven-{self.version}.tar.gz"

    def test_complete_bundle_and_standalone_match(self):
        archive = self.bundle()
        assets = self.root / "assets"
        assets.mkdir()
        for jar in self.dist.glob("*/build/libs/*.jar"):
            (assets / jar.name).write_bytes(jar.read_bytes())
        release.verify_maven_bundle(archive, assets, self.version)
        next(assets.glob("*.jar")).write_bytes(b"different")
        with self.assertRaisesRegex(ValueError, "JAR differ"):
            release.verify_maven_bundle(archive, assets, self.version)
        with self.assertRaisesRegex(ValueError, "replace"):
            self.bundle()

    def test_missing_publication(self):
        next(self.repository.rglob("*.pom")).unlink()
        with self.assertRaisesRegex(ValueError, "missing regular"):
            self.bundle()

    def test_inconsistent_pom_and_module(self):
        pom = next(self.repository.rglob("*.pom"))
        original = pom.read_text()
        pom.write_text(original.replace("<version>", "<version>wrong-", 1))
        with self.assertRaisesRegex(ValueError, "POM coordinates"):
            self.bundle()
        pom.write_text(original)
        metadata = next(self.repository.rglob("*.module"))
        value = json.loads(metadata.read_text())
        value["variants"][0]["files"][0]["sha256"] = "bad"
        metadata.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "file hash"):
            self.bundle()

    def test_spring_core_dependency_is_required(self):
        pom = next(self.repository.rglob("license-spring-*.pom"))
        pom.write_text(pom.read_text().replace("<artifactId>license-core", "<artifactId>other"))
        with self.assertRaisesRegex(ValueError, "matching core"):
            self.bundle()

    def test_different_standalone_jar(self):
        next(self.dist.glob("*/build/libs/*.jar")).write_bytes(b"other build")
        with self.assertRaisesRegex(ValueError, "JAR differ"):
            self.bundle()

    def test_spring_gradle_variants_require_matching_core(self):
        metadata = next(self.repository.rglob("license-spring-*.module"))
        original = metadata.read_text()
        for name in ("apiElements", "runtimeElements"):
            for version in (None, "0.0.0"):
                with self.subTest(variant=name, version=version):
                    value = json.loads(original)
                    variant = next(v for v in value["variants"] if v["name"] == name)
                    variant["dependencies"] = (
                        []
                        if version is None
                        else [
                            {
                                "group": "io.metor.license",
                                "module": "license-core",
                                "version": {"requires": version},
                            }
                        ]
                    )
                    metadata.write_text(json.dumps(value))
                    with self.assertRaisesRegex(ValueError, "Gradle metadata.*matching core"):
                        self.bundle()

    def test_assemble_requires_maven_and_rejects_conflicts(self):
        archive = self.bundle()
        inputs = self.root / "inputs"
        inputs.mkdir()
        for jar in self.dist.glob("*/build/libs/*.jar"):
            (inputs / jar.name).write_bytes(jar.read_bytes())
        with self.assertRaisesRegex(ValueError, "missing release asset"):
            release.assemble(inputs, self.root / "incomplete")
        (inputs / archive.name).write_bytes(archive.read_bytes())
        duplicate = inputs / "duplicate"
        duplicate.mkdir()
        (duplicate / archive.name).write_bytes(b"conflicting bundle")
        with self.assertRaisesRegex(ValueError, "different builds"):
            release.assemble(inputs, self.root / "conflicting")

    def test_tag_must_match_version(self):
        with self.assertRaisesRegex(ValueError, "tag must be"):
            release.version("v999.0.0")


if __name__ == "__main__":
    unittest.main()
