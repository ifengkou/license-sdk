"""Small installation/maintenance CLI; product trust comes from a controlled config."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from license_sdk.commercial import current_file_digest
from license_sdk.installation import (
    OnboardingError,
    absolute_path,
    initialize_installation,
    sync_directory,
    validate_installation,
    write_new,
)
from license_sdk.product import ProductConfig
from license_sdk.recovery import canonical_json, utc_text
from license_sdk.runtime import LicenseRuntime, RuntimeStatus, read_bounded


def status_dict(value: RuntimeStatus | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "state": value.state.value,
        "reasons": [reason.value for reason in value.reasons],
        "mode": value.mode,
        "effectiveNow": utc_text(value.effective_now),
        "fileDigest": value.file_digest,
        "payloadDigest": value.payload_digest,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="license-sdk")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "check", "status", "request", "import"):
        operation = commands.add_parser(command)
        operation.add_argument("--config", required=True, type=Path)
        operation.add_argument("--root", required=True, type=Path)
        if command != "check":
            operation.add_argument("--service")
        if command not in ("init", "check"):
            operation.add_argument("--machine-id", type=Path, default=Path("/etc/machine-id"))
        if command == "request":
            operation.add_argument("--output", type=Path)
        if command == "import":
            operation.add_argument("--file", required=True, type=Path)
            operation.add_argument("--shared-group", type=int, help="numeric GID for 0640 sharing")
    args = parser.parse_args(argv)
    try:
        raw = read_bounded(absolute_path(args.config), 65536)
        product = ProductConfig.from_bytes(raw)
        if args.command == "check":
            value: object = {"installationId": validate_installation(product, args.root)}
        elif args.command == "init":
            if args.service is not None:
                product.select_service(args.service)
            result = initialize_installation(product, args.root)
            value = {
                "actionStatus": result.action_status,
                "installationId": result.installation_id,
            }
        else:
            runtime = LicenseRuntime.from_config(
                raw, root=args.root, service=args.service, machine_id_file=args.machine_id
            )
            if args.command == "status":
                value = status_dict(runtime.status(read_only=True))
                assert isinstance(value, dict)
                value["fileDigest"] = runtime.license_file_digest()
            elif args.command == "request":
                value = runtime.commercial_request()
                if args.output is not None:
                    output = absolute_path(args.output)
                    content = canonical_json(value)
                    try:
                        write_new(output, content)
                    except FileExistsError:
                        if read_bounded(output, 65536) != content:
                            raise OnboardingError(
                                "LICENSE_OUTPUT_EXISTS", "request output already exists"
                            ) from None
                    sync_directory(output.parent)
            else:
                candidate = read_bounded(absolute_path(args.file), 65536)
                expected = current_file_digest(runtime.paths.root / "shared/active.license.json")
                imported = runtime.import_license(
                    candidate, expected, shared_group=args.shared_group
                )
                value = {
                    "actionStatus": imported.action_status,
                    "fileDigest": imported.file_digest,
                    "status": status_dict(imported.status),
                }
                if imported.action_status == "OUTCOME_UNKNOWN":
                    print(json.dumps(value, ensure_ascii=True))
                    return 1
        print(json.dumps(value, ensure_ascii=True))
        return 0
    except (OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, OnboardingError) else "LICENSE_CONFIG_OR_STORAGE_INVALID"
        message = str(exc) if isinstance(exc, ValueError) else "installation file unavailable"
        print(
            json.dumps({"code": code, "actionStatus": "REJECTED", "message": message}),
            file=sys.stdout,
        )
        print(
            f"{code}: check product configuration and installation files; preserve existing data",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
