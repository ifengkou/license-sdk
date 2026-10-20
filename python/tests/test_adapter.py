import pytest
from license_sdk_fastapi import require_operation

from fastapi import HTTPException
from license_sdk.contracts import Admission, Operation


class DenyGuard:
    def __init__(self) -> None:
        self.observed: list[Operation] = []

    def check(self, operation: Operation) -> Admission:
        self.observed.append(operation)
        return Admission(False, "LICENSE_REQUIRED")


def test_explicit_adapter_preserves_unknown_operation() -> None:
    guard = DenyGuard()
    with pytest.raises(HTTPException) as caught:
        require_operation(guard, Operation.UNKNOWN)()
    assert caught.value.status_code == 403
    assert guard.observed == [Operation.UNKNOWN]


def test_host_error_mapping() -> None:
    with pytest.raises(ValueError, match="LICENSE_REQUIRED"):
        require_operation(
            DenyGuard(), Operation.COMPUTE, lambda result: ValueError(result.reason)
        )()
