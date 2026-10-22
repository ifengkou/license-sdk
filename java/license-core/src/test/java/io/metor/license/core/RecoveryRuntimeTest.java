package io.metor.license.core;

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.Signature;
import java.time.Instant;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class RecoveryRuntimeTest {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final Instant NOW = Instant.parse("2026-10-22T00:00:00Z");
    private static final UUID INSTALL = UUID.fromString("11223344-5566-4788-99aa-bbccddeeff00");
    @TempDir Path temporary;
    @BeforeEach void canonicalTemporary() throws IOException { temporary = temporary.toRealPath(); }

    private static final class Clock implements TimeSource {
        Instant wall = NOW;
        long mono;
        public Instant utcNow() { return wall; }
        public long monotonicNanos() { return mono; }
    }
    private final class Fixture {
        final Path root = Files.createTempDirectory(temporary, "installation-");
        final Path clock = root.resolve("clock.json");
        final Path challenge = root.resolve("recovery-challenge.json");
        final Path license = root.resolve("active.license.json");
        final byte[] master = new byte[32];
        final KeyPair signer = KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
        final Clock time = new Clock();
        final ControlledStore store = new ControlledStore();
        final LicenseRuntime.Config config;
        Fixture() throws Exception {
            Path identity = root.resolve("installation.json");
            Files.write(identity, JSON.writeValueAsBytes(Map.of("schema_version", 1, "product_id", "icp-diag",
                "installation_id", INSTALL.toString(), "initialized", true)));
            Path key = root.resolve("service.key");
            Files.write(key, master);
            Files.write(clock, ClockStateCodec.encode(new ClockState("icp-diag", INSTALL, "metor", 0, 0,
                NOW.plusSeconds(2 * 86400), null), master));
            config = new LicenseRuntime.Config("icp-diag", "metor", "icp-diag/host-fingerprint/v1",
                "icp-diag/host-install-code/v1", "icp-diag/recovery-target/v1", identity, key, license, clock,
                root.resolve("machine-id"), "RC1J");
            commercial(false);
        }
        LicenseRuntime runtime() { return new LicenseRuntime(config, Map.of("recovery-test", signer.getPublic()), time, store); }
        ClockState clock() throws IOException { return ClockStateCodec.decode(Files.readAllBytes(clock), master, "icp-diag", INSTALL, "metor"); }
        void commercial(boolean expired) throws Exception {
            Map<String, Object> data = new LinkedHashMap<>();
            data.put("schema_version", 1); data.put("key_id", "recovery-test"); data.put("kind", "commercial-license");
            data.put("license_id", UUID.randomUUID().toString()); data.put("customer_id", "synthetic"); data.put("customer_name", "Synthetic");
            data.put("product_id", "icp-diag"); data.put("binding_type", "UNBOUND"); data.put("binding_scheme", null); data.put("binding_id", null);
            data.put("validity_type", expired ? "TERM" : "PERPETUAL"); data.put("issued_at", NOW.minusSeconds(86400).toString());
            data.put("not_before", NOW.minusSeconds(86400).toString()); data.put("expires_at", expired ? NOW.toString() : null);
            Files.write(license, sign(data));
        }
        Map<String, Object> payload(RecoveryRequest request) {
            Map<String, Object> data = new LinkedHashMap<>();
            data.put("schema_version", 1); data.put("kind", "clock-recovery"); data.put("key_id", "recovery-test");
            data.put("product_id", "icp-diag"); data.put("installation_id", INSTALL.toString()); data.put("service", "metor");
            data.put("recovery_id", UUID.randomUUID().toString()); data.put("target_id", request.targetId());
            data.put("issued_at", NOW.toString()); data.put("window_start", NOW.toString()); data.put("window_end", NOW.plusSeconds(7 * 86400).toString());
            return data;
        }
        byte[] sign(Map<String, Object> data) throws Exception { return signRaw(JSON.writeValueAsBytes(data)); }
        byte[] signRaw(byte[] raw) throws Exception {
            Signature signature = Signature.getInstance("Ed25519"); signature.initSign(signer.getPrivate()); signature.update(raw);
            return JSON.writeValueAsBytes(Map.of("payload", base(raw), "signature", base(signature.sign())));
        }
        byte[] recovery(RecoveryRequest request) throws Exception { return sign(payload(request)); }
    }
    private static final class ControlledStore implements StateStore {
        final StateStore real = new FileStateStore();
        CommitOutcome clockCommit = CommitOutcome.COMMITTED;
        CommitOutcome challengeCommit = CommitOutcome.COMMITTED;
        CommitOutcome confirmation = CommitOutcome.COMMITTED;
        boolean cleanupFails;
        CountDownLatch entered;
        CountDownLatch proceed;
        public byte[] read(Path path, int max) throws IOException { return real.read(path, max); }
        public <T> T withLock(Path path, LockedAction<T> action) throws IOException { return real.withLock(path, action); }
        public CommitOutcome replace(Path path, byte[] bytes) throws IOException { return real.replace(path, bytes); }
        public CommitOutcome replaceRecovery(Path path, byte[] expected, byte[] replacement) throws IOException {
            if (entered != null && path.getFileName().toString().equals("clock.json")) {
                entered.countDown();
                try { if (!proceed.await(5, TimeUnit.SECONDS)) throw new IOException("Test barrier timed out"); }
                catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); throw new IOException(interrupted); }
            }
            CommitOutcome result = path.getFileName().toString().equals("clock.json") ? clockCommit : challengeCommit;
            if (result == CommitOutcome.NOT_COMMITTED) return result;
            CommitOutcome actual = real.replaceRecovery(path, expected, replacement);
            return actual == CommitOutcome.COMMITTED ? result : actual;
        }
        public CommitOutcome confirmDurability(Path path, byte[] expected) throws IOException {
            return confirmation == CommitOutcome.COMMITTED ? real.confirmDurability(path, expected) : confirmation;
        }
        public void deleteRecoveryChallenge(Path path) throws IOException {
            if (cleanupFails) throw new IOException("Injected cleanup failure");
            real.deleteRecoveryChallenge(path);
        }
    }
    private static String base(byte[] raw) { return Base64.getUrlEncoder().withoutPadding().encodeToString(raw); }
    private static void refusal(RecoveryException.Code reason, org.junit.jupiter.api.function.Executable action) {
        assertEquals(reason, assertThrows(RecoveryException.class, action).reason());
    }

    @Test void requestIsStableAcrossConcurrentCallsAndRestart() throws Exception {
        Fixture f = new Fixture(); LicenseRuntime runtime = f.runtime();
        RecoveryRequest first = runtime.getOrCreateRecoveryRequest();
        byte[] raw = Files.readAllBytes(f.challenge);
        assertEquals("RC1J", first.recoveryCode().substring(0, 4));
        assertEquals(first.targetId(), new ManualCodeCodec("RC1J").decodeHex(first.recoveryCode()));
        try (var pool = java.util.concurrent.Executors.newFixedThreadPool(4)) {
            var pending = new java.util.ArrayList<java.util.concurrent.Future<RecoveryRequest>>();
            for (int index = 0; index < 8; index++) pending.add(pool.submit(runtime::getOrCreateRecoveryRequest));
            for (var result : pending) assertEquals(first, result.get(5, TimeUnit.SECONDS));
        }
        assertEquals(first, f.runtime().getOrCreateRecoveryRequest());
        assertArrayEquals(raw, Files.readAllBytes(f.challenge));
        assertEquals(JSON.readTree(JSON.writeValueAsBytes(first.toMap())), JSON.readTree(first.toJson()));
    }

    @Test void normalOrCommercialOnlyFailureCannotCreateAChallenge() throws Exception {
        Fixture f = new Fixture();
        Files.write(f.clock, ClockStateCodec.encode(new ClockState("icp-diag", INSTALL, "metor", 0, 0, NOW, null), f.master));
        refusal(RecoveryException.Code.NOT_REQUIRED, () -> f.runtime().getOrCreateRecoveryRequest());
        f.commercial(true);
        refusal(RecoveryException.Code.NOT_REQUIRED, () -> f.runtime().getOrCreateRecoveryRequest());
        Files.delete(f.license);
        refusal(RecoveryException.Code.NOT_REQUIRED, () -> f.runtime().getOrCreateRecoveryRequest());
        assertFalse(Files.exists(f.challenge));
    }

    @Test void applyRefreshesSameRuntimeAndDoesNotAlterLicenseOrAnotherService() throws Exception {
        Fixture f = new Fixture(); LicenseRuntime runtime = f.runtime();
        RecoveryRequest request = runtime.getOrCreateRecoveryRequest(); byte[] signed = f.recovery(request);
        byte[] commercial = Files.readAllBytes(f.license);
        Path other = f.root.resolve("other-clock.json"); Files.write(other, new byte[] {9});
        RecoveryResult result = runtime.applyRecovery(signed);
        assertEquals(RecoveryResult.ActionStatus.APPLIED, result.actionStatus());
        assertEquals(LicenseRuntime.State.ACTIVE, result.status().state()); runtime.requireValid();
        assertEquals(1, f.clock().epoch()); assertEquals(0, f.clock().sequence()); assertEquals(NOW, f.clock().highWatermark());
        assertEquals(result.recoveryId(), f.clock().lastRecoveryId()); assertFalse(Files.exists(f.challenge));
        assertArrayEquals(commercial, Files.readAllBytes(f.license)); assertArrayEquals(new byte[] {9}, Files.readAllBytes(other));
        f.time.wall = NOW.plusSeconds(8 * 86400); f.time.mono = 8 * 86400_000_000_000L;
        runtime.checkpoint(); ClockState after = f.clock();
        assertEquals(RecoveryResult.ActionStatus.ALREADY_APPLIED, runtime.applyRecovery(signed).actionStatus());
        assertEquals(after, f.clock());
        assertEquals(RecoveryResult.ActionStatus.ALREADY_APPLIED, f.runtime().applyRecovery(signed).actionStatus());
    }

    @Test void recoveryDoesNotExtendExpiredCommercialLicense() throws Exception {
        Fixture f = new Fixture(); f.commercial(true); LicenseRuntime runtime = f.runtime();
        var result = runtime.applyRecovery(f.recovery(runtime.getOrCreateRecoveryRequest()));
        assertEquals(RecoveryResult.ActionStatus.APPLIED, result.actionStatus());
        assertEquals(LicenseRuntime.State.EXPIRED, result.status().state());
        assertEquals("EXPIRED", assertThrows(LicenseDeniedException.class, runtime::requireValid).state().name());
    }

    @Test void missingAndDamagedClockRecoverWithStableRequestAfterRestart() throws Exception {
        for (boolean missing : List.of(false, true)) {
            Fixture f = new Fixture(); LicenseRuntime runtime = f.runtime(); runtime.inspect();
            if (missing) Files.delete(f.clock); else Files.write(f.clock, new byte[] {1, 2});
            RecoveryRequest request = runtime.getOrCreateRecoveryRequest();
            LicenseRuntime restarted = f.runtime();
            assertEquals(request, restarted.getOrCreateRecoveryRequest());
            assertEquals(RecoveryResult.ActionStatus.APPLIED, restarted.applyRecovery(f.recovery(request)).actionStatus());
            assertEquals(1, f.clock().epoch()); assertEquals(LicenseRuntime.State.ACTIVE, restarted.inspect().state());
        }
    }

    @Test void missingClockWithoutKnownEpochUsesFirstRecoveryEpoch() throws Exception {
        Fixture f = new Fixture(); Files.delete(f.clock); LicenseRuntime runtime = f.runtime();
        RecoveryRequest request = runtime.getOrCreateRecoveryRequest(); assertNull(request.baseEpoch());
        runtime.applyRecovery(f.recovery(request)); assertEquals(1, f.clock().epoch());
    }

    @Test void invalidSignaturePurposeSchemaOrEncodingNeverChangesState() throws Exception {
        Fixture f = new Fixture(); LicenseRuntime runtime = f.runtime(); RecoveryRequest request = runtime.getOrCreateRecoveryRequest();
        byte[] before = Files.readAllBytes(f.clock); byte[] challenge = Files.readAllBytes(f.challenge);
        for (Map<String, Object> patch : List.<Map<String, Object>>of(Map.of("kind", "commercial-license"),
            Map.of("schema_version", 2), Map.of("unknown", 1), Map.of("key_id", "unknown"), Map.of("product_id", "other"),
            Map.of("recovery_id", "not-a-uuid"), Map.of("target_id", "A".repeat(40)), Map.of("window_end", "2026-10-30T00:00:00Z"))) {
            Map<String, Object> data = f.payload(request); data.putAll(patch);
            refusal(RecoveryException.Code.INVALID, () -> runtime.applyRecovery(f.sign(data)));
        }
        refusal(RecoveryException.Code.INVALID, () -> runtime.applyRecovery(new byte[] {0}));
        var outer = JSON.readTree(f.recovery(request));
        byte[] padded = JSON.writeValueAsBytes(Map.of("payload", outer.path("payload").asText() + "=", "signature", outer.path("signature").asText()));
        refusal(RecoveryException.Code.INVALID, () -> runtime.applyRecovery(padded));
        byte[] duplicate = ("{\"schema_version\":1," + new String(JSON.writeValueAsBytes(f.payload(request)), StandardCharsets.UTF_8).substring(1)).getBytes(StandardCharsets.UTF_8);
        refusal(RecoveryException.Code.INVALID, () -> runtime.applyRecovery(f.signRaw(duplicate)));
        assertArrayEquals(before, Files.readAllBytes(f.clock)); assertArrayEquals(challenge, Files.readAllBytes(f.challenge));
    }

    @Test void wrongInstallServiceOrTargetIsRejected() throws Exception {
        Fixture f = new Fixture(); LicenseRuntime runtime = f.runtime(); RecoveryRequest request = runtime.getOrCreateRecoveryRequest();
        for (Map<String, Object> patch : List.<Map<String, Object>>of(Map.of("installation_id", UUID.randomUUID().toString()),
            Map.of("service", "ai"), Map.of("target_id", "0".repeat(40)))) {
            Map<String, Object> data = f.payload(request); data.putAll(patch);
            refusal(RecoveryException.Code.TARGET_CHANGED, () -> runtime.applyRecovery(f.sign(data)));
        }
        assertEquals(0, f.clock().epoch());
    }

    @Test void windowIsExactAndNotExtendedByRollbackTolerance() throws Exception {
        for (long second : new long[] {-1, 0, 7 * 86400 - 1, 7 * 86400}) {
            Fixture f = new Fixture(); LicenseRuntime runtime = f.runtime(); RecoveryRequest request = runtime.getOrCreateRecoveryRequest();
            byte[] signed = f.recovery(request); f.time.wall = NOW.plusSeconds(second);
            if (second < 0 || second == 7 * 86400) refusal(RecoveryException.Code.WINDOW_INVALID, () -> runtime.applyRecovery(signed));
            else if (second == 0) assertEquals(RecoveryResult.ActionStatus.APPLIED, runtime.applyRecovery(signed).actionStatus());
            else refusal(RecoveryException.Code.TARGET_CHANGED, () -> runtime.applyRecovery(signed)); // Wall caught up, so recovery is no longer needed.
        }
    }

    @Test void changedClockOrChallengeCannotOverwriteNewState() throws Exception {
        Fixture f = new Fixture(); LicenseRuntime runtime = f.runtime(); RecoveryRequest first = runtime.getOrCreateRecoveryRequest();
        byte[] signed = f.recovery(first); Files.write(f.clock, new byte[] {4});
        refusal(RecoveryException.Code.TARGET_CHANGED, () -> runtime.applyRecovery(signed));
        RecoveryRequest next = runtime.getOrCreateRecoveryRequest(); assertNotEquals(first.nonce(), next.nonce());
        Files.write(f.challenge, new byte[] {9});
        refusal(RecoveryException.Code.TARGET_CHANGED, () -> runtime.applyRecovery(f.recovery(next)));
        assertArrayEquals(new byte[] {4}, Files.readAllBytes(f.clock));
    }

    @Test void precommitFailureLeavesRequestUsableForRetry() throws Exception {
        Fixture f = new Fixture(); LicenseRuntime runtime = f.runtime(); RecoveryRequest request = runtime.getOrCreateRecoveryRequest();
        byte[] signed = f.recovery(request); byte[] before = Files.readAllBytes(f.clock);
        f.store.clockCommit = StateStore.CommitOutcome.NOT_COMMITTED;
        refusal(RecoveryException.Code.STORAGE_FAILED, () -> runtime.applyRecovery(signed));
        assertArrayEquals(before, Files.readAllBytes(f.clock)); assertEquals(request, runtime.getOrCreateRecoveryRequest());
        f.store.clockCommit = StateStore.CommitOutcome.COMMITTED;
        assertEquals(RecoveryResult.ActionStatus.APPLIED, runtime.applyRecovery(signed).actionStatus());
    }

    @Test void unknownCommitDoesNotClearRestrictionAndConfirmedRetryDoesNotResetTwice() throws Exception {
        Fixture f = new Fixture(); LicenseRuntime runtime = f.runtime(); RecoveryRequest request = runtime.getOrCreateRecoveryRequest();
        byte[] signed = f.recovery(request); f.store.clockCommit = StateStore.CommitOutcome.COMMIT_DURABILITY_UNKNOWN;
        var result = runtime.applyRecovery(signed); assertEquals(RecoveryResult.ActionStatus.OUTCOME_UNKNOWN, result.actionStatus());
        assertEquals(LicenseRuntime.State.CLOCK_STATE_INVALID, runtime.inspect().state()); assertEquals(1, f.clock().epoch());
        f.store.confirmation = StateStore.CommitOutcome.COMMIT_DURABILITY_UNKNOWN;
        assertEquals(RecoveryResult.ActionStatus.OUTCOME_UNKNOWN, runtime.applyRecovery(signed).actionStatus());
        f.store.confirmation = StateStore.CommitOutcome.COMMITTED;
        assertEquals(RecoveryResult.ActionStatus.ALREADY_APPLIED, runtime.applyRecovery(signed).actionStatus());
        runtime.requireValid(); assertEquals(1, f.clock().epoch()); assertEquals(NOW, f.clock().highWatermark());
        assertEquals(RecoveryResult.ActionStatus.ALREADY_APPLIED, f.runtime().applyRecovery(signed).actionStatus());
    }

    @Test void unknownChallengeCommitReusesPublishedNonceOnRetryAndRestart() throws Exception {
        Fixture f = new Fixture(); LicenseRuntime runtime = f.runtime();
        f.store.challengeCommit = StateStore.CommitOutcome.COMMIT_DURABILITY_UNKNOWN;
        refusal(RecoveryException.Code.OUTCOME_UNKNOWN, runtime::getOrCreateRecoveryRequest);
        byte[] published = Files.readAllBytes(f.challenge);
        var challenge = RecoveryChallengeCodec.decode(published, f.master, "icp-diag", INSTALL, "metor");
        var request = f.runtime().getOrCreateRecoveryRequest(); assertEquals(challenge.nonce(), request.nonce());
        assertArrayEquals(published, Files.readAllBytes(f.challenge));
    }

    @Test void cleanupFailureCannotUndoDurableRecoveryOrAllowSecondReset() throws Exception {
        Fixture f = new Fixture(); LicenseRuntime runtime = f.runtime(); var request = runtime.getOrCreateRecoveryRequest();
        byte[] signed = f.recovery(request); f.store.cleanupFails = true;
        assertEquals(LicenseRuntime.State.ACTIVE, runtime.applyRecovery(signed).status().state());
        assertTrue(Files.exists(f.challenge));
        assertEquals(RecoveryResult.ActionStatus.ALREADY_APPLIED, runtime.applyRecovery(signed).actionStatus());
        assertEquals(1, f.clock().epoch()); runtime.requireValid();
    }

    @Test void correctedNormalObservationInvalidatesOldChallenge() throws Exception {
        Fixture f = new Fixture(); LicenseRuntime runtime = f.runtime(); var request = runtime.getOrCreateRecoveryRequest();
        f.time.wall = NOW.plusSeconds(2 * 86400); runtime.inspect(); assertFalse(Files.exists(f.challenge));
        f.time.wall = NOW;
        refusal(RecoveryException.Code.TARGET_CHANGED, () -> runtime.applyRecovery(f.recovery(request)));
        assertNotEquals(request.nonce(), runtime.getOrCreateRecoveryRequest().nonce());
    }

    @Test void failedNormalInvalidationStillRejectsTheOldTargetInThisRuntime() throws Exception {
        Fixture f = new Fixture(); LicenseRuntime runtime = f.runtime(); var request = runtime.getOrCreateRecoveryRequest();
        byte[] signed = f.recovery(request); f.store.cleanupFails = true;
        f.time.wall = NOW.plusSeconds(2 * 86400); assertEquals(LicenseRuntime.State.CLOCK_STATE_INVALID, runtime.inspect().state());
        f.time.wall = NOW;
        refusal(RecoveryException.Code.TARGET_CHANGED, () -> runtime.applyRecovery(signed));
        assertNotEquals(request.nonce(), runtime.getOrCreateRecoveryRequest().nonce());
    }

    @Test void idempotentApplyNeverLowersAReferenceObservedAfterRecovery() throws Exception {
        Fixture f = new Fixture(); LicenseRuntime runtime = f.runtime(); byte[] signed = f.recovery(runtime.getOrCreateRecoveryRequest());
        runtime.applyRecovery(signed);
        f.time.wall = NOW.plusSeconds(10 * 86400); runtime.inspect();
        f.time.wall = NOW;
        var result = runtime.applyRecovery(signed);
        assertEquals(RecoveryResult.ActionStatus.ALREADY_APPLIED, result.actionStatus());
        assertEquals(LicenseRuntime.State.CLOCK_ROLLBACK, result.status().state());
        assertEquals(NOW.plusSeconds(10 * 86400), result.status().effectiveNow());
        assertEquals(1, f.clock().epoch());
    }

    @Test void unknownRecoveryCanBeConfirmedAfterProcessRestart() throws Exception {
        Fixture f = new Fixture(); LicenseRuntime runtime = f.runtime(); byte[] signed = f.recovery(runtime.getOrCreateRecoveryRequest());
        f.store.clockCommit = StateStore.CommitOutcome.COMMIT_DURABILITY_UNKNOWN;
        assertEquals(RecoveryResult.ActionStatus.OUTCOME_UNKNOWN, runtime.applyRecovery(signed).actionStatus());
        var restarted = f.runtime();
        assertEquals(LicenseRuntime.State.ACTIVE, restarted.applyRecovery(signed).status().state());
        assertEquals(1, f.clock().epoch());
    }

    @Test void recoveryStoreRequiresExactExpectedBytesAndNormalReplaceCannotCreate() throws Exception {
        Fixture f = new Fixture(); FileStateStore store = new FileStateStore(); Path missing = f.root.resolve("missing-state.json");
        assertEquals(StateStore.CommitOutcome.NOT_COMMITTED, store.replace(missing, new byte[] {1}));
        assertEquals(StateStore.CommitOutcome.COMMITTED, store.replaceRecovery(missing, null, new byte[] {1}));
        assertEquals(StateStore.CommitOutcome.NOT_COMMITTED, store.replaceRecovery(missing, null, new byte[] {2}));
        assertEquals(StateStore.CommitOutcome.NOT_COMMITTED, store.replaceRecovery(missing, new byte[] {3}, new byte[] {2}));
        assertArrayEquals(new byte[] {1}, store.read(missing, 8192));
        assertEquals(StateStore.CommitOutcome.COMMITTED, store.confirmDurability(missing, new byte[] {1}));
    }

    @Test void unsafeOrOversizedPathsAreNeverRecoveryTargets() throws Exception {
        for (String damage : List.of("symlink", "directory", "oversized")) {
            Fixture f = new Fixture(); Files.delete(f.clock);
            if (damage.equals("symlink")) Files.createSymbolicLink(f.clock, f.license);
            else if (damage.equals("directory")) Files.createDirectory(f.clock);
            else Files.write(f.clock, new byte[8193]);
            refusal(RecoveryException.Code.STORAGE_FAILED, () -> f.runtime().getOrCreateRecoveryRequest());
            assertFalse(Files.exists(f.challenge));
        }
    }

    @Test void invalidIdentityNeverBecomesARecoverableClockAnomaly() throws Exception {
        Fixture f = new Fixture(); Files.delete(f.config.serviceKeyFile());
        refusal(RecoveryException.Code.CONFIG_INVALID, () -> f.runtime().getOrCreateRecoveryRequest());
        assertFalse(Files.exists(f.challenge));
    }

    @Test void invalidTrustStillProducesConfigurationStatusInsteadOfBreakingStartup() throws Exception {
        Fixture f = new Fixture(); Map<String, java.security.PublicKey> invalid = new java.util.HashMap<>();
        invalid.put("recovery-test", null);
        var runtime = new LicenseRuntime(f.config, invalid, f.time, f.store);
        assertEquals(LicenseRuntime.State.CONFIG_INVALID, runtime.inspect().state());
        refusal(RecoveryException.Code.CONFIG_INVALID, runtime::getOrCreateRecoveryRequest);
        assertFalse(Files.exists(f.challenge));
    }

    @Test void counterOverflowRefusesWithoutWriting() throws Exception {
        Fixture f = new Fixture(); Files.write(f.clock, ClockStateCodec.encode(new ClockState("icp-diag", INSTALL, "metor",
            Long.MAX_VALUE, 0, NOW.plusSeconds(2 * 86400), null), f.master));
        var runtime = f.runtime(); byte[] before = Files.readAllBytes(f.clock);
        refusal(RecoveryException.Code.STORAGE_FAILED, () -> runtime.applyRecovery(f.recovery(runtime.getOrCreateRecoveryRequest())));
        assertArrayEquals(before, Files.readAllBytes(f.clock));
    }

    @Test void checkpointWaitsForRecoveryCommitAndUsesTheNewEpoch() throws Exception {
        Fixture f = new Fixture(); LicenseRuntime runtime = f.runtime(); byte[] signed = f.recovery(runtime.getOrCreateRecoveryRequest());
        f.store.entered = new CountDownLatch(1); f.store.proceed = new CountDownLatch(1);
        try (var pool = java.util.concurrent.Executors.newFixedThreadPool(2)) {
            var application = pool.submit(() -> runtime.applyRecovery(signed));
            assertTrue(f.store.entered.await(5, TimeUnit.SECONDS));
            var checkpoint = pool.submit(runtime::checkpoint); assertFalse(checkpoint.isDone());
            f.store.proceed.countDown();
            assertEquals(RecoveryResult.ActionStatus.APPLIED, application.get(5, TimeUnit.SECONDS).actionStatus());
            assertEquals(LicenseRuntime.State.ACTIVE, checkpoint.get(5, TimeUnit.SECONDS).state());
        }
        assertEquals(1, f.clock().epoch()); assertEquals(1, f.clock().sequence()); runtime.requireValid();
    }

    @Test void sharedRecoveryVectorsMatchBothWireFormatsAndRejectInvalidFiles() throws Exception {
        com.fasterxml.jackson.databind.JsonNode vectors;
        try (var input = getClass().getResourceAsStream("/recovery-v1-vectors.json")) { vectors = JSON.readTree(input); }
        UUID installation = UUID.fromString(vectors.path("installation_id").asText());
        byte[] master = java.util.HexFormat.of().parseHex(vectors.path("service_key_hex").asText());
        var publicKey = java.security.KeyFactory.getInstance("Ed25519").generatePublic(new java.security.spec.X509EncodedKeySpec(
            Base64.getDecoder().decode(vectors.path("public_key_der_base64").asText())));
        var verifier = new RecoveryVerifier("icp-diag", Map.of("recovery-test", publicKey));
        var derivation = new IdentityDerivation("icp-diag/host-fingerprint/v1", "icp-diag/host-install-code/v1", "icp-diag/recovery-target/v1");
        for (var vector : vectors.path("vectors")) {
            String service = vector.path("service").asText();
            byte[] rawChallenge = vector.path("challenge").asText().getBytes(StandardCharsets.UTF_8);
            var challenge = RecoveryChallengeCodec.decode(rawChallenge, master, "icp-diag", installation, service);
            assertArrayEquals(rawChallenge, RecoveryChallengeCodec.encode(challenge, master));
            var request = RecoveryChallengeCodec.request(challenge, derivation, service.equals("metor") ? "RC1J" : "RC1A");
            assertEquals(vector.path("request"), JSON.readTree(request.toJson()));
            byte[] signed = vector.path("recovery").asText().getBytes(StandardCharsets.UTF_8);
            assertEquals(request.targetId(), verifier.verify(signed, installation, service).targetId());
            assertEquals(vector.path("sha256").asText(), java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(signed)));
        }
        for (var vector : vectors.path("negative_vectors")) {
            RecoveryException error = assertThrows(RecoveryException.class,
                () -> verifier.verify(vector.path("file").asText().getBytes(StandardCharsets.UTF_8), installation, "metor"), vector.path("name").asText());
            assertEquals(vector.path("error").asText(), error.code());
        }
    }
}
