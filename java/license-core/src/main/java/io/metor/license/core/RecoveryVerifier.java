package io.metor.license.core;

import java.security.GeneralSecurityException;
import java.security.PublicKey;
import java.security.Signature;
import java.time.Instant;
import java.util.Base64;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** Strict signed recovery verification; checking its window is separate from idempotency. */
public final class RecoveryVerifier {
    public record VerifiedRecovery(String keyId, String productId, UUID installationId, String service,
        UUID recoveryId, String targetId, Instant issuedAt, Instant windowStart, Instant windowEnd) { }
    private static final Set<String> FIELDS = Set.of("schema_version", "kind", "key_id", "product_id",
        "installation_id", "service", "recovery_id", "target_id", "issued_at", "window_start", "window_end");
    private final String productId;
    private final Map<String, PublicKey> trust;
    public RecoveryVerifier(String productId, Map<String, PublicKey> trust) {
        new CommercialLicenseVerifier(productId, trust);
        this.productId = productId;
        this.trust = Map.copyOf(trust);
    }

    public VerifiedRecovery verify(byte[] envelope, UUID installation, String service) {
        try {
            if (envelope == null || envelope.length > 65536) throw new IllegalArgumentException();
            Map<String, Object> outer = StrictJson.object(envelope.clone());
            ClockStateCodec.fields(outer, Set.of("payload", "signature"));
            byte[] raw = decode(ClockStateCodec.text(outer, "payload"));
            byte[] signature = decode(ClockStateCodec.text(outer, "signature"));
            if (raw.length > 32768 || signature.length != 64) throw new IllegalArgumentException();
            Map<String, Object> data = StrictJson.object(raw);
            ClockStateCodec.fields(data, FIELDS);
            String keyId = ClockStateCodec.text(data, "key_id");
            PublicKey key = trust.get(keyId);
            if (key == null || !keyId.matches("[A-Za-z0-9._-]{1,64}")) throw new IllegalArgumentException();
            Signature verifier = Signature.getInstance("Ed25519");
            verifier.initVerify(key);
            verifier.update(raw);
            if (!verifier.verify(signature)) throw new IllegalArgumentException();
            if (ClockStateCodec.number(data, "schema_version") != 1
                || !"clock-recovery".equals(ClockStateCodec.text(data, "kind"))
                || !productId.equals(ClockStateCodec.text(data, "product_id"))) throw new IllegalArgumentException();
            VerifiedRecovery result = new VerifiedRecovery(keyId, productId,
                ClockStateCodec.uuid(ClockStateCodec.text(data, "installation_id"), true),
                ClockStateCodec.text(data, "service"), ClockStateCodec.uuid(ClockStateCodec.text(data, "recovery_id"), true),
                ClockStateCodec.text(data, "target_id"), ClockStateCodec.date(ClockStateCodec.text(data, "issued_at")),
                ClockStateCodec.date(ClockStateCodec.text(data, "window_start")), ClockStateCodec.date(ClockStateCodec.text(data, "window_end")));
            if (!result.service().matches("[a-z][a-z0-9-]{0,31}") || !result.targetId().matches("[0-9a-f]{40}")
                || result.issuedAt().isAfter(result.windowStart()) || !result.windowStart().isBefore(result.windowEnd())
                || result.windowEnd().isAfter(result.issuedAt().plusSeconds(7 * 86400))) throw new IllegalArgumentException();
            if (!result.installationId().equals(installation) || !result.service().equals(service)) {
                throw new RecoveryException(RecoveryException.Code.TARGET_CHANGED);
            }
            return result;
        } catch (GeneralSecurityException | IllegalArgumentException error) {
            throw new RecoveryException(RecoveryException.Code.INVALID);
        }
    }

    private static byte[] decode(String value) {
        if (!value.matches("[A-Za-z0-9_-]+")) throw new IllegalArgumentException();
        byte[] raw = Base64.getUrlDecoder().decode(value);
        if (!Base64.getUrlEncoder().withoutPadding().encodeToString(raw).equals(value)) throw new IllegalArgumentException();
        return raw;
    }
}
