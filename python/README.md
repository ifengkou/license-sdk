# Python license SDK

Two separately built distributions: `metor-license-core` (framework-independent) and
`metor-license-fastapi` (explicit request dependency adapter). Workspace linkage is internal
SDK development only; published adapter metadata pins `metor-license-core==0.2.0`.

Phase 1 implements product profiles, host/install binding digests, manual request/recovery
code codecs, packaged public vectors, an admission protocol and a thin FastAPI adapter.
Phase 2 adds strict commercial payload validation, raw-byte Ed25519 verification and
TERM/PERPETUAL × HOST_INSTALL/UNBOUND evaluation. It does not yet implement runtime
admission rules, persistent clock state or recovery application. The adapter requires an injected guard; no permissive default
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
