package io.metor.license.core;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
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
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.io.TempDir;

class LicenseRuntimeTest {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final Instant START = Instant.parse("2026-10-21T00:00:00Z");
    private static final UUID INSTALLATION = UUID.fromString("00112233-4455-4677-8899-aabbccddeeff");
    @TempDir Path temporary;
    @BeforeEach void canonicalTemporaryPath() throws IOException { temporary = temporary.toRealPath(); }

    private static final class FakeTime implements TimeSource {
        private Instant wall = START;
        private long mono;
        public Instant utcNow() { return wall; }
        public long monotonicNanos() { return mono; }
        void advance(long nanos) { mono += nanos; wall = wall.plusNanos(nanos); }
    }

    @Test void candidatePreflightAndInspectionNeverCheckpointEvenAfterThirtySeconds() throws Exception {
        Fixture f = new Fixture(); var runtime = f.runtime(); runtime.status();
        byte[] clockBefore = Files.readAllBytes(f.clock); byte[] candidate = Files.readAllBytes(f.license);
        Files.delete(f.license); f.time.advance(31_000_000_000L);
        assertEquals(CommercialLicenseVerifier.Status.ACTIVE, runtime.preflight(candidate).status());
        assertEquals(LicenseRuntime.State.UNLICENSED, runtime.inspect().state());
        assertArrayEquals(clockBefore, Files.readAllBytes(f.clock)); assertFalse(Files.exists(f.license));
        Files.write(f.clock, new byte[]{0});
        assertEquals(CommercialLicenseVerifier.Status.ACTIVE, runtime.preflight(candidate).status());
        assertEquals(LicenseRuntime.State.CLOCK_STATE_INVALID, afterInstall(f, runtime, candidate).state());
        assertArrayEquals(new byte[]{0}, Files.readAllBytes(f.clock));
    }
    private static LicenseRuntime.Snapshot afterInstall(Fixture f, LicenseRuntime runtime, byte[] candidate) throws IOException {
        Files.write(f.license, candidate); return runtime.inspect();
    }

    private final class Fixture {
        final Path identity;
        final Path keyFile;
        final Path license;
        final Path clock;
        final Path machine;
        final byte[] key = new byte[32];
        final FakeTime time = new FakeTime();
        final KeyPair signing;
        final LicenseRuntime.Config config;
        Fixture() throws Exception {
            identity = temporary.resolve("installation.json");
            keyFile = temporary.resolve("service.key");
            license = temporary.resolve("active.license.json");
            clock = temporary.resolve("clock.json");
            machine = temporary.resolve("machine-id");
            Arrays.fill(key, (byte) 9);
            signing = KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
            Files.write(identity, JSON.writeValueAsBytes(Map.of("schema_version", 1,
                "product_id", "icp-diag", "installation_id", INSTALLATION.toString(), "initialized", true)));
            Files.write(keyFile, key);
            Files.write(clock, ClockStateCodec.encode(state(START, 0), key));
            config = new LicenseRuntime.Config("icp-diag", "metor", "icp-diag/host-fingerprint/v1",
                "icp-diag/host-install-code/v1", "icp-diag/recovery-target/v1", identity, keyFile,
                license, clock, machine);
            writeLicense(false, null);
        }
        ClockState state(Instant high, long sequence) {
            return new ClockState("icp-diag", INSTALLATION, "metor", 0, sequence, high, null);
        }
        LicenseRuntime runtime() { return runtime(new FileStateStore()); }
        LicenseRuntime runtime(StateStore store) {
            return new LicenseRuntime(config, Map.of("runtime-test", signing.getPublic()), time, store);
        }
        void writeLicense(boolean bound, Instant expires) throws Exception {
            Map<String, Object> data = new LinkedHashMap<>();
            data.put("schema_version", 1);
            data.put("key_id", "runtime-test");
            data.put("kind", "commercial-license");
            data.put("license_id", "00000000-0000-4000-8000-000000000001");
            data.put("customer_id", "synthetic");
            data.put("customer_name", "Synthetic");
            data.put("product_id", "icp-diag");
            data.put("binding_type", bound ? "HOST_INSTALL" : "UNBOUND");
            data.put("binding_scheme", bound ? "host-install-code-v1" : null);
            String binding = java.util.HexFormat.of().formatHex(new IdentityDerivation(config.fingerprintDomain(),
                config.bindingDomain(), config.recoveryDomain()).binding("00112233445566778899aabbccddeeff", INSTALLATION));
            data.put("binding_id", bound ? binding : null);
            data.put("validity_type", expires == null ? "PERPETUAL" : "TERM");
            data.put("issued_at", START.toString());
            data.put("not_before", START.toString());
            data.put("expires_at", expires == null ? null : expires.toString());
            byte[] raw = JSON.writeValueAsBytes(data);
            Signature signature = Signature.getInstance("Ed25519");
            signature.initSign(signing.getPrivate());
            signature.update(raw);
            Files.write(license, JSON.writeValueAsBytes(Map.of("payload", base(raw), "signature", base(signature.sign()))));
        }
    }

