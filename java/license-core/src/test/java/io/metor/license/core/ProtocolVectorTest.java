package io.metor.license.core;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.io.InputStream;
import java.util.HexFormat;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class ProtocolVectorTest {
    private static final HexFormat HEX = HexFormat.of();
    private static final IdentityDerivation ICP = new IdentityDerivation(
        "icp-diag/host-fingerprint/v1", "icp-diag/host-install-code/v1", "icp-diag/recovery-target/v1");
    private static final ManualCodeCodec REQUEST = new ManualCodeCodec("IC1");

    @Test
    void packagedBindingVectorsMatchAllIntermediateBytes() throws IOException {
        for (JsonNode vector : vectors().get("binding_vectors")) {
            String machine = vector.get("machine_id").asText();
            UUID installation = UUID.fromString(vector.get("installation_id").asText());
            assertEquals(vector.get("host_fingerprint").asText(), HEX.formatHex(ICP.hostFingerprint(machine)));
            byte[] binding = ICP.binding(machine, installation);
            assertEquals(vector.get("binding_id").asText(), HEX.formatHex(binding));
            assertEquals(vector.get("request_code").asText(), REQUEST.encode(binding));
            assertArrayEquals(binding, REQUEST.decode(vector.get("request_code").asText()));
        }
    }

    @Test
    void packagedPositiveAndNegativeManualInputsAreAcceptedOrClassified() throws IOException {
        for (JsonNode vector : vectors().get("manual_positive_inputs")) {
            assertEquals(vector.get("expected_binding_id").asText(),
                REQUEST.decodeHex(vector.get("input").asText()));
        }
        for (JsonNode vector : vectors().get("manual_negative_inputs")) {
            CodeException error = assertThrows(CodeException.class,
                () -> REQUEST.decode(vector.get("input").asText()));
            assertEquals(vector.get("expected_error").asText(), error.reason().name());
        }
    }

    @Test
    void recoveryVectorsMatchServiceBoundTargetsAndCodes() throws IOException {
        for (JsonNode vector : vectors().get("recovery_vectors")) {
            String service = vector.get("service").asText();
            ManualCodeCodec recovery = new ManualCodeCodec(service.equals("metor") ? "RC1J" : "RC1A");
            byte[] target = ICP.recoveryTarget(UUID.fromString(vector.get("installation_id").asText()),
                service, UUID.fromString(vector.get("nonce").asText()),
                HEX.parseHex(vector.get("state_identity").asText()));
            assertEquals(vector.get("target_id").asText(), HEX.formatHex(target));
            assertEquals(vector.get("recovery_code").asText(), recovery.encode(target));
            assertArrayEquals(target, recovery.decode(vector.get("recovery_code").asText()));
            assertEquals(CodeException.Reason.PREFIX, assertThrows(CodeException.class,
                () -> REQUEST.decode(vector.get("recovery_code").asText())).reason());
        }
    }

    @Test
    void unicodeIsRejectedBeforeCaseFoldingAndAsciiSeparatorsAreAllowed() {
        String code = REQUEST.encodeHex("6e557d24d92ba076121accaa712ec769bbc085df");
        for (String input : new String[] {code.replace('S', '\u017f'), code + "\u00a0", code + "\u2003",
            code.replace('-', '\u2010'), code.replace('I', '\uff29'), code + "\u0000"}) {
            assertEquals(CodeException.Reason.CHARACTER,
                assertThrows(CodeException.class, () -> REQUEST.decode(input)).reason());
        }
        assertEquals(REQUEST.decodeHex(code), REQUEST.decodeHex("\t\n\r\f\u000b " + code + "\r\n"));
        assertEquals(CodeException.Reason.PREFIX, assertThrows(CodeException.class,
            () -> REQUEST.decode(code.replaceFirst("IC1", "1C1"))).reason());
        assertEquals(CodeException.Reason.PREFIX, assertThrows(CodeException.class,
            () -> REQUEST.decode(code.replaceFirst("IC1", "ICl"))).reason());
    }

    @Test
    void crcSeparatesPurposesEvenIfAttackerReplacesVisiblePrefix() {
        ManualCodeCodec javaRecovery = new ManualCodeCodec("RC1J");
        ManualCodeCodec aiRecovery = new ManualCodeCodec("RC1A");
        String request = REQUEST.encode(new byte[20]);
        String recovery = javaRecovery.encode(new byte[20]);
        assertEquals(CodeException.Reason.CHECKSUM, assertThrows(CodeException.class,
            () -> javaRecovery.decode(request.replaceFirst("IC1", "RC1J"))).reason());
        assertEquals(CodeException.Reason.PREFIX, assertThrows(CodeException.class,
            () -> aiRecovery.decode(recovery)).reason());
        assertEquals(CodeException.Reason.CHECKSUM, assertThrows(CodeException.class,
            () -> aiRecovery.decode(recovery.replaceFirst("RC1J", "RC1A"))).reason());
    }

    @Test
    void secondProductHasIndependentDomainAndPrefixWithoutChangingCore() {
        IdentityDerivation other = new IdentityDerivation("other/host-fingerprint/v1",
            "other/host-install-code/v1", "other/recovery-target/v1");
        String machine = "00112233445566778899aabbccddeeff";
        UUID install = UUID.fromString("00112233-4455-4677-8899-aabbccddeeff");
        byte[] binding = other.binding(machine, install);
        assertNotEquals(HEX.formatHex(ICP.binding(machine, install)), HEX.formatHex(binding));
        ManualCodeCodec otherCodes = new ManualCodeCodec("OT1");
        assertArrayEquals(binding, otherCodes.decode(otherCodes.encode(binding)));
        assertEquals(CodeException.Reason.PREFIX, assertThrows(CodeException.class,
            () -> REQUEST.decode(otherCodes.encode(binding))).reason());
    }

    @Test
    void machineIdentityRejectsWhitespaceUppercaseAllZeroAndMalformedUuid() {
        String machine = "00112233445566778899aabbccddeeff";
        assertArrayEquals(ICP.hostFingerprint(machine), ICP.hostFingerprint(machine + "\n"));
        for (String input : new String[] {" " + machine, machine + " ", machine + "\r\n",
            machine + "\n\n", machine.toUpperCase(java.util.Locale.ROOT), "0".repeat(32), "bad"}) {
            assertThrows(IllegalArgumentException.class, () -> ICP.hostFingerprint(input));
        }
        assertThrows(IllegalArgumentException.class,
            () -> ICP.binding(machine, UUID.fromString("00112233-4455-1677-8899-aabbccddeeff")));
        assertThrows(IllegalArgumentException.class, () -> new ManualCodeCodec("ic1"));
        assertThrows(IllegalArgumentException.class, () -> REQUEST.encode(new byte[19]));
        assertThrows(IllegalArgumentException.class, () -> REQUEST.encodeHex("0".repeat(39)));
        assertThrows(IllegalArgumentException.class, () -> new IdentityDerivation("", "x", "y"));
        assertEquals(CodeException.Reason.LENGTH, assertThrows(CodeException.class,
            () -> REQUEST.decode(" ".repeat(257))).reason());
    }

    private static JsonNode vectors() throws IOException {
        try (InputStream input = ProtocolVectorTest.class.getResourceAsStream(
            "/io/metor/license/protocol/request-code-v1-vectors.json")) {
            if (input == null) throw new IOException("Packaged public vectors missing");
            return new ObjectMapper().readTree(input);
        }
    }
}
