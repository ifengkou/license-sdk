# Python license SDK

Two separately built distributions: `metor-license-core` (framework-independent) and
`metor-license-fastapi` (explicit request dependency adapter). Workspace linkage is internal
SDK development only; published adapter metadata pins `metor-license-core==0.1.0`.

Phase 1 implements product profiles, host/install binding digests, manual request/recovery
code codecs, packaged public vectors, an admission protocol and a thin FastAPI adapter.
It does not yet implement signature verification, runtime admission rules, persistent clock
state or recovery application. The adapter requires an injected guard; no permissive default
runtime is provided. It must run after host authentication; hosts retain operation semantics.

Run `uv sync --all-packages` once, then `uv lock --check`, `uv run --locked ruff check .`,
`uv run --locked ruff format --check .`, `uv run --locked mypy src fastapi/src`,
`uv run --locked pytest`, `uv build --all-packages`.

Public protocol vectors are packaged in the core wheel at
`license_sdk/data/request-code-v1-vectors.json`, checked byte-for-byte against SDK protocol authority.
