import io.metor.license.core.IdentityDerivation;
import io.metor.license.core.ManualCodeCodec;
import java.io.IOException;
import java.util.HexFormat;
import java.util.UUID;

/** Standalone public synthetic identity example. This is not a license verifier. */
public final class StandaloneCodec {
    private StandaloneCodec() {}

    public static void main(String[] args) throws IOException {
        IdentityDerivation derivation = new IdentityDerivation("icp-diag/host-fingerprint/v1",
            "icp-diag/host-install-code/v1", "icp-diag/recovery-target/v1");
        byte[] binding = derivation.binding("00112233445566778899aabbccddeeff",
            UUID.fromString("00112233-4455-4677-8899-aabbccddeeff"));
        ManualCodeCodec codec = new ManualCodeCodec("IC1");
        String expected = "IC1-DSAQT9-6S5EG7-C4GTSJ-N72BP7-D6XW11-EZ1YYG";
        if (!expected.equals(codec.encode(binding))
            || !HexFormat.of().formatHex(binding).equals(codec.decodeHex(expected))) {
            throw new IllegalStateException("Public vector mismatch");
        }
        try (var vectors = ManualCodeCodec.class.getResourceAsStream(
            "/io/metor/license/protocol/request-code-v1-vectors.json")) {
            if (vectors == null || vectors.readAllBytes().length == 0) {
                throw new IllegalStateException("Packaged vectors missing");
            }
        }
        System.out.println("Standalone JAR codec and packaged-vector smoke passed");
    }
}
