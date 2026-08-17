"""Real UC catalog provider (Databricks SDK).

Creates a Unity Catalog catalog (managed/default storage, or a pre-approved external
location), sets isolation mode (auto -> ISOLATED for restricted data), applies the
enterprise tag set, and grants the owning group baseline privileges. Idempotent:
re-running adopts the existing catalog. Mirrors SchemaProvider.
"""
import logging
from typing import Any

from . import _sdk
from .base import Provider, ProviderUnavailable, ProvisionResult, classify_error, new_asset_id
from .. import naming

logger = logging.getLogger("pave.provider.catalog")


class CatalogProvider(Provider):
    resource_type = "catalog"

    def provision(self, *, request: dict[str, Any], resource: dict[str, Any],
                  tag_set: dict[str, str], context: dict[str, Any]) -> ProvisionResult:
        cfg = resource.get("config", {})
        project_id = request.get("project_id", "proj")
        name = naming.resolve_name("catalog", request, cfg)
        comment = cfg.get("comment") or f"PAVE: {request.get('project_name','')} ({project_id})"
        # storage_root is only ever a pre-approved external LOCATION name (never a raw
        # bucket); absent -> managed/default storage (the governed default).
        storage_root = cfg.get("storage_root") or None
        target = request.get("target_workspace")

        w = _sdk.client(target)
        # 1) create (idempotent — adopt if it already exists)
        create_err = None
        try:
            kwargs = {"name": name, "comment": comment}
            if storage_root:
                kwargs["storage_root"] = storage_root
            w.catalogs.create(**kwargs)
        except Exception as e:  # noqa: BLE001 — may be "already exists" (adopt) OR a real failure
            create_err = e
            logger.info("catalog %s create raised: %s", name, e)
        # Verify it actually exists now. If not, the create genuinely failed (e.g. the
        # metastore has no storage root / needs a MANAGED LOCATION) — surface it so the saga
        # marks the resource PARTIAL instead of falsely reporting a real catalog.
        try:
            w.catalogs.get(name)
        except Exception as e:  # noqa: BLE001
            raise ProviderUnavailable(
                f"catalog '{name}' was not created and does not exist: {create_err}. "
                f"This metastore likely needs a MANAGED LOCATION (pre-approved external "
                f"location) or account Default-Storage support for API catalog creation.",
                reason=classify_error(create_err) if create_err else "sdk_error") from e

        # 2) isolation mode: auto -> ISOLATED for restricted data, else OPEN
        iso = cfg.get("isolation_mode") or "auto"
        if iso == "auto":
            iso = "ISOLATED" if request.get("data_classification") == "restricted" else "OPEN"
        try:
            from databricks.sdk.service.catalog import CatalogIsolationMode
            mode = getattr(CatalogIsolationMode, iso, None)
            if mode is not None:
                w.catalogs.update(name=name, isolation_mode=mode)
        except Exception as e:  # noqa: BLE001
            logger.info("catalog %s isolation set skipped: %s", name, e)

        # 3) governed tags (dual-plane key vocabulary)
        tag_result = _sdk.apply_uc_tags("catalogs", name, tag_set, target_workspace=target)

        # 4) grants: owning group gets baseline catalog privileges
        grant_result = {"granted": 0}
        owner_group = request.get("owner_group")
        if owner_group:
            try:
                grant_result = _sdk.apply_grants(
                    "CATALOG", name,
                    [(owner_group, ["USE_CATALOG", "CREATE_SCHEMA"])],
                    target_workspace=target)
            except Exception as e:  # noqa: BLE001
                logger.warning("grants on catalog %s failed: %s", name, e)
                grant_result = {"granted": 0, "error": str(e)}

        return ProvisionResult(
            asset_id=new_asset_id("catalog", project_id, context),
            type="catalog",
            names={"name": name, "isolation_mode": iso,
                   "storage": storage_root or "managed (default)"},
            external_id=name,
            applied_tags=tag_set,
            mode="real",
            status="ACTIVE",
            provenance={"tags": tag_result, "grants": grant_result},
        )

    def decommission(self, *, asset: dict[str, Any], context: dict[str, Any]) -> None:
        name = asset.get("external_id") or asset.get("names", {}).get("name")
        if not name:
            return
        # force=True drops the catalog even if it still has (empty) schemas; the service
        # layer is classification-aware and blocks GxP-retained assets before we get here.
        # Targets the workspace the catalog was vended INTO, not the app's own.
        _sdk.client(context.get("target_workspace")).catalogs.delete(name=name, force=True)
