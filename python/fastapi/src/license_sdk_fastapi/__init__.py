"""Explicit FastAPI dependency adapter; products choose entry points and error shape."""

from collections.abc import Callable

from fastapi import HTTPException
from license_sdk.guard import LicenseDeniedError, LicenseGuard


def require_license(
    guard: LicenseGuard,
    on_denied: Callable[[LicenseDeniedError], Exception] | None = None,
) -> Callable[[], None]:
    """Call after host authentication; no route or HTTP-verb based exemptions."""

    def dependency() -> None:
        try:
            guard.require_valid()
        except LicenseDeniedError as denied:
            if on_denied is not None:
                raise on_denied(denied) from denied
            raise HTTPException(status_code=403, detail=denied.state) from denied

    return dependency
