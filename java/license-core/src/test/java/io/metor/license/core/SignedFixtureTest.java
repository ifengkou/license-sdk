package io.metor.license.core;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.PublicKey;
import java.security.Signature;
import java.security.spec.X509EncodedKeySpec;
import java.util.Base64;
import java.util.HashSet;
import java.util.Set;
import org.junit.jupiter.api.Test;

/** Verifies public test vectors with the JDK; this is not the future SDK verifier. */
class SignedFixtureTest {
    @Test
    void fourSyntheticEnvelopesHaveValidSignaturesAndRejectTampering() throws Exception {
        ObjectMapper mapper = new ObjectMapper();
        JsonNode fixture;
        try (InputStream input = getClass().getResourceAsStream("/commercial-v1-test-vectors.json")) {
            assertNotNull(input, "Test-only signing fixtures must be available");
            fixture = mapper.readTree(input);
        }
        assertEquals("synthetic-test-trust-only", fixture.path("purpose").asText());
        PublicKey key = KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(
            Base64.getDecoder().decode(fixture.path("public_key_spki_base64").asText())));
        Set<String> combinations = new HashSet<>();
        assertEquals(4, fixture.path("cases").size());
        for (JsonNode vector : fixture.path("cases")) {
            JsonNode envelope = vector.path("envelope");
            byte[] payload = Base64.getUrlDecoder().decode(envelope.path("payload").asText());
            byte[] signature = Base64.getUrlDecoder().decode(envelope.path("signature").asText());
            Signature verifier = Signature.getInstance("Ed25519");
            verifier.initVerify(key);
            verifier.update(payload);
            assertTrue(verifier.verify(signature));
            JsonNode parsed = mapper.readTree(new String(payload, StandardCharsets.UTF_8));
            assertEquals("phase1-test-only", parsed.path("key_id").asText());
            assertEquals("icp-diag", parsed.path("product_id").asText());
            combinations.add(parsed.path("validity_type").asText() + ":"
                + parsed.path("binding_type").asText());
            payload[0] ^= 1;
            verifier.initVerify(key);
            verifier.update(payload);
            assertFalse(verifier.verify(signature), "Changing signed bytes must fail");
        }
        assertEquals(Set.of("TERM:HOST_INSTALL", "TERM:UNBOUND",
            "PERPETUAL:HOST_INSTALL", "PERPETUAL:UNBOUND"), combinations);
    }
}
