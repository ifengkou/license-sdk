# Java license SDK — Phase 6

Java 21 standalone Gradle project. Provisional local coordinates are
`io.metor.license:license-core:0.6.0` and `io.metor.license:license-spring:0.6.0`.
Version 0.6.0 is the unified local artifact target; no remote publication is performed.
Existing 0.4.0 and Phase 5.5 validation artifacts remain unchanged. These names are
not a confirmed organization namespace or a remote publication.
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
`LicenseRuntime` implements `LicenseGuard.requireValid()`: ACTIVE continues, otherwise
`LicenseDeniedException` carries `state()` and immutable `reasons()` from the same
observation. Request validation never checkpoints; the host lifecycle calls
`checkpoint()` every 30 seconds. Clock rollback is rejected only beyond 86,400
seconds; commercial validity remains exactly [not_before, expires_at), with no grace.
Products choose protected entry points after account authorization. No read-only
operation policy, background task admission or continuation is installed.
A decoded manual
code is an identity/checksum value, never authorization.

## Single-service online recovery

Configure the product's recovery prefix explicitly in the final `Config` field
(for example `RC1J`); the compatibility constructor permits regular validation
but cannot generate recovery requests without a configured prefix.

`getOrCreateRecoveryRequest()` returns one stable request while the local clock
target is unchanged. `RecoveryRequest.toMap()` and `toJson()` expose the exact
snake_case request format and manual recovery code. A normal observation
invalidates an applicable pending challenge. The request is diagnostic input,
not authority to modify state.

After correcting host UTC, pass the vendor-signed file to the same instance's
`applyRecovery(bytes)`. The runtime validates Ed25519, product/install/service,
target and exact signed window (at most seven days), serializes with checkpoint,
atomically replaces the clock, and refreshes its own memory only after durable
commit. `RecoveryResult` contains APPLIED, ALREADY_APPLIED or OUTCOME_UNKNOWN and
the actual commercial `Snapshot`. Recovery never changes the commercial file
or its expiry. Reapplying a consumed ID confirms durability without resetting
the epoch or lowering a reference already observed after that recovery.

`RecoveryException.code()` / `httpStatus()` provide bounded host mappings.
Challenge creation with unknown commit durability returns
LICENSE_STORAGE_OUTCOME_UNKNOWN (202); retry the same operation, which confirms
and reuses a published challenge. An application result OUTCOME_UNKNOWN keeps
the runtime restricted; retry the same signed file to confirm it. Do not create
a temporary runtime or write clock files in a Controller.

This contract requires one process/runtime per service state directory. It does
not synchronize memory across replicas or coordinate another service. The
`examples/RecoveryProbe.java` driver is synthetic test tooling and is not
included in runtime JARs. Production hosts retain their own admin authentication.

```sh
./gradlew check build
```

The `examples/StandaloneCodec.java` smoke uses only the built core JAR and JDK 21.
Copy that source and `license-core/build/libs/license-core-0.6.0.jar` into an empty
directory, then run:

```sh
javac -cp license-core-0.6.0.jar StandaloneCodec.java
java -cp license-core-0.6.0.jar:. StandaloneCodec
```

For local publication verification, run
`./gradlew publishAllPublicationsToVerificationRepository`. This writes both
versioned Maven publications to `build/maven-repository`, never a remote registry.
A detached consumer may resolve `io.metor.license:license-spring:0.6.0` from a copy
of that directory; its POM pins `license-core:0.6.0` transitively. The copied
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

Customer deployment runs a prebuilt BootJar/image with SDK and host libraries already included. Customers do not execute Gradle/Maven or download dependencies at startup. The JDK-only core needs no external runtime packages; CONTROL users retain the separate adapter dependencies.
