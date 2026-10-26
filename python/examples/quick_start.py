"""Run with installed SDK after CLI init; place the product config beside this file."""

import argparse
from pathlib import Path

from license_sdk.guard import LicenseDeniedError
from license_sdk.runtime import LicenseRuntime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--service")
    parser.add_argument("--machine-id", type=Path, default=Path("/etc/machine-id"))
    args = parser.parse_args()
    # Product builders package this resource with the application, outside writable runtime data.
    raw = Path(__file__).with_name("license-product.json").read_bytes()
    runtime = LicenseRuntime.from_config(
        raw, root=args.root, service=args.service, machine_id_file=args.machine_id
    )
    runtime.start()
    try:
        print(runtime.status(read_only=True).state.value)
        try:
            runtime.require_valid()
        except LicenseDeniedError as exc:
            print("Business refused before side effects:", exc.state)
        else:
            print("Protected business operation may run")
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
