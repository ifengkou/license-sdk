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

/** Local license validation and durable time protection, independent of business routes. */
public final class LicenseRuntime implements LicenseGuard, AutoCloseable {
    public record Config(String productId, String service, String fingerprintDomain, String bindingDomain,
        String recoveryDomain, Path identityFile, Path serviceKeyFile, Path licenseFile, Path clockFile,
        Path machineIdFile, String recoveryPrefix) {
        public Config(String productId, String service, String fingerprintDomain, String bindingDomain,
            String recoveryDomain, Path identityFile, Path serviceKeyFile, Path licenseFile, Path clockFile,
            Path machineIdFile) {
            this(productId, service, fingerprintDomain, bindingDomain, recoveryDomain, identityFile,
                serviceKeyFile, licenseFile, clockFile, machineIdFile, null);
        }
    }
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
    private final Map<String, PublicKey> trustRing;
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
    private UUID invalidatedChallengeNonce;
    private CommercialLicenseVerifier.VerifiedLicense cached;
    private ProductConfig productConfig;
    private final Object lifecycleLock = new Object();
    private java.util.concurrent.ScheduledExecutorService scheduler;
    private boolean closed;

    /** Loads public trust from product-controlled bytes; never initializes customer data. */
    public static LicenseRuntime fromConfig(byte[] config, Path root, String service) throws IOException {
        return fromConfig(config, root, service, Path.of("/etc/machine-id"));
    }
    public static LicenseRuntime fromConfig(byte[] raw, Path root, String service, Path machineId) throws IOException {
        ProductConfig product = ProductConfig.parse(raw);
        String selected = product.selectService(service);
        Path safe = OnboardingFiles.checked(root);
        var config = new Config(product.productId(), selected, product.fingerprintDomain(), product.bindingDomain(),
            product.recoveryDomain(), safe.resolve("identity/installation.json"), safe.resolve("identity/service.key"),
            safe.resolve("shared/active.license.json"), safe.resolve("state").resolve(selected).resolve("clock.json"),
            machineId == null ? null : machineId.toAbsolutePath().normalize(), product.services().get(selected));
        var runtime = new LicenseRuntime(config, product.trustedKeys(), TimeSource.system());
        runtime.productConfig = product;
        return runtime;
    }

    /** Explicitly owns one checkpoint task; old constructors never start a thread. */
    public LicenseRuntime start() {
        synchronized (lifecycleLock) {
            if (closed) throw new IllegalStateException("Runtime is closed");
            if (scheduler == null) {
                var executor = java.util.concurrent.Executors.newSingleThreadScheduledExecutor(task -> {
                    Thread thread = new Thread(task, "license-checkpoint"); thread.setDaemon(true); return thread;
                });
                start(executor);
            }
        }
        return this;
    }
    LicenseRuntime start(java.util.concurrent.ScheduledExecutorService executor) {
        synchronized (lifecycleLock) {
            if (closed) throw new IllegalStateException("Runtime is closed");
            if (scheduler != null) return this;
            scheduler = executor;
            try { executor.scheduleWithFixedDelay(this::scheduledCheckpoint, 30, 30, java.util.concurrent.TimeUnit.SECONDS); }
            catch (RuntimeException error) { scheduler = null; executor.shutdownNow(); throw error; }
        }
        return this;
    }
    private void scheduledCheckpoint() {
        try {
            Snapshot result = checkpoint();
            if (result.state() != State.ACTIVE) System.getLogger(LicenseRuntime.class.getName())
                .log(System.Logger.Level.WARNING, "License checkpoint: " + result.state().name());
        } catch (RuntimeException error) {
            System.getLogger(LicenseRuntime.class.getName()).log(System.Logger.Level.WARNING, "License checkpoint failed");
        }
    }
    /** Waits outside the Runtime monitor, so an executing checkpoint can finish. */
    @Override public void close() {
        synchronized (lifecycleLock) {
            closed = true;
            if (scheduler == null) return;
            scheduler.shutdown();
            boolean interrupted = false;
            while (!scheduler.isTerminated()) {
                try { scheduler.awaitTermination(1, java.util.concurrent.TimeUnit.DAYS); }
                catch (InterruptedException error) { interrupted = true; }
            }
            scheduler = null;
            if (interrupted) Thread.currentThread().interrupt();
        }
    }

