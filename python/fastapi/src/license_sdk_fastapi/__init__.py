"""Explicit FastAPI dependency adapter; products decide operation and error shape."""

from collections.abc import Callable

from fastapi import HTTPException
from license_sdk.contracts import Admission, AdmissionGuard, Operation


def require_operation(
    guard: AdmissionGuard,
    operation: Operation,
    on_denied: Callable[[Admission], Exception] | None = None,
) -> Callable[[], None]:
    """Call after host authentication; no route or HTTP-verb based exemptions."""

    def dependency() -> None:
        admission = guard.check(operation)
        if not admission.allowed:
            if on_denied is not None:
                raise on_denied(admission)
            raise HTTPException(status_code=403, detail=admission.reason)

    return dependency
