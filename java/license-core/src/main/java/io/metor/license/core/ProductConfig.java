package io.metor.license.core;

import java.security.GeneralSecurityException;
import java.security.KeyFactory;
import java.security.PublicKey;
import java.security.interfaces.EdECPublicKey;
import java.security.spec.X509EncodedKeySpec;
import java.util.Base64;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Immutable public trust configuration, loaded from a controlled product resource. */
public final class ProductConfig {
    public static final int MAX_BYTES = 65536;
    private final String productId;
    private final String fingerprintDomain;
    private final String bindingDomain;
    private final String recoveryDomain;
    private final String requestPrefix;
    private final Map<String, String> services;
    private final Map<String, PublicKey> trustedKeys;

    private ProductConfig(Map<String, Object> data) {
        ClockStateCodec.fields(data, Set.of("schema_version", "product_id", "fingerprint_domain",
            "binding_domain", "recovery_domain", "request_prefix", "services", "trusted_keys"));
        if (!(data.get("schema_version") instanceof StrictJson.NumberToken version)
            || !version.text().equals("1")) throw invalid();
        productId = ClockStateCodec.text(data, "product_id");
        if (!productId.matches("[A-Za-z0-9._-]{1,64}")) throw invalid();
        fingerprintDomain = ClockStateCodec.text(data, "fingerprint_domain");
        bindingDomain = ClockStateCodec.text(data, "binding_domain");
        recoveryDomain = ClockStateCodec.text(data, "recovery_domain");
        new IdentityDerivation(fingerprintDomain, bindingDomain, recoveryDomain);
        requestPrefix = ClockStateCodec.text(data, "request_prefix");
        new ManualCodeCodec(requestPrefix);
        var prefixes = new HashSet<String>();
        prefixes.add(requestPrefix);
        var configuredServices = new LinkedHashMap<String, String>();
        if (!(data.get("services") instanceof List<?> entries) || entries.isEmpty()) throw invalid();
        for (Object entry : entries) {
            if (!(entry instanceof Map<?, ?> service) || !service.keySet().equals(Set.of("id", "recovery_prefix"))
                || !(service.get("id") instanceof String id) || !id.matches("[a-z][a-z0-9-]{0,31}")
                || !(service.get("recovery_prefix") instanceof String prefix)) throw invalid();
            new ManualCodeCodec(prefix);
            if (configuredServices.putIfAbsent(id, prefix) != null || !prefixes.add(prefix)) throw invalid();
        }
        services = Collections.unmodifiableMap(configuredServices);
        var keys = new LinkedHashMap<String, PublicKey>();
        if (!(data.get("trusted_keys") instanceof Map<?, ?> trust) || trust.isEmpty()) throw invalid();
        for (var entry : trust.entrySet()) {
            if (!(entry.getKey() instanceof String id) || !id.matches("[A-Za-z0-9._-]{1,64}")
                || !(entry.getValue() instanceof String pem) || pem.length() > 4096) throw invalid();
            keys.put(id, publicKey(pem));
        }
        trustedKeys = Collections.unmodifiableMap(keys);
    }

    public static ProductConfig parse(byte[] raw) {
        if (raw == null || raw.length < 1 || raw.length > MAX_BYTES) throw invalid();
        try { return new ProductConfig(StrictJson.object(raw.clone())); }
        catch (IllegalArgumentException error) { throw invalid(); }
    }

    private static PublicKey publicKey(String pem) {
        String begin = "-----BEGIN PUBLIC KEY-----\n";
        String end = "\n-----END PUBLIC KEY-----\n";
        if (!pem.startsWith(begin) || !pem.endsWith(end)) throw invalid();
        String body = pem.substring(begin.length(), pem.length() - end.length());
        if (!body.matches("[A-Za-z0-9+/=\\n]+")) throw invalid();
        try {
            byte[] der = Base64.getDecoder().decode(body.replace("\n", ""));
            PublicKey key = KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(der));
            if (!(key instanceof EdECPublicKey ed) || !ed.getParams().getName().equals("Ed25519")) throw invalid();
            String canonical = begin + Base64.getMimeEncoder(64, new byte[]{'\n'}).encodeToString(key.getEncoded()) + end;
            if (!canonical.equals(pem)) throw invalid();
            return key;
        } catch (GeneralSecurityException | IllegalArgumentException error) { throw invalid(); }
    }

    public String productId() { return productId; }
    public String fingerprintDomain() { return fingerprintDomain; }
    public String bindingDomain() { return bindingDomain; }
    public String recoveryDomain() { return recoveryDomain; }
    public String requestPrefix() { return requestPrefix; }
    public Map<String, String> services() { return services; }
    public Map<String, PublicKey> trustedKeys() { return trustedKeys; }

    public String selectService(String service) {
        if (service == null && services.size() == 1) return services.keySet().iterator().next();
        if (service == null || !services.containsKey(service)) {
            throw new IllegalArgumentException("Select a configured service explicitly for multi-service products");
        }
        return service;
    }
    private static IllegalArgumentException invalid() { return new IllegalArgumentException("Invalid public product configuration"); }
}