    /** A request contains purpose-derived IDs, never the host identifier or installation key. */
    public synchronized Map<String, Object> commercialRequest() throws IOException {
        if (productConfig == null) throw new OnboardingException(OnboardingException.Code.CONFIG_INVALID);
        Installation identity = identity();
        String binding;
        try { binding = binding(identity); }
        catch (LicenseException unavailable) { throw new OnboardingException(OnboardingException.Code.BINDING_UNAVAILABLE); }
        var data = new java.util.LinkedHashMap<String, Object>();
        data.put("schema_version", 1); data.put("kind", "license-request");
        data.put("product_id", config.productId()); data.put("request_id", UUID.randomUUID().toString());
        data.put("binding_type", "HOST_INSTALL"); data.put("binding_scheme", "host-install-code-v1");
        data.put("binding_id", binding); data.put("request_code", new ManualCodeCodec(productConfig.requestPrefix()).encodeHex(binding));
        data.put("generated_at", time.utcNow().truncatedTo(ChronoUnit.SECONDS).toString());
        return java.util.Collections.unmodifiableMap(data);
    }

    public enum ImportActionStatus { APPLIED, ALREADY_APPLIED, OUTCOME_UNKNOWN }
    public record ImportResult(ImportActionStatus actionStatus, String fileDigest, Snapshot status) { }

    /** Raw-byte digest for optimistic publishing; missing differs from unreadable. */
    public String licenseFileDigest() throws IOException {
        return new OnboardingFiles().rawDigest(config.licenseFile());
    }

    /** Lock order: shared publishing lock, then this Runtime; clock operations never take the publishing lock. */
    public ImportResult importLicense(byte[] raw, String expectedFileDigest) throws IOException {
        return importLicense(raw, expectedFileDigest, new OnboardingFiles());
    }
    /** Publish for a numeric POSIX group (0640); null retains private 0600 publication. */
    public ImportResult importLicense(byte[] raw, String expectedFileDigest, String sharedGroup) throws IOException {
        return importLicense(raw, expectedFileDigest, sharedGroup, new OnboardingFiles());
    }
    ImportResult importLicense(byte[] raw, String expectedFileDigest, OnboardingFiles files) throws IOException {
        return importLicense(raw, expectedFileDigest, null, files);
    }
    ImportResult importLicense(byte[] raw, String expectedFileDigest, String sharedGroup, OnboardingFiles files) throws IOException {
        if (raw == null || raw.length < 1 || raw.length > CommercialLicenseVerifier.MAX_FILE_BYTES)
            throw new OnboardingException(OnboardingException.Code.CANDIDATE_REJECTED);
        if (expectedFileDigest != null && !expectedFileDigest.matches("[0-9a-f]{64}"))
            throw new IllegalArgumentException("Expected raw file digest must be SHA-256 or null for absent");
        Integer group = OnboardingFiles.sharedGroup(sharedGroup);
        byte[] candidate = raw.clone();
        String candidateDigest = digest(candidate);
        Path target = OnboardingFiles.checked(config.licenseFile());
        return files.withLock(target.resolveSibling("license-import.lock"), () -> {
            synchronized (this) {
                String actual = files.rawDigest(target);
                if (!Objects.equals(actual, expectedFileDigest))
                    throw new OnboardingException(OnboardingException.Code.LICENSE_STATE_CONFLICT);
                if (candidateDigest.equals(actual)) {
                    preflight(candidate); // Same-byte confirmation still validates signature, scope and actual binding.
                    boolean attributesMatch;
                    try { attributesMatch = files.attributesMatch(target, group); }
                    catch (IOException | UnsupportedOperationException error) {
                        return importResult(ImportActionStatus.OUTCOME_UNKNOWN, candidateDigest);
                    }
                    if (!attributesMatch) {
                        StateStore.CommitOutcome repaired = files.publish(target, candidate, group);
                        if (repaired == StateStore.CommitOutcome.NOT_COMMITTED)
                            throw new OnboardingException(OnboardingException.Code.STORAGE_FAILED);
                        return importResult(repaired == StateStore.CommitOutcome.COMMITTED ? ImportActionStatus.ALREADY_APPLIED
                            : ImportActionStatus.OUTCOME_UNKNOWN, candidateDigest);
                    }
                    boolean confirmed;
                    try { confirmed = files.confirm(target, candidate); }
                    catch (IOException | UnsupportedOperationException error) {
                        return importResult(ImportActionStatus.OUTCOME_UNKNOWN, candidateDigest);
                    }
                    if (!confirmed) throw new OnboardingException(OnboardingException.Code.LICENSE_STATE_CONFLICT);
                    return importResult(ImportActionStatus.ALREADY_APPLIED, candidateDigest);
                }
                CommercialLicenseVerifier.VerifiedLicense checked = preflight(candidate);
                if (checked.status() != CommercialLicenseVerifier.Status.ACTIVE)
                    throw new OnboardingException(OnboardingException.Code.CANDIDATE_REJECTED);
                StateStore.CommitOutcome outcome = files.publish(target, candidate, group);
                if (outcome == StateStore.CommitOutcome.NOT_COMMITTED)
                    throw new OnboardingException(OnboardingException.Code.STORAGE_FAILED);
                return importResult(outcome == StateStore.CommitOutcome.COMMITTED ? ImportActionStatus.APPLIED
                    : ImportActionStatus.OUTCOME_UNKNOWN, candidateDigest);
            }
        });
    }
    private ImportResult importResult(ImportActionStatus action, String fileDigest) {
        try { return new ImportResult(action, fileDigest, inspect()); }
        catch (RuntimeException error) { return new ImportResult(ImportActionStatus.OUTCOME_UNKNOWN, fileDigest, null); }
    }

