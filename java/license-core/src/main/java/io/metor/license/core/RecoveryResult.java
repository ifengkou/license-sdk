package io.metor.license.core;

import java.util.UUID;

/** A durable recovery decision plus the actual local commercial license state. */
public record RecoveryResult(ActionStatus actionStatus, LicenseRuntime.Snapshot status, UUID recoveryId) {
    public enum ActionStatus { APPLIED, ALREADY_APPLIED, OUTCOME_UNKNOWN }
}
