# Python license SDK

Two separately built distributions: `metor-license-core` (framework-independent) and
`metor-license-fastapi` (explicit request dependency adapter). Workspace linkage is internal
SDK development only; published adapter metadata pins `metor-license-core==0.4.0`.

Phase 1 implements product profiles, host/install binding digests, manual request/recovery
code codecs, packaged public vectors, an admission protocol and a thin FastAPI adapter.
Phase 2 adds strict commercial payload validation, raw-byte Ed25519 verification and
TERM/PERPETUAL × HOST_INSTALL/UNBOUND evaluation. Phase 3 adds strict installation/clock observation and durable monotonic clock protection.
It does not yet implement execution admission or recovery application. The adapter requires an injected guard; no permissive default
runtime is provided. It must run after host authentication; hosts retain operation semantics.

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
mapping. `status(force_checkpoint=True)` provides the persistence seam for future execution
admission; it is not itself a task guard. The product lifecycle must invoke a checkpoint at
least every 30 seconds, independently of status requests. Missing/corrupt identity or clock
never initializes replacement state, and integrity or commit failures remain sticky until
controlled recovery in a later phase. Development mode never touches local installation files.

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
`status(read_only=True)` provides the same non-writing diagnostics. `check(Operation)`
implements request-level eligibility; products must supply explicit route semantics.
Unknown operations require ACTIVE. These methods do not confer task continuation rights.
