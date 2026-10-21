package io.metor.license.core;

import java.time.Instant;
import java.util.UUID;

/** Authenticated on-disk clock fields; epoch changes are reserved for signed recovery. */
public record ClockState(String productId, UUID installationId, String service, long epoch,
    long sequence, Instant highWatermark, UUID lastRecoveryId) { }
