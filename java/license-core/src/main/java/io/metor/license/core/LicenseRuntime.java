package io.metor.license.core;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.security.PublicKey;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;

/** Local state observation and durable time protection. This class installs no request/business gate. */
public final class LicenseRuntime {
    public record Config(String productId, String service, String fingerprintDomain, String bindingDomain,
        String recoveryDomain, Path identityFile, Path serviceKeyFile, Path licenseFile, Path clockFile,
        Path machineIdFile) { }
    public enum State {
        CONFIG_INVALID, UNLICENSED, INVALID, CLOCK_STATE_INVALID, CLOCK_ROLLBACK,
        NOT_YET_VALID, EXPIRED, ACTIVE
    }
    public record Snapshot(State state, List<String> reasons,
        CommercialLicenseVerifier.VerifiedLicense license, Instant effectiveNow, Instant observedAt) {
        public Snapshot { reasons = List.copyOf(reasons); }
    }
    private record Installation(UUID id, byte[] key) { }

    private final Config config;
    private final StateStore store;
    private final TimeSource time;
    private final CommercialLicenseVerifier verifier;
    private final IdentityDerivation derivation;
    private final boolean configured;
    private UUID installationId;
    private byte[] installationKey;
    private ClockState observedClock;
    private String observedClockDigest;
    private Instant anchor;
    private Instant high;
    private long anchorNanos;
    private long checkpointNanos;
    private boolean persistenceFailed;
    private CommercialLicenseVerifier.VerifiedLicense cached;

    public LicenseRuntime(Config config, Map<String, PublicKey> trustRing, TimeSource time) {
        this(config, trustRing, time, new FileStateStore());
    }

    public LicenseRuntime(Config config, Map<String, PublicKey> trustRing, TimeSource time, StateStore store) {
        this.config = Objects.requireNonNull(config, "config");
        this.time = Objects.requireNonNull(time, "time");
        this.store = Objects.requireNonNull(store, "store");
        CommercialLicenseVerifier candidateVerifier = null;
        IdentityDerivation candidateDerivation = null;
        boolean valid;
        try {
            if (trustRing.isEmpty() || !config.service().matches("[a-z][a-z0-9-]{0,31}")) {
                throw new IllegalArgumentException("Invalid controlled configuration");
            }
            Objects.requireNonNull(config.identityFile());
            Objects.requireNonNull(config.serviceKeyFile());
            Objects.requireNonNull(config.licenseFile());
            Objects.requireNonNull(config.clockFile());
            candidateVerifier = new CommercialLicenseVerifier(config.productId(), trustRing);
            candidateDerivation = new IdentityDerivation(config.fingerprintDomain(), config.bindingDomain(), config.recoveryDomain());
            valid = true;
        } catch (IllegalArgumentException | NullPointerException error) { valid = false; }
        verifier = candidateVerifier;
        derivation = candidateDerivation;
        configured = valid;
    }

    /** Observes current identity, state and complete license bytes on every call. */
    public synchronized Snapshot status() { return observe(false); }

    /** Called by a process lifecycle timer even while there are no incoming requests. */
    public synchronized Snapshot checkpoint() { return observe(true); }

    /** Force durable reference before a future execution gate admits new work. Does not itself admit work. */
    public synchronized Snapshot beforeExecution() { return observe(true); }

    private Snapshot observe(boolean force) {
        Instant wall = time.utcNow();
        long mono = time.monotonicNanos();
        if (!configured) { return snapshot(State.CONFIG_INVALID, List.of("CONFIG_INVALID"), null, wall, wall); }
        Installation identity;
        try { identity = identity(); }
        catch (IOException | IllegalArgumentException error) {
            cached = null;
            return snapshot(State.CONFIG_INVALID, List.of("CONFIG_INVALID"), null, wall, wall);
        }
        try {
            Path lock = config.clockFile().resolveSibling(config.clockFile().getFileName() + ".lock");
            return store.withLock(lock, () -> lockedObserve(identity, wall, mono, force));
        } catch (IOException | IllegalArgumentException | UnsupportedOperationException error) {
            persistenceFailed = true;
            return withoutClock(identity, wall, "CLOCK_STATE_INVALID");
        }
    }

