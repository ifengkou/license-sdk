package io.metor.license.core;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** Explicit first installation. A damaged installation is never silently regenerated. */
public final class LicenseInstallation {
    public record Result(String actionStatus, UUID installationId) { }
    private record Identity(UUID id, boolean initialized, byte[] key) { }
    private LicenseInstallation() { }

    public static Result initialize(byte[] config, Path root) throws IOException {
        return initialize(ProductConfig.parse(config), root);
    }
    public static Result initialize(ProductConfig config, Path root) throws IOException {
        return initialize(config, root, new OnboardingFiles());
    }
    /** Validate a stopped installation or consistent snapshot without creating locks or changing files. */
    public static UUID validate(byte[] config, Path root) throws IOException {
        return validate(ProductConfig.parse(config), root);
    }
    public static UUID validate(ProductConfig config, Path root) throws IOException {
        return validate(config, root, new OnboardingFiles());
    }
    static UUID validate(ProductConfig config, Path root, OnboardingFiles files) throws IOException {
        try {
            Path safe = OnboardingFiles.checked(root);
            requireDirectory(safe);
            try (var children = Files.list(safe)) {
                if (children.anyMatch(path -> path.getFileName().toString().equals(".identity-stage")
                    || path.getFileName().toString().startsWith(".identity-stage-"))) throw invalid();
            }
            Identity identity = readIdentity(safe.resolve("identity"), config, files);
            if (!identity.initialized()) throw invalid();
            validateClocks(config, safe, identity, files);
            return identity.id();
        } catch (IOException | IllegalArgumentException | UnsupportedOperationException error) {
            throw invalid();
        }
    }
    static Result initialize(ProductConfig config, Path root, OnboardingFiles files) throws IOException {
        Path safe = OnboardingFiles.checked(root);
        files.directory(safe);
        return files.withLock(safe.resolve(".init.lock"), () -> {
            try { return lockedInitialize(config, safe, files); }
            catch (OnboardingException error) { throw error; }
            catch (IllegalArgumentException error) { throw invalid(); }
            catch (IOException | UnsupportedOperationException error) {
                throw new OnboardingException(OnboardingException.Code.OUTCOME_UNKNOWN);
            }
        });
    }
    private static Result lockedInitialize(ProductConfig config, Path root, OnboardingFiles files) throws IOException {
        Path identity = root.resolve("identity");
        Path stage = root.resolve(".identity-stage");
        boolean hasIdentity = Files.exists(OnboardingFiles.checked(identity), LinkOption.NOFOLLOW_LINKS);
        boolean hasStage = Files.exists(OnboardingFiles.checked(stage), LinkOption.NOFOLLOW_LINKS);
        if (hasIdentity && hasStage) throw invalid();
        if (!hasIdentity) {
            try (var children = Files.list(root)) {
                if (children.anyMatch(path -> !Set.of(".init.lock", ".identity-stage").contains(path.getFileName().toString()))) throw invalid();
            }
            if (!hasStage) {
                files.directory(stage);
                byte[] key = new byte[32]; new SecureRandom().nextBytes(key);
                UUID id = UUID.randomUUID();
                files.writeNew(stage.resolve("installation.json"), identityBytes(config.productId(), id, false));
                files.writeNew(stage.resolve("service.key"), key);
            }
            Identity pending = readIdentity(stage, config, files);
            if (pending.initialized()) throw invalid();
            files.sync(stage.resolve("installation.json")); files.sync(stage.resolve("service.key")); files.sync(stage);
            files.move(stage, identity, false);
            files.sync(root);
        }
        Identity current = readIdentity(identity, config, files);
        Path shared = root.resolve("shared");
        Path state = root.resolve("state");
        if (current.initialized()) {
            validateClocks(config, root, current, files);
            for (String service : config.services().keySet()) {
                Path serviceRoot = state.resolve(service);
                files.sync(serviceRoot.resolve("clock.json")); files.sync(serviceRoot);
            }
            // A prior initialized=true rename may have succeeded before directory sync failed.
            files.sync(identity.resolve("installation.json")); files.sync(identity.resolve("service.key"));
            files.sync(identity); files.sync(shared); files.sync(state); files.sync(root);
            return new Result("ALREADY_INITIALIZED", current.id());
        }
        files.directory(shared); files.directory(state);
        for (String service : config.services().keySet()) {
            Path serviceRoot = state.resolve(service); files.directory(serviceRoot);
            Path clock = serviceRoot.resolve("clock.json");
            byte[] raw = files.optional(clock, 8192);
            if (raw == null) {
                var seed = new ClockState(config.productId(), current.id(), service, 0, 0,
                    Instant.now().truncatedTo(ChronoUnit.SECONDS), null);
                var outcome = files.publish(clock, ClockStateCodec.encode(seed, current.key()));
                if (outcome != StateStore.CommitOutcome.COMMITTED) throw new OnboardingException(OnboardingException.Code.OUTCOME_UNKNOWN);
            } else {
                ClockStateCodec.decode(raw, current.key(), config.productId(), current.id(), service);
                files.sync(clock); files.sync(serviceRoot);
            }
        }
        files.sync(shared); files.sync(state); files.sync(root);
        var outcome = files.publish(identity.resolve("installation.json"), identityBytes(config.productId(), current.id(), true));
        if (outcome != StateStore.CommitOutcome.COMMITTED) throw new OnboardingException(OnboardingException.Code.OUTCOME_UNKNOWN);
        return new Result("INITIALIZED", current.id());
    }
    private static void validateClocks(ProductConfig config, Path root, Identity identity, OnboardingFiles files) throws IOException {
        requireDirectory(root.resolve("shared")); requireDirectory(root.resolve("state"));
        for (String service : config.services().keySet()) {
            Path serviceRoot = root.resolve("state").resolve(service); requireDirectory(serviceRoot);
            byte[] raw;
            try { raw = files.read(serviceRoot.resolve("clock.json"), 8192); }
            catch (java.nio.file.NoSuchFileException missing) { throw invalid(); }
            ClockStateCodec.decode(raw, identity.key(), config.productId(), identity.id(), service);
        }
    }
    private static Identity readIdentity(Path directory, ProductConfig config, OnboardingFiles files) throws IOException {
        requireDirectory(directory);
        try (var children = Files.list(directory)) {
            Set<String> names = children.map(path -> path.getFileName().toString()).collect(java.util.stream.Collectors.toSet());
            if (!names.equals(Set.of("installation.json", "service.key"))) throw invalid();
        }
        Map<String, Object> data;
        try { data = StrictJson.object(files.read(directory.resolve("installation.json"), 4096)); }
        catch (java.nio.file.NoSuchFileException missing) { throw invalid(); }
        ClockStateCodec.fields(data, Set.of("schema_version", "product_id", "installation_id", "initialized"));
        if (!(data.get("schema_version") instanceof StrictJson.NumberToken version) || !version.text().equals("1")
            || !config.productId().equals(ClockStateCodec.text(data, "product_id"))
            || !(data.get("initialized") instanceof Boolean completed)) throw invalid();
        UUID id = ClockStateCodec.uuid(ClockStateCodec.text(data, "installation_id"), true);
        byte[] key;
        try { key = files.read(directory.resolve("service.key"), 32); }
        catch (java.nio.file.NoSuchFileException missing) { throw invalid(); }
        if (key.length != 32) throw invalid();
        return new Identity(id, completed, key);
    }
    private static void requireDirectory(Path path) throws IOException {
        if (!Files.isDirectory(OnboardingFiles.checked(path), LinkOption.NOFOLLOW_LINKS)) throw invalid();
    }
    private static byte[] identityBytes(String product, UUID id, boolean completed) {
        return ("{\"initialized\":" + completed + ",\"installation_id\":\"" + id
            + "\",\"product_id\":\"" + product + "\",\"schema_version\":1}").getBytes(StandardCharsets.US_ASCII);
    }
    private static OnboardingException invalid() { return new OnboardingException(OnboardingException.Code.INSTALLATION_INVALID); }
}
