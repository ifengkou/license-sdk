import io.metor.license.core.LicenseDeniedException;
import io.metor.license.core.LicenseRuntime;
import java.nio.file.Path;

/** Product resource contains only public configuration. Initialization is an explicit CLI step. */
public final class LicenseQuickStart {
    public static void main(String[] args) throws Exception {
        if (args.length < 1 || args.length > 3) {
            throw new IllegalArgumentException("Usage: LicenseQuickStart ROOT [SERVICE] [HOST_MACHINE_ID]");
        }
        byte[] publicConfig;
        try (var resource = LicenseQuickStart.class.getResourceAsStream("/license-product.json")) {
            if (resource == null) throw new IllegalStateException("Bundle license-product.json as a product resource");
            publicConfig = resource.readNBytes(65537);
        }
        String service = args.length > 1 ? args[1] : null;
        Path machine = args.length > 2 ? Path.of(args[2]) : Path.of("/etc/machine-id");
        try (var runtime = LicenseRuntime.fromConfig(publicConfig, Path.of(args[0]), service, machine).start()) {
            System.out.println("License state: " + runtime.inspect().state());
            try {
                // Apply the product's account authorization before this guard, then do business work.
                runtime.requireValid();
                System.out.println("Protected operation allowed");
            } catch (LicenseDeniedException denied) {
                System.out.println("Protected operation refused: " + denied.state());
            }
        }
    }
}