    private Snapshot lockedObserve(Installation identity, Instant wall, long mono, boolean force) throws IOException {
        ClockState clock;
        String clockDigest;
        try {
            byte[] clockBytes = store.read(config.clockFile(), 8192);
            clockDigest = digest(clockBytes);
            clock = ClockStateCodec.decode(clockBytes, identity.key(),
                config.productId(), identity.id(), config.service());
            if (observedClock != null && (clock.epoch() != observedClock.epoch()
                || clock.epoch() == observedClock.epoch() && (clock.sequence() < observedClock.sequence()
                || clock.highWatermark().isBefore(observedClock.highWatermark())
                || clock.sequence() == observedClock.sequence() && !clockDigest.equals(observedClockDigest)))) {
                throw new IllegalArgumentException("Clock state moved backwards");
            }
        } catch (IOException | IllegalArgumentException error) {
            persistenceFailed = true;
            return withoutClock(identity, wall, "CLOCK_STATE_INVALID");
        }

        if (persistenceFailed) { return withoutClock(identity, wall, "CLOCK_STATE_INVALID"); }
        if (observedClock == null) {
            high = clock.highWatermark();
            anchor = max(high, wall);
            anchorNanos = mono;
            checkpointNanos = mono;
        }
        observedClock = clock;
        observedClockDigest = clockDigest;
        long elapsed = mono - anchorNanos;
        if (elapsed < 0) { return withoutClock(identity, wall, "CLOCK_STATE_INVALID"); }
        Instant reference;
        try { reference = max(max(high, clock.highWatermark()), anchor.plusNanos(elapsed)); }
        catch (java.time.DateTimeException | ArithmeticException error) {
            return withoutClock(identity, wall, "CLOCK_STATE_INVALID");
        }
        Instant effective = max(wall, reference);
        boolean rollback = wall.plusSeconds(300).isBefore(reference);
        List<String> reasons = new ArrayList<>();
        CommercialLicenseVerifier.VerifiedLicense license = license(identity, effective, reasons);
        if (rollback) { reasons.add("CLOCK_ROLLBACK"); }
        if (!rollback) {
            high = effective;
            if (force || mono - checkpointNanos >= 30_000_000_000L) {
                try {
                    ClockState update = new ClockState(config.productId(), identity.id(), config.service(),
                        clock.epoch(), Math.addExact(clock.sequence(), 1), high.truncatedTo(ChronoUnit.SECONDS),
                        clock.lastRecoveryId());
                    byte[] updatedBytes = ClockStateCodec.encode(update, identity.key());
                    StateStore.CommitOutcome outcome = store.replace(config.clockFile(), updatedBytes);
                    if (outcome != StateStore.CommitOutcome.COMMITTED) { throw new IOException("Clock commit not durable"); }
                    observedClock = update;
                    observedClockDigest = digest(updatedBytes);
                    checkpointNanos = mono;
                } catch (IOException | IllegalArgumentException | ArithmeticException error) {
                    persistenceFailed = true;
                    reasons.add("CLOCK_STATE_INVALID");
                }
            }
        }
        temporalReason(license, reasons);
        return snapshot(priority(reasons), reasons, license, effective, wall);
    }

    private Snapshot withoutClock(Installation identity, Instant wall, String reason) {
        List<String> reasons = new ArrayList<>();
        reasons.add(reason);
        CommercialLicenseVerifier.VerifiedLicense license = license(identity, wall, reasons);
        temporalReason(license, reasons);
        return snapshot(priority(reasons), reasons, license, wall, wall);
    }

