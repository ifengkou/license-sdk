# Minimal host verification

Java: `new CommercialLicenseVerifier(productId, trustedPublicKeys).verify(bytes, localBindingId, now)`.
Python: `verify_license(bytes, product_id=..., trusted_keys=..., binding_id=..., effective_now=...)`.
Results contain immutable payload, payload/file digests, and ACTIVE/NOT_YET_VALID/EXPIRED.
Invalid files raise bounded validation errors. Trust must come from fixed host assembly.

HOST_INSTALL binding must be recomputed from actual local identity, never taken directly
from incoming requests. Java provides a lazy binding provider, unused by UNBOUND.
The low-level file verifier does not include persistent clock anomaly detection or business gates.

Run `license-issuer/.venv/bin/python license-sdk/tools/verify_phase2_cross_language.py`
from the workspace root for actual CLI issuance and Java/Python verification; see script help.
Temporary synthetic keys never enter release artifacts. Consumers use fixed release packages,
not business repository or issuer source imports.

For runtime-protected entry points use `LicenseRuntime.requireValid()` (Java) or `require_valid()` (Python), catch the typed refusal once in the host error adapter, and schedule `checkpoint()` every 30 seconds independently of requests. The FastAPI adapter is `require_license(guard)`. These entry checks never force a checkpoint or select read-only business modes.

For complete Java/Python Runtime construction and protected-entry examples, use the [Chinese integration guide](../docs/integration-cn.md). Its examples share one long-lived runtime per service; low-level verification alone does not provide persistent rollback protection.
