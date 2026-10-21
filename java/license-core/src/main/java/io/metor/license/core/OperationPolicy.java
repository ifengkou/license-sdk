package io.metor.license.core;

/** Business-meaning operations; unknown product routes must map to NEW_EXECUTION. */
public final class OperationPolicy {
    public enum Operation { MANAGEMENT, READ_EXISTING, NEW_EXECUTION }
    private OperationPolicy() { }
    public static boolean allows(LicenseRuntime.Snapshot snapshot, Operation operation) {
        if (operation == Operation.MANAGEMENT) return true;
        if (snapshot == null) return false;
        if (snapshot.state() == LicenseRuntime.State.ACTIVE) return true;
        return operation == Operation.READ_EXISTING && snapshot.license() != null
            && snapshot.license().status() != CommercialLicenseVerifier.Status.NOT_YET_VALID
            && (snapshot.state() == LicenseRuntime.State.EXPIRED
                || snapshot.state() == LicenseRuntime.State.CLOCK_ROLLBACK
                || snapshot.state() == LicenseRuntime.State.CLOCK_STATE_INVALID);
    }
}
