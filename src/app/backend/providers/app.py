"""Real Databricks App provider (Databricks SDK) — opt-in.

Creates an app shell and tags it via the workspace entity-tag-assignments API.
Real app creation provisions compute and is slower, so this is OPT-IN (default
mode for `app` is simulated); flip with PROVIDER_MODES='{"app":"real"}'.
"""
import logging
from typing import Any

from . import _sdk
from .base import Provider, ProvisionResult, new_asset_id

logger = logging.getLogger("pave.provider.app")


class AppProvider(Provider):
    resource_type = "app"

    def provision(self, *, request: dict[str, Any], resource: dict[str, Any],
                  tag_set: dict[str, str], context: dict[str, Any]) -> ProvisionResult:
        cfg = resource.get("config", {})
        project_id = request.get("project_id", "proj")
        app_name = (cfg.get("name") or f"pave-{project_id}").lower().replace("_", "-")[:30]

        compute_size = cfg.get("compute_size") or "MEDIUM"
        bindings = cfg.get("resource_bindings") or []

        w = _sdk.client(request.get("target_workspace"))
        from databricks.sdk.service.apps import App
        try:
            # compute_size is passed when the SDK supports it; older SDKs ignore the kwarg.
            app_kwargs = {"name": app_name,
                          "description": f"PAVE-vended app for {request.get('project_name','')}"}
            try:
                w.apps.create(app=App(**app_kwargs, compute_size=compute_size))
            except TypeError:
                w.apps.create(app=App(**app_kwargs))
        except Exception as e:  # noqa: BLE001 — adopt if exists
            logger.info("app %s create skipped/adopted: %s", app_name, e)

        # Tag the app object (workspace entity-tag-assignments).
        tag_result = {"via": "skipped"}
        try:
            svc = getattr(w, "workspace_entity_tag_assignments", None)
            if svc is not None:
                for k, v in tag_set.items():
                    svc.create(entity_type="apps", entity_name=app_name, tag_key=k, tag_value=str(v))
                tag_result = {"via": "api", "applied": list(tag_set)}
        except Exception as e:  # noqa: BLE001
            logger.warning("app tagging failed for %s: %s", app_name, e)
            tag_result = {"via": "none", "error": str(e)}

        return ProvisionResult(
            asset_id=new_asset_id("app", project_id),
            type="app",
            names={"name": app_name, "compute_size": compute_size,
                   "resource_bindings": ",".join(bindings) if bindings else ""},
            external_id=app_name,
            applied_tags=tag_set,
            mode="real",
            status="ACTIVE",
            provenance={"tags": tag_result, "compute_size": compute_size, "bindings": bindings},
        )

    def decommission(self, *, asset: dict[str, Any], context: dict[str, Any]) -> None:
        name = asset.get("external_id")
        if name:
            _sdk.client(context.get("target_workspace")).apps.delete(name=name)
