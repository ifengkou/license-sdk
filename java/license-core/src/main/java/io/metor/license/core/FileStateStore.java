package io.metor.license.core;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.Arrays;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.locks.ReentrantLock;

/** Local regular files, process plus OS locks, same-directory atomic replacement and directory fsync. */
public final class FileStateStore implements StateStore {
    private static final ConcurrentHashMap<Path, ReentrantLock> LOCKS = new ConcurrentHashMap<>();

    @Override
    public byte[] read(Path path, int maximumBytes) throws IOException {
        Path safe = checked(path);
        if (!Files.isRegularFile(safe, LinkOption.NOFOLLOW_LINKS)) {
            if (!Files.exists(safe, LinkOption.NOFOLLOW_LINKS)) { throw new java.nio.file.NoSuchFileException("License input missing"); }
            throw new IOException("License input must be a regular file");
        }
        try (var input = Files.newInputStream(safe, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS)) {
            byte[] result = input.readNBytes(maximumBytes + 1);
            if (result.length > maximumBytes) { throw new IOException("License input exceeds size limit"); }
            return result;
        }
    }

    @Override
    public <T> T withLock(Path path, LockedAction<T> action) throws IOException {
        Path safe = checked(path);
        ReentrantLock local = LOCKS.computeIfAbsent(safe, ignored -> new ReentrantLock());
        local.lock();
        try {
            if (!Files.exists(safe, LinkOption.NOFOLLOW_LINKS)) {
                try { Files.createFile(safe, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------"))); }
                catch (java.nio.file.FileAlreadyExistsException ignored) { /* A cooperating process won. */ }
            }
            if (!Files.isRegularFile(safe, LinkOption.NOFOLLOW_LINKS)) { throw new IOException("Invalid state lock"); }
            try (FileChannel channel = FileChannel.open(safe, StandardOpenOption.WRITE, LinkOption.NOFOLLOW_LINKS);
                FileLock lock = channel.lock()) {
                if (!lock.isValid()) { throw new IOException("State lock unavailable"); }
                return action.run();
            }
        } finally { local.unlock(); }
    }

    @Override
    public CommitOutcome replace(Path path, byte[] bytes) throws IOException {
        Path safe = checked(path);
        // Runtime may replace existing state, never initialize or recreate missing state.
        if (!Files.isRegularFile(safe, LinkOption.NOFOLLOW_LINKS)) { return CommitOutcome.NOT_COMMITTED; }
        return publish(safe, bytes);
    }

    @Override
    public CommitOutcome replaceRecovery(Path path, byte[] expected, byte[] replacement) throws IOException {
        Path safe = checked(path);
        byte[] actual;
        try { actual = read(safe, 8192); }
        catch (java.nio.file.NoSuchFileException missing) { actual = null; }
        if (!Arrays.equals(actual, expected)) return CommitOutcome.NOT_COMMITTED;
        return publish(safe, replacement);
    }

    @Override
    public CommitOutcome confirmDurability(Path path, byte[] expected) throws IOException {
        Path safe = checked(path);
        if (!Arrays.equals(read(safe, 8192), expected)) return CommitOutcome.NOT_COMMITTED;
        try (FileChannel file = FileChannel.open(safe, StandardOpenOption.WRITE, LinkOption.NOFOLLOW_LINKS)) {
            file.force(true);
            try (FileChannel parent = FileChannel.open(safe.getParent(), StandardOpenOption.READ)) { parent.force(true); }
            return CommitOutcome.COMMITTED;
        } catch (IOException | UnsupportedOperationException error) { return CommitOutcome.COMMIT_DURABILITY_UNKNOWN; }
    }

    @Override
    public void deleteRecoveryChallenge(Path path) throws IOException {
        Path safe = checked(path);
        if (Files.exists(safe, LinkOption.NOFOLLOW_LINKS) && !Files.isRegularFile(safe, LinkOption.NOFOLLOW_LINKS)) {
            throw new IOException("Invalid recovery challenge path");
        }
        if (Files.deleteIfExists(safe)) {
            try (FileChannel parent = FileChannel.open(safe.getParent(), StandardOpenOption.READ)) { parent.force(true); }
        }
    }

    private static CommitOutcome publish(Path safe, byte[] bytes) throws IOException {
        Path temporary = Files.createTempFile(safe.getParent(), ".clock-", ".tmp",
            PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
        boolean published = false;
        try {
            try (FileChannel output = FileChannel.open(temporary, StandardOpenOption.WRITE, LinkOption.NOFOLLOW_LINKS)) {
                ByteBuffer buffer = ByteBuffer.wrap(bytes);
                while (buffer.hasRemaining()) { output.write(buffer); }
                output.force(true);
            }
            Files.move(temporary, safe, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            published = true;
            try (FileChannel parent = FileChannel.open(safe.getParent(), StandardOpenOption.READ)) { parent.force(true); }
            return CommitOutcome.COMMITTED;
        } catch (IOException | UnsupportedOperationException error) {
            return published ? CommitOutcome.COMMIT_DURABILITY_UNKNOWN : CommitOutcome.NOT_COMMITTED;
        } finally {
            try { Files.deleteIfExists(temporary); }
            catch (IOException cleanupError) { System.getLogger(FileStateStore.class.getName())
                .log(System.Logger.Level.WARNING, "Could not remove license state temporary file"); }
        }
    }

    private static Path checked(Path path) throws IOException {
        Path safe = path.toAbsolutePath().normalize();
        Path component = safe.getRoot();
        for (Path name : safe) {
            component = component.resolve(name);
            if (Files.isSymbolicLink(component)) { throw new IOException("Symbolic license paths are forbidden"); }
        }
        return safe;
    }
}
