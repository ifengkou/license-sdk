package io.metor.license.core;

import java.io.IOException;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/** JDK-only installation and maintenance commands. The library never exits its host process. */
public final class LicenseCli {
    private LicenseCli() { }
    public static void main(String[] args) { System.exit(run(args, System.out, System.err)); }

    static int run(String[] args, PrintStream output, PrintStream error) {
        if (args.length == 0 || Arrays.asList(args).contains("--help")) {
            output.println("Usage: java -jar license-core-0.7.0.jar <init|status|request|import> --config FILE --root DIR "
                + "[--service ID] [--machine-id FILE] [request: --output FILE] [import: --file FILE]");
            return 0;
        }
        try {
            String command = args[0];
            if (!Set.of("init", "status", "request", "import").contains(command)) throw new IllegalArgumentException();
            Set<String> allowed = switch (command) {
                case "request" -> Set.of("--config", "--root", "--service", "--machine-id", "--output");
                case "import" -> Set.of("--config", "--root", "--service", "--machine-id", "--file");
                default -> Set.of("--config", "--root", "--service", "--machine-id");
            };
            Map<String, String> options = new LinkedHashMap<>();
            for (int i = 1; i < args.length; i += 2) {
                if (!allowed.contains(args[i]) || i + 1 >= args.length || args[i + 1].startsWith("--")
                    || options.putIfAbsent(args[i], args[i + 1]) != null) throw new IllegalArgumentException();
            }
            Path configFile = Path.of(required(options, "--config"));
            Path root = Path.of(required(options, "--root"));
            var files = new OnboardingFiles();
            byte[] config = files.read(configFile, ProductConfig.MAX_BYTES);
            ProductConfig product = ProductConfig.parse(config);
            if (command.equals("init")) {
                if (options.containsKey("--service")) product.selectService(options.get("--service"));
                var result = LicenseInstallation.initialize(product, root);
                output.println(json(Map.of("actionStatus", result.actionStatus(), "installationId", result.installationId().toString())));
                return 0;
            }
            Path machine = Path.of(options.getOrDefault("--machine-id", "/etc/machine-id"));
            try (var runtime = LicenseRuntime.fromConfig(config, root, options.get("--service"), machine)) {
                switch (command) {
                    case "status" -> {
                        var result = new LinkedHashMap<String, Object>(snapshot(runtime.inspect()));
                        result.put("fileDigest", runtime.licenseFileDigest()); output.println(json(result)); return 0;
                    }
                    case "request" -> {
                        byte[] request = json(runtime.commercialRequest()).getBytes(StandardCharsets.UTF_8);
                        if (options.containsKey("--output")) {
                            Path destination = OnboardingFiles.checked(Path.of(options.get("--output")));
                            byte[] existing = files.optional(destination, 32768);
                            if (existing != null && !Arrays.equals(existing, request)) throw new OnboardingException(OnboardingException.Code.LICENSE_STATE_CONFLICT);
                            if (existing == null) { files.writeNew(destination, request); files.sync(destination.getParent()); }
                            else if (!files.confirm(destination, request)) throw new OnboardingException(OnboardingException.Code.LICENSE_STATE_CONFLICT);
                        }
                        output.println(new String(request, StandardCharsets.UTF_8)); return 0;
                    }
                    case "import" -> {
                        byte[] candidate = files.read(Path.of(required(options, "--file")), CommercialLicenseVerifier.MAX_FILE_BYTES);
                        String expected = runtime.licenseFileDigest();
                        var result = runtime.importLicense(candidate, expected);
                        var view = new LinkedHashMap<String, Object>();
                        view.put("actionStatus", result.actionStatus().name()); view.put("fileDigest", result.fileDigest());
                        view.put("status", result.status() == null ? null : snapshot(result.status()));
                        output.println(json(view)); return result.actionStatus() == LicenseRuntime.ImportActionStatus.OUTCOME_UNKNOWN ? 1 : 0;
                    }
                    default -> throw new IllegalArgumentException();
                }
            }
        } catch (OnboardingException failure) {
            String message = switch (failure.code()) {
                case INSTALLATION_INVALID -> "Installation is incomplete, corrupt or ambiguous; preserve it. Adding service seeds requires an explicit installation migration";
                case BINDING_UNAVAILABLE -> "Provide the real host machine-id with --machine-id, or request UNBOUND issuance without a host request";
                default -> "Operation refused; preserve installation data and verify inputs or retry the same operation";
            };
            error.println(json(Map.of("code", failure.code().name(), "message", message)));
        } catch (LicenseException failure) {
            error.println(json(Map.of("code", failure.code().name(), "message", "License or host binding could not be verified")));
        } catch (IOException | UnsupportedOperationException failure) {
            error.println(json(Map.of("code", "STORAGE_FAILED", "message", "Check existing regular paths and permissions; preserve installation data")));
        } catch (IllegalArgumentException failure) {
            error.println(json(Map.of("code", "CONFIG_INVALID", "message", "Check command arguments and the controlled product configuration")));
        }
        return 1;
    }
    private static String required(Map<String, String> options, String name) {
        String value = options.get(name); if (value == null || value.isBlank()) throw new IllegalArgumentException(); return value;
    }
    private static Map<String, Object> snapshot(LicenseRuntime.Snapshot value) {
        var view = new LinkedHashMap<String, Object>();
        view.put("state", value.state().name()); view.put("reasons", value.reasons());
        view.put("fileDigest", value.license() == null ? null : value.license().fileDigest());
        view.put("effectiveNow", value.effectiveNow().toString()); view.put("observedAt", value.observedAt().toString());
        return view;
    }
    static String json(Object value) {
        if (value == null) return "null";
        if (value instanceof String text) {
            StringBuilder quoted = new StringBuilder("\"");
            for (int i = 0; i < text.length(); i++) {
                char c = text.charAt(i);
                switch (c) {
                    case '"' -> quoted.append("\\\""); case '\\' -> quoted.append("\\\\");
                    case '\n' -> quoted.append("\\n"); case '\r' -> quoted.append("\\r"); case '\t' -> quoted.append("\\t");
                    default -> { if (c < 32) quoted.append(String.format("\\u%04x", (int)c)); else quoted.append(c); }
                }
            }
            return quoted.append('"').toString();
        }
        if (value instanceof Boolean || value instanceof Number) return value.toString();
        if (value instanceof Map<?, ?> map) return map.entrySet().stream().map(entry -> json(entry.getKey()) + ":" + json(entry.getValue()))
            .collect(java.util.stream.Collectors.joining(",", "{", "}"));
        if (value instanceof Iterable<?> list) {
            var values = new java.util.ArrayList<String>(); for (Object item : list) values.add(json(item));
            return String.join(",", values).transform(text -> "[" + text + "]");
        }
        throw new IllegalArgumentException("Unsupported JSON output");
    }
}
