package io.metor.license.core;

import java.nio.charset.StandardCharsets;
import java.util.HexFormat;
import java.util.Locale;
import java.util.Objects;

/** Purpose-separated 20-byte manual code with CRC16-XMODEM. CRC is not authentication. */
public final class ManualCodeCodec {
    private static final String ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
    private final String prefix;

    public ManualCodeCodec(String prefix) {
        Objects.requireNonNull(prefix, "prefix");
        if (!prefix.matches("[A-Z][A-Z0-9]{1,7}")) {
            throw new IllegalArgumentException("Prefix must be 2 to 8 uppercase ASCII letters/digits");
        }
        this.prefix = prefix;
    }

    public String prefix() { return prefix; }

    public String encodeHex(String targetId) {
        if (targetId == null || !targetId.matches("[0-9a-f]{40}")) {
            throw new IllegalArgumentException("Target ID must be 40 lowercase hex characters");
        }
        return encode(HexFormat.of().parseHex(targetId));
    }

    public String encode(byte[] target) {
        Objects.requireNonNull(target, "target");
        if (target.length != 20) throw new IllegalArgumentException("Target must be 20 bytes");
        byte[] raw = new byte[22];
        System.arraycopy(target, 0, raw, 0, 20);
        int crc = checksum(target);
        raw[20] = (byte) (crc >>> 8);
        raw[21] = (byte) crc;
        StringBuilder result = new StringBuilder(prefix);
        for (int i = 0; i < 36; i++) {
            if (i % 6 == 0) result.append('-');
            int value = 0;
            for (int bit = 0; bit < 5; bit++) {
                int position = i * 5 + bit;
                value <<= 1;
                if (position < 176) value |= (raw[position / 8] >>> (7 - position % 8)) & 1;
            }
            result.append(ALPHABET.charAt(value));
        }
        return result.toString();
    }

    public String decodeHex(String input) { return HexFormat.of().formatHex(decode(input)); }

    public byte[] decode(String input) {
        Objects.requireNonNull(input, "input");
        if (input.length() > 256) throw new CodeException(CodeException.Reason.LENGTH);
        StringBuilder compact = new StringBuilder();
        for (int i = 0; i < input.length(); i++) {
            char c = input.charAt(i);
            // Reject Unicode before case normalization (e.g. long-s must not become S).
            if (c > 126 || (c < 32 && !(c >= '\t' && c <= '\r'))) {
                throw new CodeException(CodeException.Reason.CHARACTER);
            }
            if (c != '-' && c != ' ' && !(c >= '\t' && c <= '\r')) compact.append(c);
        }
        String normalized = compact.toString().toUpperCase(Locale.ROOT);
        if (!normalized.startsWith(prefix)) throw new CodeException(CodeException.Reason.PREFIX);
        String body = normalized.substring(prefix.length());
        if (body.length() != 36) throw new CodeException(CodeException.Reason.LENGTH);
        byte[] raw = new byte[22];
        for (int i = 0; i < body.length(); i++) {
            char c = body.charAt(i);
            if (c == 'O') c = '0';
            else if (c == 'I' || c == 'L') c = '1';
            int value = ALPHABET.indexOf(c);
            if (value < 0) throw new CodeException(CodeException.Reason.CHARACTER);
            for (int bit = 0; bit < 5; bit++) {
                int position = i * 5 + bit;
                int binary = (value >>> (4 - bit)) & 1;
                if (position < 176) raw[position / 8] |= (byte) (binary << (7 - position % 8));
                else if (binary != 0) throw new CodeException(CodeException.Reason.PADDING);
            }
        }
        byte[] target = java.util.Arrays.copyOf(raw, 20);
        int expected = ((raw[20] & 255) << 8) | (raw[21] & 255);
        if (checksum(target) != expected) throw new CodeException(CodeException.Reason.CHECKSUM);
        return target;
    }

    private int checksum(byte[] target) {
        int crc = 0;
        for (byte value : prefix.getBytes(StandardCharsets.US_ASCII)) crc = update(crc, value);
        for (byte value : target) crc = update(crc, value);
        return crc;
    }

    private static int update(int crc, byte value) {
        crc ^= (value & 255) << 8;
        for (int bit = 0; bit < 8; bit++) {
            crc = ((crc & 0x8000) != 0 ? (crc << 1) ^ 0x1021 : crc << 1) & 0xffff;
        }
        return crc;
    }
}
