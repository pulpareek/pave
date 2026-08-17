"""Intake requests: create (validate + route + persist), list, get, audit."""
import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import config
from ..auth import get_current_user, CurrentUser
from ..database import db
from ..exceptions import ValidationError, NotFoundError, ApprovalError
from ..models import RequestIn, RequestStatus, ResourceRequest
from ..routing import STANDARD_CHANGE_POLICY, route
from ..services.provisioning_service import (
    decommission_request, retry_request, trigger_provisioning)
from ..services.signature import verify as verify_signature
from ..validation import validate_request
from .. import naming, uniqueness
from ..providers import _sdk
from ..well_architected import evaluate as waf_evaluate


class DecommissionIn(BaseModel):
    esignature: str
    controlled: bool = False   # set when controlled change + retention check are done


class AddResourcesIn(BaseModel):
    """Amend an EXISTING project: add new resources to it. Approver-gated + e-signed
    because it extends the provisioned footprint. Only the NEW resources are provisioned."""
    resources: list[ResourceRequest]
    esignature: str


class AmendIn(RequestIn):
    """A full amendment (change request) against an existing project. Carries the entire
    edited request field-set (same shape as intake) plus an e-signature. Approver-gated:
    it re-tags the project's existing assets from the amended metadata and provisions any
    net-new resources as a delta. Live-reconfigure of an already-provisioned resource is
    recorded but not executed (roadmap)."""
    esignature: str = ""

logger = logging.getLogger("pave.requests")
router = APIRouter(prefix="/api/requests", tags=["requests"])


def _project_id(domain: str) -> str:
    return f"proj-{(domain or 'gen')[:8]}-{uuid.uuid4().hex[:6]}"


