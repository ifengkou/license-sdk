# Python license SDK

Two separately built distributions: `metor-license-core` (framework-independent) and
`metor-license-fastapi` (explicit request dependency adapter). Workspace linkage is internal
SDK development only; published adapter metadata pins `metor-license-core==0.6.0`.

Phase 1 implements product profiles, host/install binding digests, manual request/recovery
code codecs, packaged public vectors, a local guard protocol and a thin FastAPI adapter.
Phase 2 adds strict commercial payload validation, raw-byte Ed25519 verification and
TERM/PERPETUAL × HOST_INSTALL/UNBOUND evaluation. Phase 3 adds strict installation/clock observation and durable monotonic clock protection.
Phase 6 adds single-service recovery in the same runtime. It does not implement workflow admission. Version 0.6.0 is built locally for unified integration; existing 0.4.0 wheels remain unchanged. The adapter requires an injected guard; no permissive default
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
state and throws `LicenseDeniedError(state, reasons)` unless ACTIVE or DEVELOPMENT. The product lifecycle must invoke a checkpoint at
least every 30 seconds, independently of status requests. Missing/corrupt identity or clock
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
