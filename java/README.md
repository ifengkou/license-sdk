# Java license SDK 0.7.1

Download the core JAR or versioned Maven repository archive from [GitHub Releases](https://github.com/ifengkou/license-sdk/releases). See [release and offline installation](../docs/releasing.md) for checksums, Maven coordinates, local JAR usage, and the tag-based CI release procedure. The core requires JDK 21 only.

[中文接入与升级指南](../docs/integration-cn.md)：固定制品、Runtime 构造、生命周期、请求拦截及恢复 API。

Java 21 standalone Gradle project. Maven coordinates are
`io.metor.license:license-core:0.7.1` and `io.metor.license:license-spring:0.7.1`.
Version 0.7.1 adds atomic group-readable commercial publication and read-only installation validation to the existing onboarding APIs. GitHub Releases supplies standalone JARs and a versioned Maven repository archive; it does not publish to Maven Central. Existing artifacts and constructors remain unchanged.
The Gradle 8.14 wrapper is copied unchanged from the existing Metor wrapper.


## Five-step quick start (JDK 21 only)

Use the prebuilt `license-core-0.7.1.jar`. The standalone core has no Spring or
Python dependency. Run the following commands in a private working directory as
your normal user; no sudo or handwritten installation files are needed.

1. Unlock your Issuer, open **SDK 接入配置**, and download `license-product.json`.
   Put it in your application's controlled resources. It contains public keys,
   but determines which signatures your application trusts: do not load a
   replacement from HTTP uploads or the customer's writable license-data directory.
2. Initialize customer data once:

   ```sh
   java -jar license-core-0.7.1.jar init --config license-product.json --root ./license-data
   java -jar license-core-0.7.1.jar status --config license-product.json --root ./license-data --service YOUR_SERVICE
   ```

   `init` creates the UUID, 32-byte installation key, and all configured services'
   authenticated clock files. Expect `INITIALIZED`, then `UNLICENSED` from status.
   Repeating init gives `ALREADY_INITIALIZED` without changing the identity, key,
   clocks or commercial file. Never delete these files to repair an existing installation.
3. Create one Runtime from the bundled resource, and close it with the application's
   lifecycle. `examples/LicenseQuickStart.java` is a complete executable example:

   ```java
   byte[] publicConfig;
   try (var in = MyApplication.class.getResourceAsStream("/license-product.json")) {
       if (in == null) throw new IllegalStateException("Missing product resource");
       publicConfig = in.readNBytes(65537);
   }
   var runtime = LicenseRuntime.fromConfig(publicConfig, Path.of("./license-data"), "YOUR_SERVICE");
   runtime.start(); // application startup; call runtime.close() at application shutdown
   ```

   Replace `YOUR_SERVICE` with an ID displayed by your Issuer. A single-service
   product may pass null or omit CLI `--service`; a multi-service product must choose.
4. Export the request, sign it in Issuer's commercial page, then import the original download:

   ```sh
   java -jar license-core-0.7.1.jar request --config license-product.json --root ./license-data --service YOUR_SERVICE --output request.json
   java -jar license-core-0.7.1.jar import --config license-product.json --root ./license-data --service YOUR_SERVICE --file customer.license.json
   java -jar license-core-0.7.1.jar status --config license-product.json --root ./license-data --service YOUR_SERVICE
   ```

   Expect `APPLIED` and `ACTIVE`. These commands use the actual Linux
   `/etc/machine-id`; a container must supply a controlled host mount using
   `--machine-id /run/host/machine-id`. The factory's fourth Path argument provides
   the same override. macOS has no Linux machine ID: supply an actual deployment
   identifier for bound tests, or use UNBOUND signing, which never reads machine-id.
   `request` always requests a host/install binding and refuses unavailable host IDs.
5. Keep your account authorization, then put `runtime.requireValid()` immediately
   before each protected business side effect. `runtime.inspect()` returns the state
   and reasons for a status page. Missing/invalid licensing must not hide admin status,
   request, import, or recovery maintenance routes. A running Runtime sees imported
   licenses on its next validation; no restart or replacement Runtime is needed.

To run the example independently, copy only the JAR, downloaded public configuration,
and `LicenseQuickStart.java` to a temporary working directory:

```sh
javac -cp license-core-0.7.1.jar LicenseQuickStart.java
java -cp license-core-0.7.1.jar:. LicenseQuickStart ./license-data YOUR_SERVICE
```

Initialization uses private current-user directories/files (0700/0600). Multi-account container directory ownership, identity access and mount setup remain the product installer's responsibility. For the commercial file only, use the shared import parameter below; do not chmod the published file afterward.

## Management integration and persistence

`commercialRequest()` returns the exact issuer-compatible request map;
`licenseFileDigest()` reads the current raw file SHA-256, or null when absent.
`importLicense(candidateBytes, expectedRawDigest)` checks the digest under the
shared publish lock, preflights signature/product/actual binding/time, and atomically
publishes original bytes. `ImportResult` contains `actionStatus()`, `fileDigest()`
and the actual `status()`. `ALREADY_APPLIED` confirms existing bytes without
requiring their commercial window to remain ACTIVE; it still verifies signature,
product and binding. A new candidate must be ACTIVE.

For a commercial file read by another account in a shared POSIX group, pass a
numeric GID string (0 through 2147483647), for example:

```java
runtime.importLicense(candidateBytes, expectedRawDigest, "21000");
```

```sh
java -jar license-core-0.7.1.jar import --config license-product.json --root ./license-data --service YOUR_SERVICE --file customer.license.json --shared-group 21000
```

The SDK sets and confirms that group and mode 0640 on its temporary file before
fsync and atomic replacement, under the existing CAS lock. The caller must be
allowed to assign the group; unsupported group attributes or a permission failure
refuse publication without replacing the old file. Omitting the group keeps mode
0600. Shared settings apply only to the commercial file, never installation keys,
clock state or recovery files. They are deployment inputs, not signed fields or
additions to `license-product.json`. Both accounts still need directory access.
Same-byte imports confirm mode/group as well as durability; when attributes need
repair, the SDK republishes the same bytes atomically and returns ALREADY_APPLIED
on confirmation. This also works for a verified expired same-byte license without
making its commercial state ACTIVE.

`LicenseInstallation.validate(publicConfig, root)` returns the installation UUID
without creating a Runtime or any files. The byte-array and ProductConfig overloads
check the completed identity, 32-byte master key and every configured service's
clock structure/HMAC. The CLI equivalent needs no service or machine ID:

```sh
java -jar license-core-0.7.1.jar check --config license-product.json --root ./license-data
```

It prints `{"installationId":"..."}` on success. Missing, damaged, unfinished or
ambiguous installation material returns INSTALLATION_INVALID; no initialization,
lock creation, repair, fsync or checkpoint occurs. Commercial expiry/missing files
and the current wall clock do not affect this integrity check. Stop writers or
validate a consistent snapshot before backup: check does not make a live directory
snapshot. Filesystem reads may update access times on a writable mount.

For HTTP, obtain the expected digest from the client's status observation; a stale
value is `LICENSE_STATE_CONFLICT`, not permission to overwrite. Only an authorized
installer/admin may import. Retain account/admin authentication, Cookie CSRF,
bounded request bodies and `Cache-Control: no-store`. Map `LicenseException`
validation refusals to 400, conflicts to 409, storage errors to 503, and
`OUTCOME_UNKNOWN` to 202. Unknown means the candidate may already be published;
query its raw digest and retry the same file. Never report that the old file was
unchanged after an uncertain publish. If post-publish observation itself fails,
`status()` in that unknown result can be null; continue querying before claiming success.

Both SDKs use `.init.lock` and `shared/license-import.lock` with POSIX byte-range
locks (offset 0, length 1), plus local thread exclusion. Busy operations return a
bounded conflict. Init resumes only a valid unfinished draft with its original
UUID/key; corrupt or ambiguous data is preserved and refused. Completed installs
are never repaired or extended with new service seeds by init. Linux and macOS
local POSIX storage are supported; Windows, network filesystems, and multiple
live Runtime processes sharing one service's clock are outside this contract.

A scheduler logs restricted checkpoints/errors and continues after a failed tick.
`start()` and `close()` are idempotent; closing waits for in-flight work without
holding the Runtime monitor. A closed Runtime cannot start a new scheduler;
create a new instance when restarting the application. Original constructors, manual checkpoint scheduling,
status/preflight/guard and recovery methods remain compatible.

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
observation. Request validation never checkpoints. Use `start()` / `close()` to own one 30-second checkpoint task, or retain your existing lifecycle calling `checkpoint()`; choose one scheduler. Clock rollback is rejected only beyond 86,400
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
Copy that source and `license-core/build/libs/license-core-0.7.1.jar` into an empty
directory, then run:

```sh
javac -cp license-core-0.7.1.jar StandaloneCodec.java
java -cp license-core-0.7.1.jar:. StandaloneCodec
```

For local publication verification, run
`./gradlew publishAllPublicationsToVerificationRepository`. This writes both
versioned Maven publications to `build/maven-repository`, never a remote registry.
A detached consumer may resolve `io.metor.license:license-spring:0.7.1` from a copy
of that directory; its POM pins `license-core:0.7.1` transitively. The copied
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
