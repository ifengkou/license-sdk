package io.metor.license.core;

import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.security.PublicKey;
import java.security.Signature;
import java.security.interfaces.EdECPublicKey;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.time.format.ResolverStyle;
import java.util.Base64;
import java.util.HexFormat;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import java.util.function.Supplier;

/** Pure commercial validation. Time protection and actual host identity are caller responsibilities. */
public final class CommercialLicenseVerifier {
    public static final int MAX_FILE_BYTES = 64 * 1024;
    public static final int MAX_PAYLOAD_BYTES = 32 * 1024;
    private static final Set<String> FIELDS = Set.of("schema_version", "key_id", "kind",
        "license_id", "customer_id", "customer_name", "product_id", "binding_type",
        "binding_scheme", "binding_id", "validity_type", "issued_at", "not_before", "expires_at");
    private static final DateTimeFormatter UTC = DateTimeFormatter.ofPattern("uuuu-MM-dd'T'HH:mm:ss'Z'")
        .withResolverStyle(ResolverStyle.STRICT);
    private final String productId;
    private final Map<String, PublicKey> trustRing;

    public enum Status { ACTIVE, NOT_YET_VALID, EXPIRED }
    public enum BindingType { HOST_INSTALL, UNBOUND }
    public enum ValidityType { TERM, PERPETUAL }

    /** Signed fields only, no mutable arrays or unverified extension properties. */
    public record Payload(int schemaVersion, String keyId, String kind, UUID licenseId,
        String customerId, String customerName, String productId, BindingType bindingType,
        String bindingScheme, String bindingId, ValidityType validityType,
        Instant issuedAt, Instant notBefore, Instant expiresAt) { }

    public record VerifiedLicense(Payload payload, String payloadDigest, String fileDigest, Status status) { }

    /** Trust comes from controlled product assembly, never from fields in the candidate. */
    public CommercialLicenseVerifier(String productId, Map<String, PublicKey> trustRing) {
        if (!identifier(productId)) { throw new IllegalArgumentException("Invalid configured product ID"); }
        Objects.requireNonNull(trustRing, "trustRing");
        for (Map.Entry<String, PublicKey> entry : trustRing.entrySet()) {
            if (!identifier(entry.getKey()) || !(entry.getValue() instanceof EdECPublicKey ed)
                || !ed.getParams().getName().equals("Ed25519")) {
                throw new IllegalArgumentException("Trust must contain Ed25519 public keys with valid IDs");
            }
        }
        this.productId = productId;
        this.trustRing = Map.copyOf(trustRing);
    }

    /** UNBOUND permits null localBindingId; HOST_INSTALL requires a recomputed local binding digest. */
    public VerifiedLicense verify(byte[] envelope, String localBindingId, Instant effectiveNow) {
        return verifyWithBindingProvider(envelope, () -> localBindingId, effectiveNow);
    }

    /** Resolves actual host/install binding lazily, only after a HOST_INSTALL signature is valid. */
    public VerifiedLicense verifyWithBindingProvider(byte[] envelope, Supplier<String> localBinding,
        Instant effectiveNow) {
        Objects.requireNonNull(effectiveNow, "effectiveNow");
        if (envelope == null || envelope.length > MAX_FILE_BYTES) {
            throw failure(LicenseException.Code.SIZE_LIMIT);
        }
        // Own bytes while parsing and hashing: callers may retain their input buffer.
        byte[] file = envelope.clone();
        Map<String, Object> outer = StrictJson.object(file);
        requireFields(outer, Set.of("payload", "signature"));
        byte[] raw = decode(text(outer, "payload"));
        if (raw.length > MAX_PAYLOAD_BYTES) { throw failure(LicenseException.Code.SIZE_LIMIT); }
        byte[] signature = decode(text(outer, "signature"));
        if (signature.length != 64) { throw failure(LicenseException.Code.INVALID_SIGNATURE); }
        Map<String, Object> data = StrictJson.object(raw);
        requireFields(data, FIELDS);
        String keyId = text(data, "key_id");
        if (!identifier(keyId)) { throw schema(); }
        PublicKey key = trustRing.get(keyId);
        if (key == null) { throw failure(LicenseException.Code.UNKNOWN_KEY); }
        verifySignature(key, raw, signature);
        Payload payload = payload(data);
        if (!productId.equals(payload.productId())) { throw failure(LicenseException.Code.PRODUCT_MISMATCH); }
        if (payload.bindingType() == BindingType.HOST_INSTALL) {
            String localBindingId;
            try { localBindingId = localBinding == null ? null : localBinding.get(); }
            catch (RuntimeException error) { throw failure(LicenseException.Code.BINDING_UNAVAILABLE); }
            if (localBindingId == null || !localBindingId.matches("[0-9a-f]{40}")) {
                throw failure(LicenseException.Code.BINDING_UNAVAILABLE);
            }
            if (!payload.bindingId().equals(localBindingId)) {
                throw failure(LicenseException.Code.BINDING_MISMATCH);
            }
        }
        Status status = effectiveNow.isBefore(payload.notBefore()) ? Status.NOT_YET_VALID
            : payload.expiresAt() != null && !effectiveNow.isBefore(payload.expiresAt()) ? Status.EXPIRED
            : Status.ACTIVE;
        return new VerifiedLicense(payload, digest(raw), digest(file), status);
    }

