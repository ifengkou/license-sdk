package io.metor.license.core;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Base64;
import java.util.HexFormat;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** The installation HMAC authenticates a single service's pending recovery target. */
public final class RecoveryChallengeCodec {
    public record Challenge(String productId, UUID installationId, String service, UUID nonce,
        String reason, String stateIdentity, Long baseEpoch, Instant generatedAt) { }
    private static final Set<String> FIELDS = Set.of("schema_version", "kind", "product_id",
        "installation_id", "service", "nonce", "reason", "state_identity", "base_epoch", "generated_at");
    private RecoveryChallengeCodec() { }

    public static byte[] encode(Challenge value, byte[] master) {
        validate(value);
        if (master.length != 32) throw invalid();
        String data = "{\"base_epoch\":" + value.baseEpoch() + ",\"generated_at\":\"" + value.generatedAt()
            + "\",\"installation_id\":\"" + value.installationId() + "\",\"kind\":\"recovery-challenge\",\"nonce\":\""
            + value.nonce() + "\",\"product_id\":\"" + value.productId() + "\",\"reason\":\"" + value.reason()
            + "\",\"schema_version\":1,\"service\":\"" + value.service() + "\",\"state_identity\":\""
            + value.stateIdentity() + "\"}";
        byte[] raw = data.getBytes(StandardCharsets.US_ASCII);
        return ("{\"mac\":\"" + base(ClockStateCodec.hmac(key(master, value.productId(), value.service()), raw))
            + "\",\"payload\":\"" + base(raw) + "\"}").getBytes(StandardCharsets.US_ASCII);
    }

    public static Challenge decode(byte[] envelope, byte[] master, String product, UUID installation, String service) {
        if (envelope.length > 8192 || master.length != 32) throw invalid();
        Map<String, Object> outer = StrictJson.object(envelope);
        ClockStateCodec.fields(outer, Set.of("payload", "mac"));
        byte[] raw = unbase(ClockStateCodec.text(outer, "payload"));
        byte[] mac = unbase(ClockStateCodec.text(outer, "mac"));
        if (raw.length > 4096 || mac.length != 32
            || !MessageDigest.isEqual(mac, ClockStateCodec.hmac(key(master, product, service), raw))) throw invalid();
        Map<String, Object> data = StrictJson.object(raw);
        ClockStateCodec.fields(data, FIELDS);
        if (ClockStateCodec.number(data, "schema_version") != 1
            || !"recovery-challenge".equals(ClockStateCodec.text(data, "kind"))) throw invalid();
        Long epoch = data.get("base_epoch") == null ? null : ClockStateCodec.number(data, "base_epoch");
        Challenge result = new Challenge(ClockStateCodec.text(data, "product_id"),
            ClockStateCodec.uuid(ClockStateCodec.text(data, "installation_id"), true),
            ClockStateCodec.text(data, "service"), ClockStateCodec.uuid(ClockStateCodec.text(data, "nonce"), true),
            ClockStateCodec.text(data, "reason"), ClockStateCodec.text(data, "state_identity"), epoch,
            ClockStateCodec.date(ClockStateCodec.text(data, "generated_at")));
        validate(result);
        if (!result.productId().equals(product) || !result.installationId().equals(installation)
            || !result.service().equals(service)) throw invalid();
        return result;
    }

    public static RecoveryRequest request(Challenge value, IdentityDerivation derivation, String prefix) {
        validate(value);
        byte[] target = derivation.recoveryTarget(value.installationId(), value.service(), value.nonce(),
            HexFormat.of().parseHex(value.stateIdentity()));
        return new RecoveryRequest(value.productId(), value.installationId(), value.service(), value.nonce(),
            value.reason(), value.stateIdentity(), value.baseEpoch(), value.generatedAt(),
            HexFormat.of().formatHex(target), new ManualCodeCodec(prefix).encode(target));
    }

    private static void validate(Challenge value) {
        if (!value.productId().matches("[A-Za-z0-9._-]{1,64}")
            || !value.service().matches("[a-z][a-z0-9-]{0,31}")
            || !Set.of("CLOCK_ROLLBACK", "CLOCK_STATE_INVALID").contains(value.reason())
            || !value.stateIdentity().matches("[0-9a-f]{64}")
            || value.baseEpoch() != null && value.baseEpoch() < 0
            || !value.generatedAt().equals(value.generatedAt().truncatedTo(ChronoUnit.SECONDS))) throw invalid();
        ClockStateCodec.uuid(value.installationId().toString(), true);
        ClockStateCodec.uuid(value.nonce().toString(), true);
        ClockStateCodec.date(value.generatedAt().toString());
    }
    private static byte[] key(byte[] master, String product, String service) {
        return ClockStateCodec.hmac(master, (product + "/recovery-challenge/" + service + "/v1").getBytes(StandardCharsets.UTF_8));
    }
    private static String base(byte[] raw) { return Base64.getUrlEncoder().withoutPadding().encodeToString(raw); }
    private static byte[] unbase(String value) {
        if (!value.matches("[A-Za-z0-9_-]+")) throw invalid();
        byte[] raw = Base64.getUrlDecoder().decode(value);
        if (!base(raw).equals(value)) throw invalid();
        return raw;
    }
    private static IllegalArgumentException invalid() { return new IllegalArgumentException("Invalid recovery challenge"); }
}
