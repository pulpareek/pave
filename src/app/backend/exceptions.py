"""PAVE domain exceptions + a single error shape for the API."""
from typing import Any, Optional


class PaveError(Exception):
    """Base error. Carries a machine code + optional details for the client."""

    status_code = 500
    error_code = "pave_error"

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.message, "code": self.error_code, "details": self.details}


class ValidationError(PaveError):
    status_code = 400
    error_code = "validation_error"


class NotFoundError(PaveError):
    status_code = 404
    error_code = "not_found"


class ConflictError(PaveError):
    status_code = 409
    error_code = "conflict"


class ApprovalError(PaveError):
    status_code = 409
    error_code = "approval_error"


class DatabaseError(PaveError):
    status_code = 503
    error_code = "database_unavailable"


class ProvisioningError(PaveError):
    status_code = 500
    error_code = "provisioning_error"