    @Test void perpetualAndUnboundStillRequireIdentityAndClockButNeverReadHost() throws Exception {
        Fixture fixture = new Fixture();
        assertEquals(LicenseRuntime.State.ACTIVE, fixture.runtime().status().state());
        assertFalse(Files.exists(fixture.machine));
        Files.delete(fixture.clock);
        assertEquals(LicenseRuntime.State.CLOCK_STATE_INVALID, fixture.runtime().status().state());
        assertFalse(Files.exists(fixture.clock));
        Files.delete(fixture.keyFile);
        assertEquals(LicenseRuntime.State.CONFIG_INVALID, fixture.runtime().status().state());
    }

    @Test void rollbackStrictlyExceedsThreeHundredSecondsAndCanCatchUp() throws Exception {
        Fixture fixture = new Fixture();
        LicenseRuntime runtime = fixture.runtime();
        assertEquals(LicenseRuntime.State.ACTIVE, runtime.status().state());
        fixture.time.wall = START.minusSeconds(300);
        assertEquals(LicenseRuntime.State.ACTIVE, runtime.status().state());
        fixture.time.wall = fixture.time.wall.minusNanos(1);
        assertEquals(LicenseRuntime.State.CLOCK_ROLLBACK, runtime.status().state());
        fixture.time.wall = START;
        assertEquals(LicenseRuntime.State.ACTIVE, runtime.status().state());
    }

    @Test void repeatedSmallRollbacksDoNotLowerMonotonicReference() throws Exception {
        Fixture fixture = new Fixture();
        LicenseRuntime runtime = fixture.runtime();
        runtime.status();
        for (int step = 0; step < 3; step++) {
            fixture.time.mono += 100_000_000_000L;
            assertEquals(LicenseRuntime.State.ACTIVE, runtime.status().state());
        }
        fixture.time.mono++;
        assertEquals(LicenseRuntime.State.CLOCK_ROLLBACK, runtime.status().state());
        assertArrayEquals(ClockStateCodec.encode(fixture.state(START.plusSeconds(300), 3), fixture.key), Files.readAllBytes(fixture.clock));
    }

    @Test void exactExpiryUsesNanosecondsWithoutRoundingGraceAndRechecksCachedLicense() throws Exception {
        Fixture fixture = new Fixture();
        fixture.writeLicense(false, START.plusSeconds(1));
        LicenseRuntime runtime = fixture.runtime();
        runtime.status();
        fixture.time.advance(999_999_999L);
        assertEquals(LicenseRuntime.State.ACTIVE, runtime.status().state());
        fixture.time.advance(1);
        assertEquals(LicenseRuntime.State.EXPIRED, runtime.status().state());
        fixture.time.wall = START;
        assertEquals(LicenseRuntime.State.EXPIRED, runtime.status().state());
    }