    public LicenseRuntime(Config config, Map<String, PublicKey> trustRing, TimeSource time) {
        this(config, trustRing, time, new FileStateStore());
    }

    public LicenseRuntime(Config config, Map<String, PublicKey> trustRing, TimeSource time, StateStore store) {
        this.config = Objects.requireNonNull(config, "config");
        this.time = Objects.requireNonNull(time, "time");
        this.store = Objects.requireNonNull(store, "store");
        CommercialLicenseVerifier candidateVerifier = null;
        IdentityDerivation candidateDerivation = null;
        Map<String, PublicKey> candidateTrust = Map.of();
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
            candidateTrust = Map.copyOf(trustRing);
            valid = true;
        } catch (IllegalArgumentException | NullPointerException error) { valid = false; }
        verifier = candidateVerifier;
        derivation = candidateDerivation;
        this.trustRing = candidateTrust;
        configured = valid;
    }

    /** Observes current identity, state and complete license bytes on every call. */
    public synchronized Snapshot status() { return observe(false); }

    /** Called by a process lifecycle timer even while there are no incoming requests. */
    public synchronized Snapshot checkpoint() { return observe(true); }

    /** Validate without persisting a checkpoint on the request path. */
    @Override public synchronized void requireValid() {
        Snapshot current = observe(false, false);
        if (current.state() != State.ACTIVE) {
            throw new LicenseDeniedException(current.state(), current.reasons());
        }
    }

    /** Candidate verification uses actual UTC and never reads or writes the clock state. */
    public synchronized CommercialLicenseVerifier.VerifiedLicense preflight(byte[] candidate) throws IOException {
        if (!configured) throw new IllegalArgumentException("Invalid controlled configuration");
        Installation identity = identity();
        return verifier.verifyWithBindingProvider(candidate, () -> binding(identity), time.utcNow());
    }

    /** Stable initialized identity; management access does not reset installation or time state. */
    public synchronized UUID installationId() throws IOException { return identity().id(); }

    public synchronized String bindingId() throws IOException { return binding(identity()); }

    /** Only a purpose-derived key leaves the runtime; the master key is never returned. */
    public synchronized byte[] derivedKey(String domain) throws IOException {
        if (domain == null || !domain.matches("[A-Za-z0-9/._-]{1,128}")) throw new IllegalArgumentException("Invalid domain");
        try {
            var mac = javax.crypto.Mac.getInstance("HmacSHA256");
            mac.init(new javax.crypto.spec.SecretKeySpec(identity().key(), "HmacSHA256"));
            return mac.doFinal(domain.getBytes(StandardCharsets.UTF_8));
        } catch (GeneralSecurityException error) { throw new IllegalStateException("HMAC unavailable", error); }
    }

    /** Diagnostic snapshot for a candidate transaction: never checkpoints or replaces files. */
    public synchronized Snapshot inspect() { return observe(false, false); }

    /** Repeated calls return the same persisted request while this service's anomaly is unchanged. */
    public synchronized RecoveryRequest getOrCreateRecoveryRequest() {
        Installation identity = recoveryIdentity();
        try {
            new ManualCodeCodec(config.recoveryPrefix());
        } catch (IllegalArgumentException | NullPointerException error) {
            throw new RecoveryException(RecoveryException.Code.CONFIG_INVALID);
        }
        Instant wall = time.utcNow();
        long mono = time.monotonicNanos();
        try {
            return store.withLock(clockLock(), () -> {
                Snapshot current = lockedObserve(identity, wall, mono, false, false);
                String reason = recoveryReason(current);
                if (reason == null) throw new RecoveryException(RecoveryException.Code.NOT_REQUIRED);
                byte[] raw = optionalRead(config.clockFile());
                String stateIdentity = stateIdentity(raw);
                Long epoch = baseEpoch(validClock(raw, identity));
                byte[] previous = optionalRead(challengeFile());
                if (previous != null) {
                    try {
                        var challenge = RecoveryChallengeCodec.decode(previous, identity.key(), config.productId(),
                            identity.id(), config.service());
                        if (!challenge.nonce().equals(invalidatedChallengeNonce)
                            && challenge.stateIdentity().equals(stateIdentity) && applicableEpoch(challenge.baseEpoch(), epoch)) {
                            confirmRequest(previous);
                            return RecoveryChallengeCodec.request(challenge, derivation, config.recoveryPrefix());
                        }
                    } catch (IllegalArgumentException error) { /* A damaged challenge is replaced, never trusted. */ }
                }
                var challenge = new RecoveryChallengeCodec.Challenge(config.productId(), identity.id(), config.service(),
                    UUID.randomUUID(), reason, stateIdentity, epoch, wall.truncatedTo(ChronoUnit.SECONDS));
                byte[] encoded = RecoveryChallengeCodec.encode(challenge, identity.key());
                StateStore.CommitOutcome outcome = store.replaceRecovery(challengeFile(), previous, encoded);
                if (outcome == StateStore.CommitOutcome.NOT_COMMITTED) throw storageFailure();
                if (outcome != StateStore.CommitOutcome.COMMITTED) {
                    throw new RecoveryException(RecoveryException.Code.OUTCOME_UNKNOWN);
                }
                return RecoveryChallengeCodec.request(challenge, derivation, config.recoveryPrefix());
            });
        } catch (IOException | IllegalArgumentException | UnsupportedOperationException error) { throw storageFailure(); }
    }

    /** The same runtime commits and refreshes the local clock; business execution never holds this lock. */
    public synchronized RecoveryResult applyRecovery(byte[] signedFile) {
        Installation identity = recoveryIdentity();
        var recovery = new RecoveryVerifier(config.productId(), trustRing).verify(signedFile, identity.id(), config.service());
        Instant wall = time.utcNow();
        long mono = time.monotonicNanos();
        try {
            return store.withLock(clockLock(), () -> {
                byte[] raw = optionalRead(config.clockFile());
                ClockState clock = validClock(raw, identity);
                if (clock != null && recovery.recoveryId().equals(clock.lastRecoveryId())) {
                    if (observedClock != null && (clock.epoch() < observedClock.epoch()
                        || clock.epoch() == observedClock.epoch() && (clock.sequence() < observedClock.sequence()
                        || clock.highWatermark().isBefore(observedClock.highWatermark())))) {
                        throw new RecoveryException(RecoveryException.Code.TARGET_CHANGED);
                    }
                    StateStore.CommitOutcome outcome = store.confirmDurability(config.clockFile(), raw);
                    if (outcome != StateStore.CommitOutcome.COMMITTED) return unknownRecovery(identity, wall, recovery.recoveryId());
                    boolean adopt = observedClock == null || clock.epoch() > observedClock.epoch();
                    refreshClock(clock, raw, wall, mono, adopt);
                    cleanupChallenge();
                    return new RecoveryResult(RecoveryResult.ActionStatus.ALREADY_APPLIED,
                        lockedObserve(identity, wall, mono, false, false), recovery.recoveryId());
                }
                if (wall.isBefore(recovery.windowStart()) || !wall.isBefore(recovery.windowEnd())) {
                    throw new RecoveryException(RecoveryException.Code.WINDOW_INVALID);
                }
                Snapshot current = lockedObserve(identity, wall, mono, false, false);
                String reason = recoveryReason(current);
                if (reason == null) throw new RecoveryException(RecoveryException.Code.TARGET_CHANGED);
                // Observation can latch errors but may not rewrite the clock during recovery comparison.
                raw = optionalRead(config.clockFile());
                clock = validClock(raw, identity);
                byte[] challengeBytes = optionalRead(challengeFile());
                RecoveryChallengeCodec.Challenge challenge;
                try {
                    if (challengeBytes == null) throw new IllegalArgumentException();
                    challenge = RecoveryChallengeCodec.decode(challengeBytes, identity.key(), config.productId(), identity.id(), config.service());
                } catch (IllegalArgumentException error) { throw new RecoveryException(RecoveryException.Code.TARGET_CHANGED); }
                if (challenge.nonce().equals(invalidatedChallengeNonce) || !challenge.stateIdentity().equals(stateIdentity(raw))
                    || !applicableEpoch(challenge.baseEpoch(), baseEpoch(clock))) {
                    throw new RecoveryException(RecoveryException.Code.TARGET_CHANGED);
                }
                String target = HexFormat.of().formatHex(derivation.recoveryTarget(identity.id(), config.service(),
                    challenge.nonce(), HexFormat.of().parseHex(challenge.stateIdentity())));
                if (!target.equals(recovery.targetId())) throw new RecoveryException(RecoveryException.Code.TARGET_CHANGED);
                Long base = challenge.baseEpoch();
                long epoch;
                try { epoch = base == null ? 1 : Math.addExact(base, 1); }
                catch (ArithmeticException error) { throw storageFailure(); }
                ClockState updated = new ClockState(config.productId(), identity.id(), config.service(), epoch,
                    0, wall.truncatedTo(ChronoUnit.SECONDS), recovery.recoveryId());
                byte[] encoded = ClockStateCodec.encode(updated, identity.key());
                StateStore.CommitOutcome outcome = store.replaceRecovery(config.clockFile(), raw, encoded);
                if (outcome == StateStore.CommitOutcome.NOT_COMMITTED) throw storageFailure();
                if (outcome != StateStore.CommitOutcome.COMMITTED) return unknownRecovery(identity, wall, recovery.recoveryId());
                refreshClock(updated, encoded, wall, mono, true);
                cleanupChallenge();
                return new RecoveryResult(RecoveryResult.ActionStatus.APPLIED,
                    lockedObserve(identity, wall, mono, false, false), recovery.recoveryId());
            });
        } catch (IOException | IllegalArgumentException | UnsupportedOperationException error) { throw storageFailure(); }
    }

    private Installation recoveryIdentity() {
        if (!configured) throw new RecoveryException(RecoveryException.Code.CONFIG_INVALID);
        try { return identity(); }
        catch (IOException | IllegalArgumentException error) { throw new RecoveryException(RecoveryException.Code.CONFIG_INVALID); }
    }
    private byte[] optionalRead(Path path) throws IOException {
        try { return store.read(path, 8192); }
        catch (NoSuchFileException missing) { return null; }
    }
    private ClockState validClock(byte[] raw, Installation identity) {
        if (raw == null) return null;
        try { return ClockStateCodec.decode(raw, identity.key(), config.productId(), identity.id(), config.service()); }
        catch (IllegalArgumentException error) { return null; }
    }
    private Long baseEpoch(ClockState disk) {
        if (observedClock == null) return disk == null ? null : disk.epoch();
        return disk == null ? observedClock.epoch() : Math.max(disk.epoch(), observedClock.epoch());
    }
    private static boolean applicableEpoch(Long challenged, Long known) {
        return known == null || challenged != null && challenged >= known;
    }
    private static String stateIdentity(byte[] raw) {
        return digest(raw == null ? "MISSING".getBytes(StandardCharsets.US_ASCII) : raw);
    }
    private static String recoveryReason(Snapshot current) {
        if (current.reasons().contains("CLOCK_STATE_INVALID")) return "CLOCK_STATE_INVALID";
        return current.reasons().contains("CLOCK_ROLLBACK") ? "CLOCK_ROLLBACK" : null;
    }
    private void confirmRequest(byte[] raw) throws IOException {
        StateStore.CommitOutcome confirmed = store.confirmDurability(challengeFile(), raw);
        if (confirmed == StateStore.CommitOutcome.NOT_COMMITTED) throw storageFailure();
        if (confirmed != StateStore.CommitOutcome.COMMITTED) throw new RecoveryException(RecoveryException.Code.OUTCOME_UNKNOWN);
    }
    private RecoveryResult unknownRecovery(Installation identity, Instant wall, UUID recoveryId) {
        persistenceFailed = true;
        return new RecoveryResult(RecoveryResult.ActionStatus.OUTCOME_UNKNOWN,
            withoutClock(identity, wall, "CLOCK_STATE_INVALID"), recoveryId);
    }
    private void refreshClock(ClockState clock, byte[] raw, Instant wall, long mono, boolean allowLowerReference) {
        Instant retained = max(clock.highWatermark(), wall);
        if (!allowLowerReference) {
            if (high != null) retained = max(retained, high);
            if (anchor != null && mono >= anchorNanos) retained = max(retained, anchor.plusNanos(mono - anchorNanos));
        }
        observedClock = clock;
        observedClockDigest = digest(raw);
        high = retained;
        anchor = retained;
        anchorNanos = checkpointNanos = mono;
        persistenceFailed = false;
    }
    private void cleanupChallenge() {
        try { store.deleteRecoveryChallenge(challengeFile()); }
        catch (IOException error) { System.getLogger(LicenseRuntime.class.getName())
            .log(System.Logger.Level.WARNING, "Recovery committed; challenge cleanup failed"); }
    }
    private void invalidateApplicableChallenge(Installation identity, String clockDigest) throws IOException {
        byte[] raw = optionalRead(challengeFile());
        if (raw == null) return;
        boolean applicable;
        try {
            var challenge = RecoveryChallengeCodec.decode(raw, identity.key(), config.productId(), identity.id(), config.service());
            applicable = challenge.stateIdentity().equals(clockDigest);
            if (applicable) invalidatedChallengeNonce = challenge.nonce();
        } catch (IllegalArgumentException malformed) { applicable = false; }
        if (applicable) store.deleteRecoveryChallenge(challengeFile());
        else cleanupChallenge(); // Consumed or malformed challenges cannot authorize the current clock.
    }
    private Path challengeFile() { return config.clockFile().resolveSibling("recovery-challenge.json"); }
    private Path clockLock() { return config.clockFile().resolveSibling(config.clockFile().getFileName() + ".lock"); }
    private static RecoveryException storageFailure() { return new RecoveryException(RecoveryException.Code.STORAGE_FAILED); }

    private Snapshot observe(boolean force) { return observe(force, true); }

    private Snapshot observe(boolean force, boolean writable) {
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
            return store.withLock(clockLock(), () -> lockedObserve(identity, wall, mono, force, writable));
        } catch (IOException | IllegalArgumentException | UnsupportedOperationException error) {
            persistenceFailed = true;
            return withoutClock(identity, wall, "CLOCK_STATE_INVALID");
        }
    }

    private Snapshot lockedObserve(Installation identity, Instant wall, long mono, boolean force, boolean writable) throws IOException {
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
        boolean rollback = wall.plusSeconds(86_400).isBefore(reference);
        List<String> reasons = new ArrayList<>();
        CommercialLicenseVerifier.VerifiedLicense license = license(identity, effective, reasons);
        if (rollback) { reasons.add("CLOCK_ROLLBACK"); }
        if (!rollback) {
            high = effective;
            try {
                invalidateApplicableChallenge(identity, clockDigest);
            } catch (IOException error) {
                persistenceFailed = true;
                reasons.add("CLOCK_STATE_INVALID");
            }
            if (writable && (force || mono - checkpointNanos >= 30_000_000_000L)) {
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
