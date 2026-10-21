package io.metor.license.spring;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.nimbusds.jose.JOSEException;
import com.nimbusds.jose.JOSEObjectType;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.JWSObject;
import com.nimbusds.jose.Payload;
import com.nimbusds.jose.crypto.MACSigner;
import com.nimbusds.jose.crypto.MACVerifier;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.Base64;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** Dedicated bounded CONTROL HS256 codec. Never uses a user's JWT decoder or key selection. */
public final class ControlProofCodec {
    private static final Set<String> FIELDS = Set.of("iss", "aud", "installation_id", "purpose", "scope",
        "iat", "exp", "jti", "http_method", "http_path", "body_sha256");
    private final String issuer;
    private final String audience;
    private final ObjectMapper mapper = new ObjectMapper();

    public ControlProofCodec(String issuer, String audience) {
        if (issuer == null || audience == null || !issuer.matches("[A-Za-z0-9/._-]{1,128}")
            || !audience.matches("[A-Za-z0-9/._-]{1,128}")) throw invalid();
        this.issuer = issuer;
        this.audience = audience;
    }

    /** Accepts an already purpose-derived 256-bit key. Caller controls issuer/audience and scope. */
    public String sign(byte[] key, UUID installation, String scope, String method, String path,
        byte[] body, Instant now, UUID jti) {
        requireKey(key);
        canonicalUuid(installation.toString()); canonicalUuid(jti.toString());
        request(scope, method, path, body);
        if (now.getEpochSecond() < 0 || now.getEpochSecond() > Long.MAX_VALUE - 65) throw invalid();
        Map<String, Object> claims = new LinkedHashMap<>();
        claims.put("iss", issuer); claims.put("aud", audience); claims.put("installation_id", installation.toString());
        claims.put("purpose", "CONTROL"); claims.put("scope", scope); claims.put("iat", now.getEpochSecond());
        claims.put("exp", Math.addExact(now.getEpochSecond(), 60)); claims.put("jti", jti.toString());
        claims.put("http_method", method); claims.put("http_path", path); claims.put("body_sha256", digest(body));
        try {
            JWSObject object = new JWSObject(new JWSHeader.Builder(JWSAlgorithm.HS256).type(JOSEObjectType.JWT).build(),
                new Payload(mapper.writeValueAsBytes(claims)));
            object.sign(new MACSigner(key));
            return object.serialize();
        } catch (java.io.IOException | JOSEException error) { throw invalid(); }
    }

    /** Verifies actual request coordinates and raw body. A short-window identical retry is allowed. */
    public void verify(String token, byte[] key, UUID installation, String scope, String method, String path,
        byte[] body, Instant now) {
        requireKey(key);
        request(scope, method, path, body);
        try {
            if (token == null || token.length() > 8192) throw invalid();
            String[] parts = token.split("\\.", -1);
            if (parts.length != 3) throw invalid();
            byte[] headerBytes = decode(parts[0]); byte[] payloadBytes = decode(parts[1]);
            if (decode(parts[2]).length != 32) throw invalid();
            if (headerBytes.length > 512) throw invalid();
            JsonNode header = object(headerBytes);
            fields(header, Set.of("alg", "typ"));
            if (!"HS256".equals(text(header, "alg")) || !"JWT".equals(text(header, "typ"))) throw invalid();
            JsonNode payload = object(payloadBytes); fields(payload, FIELDS);
            if (!issuer.equals(text(payload, "iss")) || !audience.equals(text(payload, "aud"))
                || !installation.toString().equals(text(payload, "installation_id"))
                || !"CONTROL".equals(text(payload, "purpose")) || !scope.equals(text(payload, "scope"))
                || !method.equals(text(payload, "http_method")) || !path.equals(text(payload, "http_path"))
                || !digest(body).equals(text(payload, "body_sha256"))) throw invalid();
            canonicalUuid(text(payload, "installation_id")); canonicalUuid(text(payload, "jti"));
            long iat = number(payload, "iat"), exp = number(payload, "exp"), current = now.getEpochSecond();
            if (iat < 0 || exp <= iat || exp > Long.MAX_VALUE - 5 || exp - iat > 60 || iat > current + 5 || current >= exp + 5) throw invalid();
            JWSObject signed = JWSObject.parse(token);
            if (!signed.verify(new MACVerifier(key))) throw invalid();
        } catch (java.io.IOException | java.text.ParseException | JOSEException | ArithmeticException error) { throw invalid(); }
    }

    public static String digest(byte[] bytes) {
        try { return java.util.HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)); }
        catch (GeneralSecurityException error) { throw new IllegalStateException("SHA-256 unavailable", error); }
    }

    private JsonNode object(byte[] bytes) throws java.io.IOException {
        if (bytes.length == 0 || bytes.length > 4096 || bytes.length >= 3 && bytes[0] == (byte) 0xef
            && bytes[1] == (byte) 0xbb && bytes[2] == (byte) 0xbf) throw invalid();
        StandardCharsets.UTF_8.newDecoder().decode(java.nio.ByteBuffer.wrap(bytes));
        try (JsonParser parser = mapper.getFactory().createParser(bytes)) {
            parser.enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION);
            JsonNode node = mapper.readTree(parser);
            if (node == null || !node.isObject() || parser.nextToken() != null) throw invalid();
            return node;
        }
    }
    private static void fields(JsonNode node, Set<String> expected) {
        Set<String> actual = new HashSet<>(); node.fieldNames().forEachRemaining(actual::add);
        if (!actual.equals(expected)) throw invalid();
    }
    private static String text(JsonNode node, String name) {
        String value = node.path(name).textValue(); if (value == null) throw invalid(); return value;
    }
    private static long number(JsonNode node, String name) {
        JsonNode value = node.path(name);
        if (!value.isIntegralNumber() || !value.canConvertToLong()) throw invalid(); return value.longValue();
    }
    private static void canonicalUuid(String value) {
        UUID parsed = UUID.fromString(value);
        if (!parsed.toString().equals(value) || parsed.version() != 4 || parsed.variant() != 2) throw invalid();
    }
    private static byte[] decode(String text) {
        if (!text.matches("[A-Za-z0-9_-]+")) throw invalid();
        byte[] raw = Base64.getUrlDecoder().decode(text);
        if (!Base64.getUrlEncoder().withoutPadding().encodeToString(raw).equals(text)) throw invalid(); return raw;
    }
    private static void request(String scope, String method, String path, byte[] body) {
        if (scope == null || !scope.matches("[A-Z][A-Z_]{0,63}") || method == null || !method.matches("[A-Z]{1,16}")
            || path == null || !path.startsWith("/") || path.length() > 2048 || !path.matches("[\\x21-\\x7e]+")
            || path.contains("?") || path.contains("#") || body == null || body.length > 65536) throw invalid();
    }
    private static void requireKey(byte[] key) { if (key == null || key.length != 32) throw invalid(); }
    private static IllegalArgumentException invalid() { return new IllegalArgumentException("Invalid internal control proof"); }
}
