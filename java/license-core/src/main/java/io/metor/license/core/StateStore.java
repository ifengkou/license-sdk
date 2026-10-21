package io.metor.license.core;

import java.io.IOException;
import java.nio.file.Path;

/** Explicit commit outcomes prevent a failed fsync being reported as a durable commit. */
public interface StateStore {
    enum CommitOutcome { NOT_COMMITTED, COMMITTED, COMMIT_DURABILITY_UNKNOWN }
    @FunctionalInterface interface LockedAction<T> { T run() throws IOException; }
    byte[] read(Path path, int maximumBytes) throws IOException;
    <T> T withLock(Path lockPath, LockedAction<T> action) throws IOException;
    CommitOutcome replace(Path path, byte[] bytes) throws IOException;
}