    private Installation identity() throws IOException {
        Map<String, Object> data = StrictJson.object(store.read(config.identityFile(), 4096));
        ClockStateCodec.fields(data, Set.of("schema_version", "product_id", "installation_id", "initialized"));
        if (ClockStateCodec.number(data, "schema_version") != 1
            || !config.productId().equals(ClockStateCodec.text(data, "product_id"))
            || !Boolean.TRUE.equals(data.get("initialized"))) { throw new IllegalArgumentException("Invalid installation"); }
        UUID id = ClockStateCodec.uuid(ClockStateCodec.text(data, "installation_id"), true);
        byte[] key = store.read(config.serviceKeyFile(), 32);
        if (key.length != 32) { throw new IllegalArgumentException("Invalid service key"); }
        if (installationId == null) { installationId = id; installationKey = key.clone(); }
        else if (!installationId.equals(id) || !MessageDigest.isEqual(installationKey, key)) {
            throw new IllegalArgumentException("Installation identity changed in process");
        }
        return new Installation(id, key);
    }

    private CommercialLicenseVerifier.VerifiedLicense license(Installation identity, Instant now, List<String> reasons) {
        try {
            byte[] bytes = store.read(config.licenseFile(), CommercialLicenseVerifier.MAX_FILE_BYTES);
            String digest = digest(bytes);
            if (cached == null || !cached.fileDigest().equals(digest)) {
                cached = verifier.verifyWithBindingProvider(bytes, () -> binding(identity), now);
            } else {
                if (cached.payload().bindingType() == CommercialLicenseVerifier.BindingType.HOST_INSTALL
                    && !cached.payload().bindingId().equals(binding(identity))) {
                    throw new LicenseException(LicenseException.Code.BINDING_MISMATCH);
                }
                var payload = cached.payload();
                var state = now.isBefore(payload.notBefore()) ? CommercialLicenseVerifier.Status.NOT_YET_VALID
                    : payload.expiresAt() != null && !now.isBefore(payload.expiresAt()) ? CommercialLicenseVerifier.Status.EXPIRED
                    : CommercialLicenseVerifier.Status.ACTIVE;
                cached = new CommercialLicenseVerifier.VerifiedLicense(payload, cached.payloadDigest(), cached.fileDigest(), state);
            }
            return cached;
        } catch (NoSuchFileException error) { cached = null; reasons.add("UNLICENSED"); }
        catch (IOException | IllegalArgumentException error) { cached = null; reasons.add("INVALID"); }
        return null;
    }

    private String binding(Installation identity) {
        try {
            if (config.machineIdFile() == null) { throw new IOException("Host ID unavailable"); }
            byte[] raw = store.read(config.machineIdFile(), 33);
            String machine = StandardCharsets.US_ASCII.newDecoder().decode(java.nio.ByteBuffer.wrap(raw)).toString();
            return HexFormat.of().formatHex(derivation.binding(machine, identity.id()));
        } catch (IOException | IllegalArgumentException error) {
            throw new LicenseException(LicenseException.Code.BINDING_UNAVAILABLE);
        }
    }

    private static void temporalReason(CommercialLicenseVerifier.VerifiedLicense license, List<String> reasons) {
        if (license != null && license.status() != CommercialLicenseVerifier.Status.ACTIVE) {
            reasons.add(license.status().name());
        }
    }

    private static State priority(List<String> reasons) {
        for (State candidate : List.of(State.CONFIG_INVALID, State.UNLICENSED, State.INVALID,
            State.CLOCK_STATE_INVALID, State.CLOCK_ROLLBACK, State.NOT_YET_VALID, State.EXPIRED)) {
            if (reasons.contains(candidate.name())) { return candidate; }
        }
        return State.ACTIVE;
    }
    private static Snapshot snapshot(State state, List<String> reasons,
        CommercialLicenseVerifier.VerifiedLicense license, Instant effective, Instant wall) {
        return new Snapshot(state, reasons.stream().distinct()
            .sorted(java.util.Comparator.comparingInt(reason -> State.valueOf(reason).ordinal())).toList(),
            license, effective, wall);
    }
    private static Instant max(Instant left, Instant right) { return left.isAfter(right) ? left : right; }
    private static String digest(byte[] bytes) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)); }
        catch (GeneralSecurityException error) { throw new IllegalStateException("SHA-256 unavailable", error); }
    }
}
