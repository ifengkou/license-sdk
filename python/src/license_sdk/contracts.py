"""Runtime integration contracts; validation and persistence arrive in later phases."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class Operation(StrEnum):
    SESSION_OR_MANAGEMENT = "SESSION_OR_MANAGEMENT"
    READ_EXISTING = "READ_EXISTING"
    MODIFY = "MODIFY"
    COMPUTE = "COMPUTE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Admission:
    allowed: bool
    reason: str


class AdmissionGuard(Protocol):
    def check(self, operation: Operation) -> Admission: ...
