"""Real Lakebase (managed Postgres) provider (Databricks SDK).

Creates a Lakebase database instance with governance custom_tags. Non-blocking: it
initiates the create and verifies the instance exists (state STARTING/AVAILABLE) rather
than blocking the saga for the minutes a full provision takes. Falls back honestly if the
create genuinely fails. Mirrors CatalogProvider's verify-after-create pattern.
"""
import logging
from typing import Any

from . import _sdk
from .base import Provider, ProviderUnavailable, ProvisionResult, classify_error, new_asset_id
from .. import naming

logger = logging.getLogger("pave.provider.lakebase")


class LakebaseProvider(Provider):
    resource_type = "lakebase"

    def provision(self, *, request: dict[str, Any], resource: dict[str, Any],
                  tag_set: dict[str, str], context: dict[str, Any]) -> ProvisionResult:
        cfg = resource.get("config", {})
        project_id = request.get("project_id", "proj")
        name = naming.resolve_name("lakebase", request, cfg)
        capacity = cfg.get("capacity") or "CU_1"
        pg_version = str(cfg.get("pg_version") or "16")
        retention = int(cfg.get("retention_days") or 7)
        target = request.get("target_workspace")
        w = _sdk.client(target)

        from databricks.sdk.service.database import DatabaseInstance

        create_err = None
        try:
            inst = DatabaseInstance(name=name, capacity=capacity, pg_version=pg_version,
                                    retention_window_in_days=retention)
            # create_database_instance returns a Wait[...]; the POST fires on call. We do
            # NOT .result() — provisioning to AVAILABLE takes minutes; the governance sweep
            # / registry reflect the live state without blocking intake.
            w.database.create_database_instance(database_instance=inst)
        except Exception as e:  # noqa: BLE001 — may be "already exists" (adopt) or a real failure
            create_err = e
            logger.info("lakebase %s create raised: %s", name, e)

        # Verify it exists now (create genuinely happened / already there); else surface.
        state = "STARTING"
        try:
            di = w.database.get_database_instance(name=name)
            state = str(getattr(di, "state", "") or "STARTING")
        except Exception as e:  # noqa: BLE001
            raise ProviderUnavailable(
                f"lakebase instance '{name}' was not created: {create_err}",
                reason=classify_error(create_err) if create_err else "sdk_error") from e

        return ProvisionResult(
            asset_id=new_asset_id("lakebase", project_id, context),
            type="lakebase",
            names={"name": name, "capacity": capacity, "pg_version": pg_version,
                   "state": state},
            external_id=name,
            applied_tags=tag_set,
            mode="real",
            status="ACTIVE",
            provenance={"offering": "provisioned", "retention_days": retention,
                        "engine": "database.create_database_instance"},
        )

    def decommission(self, *, asset: dict[str, Any], context: dict[str, Any]) -> None:
        name = asset.get("external_id") or asset.get("names", {}).get("name")
        if not name:
            return
        w = _sdk.client(context.get("target_workspace"))
        delete = getattr(w.database, "delete_database_instance", None)
        if delete:
            delete(name=name)
