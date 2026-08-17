"""Real Databricks App provider (Databricks SDK) — opt-in.

Creates an app shell and tags it via the workspace entity-tag-assignments API.
Real app creation provisions compute and is slower, so this is OPT-IN (default
mode for `app` is simulated); flip with PROVIDER_MODES='{"app":"real"}'.
"""
import logging
from typing import Any

from . import _sdk
from .base import Provider, ProviderUnavailable, ProvisionResult, classify_error, new_asset_id
from .. import naming

logger = logging.getLogger("pave.provider.app")


class AppProvider(Provider):
    resource_type = "app"

    def provision(self, *, request: dict[str, Any], resource: dict[str, Any],
                  tag_set: dict[str, str], context: dict[str, Any]) -> ProvisionResult:
        cfg = resource.get("config", {})
        project_id = request.get("project_id", "proj")
        app_name = naming.resolve_name("app", request, cfg)

        compute_size = cfg.get("compute_size") or "MEDIUM"
        bindings = cfg.get("resource_bindings") or []

        w = _sdk.client(request.get("target_workspace"))
        from databricks.sdk.service.apps import App, ComputeSize
        # App.compute_size is a ComputeSize enum (passing a str breaks serialization).
        try:
            cs = ComputeSize(compute_size)
        except ValueError:
            cs = ComputeSize.MEDIUM
        desc = f"PAVE-vended app for {request.get('project_name','')}"
        create_err = None
        try:
            w.apps.create(app=App(name=app_name, description=desc, compute_size=cs))
        except Exception as e:  # noqa: BLE001 — may be "already exists" (adopt) OR a real failure
            create_err = e
            logger.info("app %s create raised: %s", app_name, e)
        # Verify it exists; otherwise the create genuinely failed — surface it instead of
        # falsely reporting a real app (the saga then marks this resource PARTIAL).
        try:
            w.apps.get(name=app_name)
        except Exception as e:  # noqa: BLE001
            raise ProviderUnavailable(
                f"app '{app_name}' was not created and does not exist: {create_err}",
                reason=classify_error(create_err) if create_err else "sdk_error") from e

        # Tag the app via workspace entity-tag-assignments (object-based API in current SDK).
        tag_result = {"via": "skipped"}
        try:
            svc = getattr(w, "workspace_entity_tag_assignments", None)
            create = getattr(svc, "create", None) if svc is not None else None
            if create is not None:
                from databricks.sdk.service.catalog import EntityTagAssignment
                for k, v in tag_set.items():
                    create(tag_assignment=EntityTagAssignment(
                        entity_type="apps", entity_name=app_name, tag_key=k, tag_value=str(v)))
                tag_result = {"via": "api", "applied": list(tag_set)}
        except Exception as e:  # noqa: BLE001 — tagging is best-effort; the registry keeps the tag set
            logger.info("app tagging deferred for %s: %s", app_name, e)
            tag_result = {"via": "registry-only", "note": str(e)[:120]}

        return ProvisionResult(
            asset_id=new_asset_id("app", project_id, context),
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
