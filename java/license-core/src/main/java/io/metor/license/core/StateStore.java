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
    /** Recovery-only CAS; expected null means the caller observed a missing regular file. */
    default CommitOutcome replaceRecovery(Path path, byte[] expected, byte[] replacement) throws IOException {
        throw new IOException("Recovery commit unsupported");
    }
    /** Confirm existing exact bytes are durable without changing their value. */
    default CommitOutcome confirmDurability(Path path, byte[] expected) throws IOException {
        throw new IOException("Recovery durability confirmation unsupported");
    }
    default void deleteRecoveryChallenge(Path path) throws IOException { throw new IOException("Challenge removal unsupported"); }
}