    @Test void checkpointsPersistThirtySecondsAndBeforeExecutionPersistsEarlier() throws Exception {
        Fixture fixture = new Fixture();
        LicenseRuntime runtime = fixture.runtime();
        byte[] original = Files.readAllBytes(fixture.clock);
        runtime.status();
        fixture.time.advance(29_999_999_999L);
        runtime.status();
        assertArrayEquals(original, Files.readAllBytes(fixture.clock));
        runtime.beforeExecution();
        ClockState state = ClockStateCodec.decode(Files.readAllBytes(fixture.clock), fixture.key, "icp-diag", INSTALLATION, "metor");
        assertEquals(START.plusSeconds(29), state.highWatermark());
        fixture.time.advance(30_000_000_000L);
        runtime.status();
        assertEquals(2, ClockStateCodec.decode(Files.readAllBytes(fixture.clock), fixture.key, "icp-diag", INSTALLATION, "metor").sequence());
        assertEquals("rw-------", PosixFilePermissions.toString(Files.getPosixFilePermissions(fixture.clock)));
    }

    @Test void restartKeepsPersistedHighWatermarkAndFuturePoisoningIsNotReset() throws Exception {
        Fixture fixture = new Fixture();
        LicenseRuntime first = fixture.runtime();
        first.status();
        fixture.time.advance(600_000_000_000L);
        first.checkpoint();
        fixture.time.wall = START;
        assertEquals(LicenseRuntime.State.CLOCK_ROLLBACK, fixture.runtime().status().state());
        fixture.time.wall = START.plusSeconds(600);
        assertEquals(LicenseRuntime.State.ACTIVE, fixture.runtime().status().state());
    }

    @Test void observesClockDeletionHmacTamperingAndLicenseDeletionAfterActiveCache() throws Exception {
        Fixture fixture = new Fixture();
        LicenseRuntime runtime = fixture.runtime();
        runtime.status();
        byte[] original = Files.readAllBytes(fixture.clock);
        Files.writeString(fixture.clock, new String(original, StandardCharsets.US_ASCII).replace("\"mac\":\"", "\"mac\":\"A"));
        assertEquals(LicenseRuntime.State.CLOCK_STATE_INVALID, runtime.status().state());
        Files.write(fixture.clock, original);
        assertEquals(LicenseRuntime.State.CLOCK_STATE_INVALID, runtime.status().state());
        assertEquals(LicenseRuntime.State.ACTIVE, fixture.runtime().status().state());
        Files.delete(fixture.clock);
        assertEquals(LicenseRuntime.State.CLOCK_STATE_INVALID, runtime.status().state());
        assertFalse(Files.exists(fixture.clock));
        Files.delete(fixture.license);
        var missing = runtime.status();
        assertEquals(LicenseRuntime.State.UNLICENSED, missing.state());
        assertEquals(List.of("UNLICENSED", "CLOCK_STATE_INVALID"), missing.reasons());
    }

    @Test void detectsModifiedSignatureEvenWithSameSignedPayload() throws Exception {
        Fixture fixture = new Fixture();
        LicenseRuntime runtime = fixture.runtime();
        runtime.status();
        String raw = Files.readString(fixture.license);
        var outer = JSON.readTree(raw);
        Files.write(fixture.license, JSON.writeValueAsBytes(Map.of("payload", outer.path("payload").asText(), "signature", base(new byte[64]))));
        assertEquals(LicenseRuntime.State.INVALID, runtime.status().state());
    }

    @Test void identityMustBeCompletedSameProductV4AndStableWithinProcess() throws Exception {
        Fixture fixture = new Fixture();
        LicenseRuntime runtime = fixture.runtime();
        runtime.status();
        Files.write(fixture.keyFile, new byte[32]);
        assertEquals(LicenseRuntime.State.CONFIG_INVALID, runtime.status().state());
        Files.write(fixture.keyFile, fixture.key);
        Files.writeString(fixture.identity, Files.readString(fixture.identity).replace("true", "false"));
        assertEquals(LicenseRuntime.State.CONFIG_INVALID, fixture.runtime().status().state());
    }

