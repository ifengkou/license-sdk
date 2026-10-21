package io.metor.license.spring;

import static org.junit.jupiter.api.Assertions.assertThrows;
import org.junit.jupiter.api.Test;
import java.time.Instant;
import java.util.UUID;

class ControlProofCodecTest {
    @Test void actualRequestPurposeAndTimeAreBoundIndependentlyOfUserTokens() {
        var codec = new ControlProofCodec("test/metor", "test/ai");
        byte[] key = new byte[32]; UUID installation = UUID.randomUUID(); Instant now = Instant.ofEpochSecond(1000);
        String token = codec.sign(key, installation, "LICENSE_VALIDATE", "POST", "/internal/license/validate", new byte[]{1}, now, UUID.randomUUID());
        codec.verify(token, key, installation, "LICENSE_VALIDATE", "POST", "/internal/license/validate", new byte[]{1}, now.plusSeconds(64));
        assertThrows(IllegalArgumentException.class, () -> codec.verify(token, key, installation, "LICENSE_VALIDATE", "POST", "/internal/license/validate", new byte[]{1}, now.plusSeconds(65)));
        assertThrows(IllegalArgumentException.class, () -> codec.verify(token, key, UUID.randomUUID(), "LICENSE_VALIDATE", "POST", "/internal/license/validate", new byte[]{1}, now));
        assertThrows(IllegalArgumentException.class, () -> codec.verify(token, key, installation, "LICENSE_STATUS", "POST", "/internal/license/validate", new byte[]{1}, now));
        assertThrows(IllegalArgumentException.class, () -> codec.verify(token, key, installation, "LICENSE_VALIDATE", "POST", "/internal/license/validate", new byte[]{2}, now));
        assertThrows(IllegalArgumentException.class, () -> new ControlProofCodec("other/metor", "test/ai").verify(token, key, installation, "LICENSE_VALIDATE", "POST", "/internal/license/validate", new byte[]{1}, now));
    }
}
