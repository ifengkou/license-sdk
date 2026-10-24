import io.metor.license.core.LicenseRuntime;
import io.metor.license.core.RecoveryException;
import io.metor.license.core.TimeSource;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyFactory;
import java.security.spec.X509EncodedKeySpec;
import java.time.Instant;
import java.util.Arrays;
import java.util.Map;

/** Synthetic conformance driver; not packaged in the SDK or customer product. */
public final class RecoveryProbe {
    private RecoveryProbe() { }
    public static void main(String[] args) throws Exception {
        String command = args[0];
        Path root = Path.of(args[1]);
        var key = KeyFactory.getInstance("Ed25519").generatePublic(
            new X509EncodedKeySpec(Files.readAllBytes(Path.of(args[2]))));
        String service = args[3];
        Path clock = root.resolve("state/" + service + "/clock.json");
        var config = new LicenseRuntime.Config(args[6], service, args[7], args[8], args[9],
            root.resolve("identity/installation.json"), root.resolve("identity/service.key"),
            root.resolve("shared/active.license.json"), clock, root.resolve("machine-id"), args[4]);
        Instant now = Instant.parse(args[5]);
        var runtime = new LicenseRuntime(config, Map.of("recovery-test", key), new TimeSource() {
            public Instant utcNow() { return now; }
            public long monotonicNanos() { return 0; }
        });
        try {
            if (command.equals("request")) {
                System.out.println(new String(runtime.getOrCreateRecoveryRequest().toJson(), StandardCharsets.UTF_8));
            } else if (command.equals("apply")) {
                var result = runtime.applyRecovery(Files.readAllBytes(Path.of(args[10])));
                System.out.println("{\"actionStatus\":\"" + result.actionStatus() + "\",\"state\":\"" + result.status().state()
                    + "\",\"recoveryId\":\"" + result.recoveryId() + "\"}");
            } else if (command.equals("roundtrip")) {
                // The issuer already signed this stable challenge; exercise the running instance throughout.
                var request = runtime.getOrCreateRecoveryRequest();
                byte[] signed = Files.readAllBytes(Path.of(args[10]));
                var first = runtime.applyRecovery(signed);
                var refreshed = runtime.inspect();
                byte[] after = Files.readAllBytes(clock);
                var repeated = runtime.applyRecovery(signed);
                if (!Arrays.equals(after, Files.readAllBytes(clock))) {
                    throw new AssertionError("Repeated recovery changed the clock");
                }
                System.out.println("{\"targetId\":\"" + request.targetId() + "\",\"actionStatus\":\"" + first.actionStatus()
                    + "\",\"state\":\"" + first.status().state() + "\",\"refreshedState\":\"" + refreshed.state()
                    + "\",\"repeatStatus\":\"" + repeated.actionStatus() + "\"}");
            } else if (command.equals("status")) {
                System.out.println("{\"state\":\"" + runtime.inspect().state() + "\"}");
            } else throw new IllegalArgumentException("Unknown probe command");
        } catch (RecoveryException error) {
            System.out.println("{\"error\":\"" + error.code() + "\",\"httpStatus\":" + error.httpStatus() + "}");
            System.exit(2);
        }
    }
}