    @Test void hostBindingRequiresActualMachineIdEvenAfterCachedValidation() throws Exception {
        Fixture fixture = new Fixture();
        fixture.writeLicense(true, null);
        LicenseRuntime runtime = fixture.runtime();
        assertEquals(LicenseRuntime.State.INVALID, runtime.status().state());
        Files.writeString(fixture.machine, "00112233445566778899aabbccddeeff\n");
        assertEquals(LicenseRuntime.State.ACTIVE, runtime.status().state());
        Files.delete(fixture.machine);
        assertEquals(LicenseRuntime.State.INVALID, runtime.status().state());
    }

    @Test void nonDurableOrFailedWritesRestrictWithoutUsingPriorActiveMemory() throws Exception {
        for (StateStore.CommitOutcome result : List.of(StateStore.CommitOutcome.NOT_COMMITTED,
            StateStore.CommitOutcome.COMMIT_DURABILITY_UNKNOWN)) {
            Fixture fixture = new Fixture();
            StateStore real = new FileStateStore();
            StateStore failing = new StateStore() {
                public byte[] read(Path path, int max) throws IOException { return real.read(path, max); }
                public <T> T withLock(Path path, LockedAction<T> action) throws IOException { return real.withLock(path, action); }
                public CommitOutcome replace(Path path, byte[] bytes) throws IOException {
                    if (result == CommitOutcome.COMMIT_DURABILITY_UNKNOWN) { real.replace(path, bytes); }
                    return result;
                }
            };
            LicenseRuntime runtime = fixture.runtime(failing);
            assertEquals(LicenseRuntime.State.ACTIVE, runtime.status().state());
            assertEquals(LicenseRuntime.State.CLOCK_STATE_INVALID, runtime.beforeExecution().state());
            assertEquals(LicenseRuntime.State.CLOCK_STATE_INVALID, runtime.status().state());
        }
    }

    @Test void codecUsesOriginalMacBytesAndStrictInstallationServiceNumbers() throws Exception {
        Fixture fixture = new Fixture();
        byte[] envelope = Files.readAllBytes(fixture.clock);
        assertEquals(fixture.state(START, 0), ClockStateCodec.decode(envelope, fixture.key, "icp-diag", INSTALLATION, "metor"));
        assertThrows(IllegalArgumentException.class, () -> ClockStateCodec.decode(envelope, fixture.key, "icp-diag", INSTALLATION, "ai"));
        assertThrows(IllegalArgumentException.class, () -> ClockStateCodec.decode(envelope, new byte[32], "icp-diag", INSTALLATION, "metor"));
        assertThrows(IllegalArgumentException.class, () -> ClockStateCodec.encode(fixture.state(START, -1), fixture.key));
        String raw = new String(Base64.getUrlDecoder().decode(JSON.readTree(envelope).path("payload").asText()), StandardCharsets.US_ASCII);
        for (String invalid : List.of(raw.replace("\"sequence\":0", "\"sequence\":1.0"),
            raw.replace("\"epoch\":0", "\"epoch\":9223372036854775808"),
            raw.replace("\"epoch\":0", "\"epoch\":false"), raw.replace("\"schema_version\":1", "\"schema_version\":1,\"extra\":1"))) {
            byte[] bytes = invalid.getBytes(StandardCharsets.US_ASCII);
            byte[] macKey = ClockStateCodec.hmac(fixture.key, "icp-diag/state/metor/v1".getBytes(StandardCharsets.US_ASCII));
            byte[] signed = JSON.writeValueAsBytes(Map.of("payload", base(bytes), "mac", base(ClockStateCodec.hmac(macKey, bytes))));
            assertThrows(IllegalArgumentException.class, () -> ClockStateCodec.decode(signed, fixture.key, "icp-diag", INSTALLATION, "metor"));
        }
    }

