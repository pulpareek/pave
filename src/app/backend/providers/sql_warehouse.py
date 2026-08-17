"""Real SQL Warehouse provider (Databricks SDK).

Creates a governed SQL warehouse (serverless by default, Photon on, cost-optimized spot,
short auto-stop) with the enterprise tag set applied as warehouse tags. Non-blocking:
initiates the create and resolves the warehouse id by name (verify-after-create), never
falsely reporting real. Mirrors CatalogProvider / LakebaseProvider.
"""
import logging
from typing import Any

from . import _sdk
from .base import Provider, ProviderUnavailable, ProvisionResult, classify_error, new_asset_id
from .. import naming

logger = logging.getLogger("pave.provider.sql_warehouse")


class SqlWarehouseProvider(Provider):
    resource_type = "sql_warehouse"

    def provision(self, *, request: dict[str, Any], resource: dict[str, Any],
                  tag_set: dict[str, str], context: dict[str, Any]) -> ProvisionResult:
        cfg = resource.get("config", {})
        project_id = request.get("project_id", "proj")
        name = naming.resolve_name("sql_warehouse", request, cfg)
        size = cfg.get("cluster_size") or "Small"
        wtype = (cfg.get("warehouse_type") or "serverless").lower()
        auto_stop = int(cfg.get("auto_stop_mins") or 10)
        max_clusters = int(cfg.get("max_num_clusters") or 1)
        target = request.get("target_workspace")
        w = _sdk.client(target)

        from databricks.sdk.service.sql import (
            CreateWarehouseRequestWarehouseType, SpotInstancePolicy,
            EndpointTags, EndpointTagPair)
        serverless = wtype == "serverless"
        # Serverless warehouses run as PRO type with serverless compute enabled.
        sdk_type = (CreateWarehouseRequestWarehouseType.PRO if wtype in ("serverless", "pro")
                    else CreateWarehouseRequestWarehouseType.CLASSIC)
        tags = EndpointTags(custom_tags=[EndpointTagPair(key=k, value=str(v))
                                         for k, v in tag_set.items()])

        create_err = None
        try:
            w.warehouses.create(
                name=name, cluster_size=size, auto_stop_mins=auto_stop,
                enable_serverless_compute=serverless, enable_photon=True,
                min_num_clusters=1, max_num_clusters=max_clusters,
                warehouse_type=sdk_type,
                spot_instance_policy=SpotInstancePolicy.COST_OPTIMIZED, tags=tags)
        except Exception as e:  # noqa: BLE001 — may already exist (adopt) or genuinely fail
            create_err = e
            logger.info("warehouse %s create raised: %s", name, e)

        # Resolve the id by name (also verifies it exists — else surface the failure).
        wid, state = None, ""
        try:
            for wh in w.warehouses.list():
                if (wh.name or "") == name:
                    wid = wh.id
                    state = str(getattr(wh, "state", "") or "")
                    break
        except Exception as e:  # noqa: BLE001
            logger.info("warehouse list failed after create: %s", e)
        if not wid:
            raise ProviderUnavailable(
                f"sql_warehouse '{name}' was not created: {create_err}",
                reason=classify_error(create_err) if create_err else "sdk_error")

        return ProvisionResult(
            asset_id=new_asset_id("sql_warehouse", project_id, context),
            type="sql_warehouse",
            names={"name": name, "id": wid, "size": size, "type": wtype,
                   "auto_stop_mins": auto_stop, "state": state},
            external_id=wid,
            applied_tags=tag_set,
            mode="real",
            status="ACTIVE",
            provenance={"engine": "warehouses.create", "serverless": serverless,
                        "max_num_clusters": max_clusters},
        )

    def decommission(self, *, asset: dict[str, Any], context: dict[str, Any]) -> None:
        wid = asset.get("external_id")
        if wid:
            _sdk.client(context.get("target_workspace")).warehouses.delete(id=wid)
