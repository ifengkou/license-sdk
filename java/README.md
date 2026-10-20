# Java license SDK — Phase 1

Java 21 standalone Gradle project. Provisional local coordinates are
`io.metor.license:license-core:0.1.0` and `io.metor.license:license-spring:0.1.0`.
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

`license-spring` is only a separately publishable adapter skeleton depending on
core. It has no Spring dependency, filter, auto-configuration or enforcement yet.
No signature verification, license state, signing, private-key handling, clock
protection or business integration is implemented in Phase 1. A decoded manual
code is an identity/checksum value, never authorization.

```sh
./gradlew check build
```

The `examples/StandaloneCodec.java` smoke uses only the built core JAR and JDK 21.
Copy that source and `license-core/build/libs/license-core-0.1.0.jar` into an empty
directory, then run:

```sh
javac -cp license-core-0.1.0.jar StandaloneCodec.java
java -cp license-core-0.1.0.jar:. StandaloneCodec
```

For local publication verification, run
`./gradlew publishAllPublicationsToVerificationRepository`. This writes both
versioned Maven publications to `build/maven-repository`, never a remote registry.
A detached consumer may resolve `io.metor.license:license-spring:0.1.0` from a copy
of that directory; its POM pins `license-core:0.1.0` transitively. The copied
repository is sufficient without any SDK source or business checkout.

No build uses sibling business source or `.agents` files. Gradle needs its pinned
distribution and test dependencies prepared in its cache for an offline build.
Private repository addresses, credentials, release coordinates and publication
approval remain deployment configuration work. Do not use this skeleton as a
customer licensing implementation.
