"""Provider protocol + shared helpers."""
import uuid
from typing import Any, Protocol


def new_asset_id(resource_type: str, project_id: str) -> str:
    return f"{resource_type}-{project_id}-{uuid.uuid4().hex[:8]}"


class ProvisionResult(dict):
    """Asset record returned by a provider. Keys mirror db.add_asset fields:
    asset_id, type, names, external_id, applied_tags, mode, status."""


class Provider(Protocol):
    resource_type: str

    def provision(self, *, request: dict[str, Any], resource: dict[str, Any],
                  tag_set: dict[str, str], context: dict[str, Any]) -> ProvisionResult:
        ...

    def decommission(self, *, asset: dict[str, Any], context: dict[str, Any]) -> None:
        ...
