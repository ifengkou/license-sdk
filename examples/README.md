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
