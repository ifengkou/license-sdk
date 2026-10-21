# Java license SDK — Phase 4

Java 21 standalone Gradle project. Provisional local coordinates are
`io.metor.license:license-core:0.4.0` and `io.metor.license:license-spring:0.4.0`.
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

`license-spring` provides `ControlProofCodec(issuer, audience)`, a dedicated bounded
HS256 CONTROL JWS signer/verifier using an already purpose-derived 32-byte key.
It explicitly depends on Nimbus JOSE/JWT 9.37.4 and Jackson 2.18.8; consumers
align these through their framework dependency constraints. It does not install
a filter or choose business routes automatically. Commercial signing and private
key handling remain exclusively in the issuer project.

`LicenseRuntime.preflight(candidate)` verifies against actual UTC without writing
the license or clock. `inspect()` observes restrictions without checkpointing.
`OperationPolicy` expresses MANAGEMENT, READ_EXISTING and NEW_EXECUTION; products
map explicit routes and default unknown operations to NEW_EXECUTION. Product HTTP
adapters enforce these decisions after account authorization. Background task
admission, continuation and time recovery remain later phases. A decoded manual
code is an identity/checksum value, never authorization.

```sh
./gradlew check build
```

The `examples/StandaloneCodec.java` smoke uses only the built core JAR and JDK 21.
Copy that source and `license-core/build/libs/license-core-0.4.0.jar` into an empty
directory, then run:

```sh
javac -cp license-core-0.4.0.jar StandaloneCodec.java
java -cp license-core-0.4.0.jar:. StandaloneCodec
```

For local publication verification, run
`./gradlew publishAllPublicationsToVerificationRepository`. This writes both
versioned Maven publications to `build/maven-repository`, never a remote registry.
A detached consumer may resolve `io.metor.license:license-spring:0.4.0` from a copy
of that directory; its POM pins `license-core:0.4.0` transitively. The copied
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

CONTROL proofs bind exact raw body SHA-256, method, actual path, installation,
issuer/audience, purpose, scope and UUIDv4 jti. They require integer iat/exp,
maximum 60-second lifetime and five-second transport skew using real UTC.
Only the exact alg=HS256,typ=JWT header is accepted; duplicate/extra claims,
noncanonical Base64URL, foreign products and mismatched requests are rejected.
Identical retries within the window are allowed; this is not exactly-once delivery.
Use separate control-domain derived keys; never reuse account JWT keys.

`./gradlew :license-spring:writeRuntimeClasspath` emits the adapter and transitive
runtime classpath for `tools/verify_phase4_control.py`, separately from the JDK-only
core classpath. No commercial private key or test trust enters either JAR.
