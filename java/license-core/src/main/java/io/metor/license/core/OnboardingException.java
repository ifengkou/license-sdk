package io.metor.license.core;

/** Bounded setup or publishing failure; never contains private installation material. */
public final class OnboardingException extends java.io.IOException {
    private static final long serialVersionUID = 1L;
    public enum Code { CONFIG_INVALID, INSTALLATION_INVALID, STORAGE_FAILED, OUTCOME_UNKNOWN,
        LICENSE_STATE_CONFLICT, CANDIDATE_REJECTED, BINDING_UNAVAILABLE }
    private final Code code;
    public OnboardingException(Code code) { super(code.name()); this.code = code; }
    public Code code() { return code; }
}
