"""Provisioning orchestration — the saga that walks the provider registry.

Drives a request through PROVISIONING -> ACTIVE | PARTIAL, writing one asset row
and one audit event per resource. Idempotent-friendly: assets upsert by id. Used
by both the in-process path (backend) and the Job runner.
"""
import asyncio
import logging

from ..database import db
from ..tagging import build_tag_set
from ..providers import get_provider
from ..models import RequestStatus
from ..well_architected import apply_defaults, record_for_asset, waivers_from_request

logger = logging.getLogger("pave.provisioning")


def _owner_id(email: str) -> str:
    return (email or "").strip().lower()


async def _ensure_owner(request: dict) -> str:
    email = request.get("owner_email") or request.get("requester") or ""
    oid = _owner_id(email)
    if oid:
        await db.upsert_owner(
            owner_id=oid, email=email,
            group_name=request.get("owner_group") or "",
            cost_center=request.get("cost_center") or "",
        )
        await db.set_request_owner(str(request["id"]), oid)
    return oid


async def _provision_list(request: dict, resources: list, owner_id: str,
                          context: dict, waivers: list, actor: str) -> tuple[list, list]:
    """Provision a LIST of resources for a request (the shared per-resource saga loop).

    Used by both full provisioning (provision_request) and delta provisioning
    (provision_resources, when adding resources to an existing project). Returns
    (created, failed). Failures are captured per-resource (saga) and never abort the loop.
    """
    request_id = str(request["id"])
    created, failed = [], []
    for resource in resources:
        rtype = resource.get("type")
        try:
            provider, mode = get_provider(rtype)
            tag_set = build_tag_set(
                request,
                owner_email=request.get("owner_email") or "",
                owner_group=request.get("owner_group") or "",
                cost_center=request.get("cost_center") or "",
            )
            # WAF-by-default: record the enforcement outcome against the ORIGINAL request,
            # then inject born-compliant defaults into the config the provider receives.
            waf_evidence = record_for_asset(request, resource, waivers)
            patched, _ = apply_defaults(request, resource)
            resource = {**resource, "type": patched["type"], "config": patched["config"]}
            # Providers are synchronous (SDK) -> run off the event loop.
            result = await asyncio.to_thread(
                provider.provision,
                request=request, resource=resource, tag_set=tag_set, context=context,
            )
            asset = dict(result)
            provenance = dict(asset.get("provenance") or {})
            provenance["well_architected"] = waf_evidence
            asset.update({
                "request_id": request_id,
                "owner_id": owner_id,
                "project_id": request.get("project_id"),
                "mode": asset.get("mode", mode),
                "sunset_date": request.get("sunset_date") or None,
                "provenance": provenance,
            })
            saved = await db.add_asset(asset)
            created.append(saved)
            await db.add_audit(actor=actor, event_type="resource.provisioned",
                               request_id=request_id, asset_id=asset["asset_id"],
                               to_state="ACTIVE",
                               payload={"type": rtype, "mode": asset["mode"],
                                        "tags": asset.get("applied_tags", {})})
        except Exception as e:  # noqa: BLE001 — saga: capture, continue
            logger.exception("provisioning failed for %s in %s", rtype, request_id)
            failed.append({"type": rtype, "error": str(e)})
            await db.add_audit(actor=actor, event_type="resource.failed",
                               request_id=request_id, to_state="FAILED",
                               payload={"type": rtype}, reason=str(e))
    return created, failed


async def provision_resources(request_id: str, new_resources: list, actor: str = "system") -> dict:
    """Provision ONLY a delta of new resources against an EXISTING project (add-to-existing).

    Does not change the request status the way a fresh provision does; it appends the new
    resources to the request record, provisions just those, and re-emits the as-code spec so
    the manifest reflects the amended footprint. Returns a summary dict.
    """
    request = await db.get_request(request_id)
    if not request:
        raise ValueError(f"request {request_id} not found")
    owner_id = await _ensure_owner(request)
    context = {"request_id": request_id, "owner_id": owner_id,
               "target_workspace": request.get("target_workspace")}
    waivers = waivers_from_request(request)

    await db.add_audit(actor=actor, event_type="resources.add_started",
                       request_id=request_id,
                       payload={"added": [r.get("type") for r in new_resources]})
    created, failed = await _provision_list(request, new_resources, owner_id, context, waivers, actor)

    # Append the new resources to the request record so the project reflects them.
    existing = request.get("resources") or []
    if isinstance(existing, str):
        import json
        existing = json.loads(existing)
    await db.set_request_resources(request_id, existing + list(new_resources))

    await db.add_audit(actor=actor, event_type="resources.add_finished",
                       request_id=request_id,
                       payload={"created": len(created), "failed": len(failed)})
    # Re-emit the as-code spec over the full (now-amended) asset set.
    try:
        from .spec import build_desired_state
        all_assets = await db.list_assets(project_id=request.get("project_id"))
        spec = build_desired_state(request, all_assets)
        await db.add_audit(actor=actor, event_type="spec.recorded",
                           request_id=request_id, payload=spec)
    except Exception as e:  # noqa: BLE001
        logger.warning("desired-state spec record failed for %s: %s", request_id, e)
    return {"request_id": request_id, "created": created, "failed": failed}


