package io.metor.license.core;

/** A bounded recovery refusal, independent of HTTP frameworks and customer input. */
public final class RecoveryException extends RuntimeException {
    private static final long serialVersionUID = 1L;
    public enum Code {
        INVALID("LICENSE_RECOVERY_INVALID", 400),
        WINDOW_INVALID("LICENSE_RECOVERY_WINDOW_INVALID", 400),
        TARGET_CHANGED("LICENSE_RECOVERY_TARGET_CHANGED", 409),
        NOT_REQUIRED("LICENSE_RECOVERY_NOT_REQUIRED", 409),
        STORAGE_FAILED("LICENSE_STORAGE_FAILED", 503),
        OUTCOME_UNKNOWN("LICENSE_STORAGE_OUTCOME_UNKNOWN", 202),
        CONFIG_INVALID("LICENSE_CONFIGURATION_INVALID", 503);
        private final String value;
        private final int status;
        Code(String value, int status) { this.value = value; this.status = status; }
    }
    private final Code reason;
    public RecoveryException(Code reason) { super(reason.value); this.reason = reason; }
    public String code() { return reason.value; }
    public int httpStatus() { return reason.status; }
    public Code reason() { return reason; }
}
