"""Vector Search provider — endpoint (+ optional Delta-synced index), UC-governed.

Real creation behind PAVE_ALLOW_REAL (vector_search_endpoints.create_endpoint), with
graceful fallback to a modeled asset. Keeps RAG data in-platform (the regulated story).
"""
import logging
from typing import Any

from . import _sdk
from .base import Provider, ProvisionResult, classify_error, new_asset_id
from .. import naming

logger = logging.getLogger("pave.provider.vector_search")


class VectorSearchProvider(Provider):
    resource_type = "vector_search"

    def provision(self, *, request: dict[str, Any], resource: dict[str, Any],
                  tag_set: dict[str, str], context: dict[str, Any]) -> ProvisionResult:
        cfg = resource.get("config", {})
        project_id = request.get("project_id", "proj")
        name = naming.resolve_name("vector_search", request, cfg)

        mode, external_id, prov = "simulated", f"sim-vs-{name}", {"engine": "modeled"}
        created, reason = self._try_create_real(name, cfg, request.get("target_workspace"))
        if created:
            mode, external_id, prov = "real", name, created

        return ProvisionResult(
            asset_id=new_asset_id("vector_search", project_id, context),
            mode_reason=reason,
            degraded=mode != "real" and reason != "configured_simulated",
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

    def _try_create_real(self, name, cfg, target_workspace=None) -> tuple[dict | None, str]:
        """Returns (created, reason). `reason` explains a None so the asset can say why
        it is modelled rather than reporting an unqualified ACTIVE."""
        from .. import config
        if not config.ALLOW_REAL:
            return None, "kill_switch_off"
        try:
            from databricks.sdk.service.vectorsearch import EndpointType
            w = _sdk.client(target_workspace)
            raw = cfg.get("endpoint_type", "STANDARD")
            try:
                et = EndpointType(raw)
            except ValueError:
                et = EndpointType.STANDARD
            # create_endpoint returns a Wait (endpoint provisions over minutes); do NOT block.
            w.vector_search_endpoints.create_endpoint(name=name, endpoint_type=et)
            # verify it now exists so we never falsely report real
            w.vector_search_endpoints.get_endpoint(endpoint_name=name)
            return {"engine": "vector_search_endpoints.create_endpoint",
                    "endpoint_type": et.value}, "real"
        except Exception as e:  # noqa: BLE001
            reason = classify_error(e)
            logger.warning("real vector search endpoint create failed (%s: %s); modelling instead",
                           reason, e)
            return None, reason

    def decommission(self, *, asset: dict[str, Any], context: dict[str, Any]) -> None:
        if asset.get("mode") == "real" and asset.get("external_id"):
            try:
                _sdk.client(context.get("target_workspace")).vector_search_endpoints.delete_endpoint(name=asset["external_id"])
            except Exception as e:  # noqa: BLE001
                logger.warning("vector search endpoint delete failed: %s", e)
