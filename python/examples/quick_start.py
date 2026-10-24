"""Run with installed SDK; place the downloaded config beside this product entry point."""

import argparse
import json
from pathlib import Path

from license_sdk.guard import LicenseDeniedError
from license_sdk.installation import initialize_installation
from license_sdk.product import ProductConfig
from license_sdk.runtime import LicenseRuntime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--service")
    parser.add_argument("--machine-id", type=Path, default=Path("/etc/machine-id"))
    parser.add_argument("--license", type=Path, help="Issuer's original signed file")
    args = parser.parse_args()
    # Product builders package this resource with the application, outside writable runtime data.
    raw = Path(__file__).with_name("license-product.json").read_bytes()
    initialized = initialize_installation(ProductConfig.from_bytes(raw), args.root)
    print(initialized.action_status)
    runtime = LicenseRuntime.from_config(
        raw, root=args.root, service=args.service, machine_id_file=args.machine_id
    )
    runtime.start()
    try:
        if args.license:
            result = runtime.import_license(
                args.license.read_bytes(), runtime.license_file_digest()
            )
            print(result.action_status)
            if result.action_status == "OUTCOME_UNKNOWN":
                raise RuntimeError("preserve files and retry the same signed file")
        print(runtime.status(read_only=True).state.value)
        try:
            runtime.require_valid()
        except LicenseDeniedError as exc:
            print("Business refused before side effects:", exc.state)
        else:
            print("Protected business operation may run")
        # Export with SDK; this object can be uploaded to Issuer without manual JSON assembly.
        if not args.license:
            print(json.dumps(runtime.commercial_request()))
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
