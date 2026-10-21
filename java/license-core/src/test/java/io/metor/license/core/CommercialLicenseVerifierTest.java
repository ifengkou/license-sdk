package io.metor.license.core;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.PublicKey;
import java.security.Signature;
import java.security.spec.X509EncodedKeySpec;
import java.time.Instant;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class CommercialLicenseVerifierTest {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final Instant START = Instant.parse("2026-10-20T00:00:00Z");
    private static final Instant END = Instant.parse("2027-10-20T00:00:00Z");
    private static final String BINDING = "6e557d24d92ba076121accaa712ec769bbc085df";
    private final KeyPair keys;
    private final CommercialLicenseVerifier verifier;

    CommercialLicenseVerifierTest() throws Exception {
        keys = KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
        verifier = new CommercialLicenseVerifier("icp-diag", Map.of("test", keys.getPublic()));
    }

    @Test
    void publicFixturesCoverAllFourCombinationsAndExactBoundaries() throws Exception {
        JsonNode fixtures;
        try (InputStream input = getClass().getResourceAsStream("/commercial-v1-test-vectors.json")) {
            fixtures = JSON.readTree(input);
        }
        PublicKey publicKey = KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(
            Base64.getDecoder().decode(fixtures.path("public_key_spki_base64").asText())));
        CommercialLicenseVerifier check = new CommercialLicenseVerifier("icp-diag",
            Map.of("phase1-test-only", publicKey));
        for (JsonNode item : fixtures.path("cases")) {
            byte[] file = JSON.writeValueAsBytes(item.path("envelope"));
            assertEquals(CommercialLicenseVerifier.Status.NOT_YET_VALID,
                check.verify(file, BINDING, START.minusNanos(1)).status());
            assertEquals(CommercialLicenseVerifier.Status.ACTIVE, check.verify(file, BINDING, START).status());
            assertEquals(CommercialLicenseVerifier.Status.ACTIVE,
                check.verify(file, BINDING, END.minusNanos(1)).status());
            CommercialLicenseVerifier.Status expected = item.path("case").asText().startsWith("TERM-")
                ? CommercialLicenseVerifier.Status.EXPIRED : CommercialLicenseVerifier.Status.ACTIVE;
            assertEquals(expected, check.verify(file, BINDING, END).status());
        }
    }

    @Test
    void verifiesOriginalBytesAndHashesFileIndependently() throws Exception {
        byte[] raw = JSON.writerWithDefaultPrettyPrinter().writeValueAsBytes(payload());
        byte[] file = signed(raw);
        var result = verifier.verify(file, null, START);
        assertEquals(CommercialLicenseVerifier.Status.ACTIVE, result.status());
        assertEquals("Synthetic", result.payload().customerName());
        byte[] formattedEnvelope = JSON.writerWithDefaultPrettyPrinter().writeValueAsBytes(JSON.readTree(file));
        var formatted = verifier.verify(formattedEnvelope, null, START);
        assertEquals(result.payloadDigest(), formatted.payloadDigest());
        assertNotEquals(result.fileDigest(), formatted.fileDigest());
        Map<String, String> envelope = new LinkedHashMap<>();
        envelope.put("payload", b64(JSON.writeValueAsBytes(payload())));
        envelope.put("signature", JSON.readTree(file).path("signature").asText());
        rejects(JSON.writeValueAsBytes(envelope), LicenseException.Code.INVALID_SIGNATURE);
    }

    @Test
    void strictRequiredTypesAndEnumsCannotDisableBindingOrExpiry() throws Exception {
        for (String field : payload().keySet()) {
            Map<String, Object> candidate = payload();
            candidate.remove(field);
            rejects(signed(JSON.writeValueAsBytes(candidate)), LicenseException.Code.INVALID_SCHEMA);
        }
        for (Object version : List.of(true, "1", 1.0, 2)) {
            rejectsField("schema_version", version);
        }
        for (String field : List.of("kind", "validity_type", "binding_type")) { rejectsField(field, "unknown"); }
        rejectsField("binding_scheme", "host-install-code-v1");
        rejectsField("binding_id", BINDING);
        rejectsField("expires_at", "2027-10-20T00:00:00Z");
        rejectsField("license_id", "FFFFFFFF-0000-4000-8000-000000000001");
        rejectsField("customer_id", "客户");
        rejectsField("customer_name", "");
        rejectsField("unexpected", true);
        Map<String, Object> term = payload();
        term.put("validity_type", "TERM");
        rejects(signed(JSON.writeValueAsBytes(term)), LicenseException.Code.INVALID_SCHEMA);
        term.put("expires_at", "2026-10-20T00:00:00Z");
        rejects(signed(JSON.writeValueAsBytes(term)), LicenseException.Code.INVALID_SCHEMA);
        term.put("not_before", "2026-10-19T00:00:00Z");
        term.put("issued_at", "2026-10-21T00:00:00Z");
        rejects(signed(JSON.writeValueAsBytes(term)), LicenseException.Code.INVALID_SCHEMA);
    }

    @Test
    void namesCountUnicodeCodepointsAndRejectControlOrUnpairedSurrogates() throws Exception {
        Map<String, Object> candidate = payload();
        candidate.put("customer_name", "😀".repeat(200));
        assertEquals("😀".repeat(200), verifier.verify(signed(JSON.writeValueAsBytes(candidate)), null, START)
            .payload().customerName());
        rejectsField("customer_name", "😀".repeat(201));
        for (String invalid : List.of("\u0000", "\u001f", "\u007f", "\u0080", "\u009f")) {
            rejectsField("customer_name", invalid);
        }
        String valid = JSON.writeValueAsString(payload());
        for (String bad : List.of("\\ud800", "\\udfff", "\\ud800x", "\\udfff\\ud800")) {
            rejects(signed(valid.replace("Synthetic", bad).getBytes(StandardCharsets.UTF_8)),
                LicenseException.Code.INVALID_JSON);
        }
    }

    @Test
    void strictCalendarUtcSecondsAndTermOrdering() throws Exception {
        for (String invalid : List.of("2026-02-29T00:00:00Z", "2026-10-20T24:00:00Z",
            "2026-10-20T00:00:60Z", "2026-10-20T00:00:00.0Z", "2026-10-20T00:00:00+00:00",
            "2026-10-20t00:00:00z", "0000-01-01T00:00:00Z", "2026-13-20T00:00:00Z")) {
            rejectsField("not_before", invalid);
        }
        Map<String, Object> candidate = payload();
        candidate.put("not_before", "2024-02-29T00:00:00Z");
        verifier.verify(signed(JSON.writeValueAsBytes(candidate)), null, START);
    }

    @Test
    void trustProductAndBindingAreIndependentRequirements() throws Exception {
        Map<String, Object> candidate = payload();
        candidate.put("product_id", "other-product");
        rejects(signed(JSON.writeValueAsBytes(candidate)), LicenseException.Code.PRODUCT_MISMATCH);
        candidate = payload();
        candidate.put("key_id", "unknown");
        rejects(signed(JSON.writeValueAsBytes(candidate)), LicenseException.Code.UNKNOWN_KEY);
        candidate = payload();
        candidate.put("binding_type", "HOST_INSTALL");
        candidate.put("binding_scheme", "host-install-code-v1");
        candidate.put("binding_id", BINDING);
        byte[] file = signed(JSON.writeValueAsBytes(candidate));
        assertEquals(LicenseException.Code.BINDING_UNAVAILABLE,
            assertThrows(LicenseException.class, () -> verifier.verify(file, null, START)).code());
        assertEquals(LicenseException.Code.BINDING_MISMATCH,
            assertThrows(LicenseException.class, () -> verifier.verify(file, "0".repeat(40), START)).code());
        assertEquals(CommercialLicenseVerifier.Status.ACTIVE, verifier.verify(file, BINDING, START).status());
        verifier.verify(signed(JSON.writeValueAsBytes(payload())), null, START);
        verifier.verifyWithBindingProvider(signed(JSON.writeValueAsBytes(payload())), () -> {
            throw new AssertionError("UNBOUND must not read host identity");
        }, START);
        assertThrows(IllegalArgumentException.class, () -> new CommercialLicenseVerifier("icp-diag",
            Map.of("key", KeyPairGenerator.getInstance("RSA").generateKeyPair().getPublic())));
    }

    @Test
    void rejectsMalformedJsonUtf8DepthAndDuplicateEscapedKeys() throws Exception {
        for (String invalid : List.of("{}{}", "{\"a\":01}", "{\"a\":NaN}", "{\"a\":Infinity}",
            "{\"a\":-}", "{\"a\":1.}", "{\"a\":1e}", "{\"a\":1,}", "{\"a\":[1,]}",
            "{\"a\":null,\"a\":1}", "{\"a\":0,\"\\u0061\":1}", "\ufeff{}", "[]",
            "{\"a\":" + "[".repeat(8) + "0" + "]".repeat(8) + "}")) {
            rejects(invalid.getBytes(StandardCharsets.UTF_8), LicenseException.Code.INVALID_JSON);
        }
        rejects(new byte[] {(byte) 0xc0, (byte) 0xaf}, LicenseException.Code.INVALID_JSON);
        String duplicate = JSON.writeValueAsString(payload()).replace("\"schema_version\":1",
            "\"schema_version\":1,\"schema_version\":1");
        rejects(signed(duplicate.getBytes(StandardCharsets.UTF_8)), LicenseException.Code.INVALID_JSON);
    }

    @Test
    void rejectsNoncanonicalBase64AndSizeLimits() throws Exception {
        byte[] file = signed(JSON.writeValueAsBytes(payload()));
        JsonNode node = JSON.readTree(file);
        for (String bad : List.of(node.path("payload").asText() + "=", "+/==", "a", "AB", "")) {
            rejects(JSON.writeValueAsBytes(Map.of("payload", bad, "signature", node.path("signature").asText())),
                LicenseException.Code.INVALID_BASE64);
        }
        rejects(JSON.writeValueAsBytes(Map.of("payload", node.path("payload").asText(), "signature", "AA")),
            LicenseException.Code.INVALID_SIGNATURE);
        rejects(new byte[65537], LicenseException.Code.SIZE_LIMIT);
        rejects(signed(new byte[32769]), LicenseException.Code.SIZE_LIMIT);
    }

    private void rejectsField(String field, Object value) throws Exception {
        Map<String, Object> candidate = payload();
        candidate.put(field, value);
        rejects(signed(JSON.writeValueAsBytes(candidate)), LicenseException.Code.INVALID_SCHEMA);
    }

    private void rejects(byte[] file, LicenseException.Code expected) {
        assertEquals(expected, assertThrows(LicenseException.class, () -> verifier.verify(file, null, START)).code());
    }

    private byte[] signed(byte[] raw) throws Exception {
        Signature signer = Signature.getInstance("Ed25519");
        signer.initSign(keys.getPrivate());
        signer.update(raw);
        return JSON.writeValueAsBytes(Map.of("payload", b64(raw), "signature", b64(signer.sign())));
    }

    private static String b64(byte[] bytes) { return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes); }

    private static Map<String, Object> payload() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema_version", 1);
        result.put("key_id", "test");
        result.put("kind", "commercial-license");
        result.put("license_id", "00000000-0000-4000-8000-000000000001");
        result.put("customer_id", "synthetic");
        result.put("customer_name", "Synthetic");
        result.put("product_id", "icp-diag");
        result.put("binding_type", "UNBOUND");
        result.put("binding_scheme", null);
        result.put("binding_id", null);
        result.put("validity_type", "PERPETUAL");
        result.put("issued_at", "2026-10-20T00:00:00Z");
        result.put("not_before", "2026-10-20T00:00:00Z");
        result.put("expires_at", null);
        return result;
    }
}
