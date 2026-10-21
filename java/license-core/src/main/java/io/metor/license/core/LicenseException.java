package io.metor.license.core;

/** A bounded validation error that never echoes license contents or trust material. */
public final class LicenseException extends IllegalArgumentException {
    private static final long serialVersionUID = 1L;

    public enum Code {
        SIZE_LIMIT, INVALID_JSON, INVALID_SCHEMA, INVALID_BASE64,
        UNKNOWN_KEY, INVALID_SIGNATURE, PRODUCT_MISMATCH,
        BINDING_UNAVAILABLE, BINDING_MISMATCH
    }

    private final Code code;

    public LicenseException(Code code) {
        super("Invalid commercial license: " + code.name());
        this.code = code;
    }

    public Code code() { return code; }
}
