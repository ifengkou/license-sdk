package io.metor.license.core;

/** A manual-code validation error without echoing customer input. */
public final class CodeException extends IllegalArgumentException {
    private static final long serialVersionUID = 1L;

    public enum Reason { PREFIX, LENGTH, CHARACTER, PADDING, CHECKSUM }

    private final Reason reason;

    public CodeException(Reason reason) {
        super("Invalid manual code: " + reason.name());
        this.reason = reason;
    }

    public Reason reason() { return reason; }
}
