package io.metor.license.core;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.channels.OverlappingFileLockException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.locks.ReentrantLock;

/** Shared POSIX setup/publish operations. Lock order is publish lock, then Runtime lock. */
class OnboardingFiles {
    private static final ConcurrentHashMap<Path, ReentrantLock> LOCKS = new ConcurrentHashMap<>();
    static final java.nio.file.attribute.FileAttribute<?> PRIVATE_FILE =
        PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------"));
    static final java.nio.file.attribute.FileAttribute<?> PRIVATE_DIRECTORY =
        PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------"));
    @FunctionalInterface interface Action<T> { T run() throws IOException; }

    static Path checked(Path path) throws IOException {
        Path safe = path.toAbsolutePath().normalize();
        Path component = safe.getRoot();
        for (Path name : safe) {
            component = component.resolve(name);
            if (Files.isSymbolicLink(component)) throw new IOException("Symbolic license paths are forbidden");
        }
        return safe;
    }
    void directory(Path path) throws IOException {
        Path safe = checked(path);
        if (Files.exists(safe, LinkOption.NOFOLLOW_LINKS)) {
            if (!Files.isDirectory(safe, LinkOption.NOFOLLOW_LINKS)) throw new IOException("Expected license directory");
            return;
        }
        directory(safe.getParent());
        try { Files.createDirectory(safe, PRIVATE_DIRECTORY); sync(safe.getParent()); }
        catch (java.nio.file.FileAlreadyExistsException raced) {
            if (!Files.isDirectory(safe, LinkOption.NOFOLLOW_LINKS) || Files.isSymbolicLink(safe)) throw raced;
        }
    }
    byte[] read(Path path, int maximum) throws IOException { return new FileStateStore().read(path, maximum); }
    byte[] optional(Path path, int maximum) throws IOException {
        try { return read(path, maximum); } catch (NoSuchFileException absent) { return null; }
    }
    String rawDigest(Path path) throws IOException {
        Path safe = checked(path);
        if (!Files.isRegularFile(safe, LinkOption.NOFOLLOW_LINKS)) {
            if (!Files.exists(safe, LinkOption.NOFOLLOW_LINKS)) return null;
            throw new IOException("Expected regular commercial file");
        }
        try {
            var hash = java.security.MessageDigest.getInstance("SHA-256");
            try (var input = Files.newInputStream(safe, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS)) {
                byte[] buffer = new byte[65536]; int count;
                while ((count = input.read(buffer)) != -1) hash.update(buffer, 0, count);
            }
            return HexFormat.of().formatHex(hash.digest());
        } catch (java.security.GeneralSecurityException error) { throw new IllegalStateException(error); }
    }
    <T> T withLock(Path path, Action<T> action) throws IOException {
        Path safe = checked(path);
        ReentrantLock local = LOCKS.computeIfAbsent(safe, ignored -> new ReentrantLock());
        if (!local.tryLock()) throw new OnboardingException(OnboardingException.Code.LICENSE_STATE_CONFLICT);
        try {
            if (!Files.exists(safe, LinkOption.NOFOLLOW_LINKS)) {
                try { Files.createFile(safe, PRIVATE_FILE); }
                catch (java.nio.file.FileAlreadyExistsException raced) { /* Rechecked before opening. */ }
            }
            if (!Files.isRegularFile(safe, LinkOption.NOFOLLOW_LINKS)) throw new IOException("Invalid license lock");
            try (FileChannel channel = FileChannel.open(safe, StandardOpenOption.WRITE, LinkOption.NOFOLLOW_LINKS)) {
                try (var lock = channel.tryLock(0, 1, false)) {
                    if (lock == null) throw new OnboardingException(OnboardingException.Code.LICENSE_STATE_CONFLICT);
                    return action.run();
                } catch (OverlappingFileLockException busy) {
                    throw new OnboardingException(OnboardingException.Code.LICENSE_STATE_CONFLICT);
                }
            }
        } finally { local.unlock(); }
    }
    void sync(Path path) throws IOException {
        try (var channel = FileChannel.open(checked(path), StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS)) { channel.force(true); }
    }
    void writeNew(Path path, byte[] bytes) throws IOException {
        try (var channel = FileChannel.open(checked(path), java.util.Set.of(StandardOpenOption.CREATE_NEW,
            StandardOpenOption.WRITE, LinkOption.NOFOLLOW_LINKS), PRIVATE_FILE)) {
            ByteBuffer buffer = ByteBuffer.wrap(bytes);
            while (buffer.hasRemaining()) channel.write(buffer);
            channel.force(true);
        }
    }
    void move(Path source, Path destination, boolean replace) throws IOException {
        if (replace) Files.move(checked(source), checked(destination), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        else Files.move(checked(source), checked(destination), StandardCopyOption.ATOMIC_MOVE);
    }
    StateStore.CommitOutcome publish(Path path, byte[] bytes) throws IOException {
        Path safe = checked(path);
        Path temp = Files.createTempFile(safe.getParent(), ".license-", ".tmp", PRIVATE_FILE);
        boolean published = false;
        try {
            writeExisting(temp, bytes);
            move(temp, safe, true);
            published = true;
            sync(safe.getParent());
            return StateStore.CommitOutcome.COMMITTED;
        } catch (IOException | UnsupportedOperationException error) {
            return published ? StateStore.CommitOutcome.COMMIT_DURABILITY_UNKNOWN : StateStore.CommitOutcome.NOT_COMMITTED;
        } finally {
            try { Files.deleteIfExists(temp); }
            catch (IOException cleanup) { System.getLogger(OnboardingFiles.class.getName())
                .log(System.Logger.Level.WARNING, "Could not remove license publish temporary file"); }
        }
    }
    void writeExisting(Path path, byte[] bytes) throws IOException {
        try (var channel = FileChannel.open(checked(path), StandardOpenOption.WRITE, LinkOption.NOFOLLOW_LINKS)) {
            ByteBuffer buffer = ByteBuffer.wrap(bytes);
            while (buffer.hasRemaining()) channel.write(buffer);
            channel.force(true);
        }
    }
    boolean confirm(Path path, byte[] expected) throws IOException {
        if (!Arrays.equals(read(path, 65536), expected)) return false;
        sync(path); sync(path.getParent()); return true;
    }
    static String digest(byte[] bytes) {
        try { return HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(bytes)); }
        catch (java.security.GeneralSecurityException error) { throw new IllegalStateException(error); }
    }
}
