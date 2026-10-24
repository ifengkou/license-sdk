# Public SDK product configuration v1

`license-product.json` is exported by the issuer and packaged as a controlled
product resource. It contains public trust and protocol settings, never issuer
private keys, local issuer paths, installation secrets or runtime mode switches.
The configuration schema version is independent of SDK and license versions.

```json
{
  "schema_version": 1,
  "product_id": "other-product",
  "fingerprint_domain": "other-product/host-fingerprint/v1",
  "binding_domain": "other-product/host-install-code/v1",
  "recovery_domain": "other-product/recovery-target/v1",
  "request_prefix": "OP1",
  "services": [
    {"id": "backend", "recovery_prefix": "OP1B"},
    {"id": "worker", "recovery_prefix": "OP1W"}
  ],
  "trusted_keys": {"vendor-2026": "<canonical Ed25519 SPKI PEM>"}
}
```

The PEM placeholder above is explanatory, not a valid trust input. The issuer
exports its actual startup public-key snapshot. Duplicate fields, unknown or
missing fields, invalid identifiers, repeated services/prefixes, private keys,
wrong key types, unsupported versions and inputs larger than 65536 bytes are
rejected. Domains and code prefixes retain their exact existing protocol syntax.
The `services` array must be nonempty; prefixes are distinct across the request
prefix and all recovery prefixes. A service can be omitted at runtime only when
exactly one is configured.

JSON strings encode PEM line feeds as `\n`; the decoded value must be canonical
Ed25519 SubjectPublicKeyInfo PEM with a final line feed. This PEM allowance is
specific to configuration and does not relax signed license payload validation.
Configuration ordering and JSON whitespace carry no identity semantics.

The product root and host machine-id path are separate deployment inputs. The
application loads the config from controlled resources; customer HTTP uploads
and ordinary runtime environment variables must not replace its trust ring.
Public readability does not imply that untrusted replacement is safe.

## Installation and publication interoperability

Both SDKs explicitly initialize all configured services. They create a UUIDv4,
a random 32-byte installation master key and initial clocks using the existing
clock-state-v1 codec. Initialization never issues a commercial license. Ordinary
runtime construction does not initialize or repair installation files.

For a new installation the unpublished directory is `.identity-stage/`, holding
`installation.json` with `initialized: false` and its matching `service.key`.
After syncing both files and the directory it is published as `identity/`.
Valid drafts resume with the same UUID/key; partial or ambiguous material is
preserved and rejected. Service clocks are synced before the initialized marker
is completed. A completed installation is validated without recreating missing
keys or clocks, rewriting original bytes or refreshing the clock watermark.

New operation locks use matching POSIX record-lock ranges in both languages:

| Operation | Path under root | Exclusive range |
| --- | --- | --- |
| Initialize/validate installation | `.init.lock` | offset 0, length 1 |
| Publish commercial license | `shared/license-import.lock` | offset 0, length 1 |

Acquisition is nonblocking; contention reports `LICENSE_STATE_CONFLICT` without
entering the operation. An in-process mutex also protects each lock path.
Cross-language exclusion must be tested with actual processes on each supported
platform. This contract does not change the legacy per-service clock locks or
permit two runtimes to manage the same service state concurrently.

Commercial import compares the full existing file SHA-256 under its operation
lock, validates the new candidate against actual UTC, writes a same-directory
temporary file, syncs it, atomically replaces the target, then syncs the parent.
It reports publication durability uncertainty rather than claiming the old file
is unchanged. Same-byte retries confirm durability and return the real current
license state; they do not renew the license or reset clock state. Invalid signed
content is not promoted to a successful import by matching an existing file.
