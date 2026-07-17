"""Vector Search provider — endpoint (+ optional Delta-synced index), UC-governed.

Real creation behind PAVE_ALLOW_REAL (vector_search_endpoints.create_endpoint), with
graceful fallback to a modeled asset. Keeps RAG data in-platform (the regulated story).
"""
import logging
from typing import Any

from . import _sdk
from .base import Provider, ProvisionResult, new_asset_id

logger = logging.getLogger("pave.provider.vector_search")


class VectorSearchProvider(Provider):
    resource_type = "vector_search"

    def provision(self, *, request: dict[str, Any], resource: dict[str, Any],
                  tag_set: dict[str, str], context: dict[str, Any]) -> ProvisionResult:
        cfg = resource.get("config", {})
        project_id = request.get("project_id", "proj")
        team = (tag_set.get("owner_group") or request.get("business_domain") or "team")
        name = (cfg.get("name") or f"vs-{team}-{project_id.split('-')[-1]}").lower().replace("_", "-")[:60]

        mode, external_id, prov = "simulated", f"sim-vs-{name}", {"engine": "modeled"}
        created = self._try_create_real(name, cfg, request.get("target_workspace"))
        if created:
            mode, external_id, prov = "real", name, created

        return ProvisionResult(
            asset_id=new_asset_id("vector_search", project_id),
            type="vector_search",
            names={"name": name, "endpoint_type": cfg.get("endpoint_type", "STANDARD"),
                   "source_table": cfg.get("source_table", ""),
                   "index_type": cfg.get("index_type", "DELTA_SYNC"),
                   "embedding_source": cfg.get("embedding_source", "managed"),
                   "embedding_model": cfg.get("embedding_model", ""),
                   "pipeline_type": cfg.get("pipeline_type", "TRIGGERED")},
            external_id=external_id,
            applied_tags=tag_set,
            mode=mode,
            status="ACTIVE",
            provenance=prov,
        )

    def _try_create_real(self, name, cfg, target_workspace=None) -> dict | None:
        from .. import config
        if not config.ALLOW_REAL:
            return None
        try:
            w = _sdk.client(target_workspace)
            w.vector_search_endpoints.create_endpoint(
                name=name, endpoint_type=cfg.get("endpoint_type", "STANDARD"))
            return {"engine": "vector_search_endpoints.create_endpoint"}
        except Exception as e:  # noqa: BLE001
            logger.warning("real vector search endpoint create failed (%s); modeling instead", e)
            return None

    def decommission(self, *, asset: dict[str, Any], context: dict[str, Any]) -> None:
        if asset.get("mode") == "real" and asset.get("external_id"):
            try:
                _sdk.client(context.get("target_workspace")).vector_search_endpoints.delete_endpoint(name=asset["external_id"])
            except Exception as e:  # noqa: BLE001
                logger.warning("vector search endpoint delete failed: %s", e)
