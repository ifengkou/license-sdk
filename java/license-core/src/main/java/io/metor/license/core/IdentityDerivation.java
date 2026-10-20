package io.metor.license.core;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.Objects;
import java.util.UUID;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/** Stateless identity derivation; callers provide actual host and installation identity. */
public final class IdentityDerivation {
    private final String fingerprintDomain;
    private final String bindingDomain;
    private final String recoveryDomain;

    public IdentityDerivation(String fingerprintDomain, String bindingDomain, String recoveryDomain) {
        this.fingerprintDomain = domain(fingerprintDomain);
        this.bindingDomain = domain(bindingDomain);
        this.recoveryDomain = domain(recoveryDomain);
    }

    public byte[] hostFingerprint(String machineId) {
        Objects.requireNonNull(machineId, "machineId");
        String normalized = machineId.endsWith("\n")
            ? machineId.substring(0, machineId.length() - 1) : machineId;
        if (!normalized.matches("[0-9a-f]{32}") || normalized.equals("0".repeat(32))) {
            throw new IllegalArgumentException("Invalid host machine ID");
        }
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(utf8(fingerprintDomain), "HmacSHA256"));
            return mac.doFinal(HexFormat.of().parseHex(normalized));
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("Required HMAC unavailable", e);
        }
    }

    public byte[] binding(String machineId, UUID installationId) {
        byte[] fingerprint = hostFingerprint(machineId);
        ByteBuffer input = ByteBuffer.allocate(utf8(bindingDomain).length + 1 + 32 + 16);
        input.put(utf8(bindingDomain)).put((byte) 0).put(fingerprint).put(uuidBytes(installationId));
        return Arrays.copyOf(sha256(input.array()), 20);
    }

    public byte[] recoveryTarget(UUID installationId, String service, UUID nonce, byte[] stateIdentity) {
        if (service == null || !service.matches("[a-z][a-z0-9-]{0,31}")) {
            throw new IllegalArgumentException("Invalid service identity");
        }
        if (stateIdentity == null || stateIdentity.length != 32) {
            throw new IllegalArgumentException("State identity must be 32 bytes");
        }
        ByteBuffer input = ByteBuffer.allocate(utf8(recoveryDomain).length + 1 + 16
            + service.length() + 1 + 16 + 32);
        input.put(utf8(recoveryDomain)).put((byte) 0).put(uuidBytes(installationId))
            .put(utf8(service)).put((byte) 0).put(uuidBytes(nonce)).put(stateIdentity);
        return Arrays.copyOf(sha256(input.array()), 20);
    }

    private static byte[] uuidBytes(UUID value) {
        Objects.requireNonNull(value, "uuid");
        if (value.version() != 4 || value.variant() != 2) {
            throw new IllegalArgumentException("Identity must be an RFC 4122 UUID v4");
        }
        return ByteBuffer.allocate(16).putLong(value.getMostSignificantBits())
            .putLong(value.getLeastSignificantBits()).array();
    }

    private static byte[] sha256(byte[] value) {
        try { return MessageDigest.getInstance("SHA-256").digest(value); }
        catch (GeneralSecurityException e) { throw new IllegalStateException("Required SHA-256 unavailable", e); }
    }

    private static byte[] utf8(String value) { return value.getBytes(StandardCharsets.UTF_8); }

    private static String domain(String value) {
        if (value == null || !value.matches("[a-zA-Z0-9/._-]{1,128}")) {
            throw new IllegalArgumentException("Domain must be a nonempty ASCII protocol identifier");
        }
        return value;
    }
}
