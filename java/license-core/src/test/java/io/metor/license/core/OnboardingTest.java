package io.metor.license.core;

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.Signature;
import java.time.Instant;
import java.util.Arrays;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ScheduledThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class OnboardingTest {
    private static final ObjectMapper JSON = new ObjectMapper();
    @TempDir Path temporary;
    private KeyPair signing;
    private byte[] config;
    private Path root;
    private Path machine;
    @BeforeEach void prepare() throws Exception {
        temporary = temporary.toRealPath(); root = temporary.resolve("license-data");
        signing = KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
        config = JSON.writeValueAsBytes(configData());
        machine = temporary.resolve("machine-id"); Files.writeString(machine, "00112233445566778899aabbccddeeff\n");
    }
    private Map<String, Object> configData() {
        return new LinkedHashMap<>(Map.of("schema_version", 1, "product_id", "other-product",
            "fingerprint_domain", "other-product/host-fingerprint/v1", "binding_domain", "other-product/host-install-code/v1",
            "recovery_domain", "other-product/recovery-target/v1", "request_prefix", "OP1",
            "services", List.of(Map.of("id", "backend", "recovery_prefix", "OP1B"), Map.of("id", "worker", "recovery_prefix", "OP1W")),
            "trusted_keys", Map.of("test-key", pem(signing))));
    }
    private static String pem(KeyPair key) {
        return "-----BEGIN PUBLIC KEY-----\n" + Base64.getMimeEncoder(64, new byte[]{'\n'}).encodeToString(key.getPublic().getEncoded())
            + "\n-----END PUBLIC KEY-----\n";
    }
    private LicenseRuntime runtime() throws IOException { return LicenseRuntime.fromConfig(config, root, "backend", machine); }
    private Path clock() { return root.resolve("state/backend/clock.json"); }
    private UUID id() throws IOException { return UUID.fromString(JSON.readTree(root.resolve("identity/installation.json").toFile()).get("installation_id").asText()); }
    private byte[] key() throws IOException { return Files.readAllBytes(root.resolve("identity/service.key")); }
    private ClockState clockState() throws IOException { return ClockStateCodec.decode(Files.readAllBytes(clock()), key(), "other-product", id(), "backend"); }
    private byte[] license(boolean bound, Instant before, Instant expiry) throws Exception {
        var data = new LinkedHashMap<String, Object>();
        data.put("schema_version", 1); data.put("kind", "commercial-license"); data.put("key_id", "test-key");
        data.put("product_id", "other-product"); data.put("customer_id", "customer"); data.put("customer_name", "Customer");
        data.put("license_id", UUID.randomUUID().toString()); data.put("binding_type", bound ? "HOST_INSTALL" : "UNBOUND");
        data.put("binding_scheme", bound ? "host-install-code-v1" : null); data.put("binding_id", bound ? runtime().bindingId() : null);
        data.put("validity_type", expiry == null ? "PERPETUAL" : "TERM"); data.put("issued_at", Instant.now().minusSeconds(20).truncatedTo(java.time.temporal.ChronoUnit.SECONDS).toString());
        data.put("not_before", before.truncatedTo(java.time.temporal.ChronoUnit.SECONDS).toString());
        data.put("expires_at", expiry == null ? null : expiry.truncatedTo(java.time.temporal.ChronoUnit.SECONDS).toString());
        byte[] payload = JSON.writeValueAsBytes(data); var signer = Signature.getInstance("Ed25519");
        signer.initSign(signing.getPrivate()); signer.update(payload);
        return JSON.writeValueAsBytes(Map.of("payload", Base64.getUrlEncoder().withoutPadding().encodeToString(payload),
            "signature", Base64.getUrlEncoder().withoutPadding().encodeToString(signer.sign())));
    }

    @Test void strictConfigurationAndServiceSelection() throws Exception {
        var parsed = ProductConfig.parse(config);
        assertEquals("other-product", parsed.productId()); assertEquals("OP1B", parsed.services().get("backend"));
        assertThrows(UnsupportedOperationException.class, () -> parsed.services().put("hack", "AA"));
        assertThrows(IllegalArgumentException.class, () -> parsed.selectService(null));
        assertThrows(IllegalArgumentException.class, () -> parsed.selectService("unknown"));
        var single = configData(); single.put("services", List.of(Map.of("id", "worker", "recovery_prefix", "OP1W")));
        assertEquals("worker", ProductConfig.parse(JSON.writeValueAsBytes(single)).selectService(null));
        for (Object invalid : List.of(2, 1.0, true, "1")) {
            var data = configData(); data.put("schema_version", invalid);
            assertThrows(IllegalArgumentException.class, () -> ProductConfig.parse(JSON.writeValueAsBytes(data)));
        }
        for (String field : configData().keySet()) {
            var data = configData(); data.remove(field);
            assertThrows(IllegalArgumentException.class, () -> ProductConfig.parse(JSON.writeValueAsBytes(data)));
        }
        var data = configData(); data.put("extra", "no");
        assertThrows(IllegalArgumentException.class, () -> ProductConfig.parse(JSON.writeValueAsBytes(data)));
        for (Object services : List.of(List.of(), List.of(Map.of("id", "backend", "recovery_prefix", "OP1")),
            List.of(Map.of("id", "backend", "recovery_prefix", "OP1B"), Map.of("id", "backend", "recovery_prefix", "OP1C")))) {
            var duplicate = configData(); duplicate.put("services", services);
            assertThrows(IllegalArgumentException.class, () -> ProductConfig.parse(JSON.writeValueAsBytes(duplicate)));
        }
        assertThrows(IllegalArgumentException.class, () -> ProductConfig.parse(new byte[65537]));
        assertThrows(IllegalArgumentException.class, () -> ProductConfig.parse(("{\"product_id\":\"first\"," + new String(config, StandardCharsets.UTF_8).substring(1)).getBytes(StandardCharsets.UTF_8)));
        var wrongKey = configData(); var rsa = KeyPairGenerator.getInstance("RSA"); rsa.initialize(2048);
        wrongKey.put("trusted_keys", Map.of("test-key", pem(rsa.generateKeyPair())));
        assertThrows(IllegalArgumentException.class, () -> ProductConfig.parse(JSON.writeValueAsBytes(wrongKey)));
        wrongKey.put("trusted_keys", Map.of("test-key", "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n"));
        assertThrows(IllegalArgumentException.class, () -> ProductConfig.parse(JSON.writeValueAsBytes(wrongKey)));
    }

    @Test void initializeIsIdempotentAndNeverRepairsCompletedDamage() throws Exception {
        var initialized = LicenseInstallation.initialize(config, root);
        assertEquals("INITIALIZED", initialized.actionStatus());
        assertEquals("rw-------", PosixFilePermissions.toString(Files.getPosixFilePermissions(root.resolve("identity/service.key"))));
        Map<Path, byte[]> original = tree(root); var before = Files.getLastModifiedTime(clock());
        assertEquals("ALREADY_INITIALIZED", LicenseInstallation.initialize(config, root).actionStatus());
        assertTree(original, root); assertEquals(before, Files.getLastModifiedTime(clock()));
        assertEquals(LicenseRuntime.State.UNLICENSED, runtime().inspect().state());
        assertThrows(LicenseDeniedException.class, () -> runtime().requireValid());
        Files.delete(clock());
        assertThrows(IOException.class, () -> LicenseInstallation.initialize(config, root)); assertFalse(Files.exists(clock()));
        assertArrayEquals(original.get(root.resolve("identity/service.key")), key());
    }

    @Test void readonlyValidationNeedsNoRuntimeLicenseMachineOrLock() throws Exception {
        assertEquals(OnboardingException.Code.INSTALLATION_INVALID,
            assertThrows(OnboardingException.class, () -> LicenseInstallation.validate(config, root)).code());
        assertFalse(Files.exists(root));
        LicenseInstallation.initialize(config, root); UUID expected = id();
        Files.delete(root.resolve(".init.lock")); Files.delete(machine);
        ClockState original = clockState();
        Files.write(clock(), ClockStateCodec.encode(new ClockState(original.productId(), original.installationId(), original.service(),
            original.epoch(), original.sequence(), Instant.parse("2100-01-01T00:00:00Z"), original.lastRecoveryId()), key()));
        Files.write(root.resolve("shared/active.license.json"), license(false, Instant.now().minusSeconds(20), Instant.now().minusSeconds(1)));
        Map<Path, byte[]> before = tree(root);
        var readonly = new OnboardingFiles() {
            @Override void directory(Path path) { fail("check must not create directories"); }
            @Override <T> T withLock(Path path, Action<T> action) { throw new AssertionError("check must not lock"); }
            @Override void sync(Path path) { fail("check must not sync"); }
            @Override void writeNew(Path path, byte[] bytes) { fail("check must not write"); }
            @Override void writeExisting(Path path, byte[] bytes) { fail("check must not write"); }
            @Override void move(Path from, Path to, boolean replace) { fail("check must not rename"); }
        };
        assertEquals(expected, LicenseInstallation.validate(ProductConfig.parse(config), root, readonly));
        assertTree(before, root); assertFalse(Files.exists(root.resolve(".init.lock")));
        Files.delete(root.resolve("shared/active.license.json"));
        assertEquals(expected, LicenseInstallation.validate(config, root));
        List<Path> paths;
        try (var entries = Files.walk(root)) { paths = entries.toList(); }
        for (Path path : paths) Files.setPosixFilePermissions(path,
            PosixFilePermissions.fromString(Files.isDirectory(path) ? "r-x------" : "r--------"));
        try { assertEquals(expected, LicenseInstallation.validate(config, root)); }
        finally {
            for (Path path : paths) Files.setPosixFilePermissions(path,
                PosixFilePermissions.fromString(Files.isDirectory(path) ? "rwx------" : "rw-------"));
        }
    }

    @Test void readonlyValidationRefusesIncompleteCorruptOrAmbiguousInstallations() throws Exception {
        LicenseInstallation.initialize(config, root);
        Path identity = root.resolve("identity/installation.json"), master = root.resolve("identity/service.key");
        byte[] identityBytes = Files.readAllBytes(identity), masterBytes = key(), clockBytes = Files.readAllBytes(clock());
        for (String damaged : List.of(new String(identityBytes, StandardCharsets.US_ASCII).replace("true", "false"),
            new String(identityBytes, StandardCharsets.US_ASCII).replace("other-product", "foreign-product"),
            new String(identityBytes, StandardCharsets.US_ASCII).replace(id().toString(), "invalid-uuid"))) {
            Files.writeString(identity, damaged);
            assertEquals(OnboardingException.Code.INSTALLATION_INVALID,
                assertThrows(OnboardingException.class, () -> LicenseInstallation.validate(config, root)).code());
            assertEquals(damaged, Files.readString(identity)); Files.write(identity, identityBytes);
        }
        Files.write(master, new byte[31]); assertThrows(OnboardingException.class, () -> LicenseInstallation.validate(config, root)); Files.write(master, masterBytes);
        Files.writeString(clock(), "{}"); assertThrows(OnboardingException.class, () -> LicenseInstallation.validate(config, root));
        var envelope = (com.fasterxml.jackson.databind.node.ObjectNode) JSON.readTree(clockBytes);
        envelope.put("mac", Base64.getUrlEncoder().withoutPadding().encodeToString(new byte[32]));
        Files.write(clock(), JSON.writeValueAsBytes(envelope));
        assertThrows(OnboardingException.class, () -> LicenseInstallation.validate(config, root)); Files.write(clock(), clockBytes);
        Files.delete(clock()); assertThrows(OnboardingException.class, () -> LicenseInstallation.validate(config, root)); assertFalse(Files.exists(clock()));
        Files.createSymbolicLink(clock(), root.resolve("state/worker/clock.json"));
        assertThrows(OnboardingException.class, () -> LicenseInstallation.validate(config, root)); Files.delete(clock()); Files.write(clock(), clockBytes);
        Path extra = root.resolve("identity/extra"); Files.writeString(extra, "keep");
        assertThrows(OnboardingException.class, () -> LicenseInstallation.validate(config, root)); Files.delete(extra);
        for (String stage : List.of(".identity-stage", ".identity-stage-legacy")) {
            Path staged = Files.createDirectory(root.resolve(stage));
            assertThrows(OnboardingException.class, () -> LicenseInstallation.validate(config, root)); Files.delete(staged);
        }
        var changed = configData(); changed.put("services", List.of(Map.of("id", "third", "recovery_prefix", "OP1T")));
        assertThrows(OnboardingException.class, () -> LicenseInstallation.validate(JSON.writeValueAsBytes(changed), root));
        assertEquals(id(), LicenseInstallation.validate(config, root));
    }

    @Test void draftResumesWithoutChangingIdentityKeyOrExistingSeeds() throws Exception {
        LicenseInstallation.initialize(config, root);
        UUID originalId = id(); byte[] originalKey = key(); byte[] originalClock = Files.readAllBytes(clock());
        Path identity = root.resolve("identity/installation.json");
        Files.writeString(identity, Files.readString(identity).replace("true", "false"));
        Files.delete(root.resolve("state/worker/clock.json"));
        assertEquals("INITIALIZED", LicenseInstallation.initialize(config, root).actionStatus());
        assertEquals(originalId, id()); assertArrayEquals(originalKey, key()); assertArrayEquals(originalClock, Files.readAllBytes(clock()));
    }

    @Test void identityStageResumesButPartialOrUncertainStagesArePreserved() throws Exception {
        LicenseInstallation.initialize(config, root);
        Path identity = root.resolve("identity/installation.json"); Files.writeString(identity, Files.readString(identity).replace("true", "false"));
        UUID before = id(); byte[] master = key();
        Files.delete(root.resolve("state/worker/clock.json")); Files.delete(clock());
        Files.delete(root.resolve("state/worker")); Files.delete(root.resolve("state/backend")); Files.delete(root.resolve("state")); Files.delete(root.resolve("shared"));
        Files.move(root.resolve("identity"), root.resolve(".identity-stage"));
        assertEquals("INITIALIZED", LicenseInstallation.initialize(config, root).actionStatus()); assertEquals(before, id()); assertArrayEquals(master, key());
        Path broken = temporary.resolve("broken"); Files.createDirectories(broken.resolve(".identity-stage"));
        Files.writeString(broken.resolve(".identity-stage/installation.json"), "{}");
        assertThrows(IOException.class, () -> LicenseInstallation.initialize(config, broken));
        assertEquals("{}", Files.readString(broken.resolve(".identity-stage/installation.json")));
        Files.createDirectory(root.resolve(".identity-stage"));
        assertThrows(IOException.class, () -> LicenseInstallation.initialize(config, root)); assertArrayEquals(master, key());
    }

    @Test void symlinksAndDirtyTargetsNeverInitialize() throws Exception {
        Path real = temporary.resolve("real"); Files.createDirectory(real); Path link = temporary.resolve("linked"); Files.createSymbolicLink(link, real);
        assertThrows(IOException.class, () -> LicenseInstallation.initialize(config, link));
        Files.createDirectory(root); Files.writeString(root.resolve("orphan"), "keep");
        assertThrows(IOException.class, () -> LicenseInstallation.initialize(config, root)); assertEquals("keep", Files.readString(root.resolve("orphan")));
        assertFalse(Files.exists(root.resolve("identity")));
    }

    @Test void setupFailuresPreserveDraftAndReturnUnknownWithoutClaimingCompletion() throws Exception {
        AtomicInteger writes = new AtomicInteger();
        var failure = new OnboardingFiles() {
            @Override void writeNew(Path path, byte[] bytes) throws IOException {
                super.writeNew(path, bytes); if (writes.incrementAndGet() == 1) throw new IOException("injected");
            }
        };
        assertEquals(OnboardingException.Code.OUTCOME_UNKNOWN,
            assertThrows(OnboardingException.class, () -> LicenseInstallation.initialize(ProductConfig.parse(config), root, failure)).code());
        assertTrue(Files.exists(root.resolve(".identity-stage/installation.json")));
        assertThrows(IOException.class, () -> LicenseInstallation.initialize(config, root)); assertFalse(Files.exists(root.resolve("identity")));
    }

    @Test void setupPublishAndDirectorySyncFailureCanResumeSameIdentity() throws Exception {
        var failure = new OnboardingFiles() {
            @Override void move(Path source, Path target, boolean replace) throws IOException {
                super.move(source, target, replace); if (target.getFileName().toString().equals("identity")) throw new IOException("after identity rename");
            }
        };
        assertThrows(OnboardingException.class, () -> LicenseInstallation.initialize(ProductConfig.parse(config), root, failure));
        byte[] master = key(); UUID before = id();
        assertEquals("INITIALIZED", LicenseInstallation.initialize(config, root).actionStatus()); assertEquals(before, id()); assertArrayEquals(master, key());
    }

    @Test void initializationFailuresAtSeedAndCompletedIdentitySyncRetainOriginalMaterial() throws Exception {
        for (String point : List.of("clock.json", "installation.json")) {
            Path targetRoot = temporary.resolve("failure-" + point);
            AtomicInteger failures = new AtomicInteger();
            var failure = new OnboardingFiles() {
                @Override void sync(Path path) throws IOException {
                    if (point.equals("clock.json") && path.toString().endsWith("state/backend") && failures.getAndIncrement() == 0)
                        throw new IOException("seed directory sync");
                    if (point.equals("installation.json") && path.endsWith("identity")) {
                        Path marker = path.resolve("installation.json");
                        if (Files.exists(marker) && Files.readString(marker).contains("true") && failures.getAndIncrement() == 0)
                            throw new IOException("completed identity directory sync");
                    }
                    super.sync(path);
                }
            };
            assertEquals(OnboardingException.Code.OUTCOME_UNKNOWN, assertThrows(OnboardingException.class,
                () -> LicenseInstallation.initialize(ProductConfig.parse(config), targetRoot, failure)).code());
            byte[] retainedKey = Files.readAllBytes(targetRoot.resolve("identity/service.key"));
            String retainedId = JSON.readTree(targetRoot.resolve("identity/installation.json").toFile()).get("installation_id").asText();
            LicenseInstallation.initialize(config, targetRoot);
            assertArrayEquals(retainedKey, Files.readAllBytes(targetRoot.resolve("identity/service.key")));
            assertEquals(retainedId, JSON.readTree(targetRoot.resolve("identity/installation.json").toFile()).get("installation_id").asText());
        }
        LicenseInstallation.initialize(config, root);
        var syncFailure = new OnboardingFiles() { @Override void sync(Path path) throws IOException { throw new IOException("unconfirmed"); } };
        assertEquals(OnboardingException.Code.OUTCOME_UNKNOWN, assertThrows(OnboardingException.class,
            () -> LicenseInstallation.initialize(ProductConfig.parse(config), root, syncFailure)).code());
    }

    @Test void requestUsesActualIdentityAndFactoryNeverSilentlyInitializes() throws Exception {
        assertEquals(LicenseRuntime.State.CONFIG_INVALID, runtime().inspect().state()); assertFalse(Files.exists(root));
        assertThrows(IllegalArgumentException.class, () -> LicenseRuntime.fromConfig(config, root, null));
        LicenseInstallation.initialize(config, root); var runtime = runtime();
        Map<Path, byte[]> before = tree(root); var request = runtime.commercialRequest();
        assertEquals(SetHolder.REQUEST_FIELDS, request.keySet()); assertEquals(runtime.bindingId(), request.get("binding_id"));
        assertEquals(runtime.bindingId(), new ManualCodeCodec("OP1").decodeHex((String)request.get("request_code")));
        assertTree(before, root);
        Files.delete(machine); assertEquals(OnboardingException.Code.BINDING_UNAVAILABLE,
            assertThrows(OnboardingException.class, runtime::commercialRequest).code());
        var legacy = new LicenseRuntime(new LicenseRuntime.Config("other-product", "backend", "fd", "bd", "rd",
            root.resolve("identity/installation.json"), root.resolve("identity/service.key"), root.resolve("shared/active.license.json"), clock(), null),
            Map.of("test-key", signing.getPublic()), TimeSource.system());
        assertEquals(OnboardingException.Code.CONFIG_INVALID, assertThrows(OnboardingException.class, legacy::commercialRequest).code());
    }
    private static final class SetHolder {
        static final java.util.Set<String> REQUEST_FIELDS = java.util.Set.of("schema_version", "kind", "product_id", "binding_type", "binding_scheme", "binding_id", "request_code", "request_id", "generated_at");
    }

    @Test void commercialImportIsAtomicCasAndVisibleToSameRuntime() throws Exception {
        LicenseInstallation.initialize(config, root); var runtime = runtime();
        byte[] candidate = license(true, Instant.now().minusSeconds(10), null); byte[] originalClock = Files.readAllBytes(clock());
        assertEquals(LicenseRuntime.State.UNLICENSED, runtime.inspect().state());
        var result = runtime.importLicense(candidate, null);
        assertEquals(LicenseRuntime.ImportActionStatus.APPLIED, result.actionStatus()); assertEquals(LicenseRuntime.State.ACTIVE, result.status().state());
        runtime.requireValid(); assertArrayEquals(candidate, Files.readAllBytes(root.resolve("shared/active.license.json")));
        assertArrayEquals(originalClock, Files.readAllBytes(clock()));
        assertEquals(LicenseRuntime.ImportActionStatus.ALREADY_APPLIED, runtime.importLicense(candidate, result.fileDigest()).actionStatus());
        byte[] different = license(false, Instant.now().minusSeconds(10), null);
        assertEquals(OnboardingException.Code.LICENSE_STATE_CONFLICT, assertThrows(OnboardingException.class, () -> runtime.importLicense(different, null)).code());
        assertArrayEquals(candidate, Files.readAllBytes(root.resolve("shared/active.license.json")));
        byte[] invalid = new byte[]{'x'};
        assertThrows(LicenseException.class, () -> runtime.importLicense(invalid, result.fileDigest()));
        assertArrayEquals(candidate, Files.readAllBytes(root.resolve("shared/active.license.json")));
        byte[] expired = license(false, Instant.now().minusSeconds(20), Instant.now().minusSeconds(1));
        assertThrows(OnboardingException.class, () -> runtime.importLicense(expired, result.fileDigest()));
        assertArrayEquals(candidate, Files.readAllBytes(root.resolve("shared/active.license.json")));
        assertThrows(OnboardingException.class, () -> runtime.importLicense(new byte[65537], result.fileDigest()));
        Files.delete(machine);
        assertThrows(LicenseException.class, () -> runtime.importLicense(candidate, result.fileDigest()));
    }

    @Test void sharedImportHasConfirmedAttributesBeforeAtomicVisibilityAndRepairsSameBytes() throws Exception {
        LicenseInstallation.initialize(config, root); var runtime = runtime();
        Path target = root.resolve("shared/active.license.json");
        String group = Files.getAttribute(root, "unix:gid").toString();
        byte[] candidate = license(false, Instant.now().minusSeconds(10), null);
        var publication = new OnboardingFiles() {
            @Override void move(Path source, Path destination, boolean replace) throws IOException {
                assertTrue(attributesMatch(source, Integer.valueOf(group)));
                assertArrayEquals(candidate, Files.readAllBytes(source));
                super.move(source, destination, replace);
            }
        };
        assertEquals(LicenseRuntime.ImportActionStatus.APPLIED, runtime.importLicense(candidate, null, group, publication).actionStatus());
        assertTrue(new OnboardingFiles().attributesMatch(target, Integer.valueOf(group)));
        Object originalInode = Files.getAttribute(target, "unix:ino");
        assertEquals(LicenseRuntime.ImportActionStatus.ALREADY_APPLIED, runtime.importLicense(candidate, runtime.licenseFileDigest(), group).actionStatus());
        assertEquals(originalInode, Files.getAttribute(target, "unix:ino"));
        Files.setPosixFilePermissions(target, PosixFilePermissions.fromString("rw-------"));
        assertEquals(LicenseRuntime.ImportActionStatus.ALREADY_APPLIED,
            runtime.importLicense(candidate, runtime.licenseFileDigest(), group, publication).actionStatus());
        assertTrue(new OnboardingFiles().attributesMatch(target, Integer.valueOf(group)));
        assertEquals("rw-------", PosixFilePermissions.toString(Files.getPosixFilePermissions(root.resolve("identity/service.key"))));
        assertEquals("rw-------", PosixFilePermissions.toString(Files.getPosixFilePermissions(clock())));
        byte[] expired = license(false, Instant.now().minusSeconds(20), Instant.now().minusSeconds(1));
        Files.write(target, expired); Files.setPosixFilePermissions(target, PosixFilePermissions.fromString("rw-------"));
        var result = runtime.importLicense(expired, runtime.licenseFileDigest(), group);
        assertEquals(LicenseRuntime.ImportActionStatus.ALREADY_APPLIED, result.actionStatus());
        assertEquals(LicenseRuntime.State.EXPIRED, result.status().state());
        assertTrue(new OnboardingFiles().attributesMatch(target, Integer.valueOf(group)));
    }

    @Test void sharedAttributeAndSyncFailuresRespectPublicationBoundary() throws Exception {
        LicenseInstallation.initialize(config, root); var runtime = runtime();
        byte[] old = license(false, Instant.now().minusSeconds(10), null), candidate = license(false, Instant.now().minusSeconds(10), null);
        var original = runtime.importLicense(old, null); String group = Files.getAttribute(root, "unix:gid").toString();
        for (String point : List.of("attributes", "sync", "rename")) {
            var failure = new OnboardingFiles() {
                @Override void attributes(Path path, int gid) throws IOException {
                    super.attributes(path, gid); if (point.equals("attributes")) throw new IOException("attributes");
                }
                @Override void sync(Path path) throws IOException { if (point.equals("sync")) throw new IOException("file sync"); super.sync(path); }
                @Override void move(Path from, Path to, boolean replace) throws IOException { throw new IOException("rename"); }
            };
            assertEquals(OnboardingException.Code.STORAGE_FAILED,
                assertThrows(OnboardingException.class, () -> runtime.importLicense(candidate, original.fileDigest(), group, failure)).code());
            assertEquals(original.fileDigest(), runtime.licenseFileDigest());
            assertTrue(new OnboardingFiles().attributesMatch(root.resolve("shared/active.license.json"), null));
        }
        var failure = new OnboardingFiles() {
            @Override void sync(Path path) throws IOException {
                if (Files.isDirectory(path)) throw new IOException("directory sync"); super.sync(path);
            }
        };
        var unknown = runtime.importLicense(candidate, original.fileDigest(), group, failure);
        assertEquals(LicenseRuntime.ImportActionStatus.OUTCOME_UNKNOWN, unknown.actionStatus());
        assertTrue(new OnboardingFiles().attributesMatch(root.resolve("shared/active.license.json"), Integer.valueOf(group)));
        for (boolean unsupported : List.of(false, true)) {
            var unreadableAttributes = new OnboardingFiles() {
                @Override boolean attributesMatch(Path path, Integer gid) throws IOException {
                    if (unsupported) throw new UnsupportedOperationException("attribute unavailable");
                    throw new IOException("attribute read failed");
                }
            };
            assertEquals(LicenseRuntime.ImportActionStatus.OUTCOME_UNKNOWN,
                runtime.importLicense(candidate, unknown.fileDigest(), group, unreadableAttributes).actionStatus());
            assertEquals(unknown.fileDigest(), runtime.licenseFileDigest());
        }
        assertEquals(LicenseRuntime.ImportActionStatus.ALREADY_APPLIED, runtime.importLicense(candidate, unknown.fileDigest(), group).actionStatus());
        for (String invalid : List.of("", "-1", "+1", "staff", "2147483648", "99999999999999999999999")) {
            assertThrows(IllegalArgumentException.class, () -> runtime.importLicense(candidate, runtime.licenseFileDigest(), invalid));
        }
    }

    @Test void damagedOldFileDigestIsCompleteAndBoundedInMemory() throws Exception {
        LicenseInstallation.initialize(config, root); var runtime = runtime();
        byte[] damaged = new byte[100000]; Arrays.fill(damaged, (byte)'x');
        Files.write(root.resolve("shared/active.license.json"), damaged);
        assertEquals(OnboardingFiles.digest(damaged), runtime.licenseFileDigest());
        byte[] candidate = license(false, Instant.now().minusSeconds(10), null);
        assertEquals(LicenseRuntime.ImportActionStatus.APPLIED,
            runtime.importLicense(candidate, OnboardingFiles.digest(damaged)).actionStatus());
        assertArrayEquals(candidate, Files.readAllBytes(root.resolve("shared/active.license.json")));
    }

    @Test void sameByteConfirmationUsesActualExpiredStateAndNeverRewrites() throws Exception {
        LicenseInstallation.initialize(config, root); var runtime = runtime();
        byte[] expired = license(false, Instant.now().minusSeconds(20), Instant.now().minusSeconds(1));
        Path target = root.resolve("shared/active.license.json"); Files.write(target, expired);
        Files.setPosixFilePermissions(target, PosixFilePermissions.fromString("rw-------"));
        var modified = Files.getLastModifiedTime(target);
        var result = runtime.importLicense(expired, runtime.licenseFileDigest());
        assertEquals(LicenseRuntime.ImportActionStatus.ALREADY_APPLIED, result.actionStatus()); assertEquals(LicenseRuntime.State.EXPIRED, result.status().state());
        assertEquals(modified, Files.getLastModifiedTime(target));
        Files.writeString(target, "broken");
        assertThrows(LicenseException.class, () -> runtime.importLicense("broken".getBytes(StandardCharsets.US_ASCII), runtime.licenseFileDigest()));
    }

    @Test void failuresBeforeAndAfterPublishHaveDifferentResults() throws Exception {
        LicenseInstallation.initialize(config, root); var runtime = runtime(); byte[] candidate = license(false, Instant.now().minusSeconds(10), null);
        var beforeWrite = new OnboardingFiles() { @Override void writeExisting(Path path, byte[] raw) throws IOException { throw new IOException("write"); } };
        assertEquals(OnboardingException.Code.STORAGE_FAILED, assertThrows(OnboardingException.class, () -> runtime.importLicense(candidate, null, beforeWrite)).code());
        assertNull(runtime.licenseFileDigest());
        var beforeRename = new OnboardingFiles() { @Override void move(Path source, Path target, boolean replace) throws IOException { throw new IOException("rename"); } };
        assertThrows(OnboardingException.class, () -> runtime.importLicense(candidate, null, beforeRename)); assertNull(runtime.licenseFileDigest());
        var afterPublish = new OnboardingFiles() { @Override void sync(Path path) throws IOException { throw new IOException("directory sync"); } };
        var unknown = runtime.importLicense(candidate, null, afterPublish);
        assertEquals(LicenseRuntime.ImportActionStatus.OUTCOME_UNKNOWN, unknown.actionStatus()); assertEquals(OnboardingFiles.digest(candidate), runtime.licenseFileDigest());
        assertEquals(LicenseRuntime.ImportActionStatus.ALREADY_APPLIED, runtime.importLicense(candidate, unknown.fileDigest()).actionStatus());
    }

    @Test void importThreadsWithSameOldDigestPublishAtMostOnce() throws Exception {
        LicenseInstallation.initialize(config, root); byte[] left = license(false, Instant.now().minusSeconds(10), null), right = license(false, Instant.now().minusSeconds(10), null);
        CountDownLatch ready = new CountDownLatch(2), go = new CountDownLatch(1); AtomicInteger applied = new AtomicInteger(), refused = new AtomicInteger();
        try (var executor = java.util.concurrent.Executors.newFixedThreadPool(2)) {
            var tasks = new java.util.ArrayList<java.util.concurrent.Future<?>>();
            for (byte[] bytes : List.of(left, right)) tasks.add(executor.submit(() -> {
                ready.countDown(); try { go.await(); runtime().importLicense(bytes, null); applied.incrementAndGet(); }
                catch (OnboardingException expected) { assertEquals(OnboardingException.Code.LICENSE_STATE_CONFLICT, expected.code()); refused.incrementAndGet(); }
                catch (IOException | InterruptedException failure) { throw new AssertionError(failure); }
            }));
            assertTrue(ready.await(3, TimeUnit.SECONDS)); go.countDown(); for (var task : tasks) task.get(3, TimeUnit.SECONDS);
        }
        assertEquals(1, applied.get()); assertEquals(1, refused.get());
    }

    private static final class ControlledScheduler extends ScheduledThreadPoolExecutor {
        Runnable task; int schedules;
        ControlledScheduler() { super(1); setExecuteExistingDelayedTasksAfterShutdownPolicy(false); }
        @Override public ScheduledFuture<?> scheduleWithFixedDelay(Runnable command, long initial, long delay, TimeUnit unit) {
            assertEquals(30, initial); assertEquals(30, delay); assertEquals(TimeUnit.SECONDS, unit);
            task = command; schedules++; return super.scheduleWithFixedDelay(command, 1, 1, TimeUnit.DAYS);
        }
    }
    @Test void lifecycleIsExplicitIdempotentAndDoesNotCheckpointGuards() throws Exception {
        LicenseInstallation.initialize(config, root); var runtime = runtime();
        byte[] candidate = license(false, Instant.now().minusSeconds(10), null); runtime.importLicense(candidate, null);
        var executor = new ControlledScheduler(); byte[] before = Files.readAllBytes(clock());
        assertSame(runtime, runtime.start(executor)); assertSame(runtime, runtime.start()); assertEquals(1, executor.schedules);
        runtime.requireValid(); runtime.inspect(); runtime.preflight(candidate); assertArrayEquals(before, Files.readAllBytes(clock()));
        executor.task.run(); assertEquals(1, clockState().sequence()); runtime.close(); runtime.close(); assertTrue(executor.isTerminated());
        assertThrows(IllegalStateException.class, runtime::start); assertEquals(1, clockState().sequence());
        var neverStarted = runtime(); neverStarted.close(); neverStarted.close();
        assertThrows(IllegalStateException.class, neverStarted::start);
    }

    @Test void factoryRuntimeRecoversSameInstanceAndRestartPreservesIdempotence() throws Exception {
        LicenseInstallation.initialize(config, root);
        byte[] commercial = license(false, Instant.now().minusSeconds(10), null);
        var runtime = runtime(); runtime.importLicense(commercial, null);
        byte[] other = Files.readAllBytes(root.resolve("state/worker/clock.json"));
        Files.writeString(clock(), "corrupt clock for controlled recovery test");
        assertEquals(LicenseRuntime.State.CLOCK_STATE_INVALID, runtime.inspect().state());
        var request = runtime.getOrCreateRecoveryRequest();
        Instant now = Instant.now().truncatedTo(java.time.temporal.ChronoUnit.SECONDS);
        var data = new LinkedHashMap<String, Object>();
        data.put("schema_version", 1); data.put("kind", "clock-recovery"); data.put("key_id", "test-key");
        data.put("product_id", "other-product"); data.put("installation_id", id().toString()); data.put("service", "backend");
        data.put("recovery_id", UUID.randomUUID().toString()); data.put("target_id", request.targetId());
        data.put("issued_at", now.toString()); data.put("window_start", now.toString()); data.put("window_end", now.plusSeconds(86400).toString());
        byte[] payload = JSON.writeValueAsBytes(data); var signer = Signature.getInstance("Ed25519");
        signer.initSign(signing.getPrivate()); signer.update(payload);
        byte[] recovery = JSON.writeValueAsBytes(Map.of("payload", Base64.getUrlEncoder().withoutPadding().encodeToString(payload),
            "signature", Base64.getUrlEncoder().withoutPadding().encodeToString(signer.sign())));
        assertEquals(RecoveryResult.ActionStatus.APPLIED, runtime.applyRecovery(recovery).actionStatus());
        assertEquals(LicenseRuntime.State.ACTIVE, runtime.inspect().state()); runtime.requireValid();
        assertEquals(RecoveryResult.ActionStatus.ALREADY_APPLIED, runtime.applyRecovery(recovery).actionStatus()); runtime.close();
        try (var restarted = runtime()) {
            assertEquals(RecoveryResult.ActionStatus.ALREADY_APPLIED, restarted.applyRecovery(recovery).actionStatus());
            restarted.requireValid();
        }
        assertArrayEquals(commercial, Files.readAllBytes(root.resolve("shared/active.license.json")));
        assertArrayEquals(other, Files.readAllBytes(root.resolve("state/worker/clock.json")));
    }

    @Test void lifecycleContinuesAfterFailureAndCloseWaitsWithoutRuntimeDeadlock() throws Exception {
        LicenseInstallation.initialize(config, root); var product = ProductConfig.parse(config); AtomicInteger calls = new AtomicInteger();
        var time = new TimeSource() { public Instant utcNow() { if (calls.incrementAndGet() == 1) throw new IllegalStateException("injected"); return Instant.now(); }
            public long monotonicNanos() { return System.nanoTime(); } };
        var configuration = new LicenseRuntime.Config(product.productId(), "backend", product.fingerprintDomain(), product.bindingDomain(), product.recoveryDomain(),
            root.resolve("identity/installation.json"), root.resolve("identity/service.key"), root.resolve("shared/active.license.json"), clock(), machine, "OP1B");
        var runtime = new LicenseRuntime(configuration, product.trustedKeys(), time); var executor = new ControlledScheduler(); runtime.start(executor);
        executor.task.run(); executor.task.run(); assertEquals(1, clockState().sequence()); runtime.close();
        var restarted = new LicenseRuntime(configuration, product.trustedKeys(), time);
        var actual = new ScheduledThreadPoolExecutor(1); actual.setExecuteExistingDelayedTasksAfterShutdownPolicy(false);
        restarted.start(actual); CountDownLatch begun = new CountDownLatch(1), release = new CountDownLatch(1);
        var work = actual.submit(() -> { synchronized (restarted) { begun.countDown(); try { release.await(); restarted.checkpoint(); } catch (InterruptedException e) { throw new AssertionError(e); } } });
        assertTrue(begun.await(3, TimeUnit.SECONDS));
        try (var closer = java.util.concurrent.Executors.newSingleThreadExecutor()) {
            var closing = closer.submit(restarted::close); release.countDown(); work.get(3, TimeUnit.SECONDS); closing.get(3, TimeUnit.SECONDS);
        }
    }

    @Test void cliRunsWithoutFrameworkAndDoesNotExitItsCaller() throws Exception {
        Path configuration = temporary.resolve("license-product.json"); Files.write(configuration, config);
        var out = new ByteArrayOutputStream(); var err = new ByteArrayOutputStream();
        try (var output = new PrintStream(out); var error = new PrintStream(err)) {
            assertEquals(0, LicenseCli.run(new String[]{"init", "--config", configuration.toString(), "--root", root.toString()}, output, error));
            assertTrue(out.toString(StandardCharsets.UTF_8).contains("INITIALIZED")); out.reset();
            assertEquals(0, LicenseCli.run(new String[]{"status", "--config", configuration.toString(), "--root", root.toString(), "--service", "backend"}, output, error));
            assertTrue(out.toString(StandardCharsets.UTF_8).contains("UNLICENSED"));
            out.reset();
            assertEquals(0, LicenseCli.run(new String[]{"request", "--config", configuration.toString(), "--root", root.toString(), "--service", "backend", "--machine-id", machine.toString()}, output, error));
            assertEquals("license-request", JSON.readTree(out.toByteArray()).get("kind").asText());
            Path request = temporary.resolve("request.json"); out.reset();
            assertEquals(0, LicenseCli.run(new String[]{"request", "--config", configuration.toString(), "--root", root.toString(), "--service", "backend", "--machine-id", machine.toString(), "--output", request.toString()}, output, error));
            assertEquals("license-request", JSON.readTree(request.toFile()).get("kind").asText());
            assertEquals(1, LicenseCli.run(new String[]{"request", "--config", configuration.toString(), "--root", root.toString(), "--service", "backend", "--machine-id", machine.toString(), "--output", request.toString()}, output, error));
            assertTrue(err.toString(StandardCharsets.UTF_8).contains("LICENSE_STATE_CONFLICT"));
            assertEquals(1, LicenseCli.run(new String[]{"status", "--config", configuration.toString(), "--root", root.toString()}, output, error));
            Files.delete(root.resolve(".init.lock")); Files.delete(machine); out.reset();
            assertEquals(0, LicenseCli.run(new String[]{"check", "--config", configuration.toString(), "--root", root.toString()}, output, error));
            assertEquals(id().toString(), JSON.readTree(out.toByteArray()).get("installationId").asText());
            assertFalse(Files.exists(root.resolve(".init.lock")));
            assertEquals(1, LicenseCli.run(new String[]{"check", "--config", configuration.toString(), "--root", root.toString(), "--service", "backend"}, output, error));
            Path candidate = temporary.resolve("customer.license.json"); Files.write(candidate, license(false, Instant.now().minusSeconds(10), null));
            String group = Files.getAttribute(root, "unix:gid").toString(); out.reset();
            assertEquals(0, LicenseCli.run(new String[]{"import", "--config", configuration.toString(), "--root", root.toString(), "--service", "backend", "--file", candidate.toString(), "--shared-group", group}, output, error));
            assertEquals("APPLIED", JSON.readTree(out.toByteArray()).get("actionStatus").asText());
            assertTrue(new OnboardingFiles().attributesMatch(root.resolve("shared/active.license.json"), Integer.valueOf(group)));
            Files.writeString(clock(), "damaged"); err.reset();
            assertEquals(1, LicenseCli.run(new String[]{"check", "--config", configuration.toString(), "--root", root.toString()}, output, error));
            assertTrue(err.toString(StandardCharsets.UTF_8).contains("INSTALLATION_INVALID"));
        }
    }
    private static Map<Path, byte[]> tree(Path root) throws IOException {
        Map<Path, byte[]> result = new LinkedHashMap<>(); try (var paths = Files.walk(root)) {
            for (Path path : paths.filter(Files::isRegularFile).toList()) if (!path.toString().endsWith(".lock")) result.put(path, Files.readAllBytes(path));
        } return result;
    }
    private static void assertTree(Map<Path, byte[]> before, Path root) throws IOException {
        Map<Path, byte[]> after = tree(root); assertEquals(before.keySet(), after.keySet());
        for (var entry : before.entrySet()) assertArrayEquals(entry.getValue(), after.get(entry.getKey()));
    }
}
