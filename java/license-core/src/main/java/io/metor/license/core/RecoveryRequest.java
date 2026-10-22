package io.metor.license.core;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/** An offline request is diagnostic input, never authorization to reset the clock. */
public record RecoveryRequest(String productId, UUID installationId, String service, UUID nonce,
    String reason, String stateIdentity, Long baseEpoch, Instant generatedAt, String targetId,
    String recoveryCode) {
    public Map<String, Object> toMap() {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("base_epoch", baseEpoch);
        data.put("generated_at", generatedAt.toString());
        data.put("installation_id", installationId.toString());
        data.put("kind", "clock-recovery-request");
        data.put("nonce", nonce.toString());
        data.put("product_id", productId);
        data.put("reason", reason);
        data.put("recovery_code", recoveryCode);
        data.put("schema_version", 1);
        data.put("service", service);
        data.put("state_identity", stateIdentity);
        data.put("target_id", targetId);
        return Collections.unmodifiableMap(data);
    }

    /** Fixed validated ASCII fields make the request's sorted wire bytes deterministic. */
    public byte[] toJson() {
        return ("{\"base_epoch\":" + baseEpoch + ",\"generated_at\":\"" + generatedAt
            + "\",\"installation_id\":\"" + installationId + "\",\"kind\":\"clock-recovery-request\",\"nonce\":\""
            + nonce + "\",\"product_id\":\"" + productId + "\",\"reason\":\"" + reason
            + "\",\"recovery_code\":\"" + recoveryCode + "\",\"schema_version\":1,\"service\":\"" + service
            + "\",\"state_identity\":\"" + stateIdentity + "\",\"target_id\":\"" + targetId + "\"}")
            .getBytes(StandardCharsets.US_ASCII);
    }
}
