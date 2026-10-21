package io.metor.license.core;

import java.time.Instant;

/** Both clocks must refer to the same process; monotonic values are never persisted. */
public interface TimeSource {
    Instant utcNow();
    long monotonicNanos();

    static TimeSource system() {
        return new TimeSource() {
            public Instant utcNow() { return Instant.now(); }
            public long monotonicNanos() { return System.nanoTime(); }
        };
    }
}
