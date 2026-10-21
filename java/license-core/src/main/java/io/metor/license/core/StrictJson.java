package io.metor.license.core;

import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Small, bounded protocol parser; no coercion, duplicate fields, BOM or invalid Unicode. */
final class StrictJson {
    record NumberToken(String text) { }

    private final String input;
    private int position;

    private StrictJson(byte[] bytes) {
        try {
            input = StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(bytes)).toString();
        } catch (CharacterCodingException error) {
            throw invalid();
        }
    }

    static Map<String, Object> object(byte[] bytes) {
        StrictJson parser = new StrictJson(bytes);
        Object value = parser.value(0);
        parser.whitespace();
        if (parser.position != parser.input.length() || !(value instanceof Map<?, ?> map)) {
            throw invalid();
        }
        Map<String, Object> result = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : map.entrySet()) {
            result.put((String) entry.getKey(), entry.getValue());
        }
        return result;
    }

    private Object value(int depth) {
        whitespace();
        if (position >= input.length()) { throw invalid(); }
        char next = input.charAt(position);
        if (next == '{' || next == '[') {
            if (depth >= 8) { throw invalid(); }
            position++;
            return next == '{' ? map(depth + 1) : array(depth + 1);
        }
        if (next == '"') { return string(); }
        if (next == 't') { literal("true"); return Boolean.TRUE; }
        if (next == 'f') { literal("false"); return Boolean.FALSE; }
        if (next == 'n') { literal("null"); return null; }
        return number();
    }

    private Map<String, Object> map(int depth) {
        Map<String, Object> result = new LinkedHashMap<>();
        whitespace();
        if (take('}')) { return result; }
        do {
            whitespace();
            if (position >= input.length() || input.charAt(position) != '"') { throw invalid(); }
            String key = string();
            whitespace();
            require(':');
            if (result.containsKey(key)) { throw invalid(); }
            result.put(key, value(depth));
            whitespace();
            if (take('}')) { return result; }
        } while (take(','));
        throw invalid();
    }

    private List<Object> array(int depth) {
        List<Object> result = new ArrayList<>();
        whitespace();
        if (take(']')) { return result; }
        do {
            result.add(value(depth));
            whitespace();
            if (take(']')) { return result; }
        } while (take(','));
        throw invalid();
    }

    private String string() {
        require('"');
        StringBuilder result = new StringBuilder();
        while (position < input.length()) {
            char next = input.charAt(position++);
            if (next == '"') {
                String text = result.toString();
                for (int index = 0; index < text.length(); index++) {
                    char ch = text.charAt(index);
                    if (Character.isHighSurrogate(ch)) {
                        if (++index >= text.length() || !Character.isLowSurrogate(text.charAt(index))) {
                            throw invalid();
                        }
                    } else if (Character.isLowSurrogate(ch)) { throw invalid(); }
                }
                return text;
            }
            if (next < 0x20) { throw invalid(); }
            if (next != '\\') { result.append(next); continue; }
            if (position >= input.length()) { throw invalid(); }
            char escaped = input.charAt(position++);
            switch (escaped) {
                case '"', '\\', '/' -> result.append(escaped);
                case 'b' -> result.append('\b');
                case 'f' -> result.append('\f');
                case 'n' -> result.append('\n');
                case 'r' -> result.append('\r');
                case 't' -> result.append('\t');
                case 'u' -> {
                    if (input.length() - position < 4) { throw invalid(); }
                    int code = 0;
                    for (int count = 0; count < 4; count++) {
                        char digit = input.charAt(position++);
                        int value = digit >= '0' && digit <= '9' ? digit - '0'
                            : digit >= 'a' && digit <= 'f' ? digit - 'a' + 10
                            : digit >= 'A' && digit <= 'F' ? digit - 'A' + 10 : -1;
                        if (value < 0) { throw invalid(); }
                        code = code * 16 + value;
                    }
                    result.append((char) code);
                }
                default -> throw invalid();
            }
        }
        throw invalid();
    }

    private NumberToken number() {
        int start = position;
        take('-');
        if (!take('0')) { digits(); }
        if (take('.')) { digits(); }
        if (take('e') || take('E')) {
            if (!take('+')) { take('-'); }
            digits();
        }
        if (position == start) { throw invalid(); }
        return new NumberToken(input.substring(start, position));
    }

    private void digits() {
        int start = position;
        while (position < input.length() && input.charAt(position) >= '0' && input.charAt(position) <= '9') {
            position++;
        }
        if (start == position) { throw invalid(); }
    }

    private void literal(String text) {
        if (!input.startsWith(text, position)) { throw invalid(); }
        position += text.length();
    }

    private void whitespace() {
        while (position < input.length()) {
            char ch = input.charAt(position);
            if (ch != ' ' && ch != '\t' && ch != '\r' && ch != '\n') { break; }
            position++;
        }
    }

    private boolean take(char expected) {
        if (position < input.length() && input.charAt(position) == expected) { position++; return true; }
        return false;
    }

    private void require(char expected) { if (!take(expected)) { throw invalid(); } }
    private static LicenseException invalid() { return new LicenseException(LicenseException.Code.INVALID_JSON); }
}
