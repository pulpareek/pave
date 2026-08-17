"""Provider protocol + shared helpers.

Every provisioned asset records not just WHICH mode produced it (real vs simulated)
but WHY. A governance portal that cannot distinguish "created it" from "could not
create it and modelled it instead" is worse than no portal, because the audit trail
then asserts something untrue. `mode_reason` + `degraded` carry that distinction from
the provider through the registry into the asset row and the UI.
"""
import hashlib
import uuid
from typing import Any, Protocol

# Why an asset ended up in the mode it did. Surfaced verbatim in the registry UI.
MODE_REASONS: dict[str, str] = {
    "real": "Created in the workspace via the Databricks SDK.",
    "configured_simulated": "This resource type is configured to be modelled, not created.",
    "kill_switch_off": "PAVE_ALLOW_REAL is not set, so real provisioning is disabled.",
    "no_permission": "The service principal is not permitted to create this here.",
    "unsupported_here": "The target workspace does not support this resource type.",
    "missing_prerequisite": "A prerequisite (credential, secret, or config id) is absent.",
    "sdk_unavailable": "The installed SDK does not expose the API this needs.",
    "sdk_error": "The real create call failed.",
    "provider_unavailable": "No real provider is implemented for this resource type.",
}


class ProviderUnavailable(Exception):
    """A real provider could not complete its work.

    Carries a `reason` code so the registry can fall back to a modelled result
    deliberately and record why, rather than the provider quietly returning a
    synthetic handle that is indistinguishable from success.
    """

    def __init__(self, message: str, *, reason: str = "sdk_error"):
        super().__init__(message)
        self.reason = reason if reason in MODE_REASONS else "sdk_error"


def classify_error(exc: BaseException) -> str:
    """Map an SDK exception to a MODE_REASONS code, so the UI can say something more
    useful than "it failed"."""
    msg = str(exc).lower()
    if any(s in msg for s in ("permission", "not authorized", "unauthorized", "forbidden",
                              "403", "does not have", "access denied")):
        return "no_permission"
    if any(s in msg for s in ("not supported", "unsupported", "not enabled", "not available",
                              "feature is not", "disabled for this workspace")):
        return "unsupported_here"
    if any(s in msg for s in ("no module named", "cannot import", "has no attribute")):
        return "sdk_unavailable"
    return "sdk_error"


def new_asset_id(resource_type: str, project_id: str,
                 context: dict[str, Any] | None = None) -> str:
    """Asset id for a provisioned resource.

    Derived deterministically from (request_id, resource_type, resource_index) when the
    saga supplies that context, so re-driving a failed request adopts the existing
    registry row instead of creating a duplicate alongside it. Falls back to a random
    suffix only when there is no request context to key on.
    """
    ctx = context or {}
    request_id, index = ctx.get("request_id"), ctx.get("resource_index")
    if request_id and index is not None:
        seed = f"{request_id}:{resource_type}:{index}".encode()
        suffix = hashlib.sha1(seed).hexdigest()[:8]
    else:
        suffix = uuid.uuid4().hex[:8]
    return f"{resource_type}-{project_id}-{suffix}"


class ProvisionResult(dict):
    """Asset record returned by a provider. Keys mirror db.add_asset fields:
    asset_id, type, names, external_id, applied_tags, mode, mode_reason, degraded,
    status."""


class Provider(Protocol):
    resource_type: str

    def provision(self, *, request: dict[str, Any], resource: dict[str, Any],
                  tag_set: dict[str, str], context: dict[str, Any]) -> ProvisionResult:
        ...

    def decommission(self, *, asset: dict[str, Any], context: dict[str, Any]) -> None:
        ...
