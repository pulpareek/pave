"""Provisioning orchestration — the saga that walks the provider registry.

Drives a request through PROVISIONING -> ACTIVE | PARTIAL, writing one asset row
and one audit event per resource. Idempotent-friendly: assets upsert by id. Used
by both the in-process path (backend) and the Job runner.
"""
import asyncio
import logging

from ..database import db
from ..tagging import build_tag_set
from ..providers import MODE_REASONS, get_provider, provision as provision_resource
from ..providers.base import new_asset_id
from ..models import RequestStatus
from ..well_architected import apply_defaults, record_for_asset, waivers_from_request

logger = logging.getLogger("pave.provisioning")


async def _supervised(request_id: str, actor: str):
    """Run the in-process saga so a crash cannot leave the request stuck.

    The engine runs as a background task in a single app container that Databricks Apps
    restarts on every deploy, so an unhandled failure here used to leave the row in
    PROVISIONING with nothing watching it.
    """
    try:
        await provision_request(request_id, actor=actor)
    except Exception as e:  # noqa: BLE001 — last resort: never leave PROVISIONING hanging
        logger.exception("in-process provisioning crashed for %s", request_id)
        await db.update_request_status(request_id, RequestStatus.FAILED.value)
        await db.add_audit(actor=actor, event_type="provisioning.crashed",
                           request_id=request_id, to_state=RequestStatus.FAILED.value,
                           reason=str(e)[:500])


async def trigger_provisioning(request_id: str, actor: str) -> str:
    """Hand an authorized request to the provisioning engine.

    In job mode the Job runs as the provisioner identity — that separation is the entire
    point — so a failed trigger parks the request as FAILED rather than quietly re-running
    it in-process as the app service principal.

    Returns one of: job_triggered | inprocess_started | failed.
    """
    from .. import config
    if config.PROVISION_MODE == "job":
        from .databricks_jobs import trigger_provisioning_job
        try:
            await trigger_provisioning_job(request_id, action="provision")
            return "job_triggered"
        except Exception as e:  # noqa: BLE001
            logger.error("provisioning Job trigger failed for %s: %s", request_id, e)
            await db.update_request_status(request_id, RequestStatus.FAILED.value)
            await db.add_audit(
                actor=actor, event_type="provisioning.trigger_failed",
                request_id=request_id, to_state=RequestStatus.FAILED.value,
                reason=f"provisioning Job could not be triggered: {str(e)[:400]}",
                payload={"provision_mode": "job",
                         "separation_of_duties": "not falling back in-process; the app "
                                                 "service principal must not provision"})
            return "failed"
    asyncio.create_task(_supervised(request_id, actor))
    return "inprocess_started"


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


async def _provision_list(request: dict, indexed_resources: list[tuple[int, dict]],
                          owner_id: str, context: dict, waivers: list,
                          actor: str) -> tuple[list, list]:
    """Provision resources for a request (the shared per-resource saga loop).

    Takes (index, resource) pairs rather than a bare list because the index is the
    resource's permanent slot in the request: it keys the deterministic asset id, so a
    delta add or a retry must carry the ORIGINAL index or it would mint a second asset
    for a resource that already exists.

    Used by full provisioning, delta provisioning (add-to-existing) and retry. Returns
    (created, failed); failures are captured per-resource and never abort the loop.
    """
    request_id = str(request["id"])
    created, failed = [], []
    for index, resource in indexed_resources:
        rtype = resource.get("type")
        try:
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
            # resource_index makes the asset id deterministic per (request, resource), so a
            # re-drive adopts the existing row instead of duplicating it.
            rctx = {**context, "resource_index": index}
            # Providers are synchronous (SDK) -> run off the event loop. provision() owns the
            # real->simulated fallback so a degraded result always carries its reason.
            result = await asyncio.to_thread(
                provision_resource, rtype,
                request=request, resource=resource, tag_set=tag_set, context=rctx,
            )
            asset = dict(result)
            provenance = dict(asset.get("provenance") or {})
            provenance["well_architected"] = waf_evidence
            asset.update({
                "request_id": request_id,
                "owner_id": owner_id,
                "project_id": request.get("project_id"),
                "mode": asset.get("mode", "simulated"),
                "mode_reason": asset.get("mode_reason") or "configured_simulated",
                "degraded": bool(asset.get("degraded")),
                "sunset_date": request.get("sunset_date") or None,
                "provenance": provenance,
            })
            saved = await db.add_asset(asset)
            created.append(saved)
            await db.add_audit(actor=actor, event_type="resource.provisioned",
                               request_id=request_id, asset_id=asset["asset_id"],
                               to_state="ACTIVE",
                               payload={"type": rtype, "mode": asset["mode"],
                                        "mode_reason": asset["mode_reason"],
                                        "degraded": asset["degraded"],
                                        "tags": asset.get("applied_tags", {})})
            if asset["degraded"]:
                # Loud on its own event: a modelled asset that was meant to be real is the
                # thing an operator most needs to notice.
                await db.add_audit(actor=actor, event_type="resource.degraded",
                                   request_id=request_id, asset_id=asset["asset_id"],
                                   reason=MODE_REASONS.get(asset["mode_reason"],
                                                           asset["mode_reason"]),
                                   payload={"type": rtype, "mode_reason": asset["mode_reason"]})
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

    # The new resources take the slots AFTER the existing ones, so their deterministic
    # asset ids cannot collide with assets the request already owns.
    existing = request.get("resources") or []
    if isinstance(existing, str):
        import json
        existing = json.loads(existing)
    indexed = list(enumerate(new_resources, start=len(existing)))
    created, failed = await _provision_list(request, indexed, owner_id, context, waivers, actor)

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


