"""Real UC schema provider (Databricks SDK).

Creates a schema in the parent catalog, applies the enterprise tag set
(entity_tag_assignments, SQL fallback), and grants the owning group baseline
privileges. Idempotent: re-running adopts the existing schema.
"""
import logging
from typing import Any

from . import _sdk
from .base import Provider, ProvisionResult, new_asset_id
from .. import config

logger = logging.getLogger("pave.provider.schema")


class SchemaProvider(Provider):
    resource_type = "schema"

    def provision(self, *, request: dict[str, Any], resource: dict[str, Any],
                  tag_set: dict[str, str], context: dict[str, Any]) -> ProvisionResult:
        cfg = resource.get("config", {})
        project_id = request.get("project_id", "proj")
        catalog = request.get("parent_catalog") or config.PARENT_CATALOG
        schema_name = cfg.get("name") or f"{request.get('business_domain','proj')}_{project_id.split('-')[-1]}"
        full_name = f"{catalog}.{schema_name}"

        # Route to the request's TARGET workspace (empty -> the app's own). See
        # _sdk.client() + docs/ADMIN_CAPABILITIES.md for the multi-workspace story.
        # Governed options: managed location (inherit) vs a pre-approved external location,
        # and an optional comment. storage_root is only ever a pre-approved location name/URI
        # resolved by the platform — never a raw requester-supplied bucket.
        storage_root = cfg.get("storage_root") or None
        comment = cfg.get("comment") or f"PAVE: {request.get('project_name','')} ({project_id})"

        w = _sdk.client(request.get("target_workspace"))
        # 1) create (idempotent)
        try:
            kwargs = {"name": schema_name, "catalog_name": catalog, "comment": comment}
            if storage_root:
                kwargs["storage_root"] = storage_root
            w.schemas.create(**kwargs)
        except Exception as e:  # noqa: BLE001 — likely already exists; adopt it
            logger.info("schema %s create skipped/adopted: %s", full_name, e)

        target = request.get("target_workspace")
        # 2) tags (governed-style, dual-plane key vocabulary)
        tag_result = _sdk.apply_uc_tags("schemas", full_name, tag_set, target_workspace=target)

        # 3) grants: owning group gets baseline privileges
        grant_result = {"granted": 0}
        owner_group = request.get("owner_group")
        if owner_group:
            try:
                grant_result = _sdk.apply_grants(
                    "SCHEMA", full_name,
                    [(owner_group, ["USE_SCHEMA", "CREATE_TABLE", "SELECT"])],
                    target_workspace=target)
            except Exception as e:  # noqa: BLE001
                logger.warning("grants on %s failed: %s", full_name, e)
                grant_result = {"granted": 0, "error": str(e)}

        return ProvisionResult(
            asset_id=new_asset_id("schema", project_id),
            type="schema",
            names={"name": schema_name, "catalog": catalog, "full_name": full_name,
                   "storage": storage_root or "managed (inherited)"},
            external_id=full_name,
            applied_tags=tag_set,
            mode="real",
            status="ACTIVE",
            provenance={"tags": tag_result, "grants": grant_result},
        )

    def decommission(self, *, asset: dict[str, Any], context: dict[str, Any]) -> None:
        full_name = asset.get("external_id") or asset.get("names", {}).get("full_name")
        if not full_name:
            return
        # Classification-aware: drop only if not GxP-retained (checked by service).
        _sdk.client().schemas.delete(full_name=full_name)
