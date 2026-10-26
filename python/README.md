# Python license SDK

Download wheels or a complete platform bundle from [GitHub Releases](https://github.com/ifengkou/license-sdk/releases). See [release and offline installation](../docs/releasing.md) for CPython 3.12 platform selection, virtual environments, hash verification, and the CI release procedure. Install dependencies at product build time; application startup does not require network access.

## Five-step quick start (0.7.1)

Linux/macOS, local filesystem, one process/Runtime per service. Install the prebuilt
`metor-license-core==0.7.1` distribution (and `metor-license-fastapi==0.7.1` only
when using its FastAPI dependency). Offline installations also need the target
platform dependency wheels. Do this at build/install time, never application startup.

1. Download **license-product.json** from your unlocked Issuer's SDK configuration
   page. Include it in your product's controlled package resources. It contains
   public keys, but determines trust: never accept replacements through customer
   HTTP uploads or customer runtime data.
2. Run SDK initialization once, as the account that owns the installation directory:

   ```bash
   license-sdk init --config ./license-product.json --root ./license-data
   license-sdk status --config ./license-product.json --root ./license-data --service backend
   ```

   Replace `backend` with the service displayed by your Issuer; omit it only for
   single-service products. Expected: `INITIALIZED`, then `UNLICENSED`.
   SDK creates identity, random master key and all service clocks. Repeated init
   returns `ALREADY_INITIALIZED` without changing those bytes. Damaged installs
   must be investigated or recovered, never deleted/reinitialized as a workaround.
   To validate an existing installation without initializing or changing it, run
   `license-sdk check --config ./license-product.json --root ./license-data`.
   It checks every configured service and needs no service choice or machine-id.
3. Create one Runtime from the resource, start it for the process lifecycle and
   close it on shutdown. For a package named `your_product` with the JSON included:

   ```python
   from importlib.resources import files
   from pathlib import Path
   from license_sdk.runtime import LicenseRuntime

   raw = files("your_product").joinpath("license-product.json").read_bytes()
   runtime = LicenseRuntime.from_config(raw, root=Path("./license-data"), service="backend")
   runtime.start()
   try:
       print(runtime.status(read_only=True).state)
       # Run your application, injecting this same Runtime.
   finally:
       runtime.close()
   ```

   `start()` maintains checkpoint every 30 seconds even without requests. Do not
   also run your own checkpoint loop. Old explicit constructors and manual
   checkpoint scheduling remain supported.
4. Export the actual installation's request, sign it in Issuer, then safely import:

   ```bash
   license-sdk request --config ./license-product.json --root ./license-data \
     --service backend --output ./request.json
   # Upload request.json to Issuer; download customer.license.json.
   license-sdk import --config ./license-product.json --root ./license-data \
     --service backend --file ./customer.license.json
   ```

   The default host identifier is Linux `/etc/machine-id`. When using a real host
   mount or macOS test environment, add `--machine-id /absolute/real-machine-id`
   to request/import and pass `machine_id_file=Path(...)` to the factory. No fake
   machine identifier is generated; UNBOUND signing needs none.
   Import returns `APPLIED` with the real current state, normally `ACTIVE`.
   `OUTCOME_UNKNOWN` means preserve the files and retry the same signed bytes.
5. After your normal authentication, before a protected operation has side effects,
   call `runtime.require_valid()`. Map `LicenseDeniedError` into your application's
   error response. Admin status, request/import and recovery must remain accessible
   through their own authentication when business operations are restricted.

[examples/quick_start.py](examples/quick_start.py) demonstrates Runtime startup,
status and the business guard using the installed SDK. Place the public
`license-product.json` beside the example and initialize the data directory with
CLI `init` before running it. Use CLI `request` and `import` separately for
activation; ordinary example startup does none of these operations.
Run `python examples/quick_start.py --help` for its Runtime options. No installation
file format knowledge or application-specific deployment project is needed.

`runtime.commercial_request()` returns the exact issuer request object.
`runtime.license_file_digest()` supplies the current full raw-file SHA-256 or `None`
when absent. Pass that snapshot to `runtime.import_license(raw, expected_file_digest)`;
a concurrent change returns `LICENSE_STATE_CONFLICT`. File read failure is never
reported as absence. `ImportResult.status` can be `None` for an unconfirmed outcome.
Same-file retries confirm durability without extending validity or claiming ACTIVE.
HTTP adapters enforce their own admin authentication, upload size/CSRF/no-store and
error mapping; the SDK handles verification and publication.

## Shared publication and read-only installation checks

Import defaults to a private `0600` commercial file. If separate service accounts
must read the same license, select the deployment's numeric group explicitly:

```python
result = runtime.import_license(raw, expected_file_digest, shared_group=12345)
```

The equivalent CLI option is `license-sdk import ... --shared-group 12345`.
Replace `12345` with the group provided by your deployment. The publisher must
have permission to assign that group; the SDK does not create OS accounts or
groups. The GID must be an integer from 0 to 2147483647. This local deployment
option is not part of `license-product.json` or signed license data.

The SDK writes a private temporary file, sets and confirms its group and `0640`
permissions, syncs it and only then atomically replaces the commercial file under
the existing import lock. Attribute failures before replacement preserve the old
file. An unconfirmed directory sync after replacement returns `OUTCOME_UNKNOWN`.
Do not add a chmod/chgrp step after import. Identity keys, clocks and recovery
files are unaffected. Same-byte retries confirm the requested attributes; if
needed, the SDK republishes the same bytes atomically and returns
`ALREADY_APPLIED`. This can confirm an expired license without extending it or
claiming it is active.

For backup or upgrade tooling, use the public read-only validator:

```python
from license_sdk.installation import validate_installation
from license_sdk.product import ProductConfig

installation_id = validate_installation(ProductConfig.from_bytes(config_bytes), license_root)
```

It validates the completed identity, 32-byte master key and all configured service
clocks using the SDK's existing HMAC codec. It returns the installation ID or
raises `OnboardingError` with code `LICENSE_INSTALLATION_INVALID`. It does not
create a Runtime, lock, directory or checkpoint, and works with a read-only mount.
It does not read the commercial license, machine-id or current time: a missing
or expired license and a clock high watermark ahead of UTC do not invalidate an
otherwise complete installation. A malformed or incorrectly authenticated clock
does fail. For an incomplete installation, use the explicit init workflow after
investigation; check never repairs it.

`license-sdk check --config ... --root ...` calls the same validator and returns
`{"installationId":"..."}` on success. Stop all installation writers or validate
a consistent snapshot first: a read-only check does not make a live multi-file
snapshot atomic. Archive paths, permissions and restore policy remain the
deployment tool's responsibility.


[中文接入与交付指南](../docs/integration-cn.md)：按开发接入、构建发布、首次部署和授权开通分阶段操作。

Two separately built distributions: `metor-license-core` (framework-independent) and
`metor-license-fastapi` (explicit request dependency adapter). Workspace linkage is internal
SDK development only; published adapter metadata pins `metor-license-core==0.7.1`.

Phase 1 implements product profiles, host/install binding digests, manual request/recovery
code codecs, packaged public vectors, a local guard protocol and a thin FastAPI adapter.
Phase 2 adds strict commercial payload validation, raw-byte Ed25519 verification and
TERM/PERPETUAL × HOST_INSTALL/UNBOUND evaluation. Phase 3 adds strict installation/clock observation and durable monotonic clock protection.
Phase 6 adds single-service recovery in the same runtime. Version 0.7.0 introduced
the configuration, initialization, import and lifecycle entry points; 0.7.1 adds
shared publication and read-only installation validation. Existing 0.6.0 and
0.7.0 artifacts remain unchanged. The adapter requires an injected guard; no permissive default
runtime is provided. It must run after host authentication; hosts choose protected entry points.

Run `uv sync --all-packages` once, then `uv lock --check`, `uv run --locked ruff check .`,
`uv run --locked ruff format --check .`, `uv run --locked mypy src fastapi/src`,
`uv run --locked pytest`, `uv build --all-packages`.

Public protocol vectors are packaged in the core wheel at
`license_sdk/data/request-code-v1-vectors.json`, checked byte-for-byte against SDK protocol authority.

`license_sdk.verify_license(file_bytes, product_id=..., trusted_keys=..., binding_id=...,
effective_now=...)` returns an immutable verified payload with SHA-256 payload/file digests
and ACTIVE, NOT_YET_VALID or EXPIRED status. Trust is an explicitly assembled key-ID to
SPKI PEM/DER bytes mapping. `effective_now` must be timezone-aware; no local clocks,
identity files or terminal state are read. UNBOUND accepts a missing binding; HOST_INSTALL
requires the locally recomputed lowercase binding digest.

`validate_commercial_payload(payload_bytes, product_id)` is an unsigned schema validator
for vendor-side payload construction. Its result is not evidence of signature validity.
`parse_json_object` and `parse_utc_timestamp` share the same strict bounded wire rules with
request handling. All protocol errors carry a `LicenseValidationError.reason` category.

Phase 3 public runtime lives in `license_sdk.runtime`. Construct `LicenseRuntime` with an
explicit `ProductProfile`, service, `RuntimePaths`, build mode and immutable trusted key
mapping. `checkpoint()` explicitly persists time state; `require_valid()` only observes
state and throws `LicenseDeniedError(state, reasons)` unless ACTIVE or DEVELOPMENT. Use `start()`/`close()` for lifecycle maintenance, or invoke a checkpoint every
30 seconds yourself; choose one scheduler. Missing/corrupt identity or clock
never initializes replacement state, and integrity or commit failures remain sticky until
a signed recovery is durably applied by the same runtime. Development mode never touches local installation files.

`RuntimeStatus` preserves ordered reasons, primary state, operating mode, current effective
time, and verified commercial payload/digests. Each status observes current license bytes
and current clock integrity; it does not retain an ACTIVE result across missing/tampered files.
UNBOUND never reads machine-id. HOST_INSTALL recomputes the actual local binding.

`encode_clock` and `decode_clock` implement the v1 raw-byte HMAC envelope with signed 64-bit
nonnegative counters and 8 KiB/4 KiB envelope/payload bounds. Public synthetic clock vectors
are packaged as `license_sdk/data/clock-state-v1-vectors.json`; they are test material only.
The core uses POSIX file locks, no-follow regular file reads and atomic same-directory replace
with file/directory fsync. Production is Linux; tests also run on macOS. It does not provide
Windows locking fallbacks or claim cross-service atomic transactions.

Phase 4 adds `license_sdk.proofs.ControlProof` with fixed HS256, explicitly selected
product domains and dedicated installation credentials. The runtime credential seam
`control_proof(config=...)` requires the product to supply its explicit issuer/audience/domain. CONTROL tokens bind issuer,
audience, installation UUID, scope, HTTP method, exact path and raw body SHA-256.
They have a maximum 60-second lifetime and 5-second transport skew, using real UTC.
They are unrelated to user JWTs, commercial Ed25519 signatures and TASK proofs.

`LicenseRuntime.preflight(raw)` validates a candidate against real UTC, independently
reports current clock restrictions, and never publishes, checkpoints or creates lock
files. Initial activation works without an existing commercial file.
`status(read_only=True)` provides the same non-writing diagnostics. The `LicenseGuard`
Protocol exposes only `require_valid()`, and `LicenseDeniedError` preserves the state
string and reasons tuple from one observation. Display modes are NORMAL, RESTRICTED
and DEVELOPMENT; there is no SDK read-only exemption. FastAPI products can use
`require_license(guard, on_denied=...)` after their own authentication.

Rollback tolerance is 86,400 seconds: exactly one day is tolerated, the effective high watermark never decreases, and commercial validity stays precisely [not_before, expires_at). This does not extend CONTROL token windows.

Customer startup uses prebuilt images/installations with the SDK, cryptography, PyJWT and all runtime dependencies preinstalled. No pip/uv or external downloads run at startup. A standalone SDK wheel is not self-contained: a separate offline integration bundle must include the target Linux/Python platform wheelhouse and install with no-index/offline.


`get_or_create_recovery_request()` returns a frozen `RecoveryRequest` with `to_dict()` and
`to_bytes()` for the exact application file and RC1 code. An applicable persisted HMAC
challenge is reused after repeated calls or restart. `apply_recovery(bytes)` returns
`RecoveryResult(action_status, status, recovery_id)`; failures have `RecoveryError.code`
and `.http_status`. APPLIED and ALREADY_APPLIED return current commercial status.
OUTCOME_UNKNOWN remains restricted until the same file is retried and durability confirmed.
Requests and applies share the runtime mutex and clock file lock; a committed recovery
refreshes that exact process instance. Each service uses one process/runtime and its own
state directory. Recovery never changes the commercial license, and requires a separate
vendor signature with an exact at-most-seven-day wall-clock window. Product administrators
must correct host UTC first. A one-day rollback tolerance does not extend this window.