async def provision_request(request_id: str, actor: str = "system") -> dict:
    """Provision all resources in a request. Returns a summary dict."""
    request = await db.get_request(request_id)
    if not request:
        raise ValueError(f"request {request_id} not found")

    await db.update_request_status(request_id, RequestStatus.PROVISIONING.value)
    await db.add_audit(actor=actor, event_type="provisioning.started",
                       request_id=request_id,
                       from_state=request.get("status"),
                       to_state=RequestStatus.PROVISIONING.value)

    owner_id = await _ensure_owner(request)
    resources = request.get("resources") or []
    if isinstance(resources, str):
        import json
        resources = json.loads(resources)

    context = {"request_id": request_id, "owner_id": owner_id,
               "target_workspace": request.get("target_workspace")}
    waivers = waivers_from_request(request)
    created, failed = await _provision_list(request, resources, owner_id, context, waivers, actor)

    final = RequestStatus.ACTIVE.value if not failed else (
        RequestStatus.PARTIAL.value if created else RequestStatus.FAILED.value)
    await db.update_request_status(request_id, final)
    await db.add_audit(actor=actor, event_type="provisioning.finished",
                       request_id=request_id, to_state=final,
                       payload={"created": len(created), "failed": len(failed)})

    # Record-as-code: emit the resolved declarative desired-state into the
    # append-only audit log (immutable, diffable, GitOps-grade evidence).
    try:
        from .spec import build_desired_state
        spec = build_desired_state(request, created)
        await db.add_audit(actor=actor, event_type="spec.recorded",
                           request_id=request_id, payload=spec)
    except Exception as e:  # noqa: BLE001
        logger.warning("desired-state spec record failed for %s: %s", request_id, e)

    return {"request_id": request_id, "status": final,
            "created": created, "failed": failed}


async def decommission_request(request_id: str, actor: str = "system",
                               controlled: bool = False) -> dict:
    """Decommission active assets for a request. Classification-aware: restricted
    (PHI/GxP) assets are NOT hard-deleted unless `controlled` (controlled change +
    retention check completed) — they move to DECOMMISSION_REQUESTED instead. Real
    deletes only run when PAVE_ALLOW_REAL is set (the provider guard handles this)."""
    request = await db.get_request(request_id)
    pid = request.get("project_id") if request else None

    # Dependency impact check: refuse to tear down something others depend on.
    if pid and not controlled:
        others = await db.list_requests(limit=1000)
        dependents = [r.get("project_id") for r in others
                      if r.get("status") in ("ACTIVE", "PARTIAL")
                      and r.get("project_id") != pid
                      and pid in (r.get("depends_on") or [])]
        if dependents:
            await db.add_audit(actor=actor, event_type="decommission.blocked_by_dependents",
                               request_id=request_id,
                               payload={"dependents": dependents})
            return {"request_id": request_id, "decommissioned": [],
                    "held_for_controlled_change": [], "failed": [],
                    "blocked_by_dependents": dependents}

    assets = await db.list_assets(project_id=pid, status="ACTIVE")
    decommissioned, held, failed = [], [], []
    for asset in assets:
        classification = (asset.get("applied_tags") or {}).get("data_classification")
        if classification == "restricted" and not controlled:
            await db.update_asset(asset["asset_id"], status="DECOMMISSION_REQUESTED")
            held.append(asset["asset_id"])
            await db.add_audit(actor=actor, event_type="resource.decommission_held",
                               request_id=request_id, asset_id=asset["asset_id"],
                               from_state="ACTIVE", to_state="DECOMMISSION_REQUESTED",
                               reason="restricted -> controlled change + retention check required")
            continue
        try:
            provider, _ = get_provider(asset["type"], mode=asset.get("mode"))
            await asyncio.to_thread(provider.decommission, asset=asset,
                                    context={"target_workspace": request.get("target_workspace") if request else None})
            await db.update_asset(asset["asset_id"], status="DECOMMISSIONED")
            decommissioned.append(asset["asset_id"])
            await db.add_audit(actor=actor, event_type="resource.decommissioned",
                               request_id=request_id, asset_id=asset["asset_id"],
                               from_state="ACTIVE", to_state="DECOMMISSIONED")
        except Exception as e:  # noqa: BLE001
            failed.append({"asset_id": asset["asset_id"], "error": str(e)})
            await db.add_audit(actor=actor, event_type="resource.decommission_failed",
                               request_id=request_id, asset_id=asset["asset_id"], reason=str(e))
    if request and not held:
        await db.update_request_status(request_id, RequestStatus.DECOMMISSIONED.value)
    elif request and held:
        await db.update_request_status(request_id, RequestStatus.DECOMMISSION_REQUESTED.value)
    return {"request_id": request_id, "decommissioned": decommissioned,
            "held_for_controlled_change": held, "failed": failed}
