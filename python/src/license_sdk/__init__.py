"""Framework-independent offline license SDK core."""

from license_sdk.codes import CodeError, CodeErrorReason, decode_code, encode_code
from license_sdk.guard import LicenseDeniedError, LicenseGuard
from license_sdk.product import ProductConfig
from license_sdk.profile import ICP_DIAG, ProductProfile
from license_sdk.verification import (
    CommercialLicense,
    LicenseErrorReason,
    LicenseStatus,
    LicenseValidationError,
    VerifiedLicense,
    parse_json_object,
    parse_utc_timestamp,
    validate_commercial_payload,
    verify_license,
)

__all__ = [
    "ProductConfig",
    "CommercialLicense",
    "LicenseDeniedError",
    "LicenseGuard",
    "LicenseErrorReason",
    "LicenseStatus",
    "LicenseValidationError",
    "VerifiedLicense",
    "parse_json_object",
    "parse_utc_timestamp",
    "validate_commercial_payload",
    "verify_license",
    "CodeError",
    "CodeErrorReason",
    "ICP_DIAG",
    "ProductProfile",
    "decode_code",
    "encode_code",
]