async def trigger_delta(request_id: str, resources: list, actor: str = "system") -> dict:
    """Provision a DELTA of net-new resources against an existing project — in-process, or
    (PROVISION_MODE=job) through the durable provisioning Job. Mirrors trigger_provisioning so
    add-to-existing and amendment get the same async/durable path as the initial provision.

    Job mode returns immediately ({provisioning: job_triggered, run_id}); the UI polls the
    request. In-process runs the delta synchronously and returns created/failed.
    """
    from .. import config
    if config.PROVISION_MODE == "job":
        from .databricks_jobs import trigger_add_resources_job
        try:
            run_id = await trigger_add_resources_job(request_id, resources)
            await db.add_audit(actor=actor, event_type="resources.add_job_triggered",
                               request_id=request_id,
                               payload={"run_id": run_id,
                                        "added": [r.get("type") for r in resources]})
            return {"request_id": request_id, "provisioning": "job_triggered",
                    "run_id": run_id, "created": [], "failed": []}
        except Exception as e:  # noqa: BLE001
            logger.error("delta provisioning Job trigger failed for %s: %s", request_id, e)
            return {"request_id": request_id, "provisioning": "trigger_failed",
                    "error": str(e)[:400], "created": [], "failed": []}
    res = await provision_resources(request_id, resources, actor=actor)
    res.setdefault("provisioning", "inprocess")
    return res


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
    created, failed = await _provision_list(request, list(enumerate(resources)),
                                            owner_id, context, waivers, actor)

    # Born-governed cost control: if the request carries a budget, provision a budget-breach
    # SQL Alert (over system.billing.usage) that emails the owner/lead when month-to-date
    # attributed cost crosses it. Once per project; best-effort — never fails the request.
    try:
        from ..providers.budget_alert import provision_budget_alert, budget_for_request
        if budget_for_request(request) is not None:
            already = [a for a in await db.list_assets(project_id=request.get("project_id"))
                       if a.get("type") == "budget_alert" and a.get("status") == "ACTIVE"]
            if not already:
                from ..tagging import build_tag_set
                ts = build_tag_set(request)
                res = await asyncio.to_thread(
                    provision_budget_alert, request=request, tag_set=ts, context=context)
                if res:
                    res.update({"request_id": request_id, "owner_id": owner_id,
                                "project_id": request.get("project_id"),
                                "mode": res.get("mode", "simulated"),
                                "mode_reason": res.get("mode_reason") or "configured",
                                "degraded": bool(res.get("degraded")),
                                "sunset_date": request.get("sunset_date") or None})
                    created.append(await db.add_asset(res))
                    await db.add_audit(actor=actor, event_type="budget_alert.provisioned",
                                       request_id=request_id, asset_id=res["asset_id"],
                                       to_state="ACTIVE",
                                       payload={"mode": res["mode"],
                                                "notify": (res.get("names") or {}).get("notify"),
                                                "threshold_usd": (res.get("names") or {}).get("threshold_usd"),
                                                "reason": res.get("mode_reason")})
    except Exception as e:  # noqa: BLE001
        logger.warning("budget-alert step failed for %s: %s", request_id, e)

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


async def retry_request(request_id: str, actor: str = "system") -> dict:
    """Re-drive the resources of a FAILED or PARTIAL request that are not yet live.

    Previously a failed request was a dead end: there was no re-drive, and resubmitting
    tripped the uniqueness check on names the first attempt had already claimed. This
    retries only the slots without a live asset, and because asset ids are derived from
    (request, slot) the providers adopt whatever the first attempt did create instead of
    provisioning a parallel copy.
    """
    request = await db.get_request(request_id)
    if not request:
        raise ValueError(f"request {request_id} not found")
    status = request.get("status")
    if status not in (RequestStatus.FAILED.value, RequestStatus.PARTIAL.value):
        raise ValueError(f"only FAILED or PARTIAL requests can be retried (status={status})")

    resources = request.get("resources") or []
    if isinstance(resources, str):
        import json
        resources = json.loads(resources)

    project_id = request.get("project_id") or "proj"
    live = {a.get("asset_id") for a in await db.list_assets(project_id=project_id)
            if a.get("status") in ("ACTIVE", "PARTIAL")}
    pending = [
        (i, r) for i, r in enumerate(resources)
        if new_asset_id(r.get("type"), project_id,
                        {"request_id": request_id, "resource_index": i}) not in live
    ]
    if not pending:
        await db.update_request_status(request_id, RequestStatus.ACTIVE.value)
        return {"request_id": request_id, "status": RequestStatus.ACTIVE.value,
                "retried": [], "created": [], "failed": [],
                "detail": "every resource was already provisioned"}

    await db.update_request_status(request_id, RequestStatus.PROVISIONING.value)
    await db.add_audit(actor=actor, event_type="provisioning.retry_started",
                       request_id=request_id, from_state=status,
                       to_state=RequestStatus.PROVISIONING.value,
                       payload={"retrying": [r.get("type") for _, r in pending],
                                "already_live": len(resources) - len(pending)})

    owner_id = await _ensure_owner(request)
    context = {"request_id": request_id, "owner_id": owner_id,
               "target_workspace": request.get("target_workspace")}
    created, failed = await _provision_list(request, pending, owner_id, context,
                                            waivers_from_request(request), actor)

    still_missing = len(pending) - len(created)
    final = (RequestStatus.ACTIVE.value if not still_missing else
             RequestStatus.PARTIAL.value if (len(resources) - len(pending) + len(created))
             else RequestStatus.FAILED.value)
    await db.update_request_status(request_id, final)
    await db.add_audit(actor=actor, event_type="provisioning.retry_finished",
                       request_id=request_id, to_state=final,
                       payload={"created": len(created), "failed": len(failed)})
    return {"request_id": request_id, "status": final,
            "retried": [r.get("type") for _, r in pending],
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