def _build_request_record(payload: RequestIn, *, project_id: str, requester: str,
                          decision, waf, status: str,
                          extra_metadata: dict | None = None) -> dict:
    """Assemble the persisted request record (top-level columns + metadata jsonb).

    Single source of truth for the record shape so create and amend can't drift apart.
    `_flatten()` surfaces the metadata keys back to the top level on read.
    """
    metadata = {
        "use_case_name": payload.use_case_name,
        "medallion_layer": payload.medallion_layer,
        "business_function": payload.business_function,
        "business_sub_function": payload.business_sub_function,
        "business_owner": payload.business_owner,
        "target_workspace": payload.target_workspace,
        "technical_lead": payload.technical_lead,
        "backup_owner": payload.backup_owner,
        "department": payload.department,
        "budget_monthly_cap": payload.budget_monthly_cap,
        "cost_type": payload.cost_type,
        "wbs_code": payload.wbs_code,
        "lifecycle_stage": payload.lifecycle_stage,
        "sla_tier": payload.sla_tier,
        "rto_hours": payload.rto_hours,
        "rpo_hours": payload.rpo_hours,
        "go_live_date": payload.go_live_date,
        "validated_system": payload.validated_system,
        "dpia_ref": payload.dpia_ref,
        "data_retention": payload.data_retention,
        "support_contact": payload.support_contact,
        "ai_risk_tier": payload.ai_risk_tier,
        "intended_use": payload.intended_use,
        "out_of_scope_uses": payload.out_of_scope_uses,
        "model_card_ref": payload.model_card_ref,
        "human_oversight": payload.human_oversight,
        "depends_on": payload.depends_on,
        "source_systems": payload.source_systems,
        "consumed_by": payload.consumed_by,
        # The full routing decision, so the approval path enforces the SAME gates that
        # intake computed rather than re-deriving them from the tier alone.
        "routing": decision.to_dict(),
        "change_type": decision.change_type,
        "change_ref": payload.change_ref,
        "servicenow_ref": payload.servicenow_ref,
        "jira_epic": payload.jira_epic,
        "confluence_url": payload.confluence_url,
        "security_review_status": payload.security_review_status,
        "waf_waivers": payload.waf_waivers,
        "waf": waf.to_dict(),
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    return {
        "project_id": project_id,
        "project_name": payload.project_name,
        "requester": requester,
        "owner_email": requester,
        "owner_group": payload.owner_group,
        "cost_center": payload.cost_center,
        "business_domain": payload.business_domain,
        "data_classification": payload.data_classification.value,
        "environment": payload.environment.value,
        "region": payload.region,
        "compliance_scope": payload.compliance_scope,
        "custom_tags": payload.custom_tags,
        "resources": [r.model_dump(mode="json") for r in payload.resources],
        "description": payload.description,
        "justification": payload.justification,
        "gxp_relevant": payload.gxp_relevant,
        "contains_phi": payload.contains_phi,
        "sunset_date": payload.sunset_date,
        "status": status,
        "risk_tier": decision.risk_tier.value,
        "metadata": metadata,
    }


@router.post("")
async def create_request(payload: RequestIn,
                         user: CurrentUser = Depends(get_current_user)):
    # project_id first: the naming context (its `short` suffix) and the collision check
    # both need it before we validate.
    project_id = _project_id(payload.business_domain)
    # Resolve the owning group against Databricks: existing group -> accepted as-is; else
    # the naming convention is enforced (it will be created). Best-effort, never blocks.
    owner_group_resolvable = await asyncio.to_thread(
        _sdk.group_exists, payload.owner_group, payload.target_workspace or None)
    errors = validate_request(payload, user.email, owner_group_resolvable=owner_group_resolvable)
    # Uniqueness gate: block a NEW request that targets a name already in use (registry +
    # best-effort live). Merged into the same 422 shape as validation errors.
    errors += await uniqueness.check_collisions(
        uniqueness.view_from_payload(payload, project_id), payload.resources, db)
    if errors:
        raise ValidationError("request failed validation", {"errors": errors})

    # Well-Architected gate: hard violations block; defaults + soft findings are recorded.
    waf = waf_evaluate(payload, payload.resources, payload.waf_waivers)
    if waf.blocked:
        raise ValidationError(
            "request violates Well-Architected controls",
            {"errors": [f"{f['rule_id']}: {f['title']} — {f['remediation']}"
                        for f in waf.blocking], "waf": waf.to_dict()})

    # estimate monthly cost so the cost-escalation -> TIER2 branch can fire (routing.py)
    from .finops import RATE_CARD
    estimated_cost = sum(RATE_CARD.get(r.type.value, 10) for r in payload.resources)
    decision = route(payload, estimated_cost=estimated_cost)
    status = (RequestStatus.APPROVED.value if _fast_lane(decision)
              else RequestStatus.PENDING_APPROVAL.value)
    rec = _build_request_record(payload, project_id=project_id, requester=user.email,
                                decision=decision, waf=waf, status=status)
    saved = await db.create_request(rec)
    await db.add_audit(actor=user.email, event_type="request.created",
                       request_id=str(saved["id"]), to_state=saved["status"],
                       payload={"routing": decision.to_dict(),
                                "waf": waf.to_dict(),
                                "project_id": project_id,
                                "resources": [r.type.value for r in payload.resources]})

    if _fast_lane(decision):
        # Pre-authorized standard change: provision against policy, no human in the loop.
        # The audit event names the policy, because "who approved this?" must have an
        # answer even when the answer is not a person.
        await db.add_audit(
            actor="policy:" + STANDARD_CHANGE_POLICY["id"],
            event_type="request.auto_approved",
            request_id=str(saved["id"]),
            from_state=RequestStatus.SUBMITTED.value,
            to_state=RequestStatus.APPROVED.value,
            reason=f"pre-authorized by {STANDARD_CHANGE_POLICY['name']} "
                   f"({STANDARD_CHANGE_POLICY['id']}); no human approval required for a "
                   f"Tier-0 standard change",
            payload={"policy": STANDARD_CHANGE_POLICY, "risk_tier": decision.risk_tier.value,
                     "rationale": decision.rationale, "requester": user.email})
        outcome = await trigger_provisioning(str(saved["id"]), user.email)
        return {"request": saved, "routing": decision.to_dict(), "waf": waf.to_dict(),
                "auto_approved": True, "policy": STANDARD_CHANGE_POLICY,
                "provisioning": outcome}

    # Notify approvers the request is pending (email + deep-link when SMTP configured,
    # else simulated + audited). Fire-and-forget — must never fail the request.
    _notify_approvers_bg(saved)
    return {"request": saved, "routing": decision.to_dict(), "waf": waf.to_dict(),
            "auto_approved": False}


def _fast_lane(decision) -> bool:
    """Whether this request skips human approval entirely."""
    return bool(decision.auto_approve and config.AUTO_APPROVE_TIER0)


def _notify_approvers_bg(saved: dict) -> None:
    """Schedule the approver notification without blocking the response."""
    import asyncio
    from ..auth import APPROVERS, DEV_APPROVERS
    from ..services.notifications import notify_approvers
    approvers = sorted(APPROVERS | DEV_APPROVERS)
    try:
        asyncio.create_task(notify_approvers(saved, approvers))
    except RuntimeError:  # no running loop (e.g. sync test context) — best-effort skip
        logger.info("no event loop for approval notification of %s", saved.get("id"))


@router.get("")
async def list_requests(mine: bool = False, status: str | None = None,
                        user: CurrentUser = Depends(get_current_user)):
    requester = user.email if mine else None
    rows = await db.list_requests(requester=requester, status=status)
    # Enrich each row with a lightweight approvals summary so the Requests table can show
    # "signed / required" (or auto / rejected) instead of a bare dash — the list endpoint
    # otherwise carries no approval data (only GET /{id} does).
    for r in rows:
        md = r.get("metadata") or {}
        routing = md.get("routing") or {}
        gates = routing.get("gates") or []
        r["required_approvals"] = len(gates) if gates else (2 if r.get("risk_tier") == "TIER2" else 1)
        r["auto_approved"] = bool(routing.get("auto_approve"))
        try:
            appr = await db.list_approvals(str(r["id"]))
            r["approvals_count"] = sum(1 for a in appr if a.get("decision") == "approve")
            r["rejected"] = any(a.get("decision") == "reject" for a in appr)
        except Exception:  # noqa: BLE001 — summary is best-effort, never break the list
            r["approvals_count"] = 0
    return rows


@router.get("/{request_id}")
async def get_request(request_id: str, user: CurrentUser = Depends(get_current_user)):
    rec = await db.get_request(request_id)
    if not rec:
        raise NotFoundError(f"request {request_id} not found")
    rec["approvals"] = await db.list_approvals(request_id)
    # What actually got built, and what did not. Without the failures a client polling a
    # FAILED/PARTIAL request can only report the word "FAILED"; the per-resource errors
    # are recorded in the audit log, so surface them here rather than making every caller
    # go read the audit trail.
    rec["assets"] = await db.list_assets(request_id=request_id)
    rec["failed"] = [
        {"type": (e.get("payload") or {}).get("type"),
         "error": (e.get("payload") or {}).get("error")}
        for e in await db.list_audit(request_id=request_id)
        if e.get("event_type") == "resource.failed"
    ]
    return rec


@router.get("/{request_id}/audit")
async def request_audit(request_id: str, user: CurrentUser = Depends(get_current_user)):
    return await db.list_audit(request_id=request_id)


@router.get("/{request_id}/signatures")
async def request_signatures(request_id: str,
                             user: CurrentUser = Depends(get_current_user)):
    """The e-signatures on a request, each re-verified against the record it signed.

    Answers the question an auditor actually asks: not "was this signed?" but "was THIS
    footprint signed, by whom, and for what stated purpose?"
    """
    rec = await db.get_request(request_id)
    if not rec:
        raise NotFoundError(f"request {request_id} not found")
    out = []
    for a in await db.list_approvals(request_id):
        sig = a.get("signature") or {}
        out.append({
            "approver": a.get("approver"),
            "decision": a.get("decision"),
            "gate": a.get("gate"),
            "manifestation": sig,
            "verification": verify_signature(sig, rec),
        })
    return {"request_id": request_id, "signatures": out}


@router.post("/{request_id}/retry")
async def retry(request_id: str, user: CurrentUser = Depends(get_current_user)):
    """Re-drive a FAILED or PARTIAL request.

    Only the resources without a live asset are retried, and they keep their original
    slot so providers adopt what the first attempt created rather than duplicating it.
    No new approval is needed: the request was already authorized, and this provisions
    strictly less than what was approved.
    """
    rec = await db.get_request(request_id)
    if not rec:
        raise NotFoundError(f"request {request_id} not found")
    if not (user.is_approver or _same_user(user.email, rec.get("requester"))):
        raise ApprovalError("only the requester or an approver can retry a request")
    try:
        result = await retry_request(request_id, actor=user.email)
    except ValueError as e:
        raise ValidationError(str(e))
    return result


def _same_user(a: str | None, b: str | None) -> bool:
    return bool(a and b and a.strip().lower() == b.strip().lower())


@router.post("/{request_id}/decommission")
async def decommission(request_id: str, payload: DecommissionIn,
                       user: CurrentUser = Depends(get_current_user)):
    """Decommission a project's assets. Approver-gated + e-signed. Restricted/GxP
    assets are held for controlled change unless `controlled=true`."""
    if not user.is_approver:
        raise ApprovalError("decommission requires an approver/admin")
    if not payload.esignature.strip():
        raise ValidationError("an electronic signature is required to decommission")
    rec = await db.get_request(request_id)
    if not rec:
        raise NotFoundError(f"request {request_id} not found")
    await db.add_audit(actor=user.email, event_type="decommission.requested",
                       request_id=request_id,
                       payload={"esignature": payload.esignature, "controlled": payload.controlled})
    return await decommission_request(request_id, actor=user.email, controlled=payload.controlled)


@router.post("/{request_id}/resources")
async def add_resources(request_id: str, payload: AddResourcesIn,
                        user: CurrentUser = Depends(get_current_user)):
    """Add NEW resources to an existing project. Approver-gated + e-signed (it extends the
    provisioned footprint). The new resources are WAF-checked against the original request's
    governance context, then ONLY the new resources are provisioned (delta)."""
    if not user.is_approver:
        raise ApprovalError("adding resources to a project requires an approver/admin")
    if not payload.esignature.strip():
        raise ValidationError("an electronic signature is required to add resources")
    if not payload.resources:
        raise ValidationError("no resources to add")
    rec = await db.get_request(request_id)
    if not rec:
        raise NotFoundError(f"request {request_id} not found")

    # Naming + uniqueness gate on the DELTA (same rules as intake), using the existing
    # project's context. Name overrides on the new resources are convention-checked; names
    # are collision-checked against the registry + best-effort live.
    name_errors: list[str] = []
    from ..naming import validate_name, suggest_name, build_context
    view = uniqueness.view_from_record(rec, rec.get("project_id") or "")
    for r in payload.resources:
        override = (r.config or {}).get("name") or (r.config or {}).get("deployment_name")
        if override:
            for e in validate_name(r.type.value, str(override)):
                name_errors.append(
                    f"{e} — suggested: '{suggest_name(r.type.value, build_context(view, r.config or {}))}'")
    name_errors += await uniqueness.check_collisions(view, payload.resources, db)
    if name_errors:
        raise ValidationError("added resources failed naming/uniqueness checks",
                              {"errors": name_errors})

    # WAF gate on the DELTA, using the existing project's governance context (hard blocks stop).
    waf = waf_evaluate(rec, payload.resources, rec.get("waf_waivers") or [])
    if waf.blocked:
        raise ValidationError(
            "added resources violate Well-Architected controls",
            {"errors": [f"{f['rule_id']}: {f['title']} — {f['remediation']}"
                        for f in waf.blocking], "waf": waf.to_dict()})

    new_resources = [r.model_dump(mode="json") for r in payload.resources]
    types = [r.type.value for r in payload.resources]
    # Record the add as its OWN change request (own id + timestamp), linked to the project.
    change_rec = _change_request_from(
        rec, requester=user.email, resources=new_resources, change_kind="add_resources",
        amends=request_id, esignature=payload.esignature,
        justification=f"Added {', '.join(types)} to project {rec.get('project_id')}")
    saved = await db.create_request(change_rec)
    change_id = str(saved["id"])
    await db.add_audit(actor=user.email, event_type="resources.add_requested",
                       request_id=change_id,
                       payload={"esignature": payload.esignature, "amends": request_id,
                                "resources": types})
    # Provision the change request through the standard (job-durable) path.
    outcome = await trigger_provisioning(change_id, user.email)
    return {"change_request_id": change_id, "project_id": rec.get("project_id"),
            "amends": request_id, "resources": types, "provisioning": outcome}


def _resolved_name(payload_view: dict, r: ResourceRequest) -> str:
    """The name a resource resolves to (explicit override or generated from convention)."""
    override = (r.config or {}).get("name") or (r.config or {}).get("deployment_name")
    if override:
        return str(override)
    return naming.generate_name(r.type.value, naming.build_context(payload_view, r.config or {}))


def _change_request_from(orig: dict, *, requester: str, resources: list, change_kind: str,
                         amends: str, esignature: str, justification: str) -> dict:
    """Build a NEW first-class request record for a change (add-resources / amendment),
    cloning the original project's governance context but carrying ONLY the net-new
    resources. This gives every change its own request id + timestamp in the history, and
    each new asset traces to the change request that created it (not the original)."""
    md = dict(orig.get("metadata") or {})
    md.update({"change_kind": change_kind, "amends": amends, "esignature": esignature})
    return {
        "project_id": orig.get("project_id"),
        "project_name": orig.get("project_name"),
        "requester": requester,
        "owner_id": orig.get("owner_id") or orig.get("owner_email"),
        "owner_group": orig.get("owner_group"),
        "owner_email": orig.get("owner_email"),
        "cost_center": orig.get("cost_center"),
        "business_domain": orig.get("business_domain"),
        "data_classification": orig.get("data_classification"),
        "environment": orig.get("environment"),
        "region": orig.get("region"),
        "compliance_scope": orig.get("compliance_scope") or [],
        "custom_tags": orig.get("custom_tags") or {},
        "resources": resources,
        "description": orig.get("description"),
        "justification": justification,
        "gxp_relevant": orig.get("gxp_relevant"),
        "contains_phi": orig.get("contains_phi"),
        "sunset_date": orig.get("sunset_date"),
        "status": RequestStatus.APPROVED.value,
        "risk_tier": orig.get("risk_tier"),
        "metadata": md,
    }


async def _retag_project(project_id: str, payload: RequestIn, actor: str) -> list[str]:
    """Re-derive the enterprise tag set from the amended request and merge it onto every
    ACTIVE asset in the project (the honest effect of a metadata amendment: attribution,
    classification, retention, ownership tags follow the change)."""
    from ..tagging import build_tag_set
    req_like = {
        "cost_center": payload.cost_center, "business_domain": payload.business_domain,
        "data_classification": payload.data_classification.value,
        "environment": payload.environment.value, "project_id": project_id,
        "project_name": payload.project_name, "owner_group": payload.owner_group,
        "compliance_scope": payload.compliance_scope, "gxp_relevant": payload.gxp_relevant,
        "region": payload.region, "sunset_date": payload.sunset_date,
        "sla_tier": payload.sla_tier, "lifecycle_stage": payload.lifecycle_stage,
        "data_retention": payload.data_retention, "cost_type": payload.cost_type,
        "ai_risk_tier": payload.ai_risk_tier, "use_case_name": payload.use_case_name,
        "business_function": payload.business_function,
        "business_sub_function": payload.business_sub_function,
        "business_owner": payload.business_owner, "custom_tags": payload.custom_tags,
    }
    retagged: list[str] = []
    for a in await db.list_assets(project_id=project_id):
        if a.get("status") != "ACTIVE":
            continue
        new_tags = build_tag_set(req_like, owner_group=payload.owner_group,
                                 cost_center=payload.cost_center)
        merged = {**(a.get("applied_tags") or {}), **new_tags}
        await db.update_asset(a["asset_id"], applied_tags=merged)
        retagged.append(a["asset_id"])
        # Push the refreshed tags onto the LIVE securable for real UC assets (best-effort;
        # compute/cross-workspace degrade to registry-only, recorded in the audit event).
        import asyncio
        from ..providers._sdk import push_live_tags
        live = await asyncio.to_thread(push_live_tags, a, merged, payload.target_workspace)
        await db.add_audit(actor=actor, event_type="asset.retagged", asset_id=a["asset_id"],
                           request_id=str(a.get("request_id") or "") or None,
                           payload={"tags": merged, "live": live})
    return retagged


@router.post("/{request_id}/amend")
async def amend_request(request_id: str, payload: AmendIn,
                        user: CurrentUser = Depends(get_current_user)):
    """Full amendment: submit an edited copy of a past request as a NEW change request
    linked to the same project. Approver-gated + e-signed. It (1) re-tags the project's
    existing assets from the amended metadata and (2) provisions any net-new resources as a
    delta. Config changes to an already-provisioned resource are recorded but not
    live-reconfigured (roadmap)."""
    if not user.is_approver:
        raise ApprovalError("amending a project requires an approver/admin persona + e-signature")
    if not payload.esignature.strip():
        raise ValidationError("an electronic signature is required to amend a request")
    orig = await db.get_request(request_id)
    if not orig:
        raise NotFoundError(f"request {request_id} not found")
    project_id = orig.get("project_id") or _project_id(payload.business_domain)

    # Same authoritative validation as intake (naming, vocab, compliance, cross-field).
    owner_group_resolvable = await asyncio.to_thread(
        _sdk.group_exists, payload.owner_group, payload.target_workspace or None)
    errors = validate_request(payload, user.email, owner_group_resolvable=owner_group_resolvable)
    if errors:
        raise ValidationError("amendment failed validation", {"errors": errors})

    # Split the amended resource list into net-new vs. already-provisioned (by resolved
    # name). Only genuinely-new resources are provisioned; the rest are carried for the record.
    view = uniqueness.view_from_payload(payload, project_id)
    existing = [a for a in await db.list_assets(project_id=project_id) if a.get("status") == "ACTIVE"]
    existing_keys = {(a.get("type"), (a.get("names") or {}).get("name")) for a in existing}
    existing_keys |= {(a.get("type"), (a.get("external_id") or "").split(".")[-1]) for a in existing}
    delta, unchanged = [], []
    for r in payload.resources:
        nm = _resolved_name(view, r)
        (unchanged if (r.type.value, nm) in existing_keys else delta).append(r)

    # Uniqueness only on the delta (existing names legitimately already exist in-project).
    coll = await uniqueness.check_collisions(view, delta, db)
    if coll:
        raise ValidationError("amendment's new resources failed uniqueness", {"errors": coll})

    # WAF gate on the full amended footprint (hard blocks stop).
    waf = waf_evaluate(payload, payload.resources, payload.waf_waivers)
    if waf.blocked:
        raise ValidationError(
            "amendment violates Well-Architected controls",
            {"errors": [f"{f['rule_id']}: {f['title']} — {f['remediation']}"
                        for f in waf.blocking], "waf": waf.to_dict()})

    from .finops import RATE_CARD
    estimated_cost = sum(RATE_CARD.get(r.type.value, 10) for r in payload.resources)
    decision = route(payload, estimated_cost=estimated_cost)

    # Persist the amendment as its own APPROVED (e-signed) request, linked to the project.
    rec = _build_request_record(
        payload, project_id=project_id, requester=user.email, decision=decision, waf=waf,
        status=RequestStatus.APPROVED.value,
        extra_metadata={"change_kind": "amendment", "amends": request_id,
                        "esignature": payload.esignature})
    # The amendment record carries ONLY the net-new resources (the change itself), not the
    # full footprint — so each new asset traces to this change request and existing resources
    # stay under their original request.
    rec["resources"] = [r.model_dump(mode="json") for r in delta]
    saved = await db.create_request(rec)
    amend_id = str(saved["id"])
    await db.add_audit(actor=user.email, event_type="request.amended", request_id=amend_id,
                       to_state=RequestStatus.APPROVED.value,
                       payload={"amends": request_id, "project_id": project_id,
                                "esignature": payload.esignature,
                                "delta": [r.type.value for r in delta],
                                "unchanged": [r.type.value for r in unchanged],
                                "routing": decision.to_dict()})

    # (1) Re-tag existing project assets from the amended metadata (live push for real UC).
    retagged = await _retag_project(project_id, payload, user.email)

    # (2) Provision any net-new resources through the standard (job-durable) path, under the
    # amendment's own request id. Metadata-only amendments have no delta -> nothing to provision.
    provisioning = "none"
    if delta:
        provisioning = await trigger_provisioning(amend_id, user.email)

    return {"amendment_request_id": saved["id"], "project_id": project_id,
            "risk_tier": decision.risk_tier.value, "change_type": decision.change_type,
            "retagged": retagged, "provisioning": provisioning,
            "provisioned_new": [r.type.value for r in delta],
            "unchanged": [r.type.value for r in unchanged], "waf": waf.to_dict()}


@router.get("/{request_id}/spec")
async def request_spec(request_id: str):
    """The declarative 'as-code' desired-state record (execute imperatively, record
    declaratively). Returns both the structured spec and a YAML rendering."""
    from ..services.spec import build_desired_state, to_yaml, to_terraform
    rec = await db.get_request(request_id)
    if not rec:
        raise NotFoundError(f"request {request_id} not found")
    assets = await db.list_assets(project_id=rec.get("project_id"))
    spec = build_desired_state(rec, assets)
    out = {"spec": spec, "yaml": to_yaml(spec)}
    tf = to_terraform(spec)
    if tf:   # only present when the request includes an account-level workspace
        out["terraform"] = tf
    return out