    private static Payload payload(Map<String, Object> data) {
        if (!(data.get("schema_version") instanceof StrictJson.NumberToken version)
            || !version.text().equals("1") || !text(data, "kind").equals("commercial-license")) {
            throw schema();
        }
        String keyId = text(data, "key_id");
        String customerId = text(data, "customer_id");
        String productId = text(data, "product_id");
        if (!identifier(customerId) || !identifier(productId)) { throw schema(); }
        String name = text(data, "customer_name");
        int points = name.codePointCount(0, name.length());
        if (points < 1 || points > 200 || name.codePoints().anyMatch(Character::isISOControl)) { throw schema(); }
        String licenseId = text(data, "license_id");
        if (!licenseId.matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")) {
            throw schema();
        }
        BindingType binding;
        ValidityType validity;
        try {
            binding = BindingType.valueOf(text(data, "binding_type"));
            validity = ValidityType.valueOf(text(data, "validity_type"));
        } catch (IllegalArgumentException error) { throw schema(); }
        String scheme = null;
        String bindingId = null;
        if (binding == BindingType.HOST_INSTALL) {
            scheme = text(data, "binding_scheme");
            bindingId = text(data, "binding_id");
            if (!scheme.equals("host-install-code-v1") || !bindingId.matches("[0-9a-f]{40}")) { throw schema(); }
        } else if (data.get("binding_scheme") != null || data.get("binding_id") != null) { throw schema(); }
        Instant issued = date(text(data, "issued_at"));
        Instant before = date(text(data, "not_before"));
        Instant expires = null;
        if (validity == ValidityType.TERM) {
            expires = date(text(data, "expires_at"));
            if (issued.isAfter(expires) || !before.isBefore(expires)) { throw schema(); }
        } else if (data.get("expires_at") != null) { throw schema(); }
        return new Payload(1, keyId, "commercial-license", UUID.fromString(licenseId), customerId,
            name, productId, binding, scheme, bindingId, validity, issued, before, expires);
    }

    private static Instant date(String value) {
        if (!value.matches("[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
            || value.startsWith("0000-")) { throw schema(); }
        try { return LocalDateTime.parse(value, UTC).toInstant(ZoneOffset.UTC); }
        catch (DateTimeParseException error) { throw schema(); }
    }

    private static void verifySignature(PublicKey key, byte[] payload, byte[] signature) {
        try {
            Signature verifier = Signature.getInstance("Ed25519");
            verifier.initVerify(key);
            verifier.update(payload);
            if (!verifier.verify(signature)) { throw failure(LicenseException.Code.INVALID_SIGNATURE); }
        } catch (GeneralSecurityException error) { throw failure(LicenseException.Code.INVALID_SIGNATURE); }
    }

    private static byte[] decode(String encoded) {
        if (!encoded.matches("[A-Za-z0-9_-]+")) { throw failure(LicenseException.Code.INVALID_BASE64); }
        try {
            byte[] decoded = Base64.getUrlDecoder().decode(encoded);
            if (!Base64.getUrlEncoder().withoutPadding().encodeToString(decoded).equals(encoded)) {
                throw failure(LicenseException.Code.INVALID_BASE64);
            }
            return decoded;
        } catch (IllegalArgumentException error) { throw failure(LicenseException.Code.INVALID_BASE64); }
    }

    private static String digest(byte[] bytes) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)); }
        catch (GeneralSecurityException error) { throw new IllegalStateException("Required SHA-256 unavailable", error); }
    }

    private static String text(Map<String, Object> data, String name) {
        if (!(data.get(name) instanceof String value)) { throw schema(); }
        return value;
    }

    private static void requireFields(Map<String, Object> data, Set<String> fields) {
        if (!data.keySet().equals(fields)) { throw schema(); }
    }

    private static boolean identifier(String value) {
        return value != null && value.matches("[A-Za-z0-9._-]{1,64}");
    }

    private static LicenseException schema() { return failure(LicenseException.Code.INVALID_SCHEMA); }
    private static LicenseException failure(LicenseException.Code code) { return new LicenseException(code); }
}
