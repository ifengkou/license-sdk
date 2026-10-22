import io.metor.license.core.LicenseRuntime;
import io.metor.license.core.RecoveryException;
import io.metor.license.core.TimeSource;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyFactory;
import java.security.spec.X509EncodedKeySpec;
import java.time.Instant;
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
        var config = new LicenseRuntime.Config("icp-diag", service, "icp-diag/host-fingerprint/v1",
            "icp-diag/host-install-code/v1", "icp-diag/recovery-target/v1", root.resolve("identity/installation.json"),
            root.resolve("identity/service.key"), root.resolve("shared/active.license.json"),
            root.resolve("state/" + service + "/clock.json"), root.resolve("machine-id"), args[4]);
        Instant now = Instant.parse(args[5]);
        var runtime = new LicenseRuntime(config, Map.of("recovery-test", key), new TimeSource() {
            public Instant utcNow() { return now; }
            public long monotonicNanos() { return 0; }
        });
        try {
            if (command.equals("request")) System.out.println(new String(runtime.getOrCreateRecoveryRequest().toJson(), java.nio.charset.StandardCharsets.UTF_8));
            else if (command.equals("apply")) {
                var result = runtime.applyRecovery(Files.readAllBytes(Path.of(args[6])));
                System.out.println("{\"actionStatus\":\"" + result.actionStatus() + "\",\"state\":\"" + result.status().state()
                    + "\",\"recoveryId\":\"" + result.recoveryId() + "\"}");
            } else if (command.equals("status")) System.out.println("{\"state\":\"" + runtime.inspect().state() + "\"}");
            else throw new IllegalArgumentException("Unknown probe command");
        } catch (RecoveryException error) {
            System.out.println("{\"error\":\"" + error.code() + "\",\"httpStatus\":" + error.httpStatus() + "}");
            System.exit(2);
        }
    }
}
