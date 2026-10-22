package io.metor.license.core;

import java.util.List;
import java.util.Objects;

/** One observed refusal, carried to the host's error response without rechecking. */
public final class LicenseDeniedException extends RuntimeException {
    private static final long serialVersionUID = 1L;
    private final LicenseRuntime.State state;
    @SuppressWarnings("serial") // List.copyOf below contains serializable strings only.
    private final List<String> reasons;

    public LicenseDeniedException(LicenseRuntime.State state, List<String> reasons) {
        super(Objects.requireNonNull(state, "state").name());
        this.state = state;
        this.reasons = List.copyOf(reasons);
    }

    public LicenseRuntime.State state() { return state; }
    public List<String> reasons() { return reasons; }
}
