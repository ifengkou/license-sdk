package io.metor.license.core;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.time.format.ResolverStyle;
import java.time.temporal.ChronoUnit;
import java.util.Base64;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/** Raw payload HMAC protocol, shared by deployment seeds and both SDK implementations. */
public final class ClockStateCodec {
    private static final Set<String> FIELDS = Set.of("schema_version", "product_id", "installation_id",
        "service", "epoch", "sequence", "high_watermark", "last_recovery_id");
    private static final DateTimeFormatter UTC = DateTimeFormatter.ofPattern("uuuu-MM-dd'T'HH:mm:ss'Z'")
        .withResolverStyle(ResolverStyle.STRICT);

    private ClockStateCodec() { }

    public static ClockState decode(byte[] envelope, byte[] serviceKey, String productId,
        UUID installationId, String service) {
        if (envelope.length > 8192 || serviceKey.length != 32) { throw invalid(); }
        Map<String, Object> outer = StrictJson.object(envelope);
        fields(outer, Set.of("payload", "mac"));
        byte[] raw = unbase(text(outer, "payload"));
        if (raw.length > 4096) { throw invalid(); }
        byte[] expected = hmac(derivedKey(serviceKey, productId, service), raw);
        if (!MessageDigest.isEqual(expected, unbase(text(outer, "mac")))) { throw invalid(); }
        Map<String, Object> data = StrictJson.object(raw);
        fields(data, FIELDS);
        uuid(text(data, "installation_id"), true);
        if (number(data, "schema_version") != 1 || !productId.equals(text(data, "product_id"))
            || !installationId.toString().equals(text(data, "installation_id"))
            || !service.equals(text(data, "service"))) { throw invalid(); }
        Object recovery = data.get("last_recovery_id");
        UUID recoveryId = recovery == null ? null : uuid(text(data, "last_recovery_id"), false);
        return new ClockState(productId, installationId, service, number(data, "epoch"),
            number(data, "sequence"), date(text(data, "high_watermark")), recoveryId);
    }

    /** Encodes sorted ASCII JSON at UTC second precision. Does not create runtime files. */
    public static byte[] encode(ClockState state, byte[] serviceKey) {
        if (!state.productId().matches("[A-Za-z0-9._-]{1,64}")
            || !state.service().matches("[a-z][a-z0-9-]{0,31}") || state.epoch() < 0 || state.sequence() < 0
            || serviceKey.length != 32) { throw invalid(); }
        uuid(state.installationId().toString(), true);
        String raw = "{\"epoch\":" + state.epoch() + ",\"high_watermark\":\""
            + UTC.format(state.highWatermark().truncatedTo(ChronoUnit.SECONDS).atOffset(ZoneOffset.UTC))
            + "\",\"installation_id\":\"" + state.installationId() + "\",\"last_recovery_id\":"
            + (state.lastRecoveryId() == null ? "null" : "\"" + state.lastRecoveryId() + "\"")
            + ",\"product_id\":\"" + state.productId() + "\",\"schema_version\":1,\"sequence\":"
            + state.sequence() + ",\"service\":\"" + state.service() + "\"}";
        byte[] payload = raw.getBytes(StandardCharsets.US_ASCII);
        return ("{\"mac\":\"" + base(hmac(derivedKey(serviceKey, state.productId(), state.service()), payload))
            + "\",\"payload\":\"" + base(payload) + "\"}").getBytes(StandardCharsets.US_ASCII);
    }

    static byte[] hmac(byte[] key, byte[] bytes) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key, "HmacSHA256"));
            return mac.doFinal(bytes);
        } catch (GeneralSecurityException error) { throw new IllegalStateException("HMAC unavailable", error); }
    }

    private static byte[] derivedKey(byte[] key, String product, String service) {
        return hmac(key, (product + "/state/" + service + "/v1").getBytes(StandardCharsets.UTF_8));
    }

    static Instant date(String value) {
        if (!value.matches("[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
            || value.startsWith("0000-")) { throw invalid(); }
        try { return LocalDateTime.parse(value, UTC).toInstant(ZoneOffset.UTC); }
        catch (java.time.DateTimeException error) { throw invalid(); }
    }

    static UUID uuid(String value, boolean v4) {
        if (!value.matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")) { throw invalid(); }
        UUID result = UUID.fromString(value);
        if (v4 && (result.version() != 4 || result.variant() != 2)) { throw invalid(); }
        return result;
    }

    static long number(Map<String, Object> data, String field) {
        if (!(data.get(field) instanceof StrictJson.NumberToken value) || !value.text().matches("-?0|[1-9][0-9]*")) {
            throw invalid();
        }
        try { return Long.parseLong(value.text()); }
        catch (NumberFormatException error) { throw invalid(); }
    }

    static String text(Map<String, Object> data, String field) {
        if (!(data.get(field) instanceof String value)) { throw invalid(); }
        return value;
    }

    static void fields(Map<String, Object> data, Set<String> fields) {
        if (!data.keySet().equals(fields)) { throw invalid(); }
    }

    private static String base(byte[] bytes) { return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes); }
    private static byte[] unbase(String text) {
        if (!text.matches("[A-Za-z0-9_-]+")) { throw invalid(); }
        try {
            byte[] result = Base64.getUrlDecoder().decode(text);
            if (!base(result).equals(text)) { throw invalid(); }
            return result;
        } catch (IllegalArgumentException error) { throw invalid(); }
    }
    private static IllegalArgumentException invalid() { return new IllegalArgumentException("Invalid clock state"); }
}
