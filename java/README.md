# Java license SDK — Phase 3

Java 21 standalone Gradle project. Provisional local coordinates are
`io.metor.license:license-core:0.3.0` and `io.metor.license:license-spring:0.3.0`.
These names are not a confirmed organization namespace or a remote publication.
The Gradle 8.14 wrapper is copied unchanged from the existing Metor wrapper.

Implemented:

- `ManualCodeCodec(prefix)`: 20-byte Crockford/CRC16-XMODEM encode/decode with
  purpose isolation, ASCII-only normalization, canonical padding and typed errors.
- `IdentityDerivation(fingerprintDomain, bindingDomain, recoveryDomain)`: host
  fingerprint, host/install binding and recovery-target digest. Product domains
  and prefixes are explicit input, not global ICP constants.
- Public synthetic v1 vectors packaged at
  `io/metor/license/protocol/request-code-v1-vectors.json` in the core JAR.
- Core has no runtime dependencies. Jackson/JUnit are test dependencies only.
  Both modules provide normal Maven publications, source JARs and Javadoc JARs.
- `CommercialLicenseVerifier(productId, trustRing)`: strict bounded UTF-8 JSON,
  canonical unpadded Base64URL and Ed25519 signatures over original payload bytes;
  required schema, product, binding and exact UTC validity checks. The immutable
  result includes signed fields, payload/file SHA-256 digests and
  ACTIVE/NOT_YET_VALID/EXPIRED status. Invalid input raises `LicenseException`
  with a bounded code rather than customer input.
- `verifyWithBindingProvider(bytes, supplier, now)` invokes the actual local
  binding provider only after a verified HOST_INSTALL payload. UNBOUND never
  calls the provider. Ordinary `verify(bytes, localBindingId, now)` supports
  hosts that already have the recomputed binding available.

`license-spring` is only a separately publishable adapter skeleton depending on
core. It has no Spring dependency, filter, auto-configuration or enforcement yet.
Phase 3 adds `ClockStateCodec`, `LicenseRuntime`, `TimeSource` and atomic `StateStore`
for installation observation and durable UTC/monotonic rollback protection. No signing,
private-key handling, recovery application or business enforcement is implemented yet. A decoded manual
code is an identity/checksum value, never authorization.

```sh
./gradlew check build
```

The `examples/StandaloneCodec.java` smoke uses only the built core JAR and JDK 21.
Copy that source and `license-core/build/libs/license-core-0.3.0.jar` into an empty
directory, then run:

```sh
javac -cp license-core-0.3.0.jar StandaloneCodec.java
java -cp license-core-0.3.0.jar:. StandaloneCodec
```

For local publication verification, run
`./gradlew publishAllPublicationsToVerificationRepository`. This writes both
versioned Maven publications to `build/maven-repository`, never a remote registry.
A detached consumer may resolve `io.metor.license:license-spring:0.3.0` from a copy
of that directory; its POM pins `license-core:0.3.0` transitively. The copied
repository is sufficient without any SDK source or business checkout.

No build uses sibling business source or `.agents` files. Gradle needs its pinned
distribution and test dependencies prepared in its cache for an offline build.
Private repository addresses, credentials, release coordinates and publication
approval remain deployment configuration work. Do not use this isolated verifier as a
customer licensing implementation.

`./gradlew :license-core:writeRuntimeClasspath` writes the built core JAR and
runtime dependency classpath to `license-core/build/runtime-classpath.txt` for
standalone cross-language conformance drivers. No test keys or signing entry
points are included in the runtime JAR. Product assembly must supply its own
trusted Ed25519 public keys and actual identity/time sources.
