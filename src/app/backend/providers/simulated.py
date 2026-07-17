"""Simulated provider — records a registry row + synthetic handle, no real spend.

Used for risky/costly resource types (cluster, job_cluster, lakebase, catalog) in
the hybrid demo, and as the universal fallback when a real provider isn't wired.
The full tag set is still recorded so FinOps/tag-coverage tells a complete story.
"""
import uuid
from typing import Any

from .base import Provider, ProvisionResult, new_asset_id


class SimulatedProvider(Provider):
    def __init__(self, resource_type: str):
        self.resource_type = resource_type

    def provision(self, *, request: dict[str, Any], resource: dict[str, Any],
                  tag_set: dict[str, str], context: dict[str, Any]) -> ProvisionResult:
        cfg = resource.get("config", {})
        project_id = request.get("project_id", "proj")
        name = cfg.get("name") or f"{self.resource_type}-{project_id}"
        synthetic_id = f"sim-{self.resource_type}-{uuid.uuid4().hex[:12]}"

        # Model the chosen governed options so the (simulated) result tells the real story.
        modeled = self._model(cfg, request)
        names = {"name": name, **{k: str(v) for k, v in modeled.items()
                                  if isinstance(v, (str, int, float, bool)) and v not in (None, "")}}
        return ProvisionResult(
            asset_id=new_asset_id(self.resource_type, project_id),
            type=self.resource_type,
            names=names,
            external_id=synthetic_id,
            applied_tags=tag_set,
            mode="simulated",
            status="ACTIVE",
            provenance={"modeled_config": modeled},
        )

    def _model(self, cfg: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        """Return the governance-shaped config a real create would use, per resource type."""
        rt = self.resource_type
        if rt == "catalog":
            restricted = request.get("data_classification") == "restricted"
            iso = cfg.get("isolation_mode") or "auto"
            if iso == "auto":
                iso = "ISOLATED" if restricted else "OPEN"
            kind = cfg.get("kind") or "managed"
            return {"catalog_type": "MANAGED_CATALOG" if kind == "managed" else "EXTERNAL",
                    "isolation_mode": iso,
                    "storage_root": cfg.get("storage_root") or "metastore-managed",
                    "comment": cfg.get("comment") or ""}
        if rt == "lakebase":
            offering = cfg.get("offering") or "provisioned"
            base = {"offering": offering, "pg_version": cfg.get("pg_version") or "16"}
            if offering == "provisioned":
                base.update({"capacity": cfg.get("capacity") or "CU_2",
                             "retention_days": cfg.get("retention_days") or 7})
            else:
                base.update({"min_cu": cfg.get("min_cu"), "max_cu": cfg.get("max_cu"),
                             "scale_to_zero": bool(cfg.get("scale_to_zero"))})
            return base
        # generic: pass through the config so nothing is lost
        return dict(cfg)

    def decommission(self, *, asset: dict[str, Any], context: dict[str, Any]) -> None:
        # Nothing real to delete; the service flips status to DECOMMISSIONED.
        return None
