import pytest
from license_sdk_fastapi import require_license

from fastapi import HTTPException
from license_sdk.guard import LicenseDeniedError


class DenyGuard:
    def __init__(self) -> None:
        self.calls = 0

    def require_valid(self) -> None:
        self.calls += 1
        raise LicenseDeniedError("UNLICENSED", ("UNLICENSED",))


def test_explicit_adapter_checks_once_and_preserves_refusal() -> None:
    guard = DenyGuard()
    with pytest.raises(HTTPException) as caught:
        require_license(guard)()
    assert caught.value.status_code == 403
    assert caught.value.detail == "UNLICENSED"
    assert guard.calls == 1


def test_host_error_mapping() -> None:
    with pytest.raises(ValueError, match="UNLICENSED"):
        require_license(DenyGuard(), lambda denied: ValueError(denied.reasons[0]))()


def test_explicit_adapter_accepts_a_valid_guard() -> None:
    class ValidGuard:
        def require_valid(self) -> None:
            pass

    require_license(ValidGuard())()
