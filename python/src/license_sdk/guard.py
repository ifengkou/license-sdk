"""One local validation contract; products choose the protected entry points."""

from typing import Protocol


class LicenseDeniedError(RuntimeError):
    """One observed refusal, preserved when the host maps its error response."""

    def __init__(self, state: str, reasons: tuple[str, ...]) -> None:
        self.state = state
        self.reasons = tuple(reasons)
        super().__init__(state)


class LicenseGuard(Protocol):
    def require_valid(self) -> None: ...