    @Test void fileStoreDoesNotInitializeMissingStateAndRejectsSymlinks() throws Exception {
        FileStateStore store = new FileStateStore();
        Path missing = temporary.resolve("missing");
        assertEquals(StateStore.CommitOutcome.NOT_COMMITTED, store.replace(missing, new byte[] {1}));
        assertFalse(Files.exists(missing));
        Path real = temporary.resolve("real");
        Files.writeString(real, "old");
        assertEquals(StateStore.CommitOutcome.COMMITTED, store.replace(real, new byte[] {1}));
        assertNotEquals("old", Files.readString(real));
        Path link = temporary.resolve("link");
        Files.createSymbolicLink(link, real);
        assertThrows(IOException.class, () -> store.read(link, 32));
        assertThrows(IOException.class, () -> store.replace(link, new byte[0]));
    }

    @Test void publicDeploymentSeedsMatchBothServicesByteForByte() throws Exception {
        com.fasterxml.jackson.databind.JsonNode vectors;
        try (var input = getClass().getResourceAsStream("/clock-state-v1-vectors.json")) {
            vectors = JSON.readTree(input);
        }
        byte[] key = java.util.HexFormat.of().parseHex(vectors.path("service_key_hex").asText());
        UUID installation = UUID.fromString(vectors.path("installation_id").asText());
        for (var vector : vectors.path("vectors")) {
            String service = vector.path("service").asText();
            byte[] envelope = vector.path("envelope").asText().getBytes(StandardCharsets.US_ASCII);
            ClockState state = ClockStateCodec.decode(envelope, key, "icp-diag", installation, service);
            assertArrayEquals(envelope, ClockStateCodec.encode(state, key));
        }
    }

    @Test void liveEpochOrSameSequenceReplacementLatchesAnomaly() throws Exception {
        Fixture fixture = new Fixture();
        LicenseRuntime runtime = fixture.runtime();
        runtime.status();
        ClockState changed = new ClockState("icp-diag", INSTALLATION, "metor", 1, 0, START, null);
        Files.write(fixture.clock, ClockStateCodec.encode(changed, fixture.key));
        assertEquals(LicenseRuntime.State.CLOCK_STATE_INVALID, runtime.status().state());
        Files.write(fixture.clock, ClockStateCodec.encode(fixture.state(START, 0), fixture.key));
        assertEquals(LicenseRuntime.State.CLOCK_STATE_INVALID, runtime.status().state());
        runtime = fixture.runtime();
        runtime.status();
        Files.write(fixture.clock, ClockStateCodec.encode(fixture.state(START.plusSeconds(1), 0), fixture.key));
        assertEquals(LicenseRuntime.State.CLOCK_STATE_INVALID, runtime.status().state());
    }

    @Test void independentInstancesSerializeClockWritesWithoutLostSequence() throws Exception {
        Fixture fixture = new Fixture();
        LicenseRuntime first = fixture.runtime();
        LicenseRuntime second = fixture.runtime();
        first.status(); second.status();
        try (var executor = java.util.concurrent.Executors.newFixedThreadPool(2)) {
            var left = executor.submit(() -> { for (int index = 0; index < 10; index++)
                assertEquals(LicenseRuntime.State.ACTIVE, first.beforeExecution().state()); });
            var right = executor.submit(() -> { for (int index = 0; index < 10; index++)
                assertEquals(LicenseRuntime.State.ACTIVE, second.beforeExecution().state()); });
            left.get(); right.get();
        }
        assertEquals(20, ClockStateCodec.decode(Files.readAllBytes(fixture.clock), fixture.key, "icp-diag", INSTALLATION, "metor").sequence());
    }

    private static String base(byte[] bytes) { return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes); }
}
