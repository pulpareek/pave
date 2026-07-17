"""Intake requests: create (validate + route + persist), list, get, audit."""
import logging
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import get_current_user, CurrentUser
from ..database import db
from ..exceptions import ValidationError, NotFoundError, ApprovalError
from ..models import RequestIn, RequestStatus, ResourceRequest
from ..routing import route
from ..services.provisioning_service import decommission_request
from ..validation import validate_request
from ..well_architected import evaluate as waf_evaluate


class DecommissionIn(BaseModel):
    esignature: str
    controlled: bool = False   # set when controlled change + retention check are done


class AddResourcesIn(BaseModel):
    """Amend an EXISTING project: add new resources to it. Approver-gated + e-signed
    because it extends the provisioned footprint. Only the NEW resources are provisioned."""
    resources: list[ResourceRequest]
    esignature: str

logger = logging.getLogger("pave.requests")
router = APIRouter(prefix="/api/requests", tags=["requests"])


def _project_id(domain: str) -> str:
    return f"proj-{(domain or 'gen')[:8]}-{uuid.uuid4().hex[:6]}"


@router.post("")
async def create_request(payload: RequestIn,
                         user: CurrentUser = Depends(get_current_user)):
    errors = validate_request(payload, user.email)
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
    project_id = _project_id(payload.business_domain)
    rec = {
        "project_id": project_id,
        "project_name": payload.project_name,
        "requester": user.email,
        "owner_email": user.email,
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
        "status": RequestStatus.PENDING_APPROVAL.value,
        "risk_tier": decision.risk_tier.value,
        # expanded enterprise metadata (stored in the metadata jsonb column;
        # _flatten() surfaces these back to the top level on read)
        "metadata": {
            "use_case_name": payload.use_case_name,
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
            "change_type": decision.change_type,
            "change_ref": payload.change_ref,
            "servicenow_ref": payload.servicenow_ref,
            "jira_epic": payload.jira_epic,
            "confluence_url": payload.confluence_url,
            "security_review_status": payload.security_review_status,
            "waf_waivers": payload.waf_waivers,
            "waf": waf.to_dict(),
        },
    }
    saved = await db.create_request(rec)
    await db.add_audit(actor=user.email, event_type="request.created",
                       request_id=str(saved["id"]), to_state=saved["status"],
                       payload={"routing": decision.to_dict(),
                                "waf": waf.to_dict(),
                                "project_id": project_id,
                                "resources": [r.type.value for r in payload.resources]})
    # Notify approvers the request is pending (email + deep-link when SMTP configured,
    # else simulated + audited). Fire-and-forget — must never fail the request.
    _notify_approvers_bg(saved)
    return {"request": saved, "routing": decision.to_dict(), "waf": waf.to_dict()}


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
    return await db.list_requests(requester=requester, status=status)


@router.get("/{request_id}")
async def get_request(request_id: str):
    rec = await db.get_request(request_id)
    if not rec:
        raise NotFoundError(f"request {request_id} not found")
    rec["approvals"] = await db.list_approvals(request_id)
    return rec


@router.get("/{request_id}/audit")
async def request_audit(request_id: str):
    return await db.list_audit(request_id=request_id)


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

    # WAF gate on the DELTA, using the existing project's governance context (hard blocks stop).
    waf = waf_evaluate(rec, payload.resources, rec.get("waf_waivers") or [])
    if waf.blocked:
        raise ValidationError(
            "added resources violate Well-Architected controls",
            {"errors": [f"{f['rule_id']}: {f['title']} — {f['remediation']}"
                        for f in waf.blocking], "waf": waf.to_dict()})

    new_resources = [r.model_dump(mode="json") for r in payload.resources]
    await db.add_audit(actor=user.email, event_type="resources.add_requested",
                       request_id=request_id,
                       payload={"esignature": payload.esignature,
                                "resources": [r.type.value for r in payload.resources]})
    from ..services.provisioning_service import provision_resources
    return await provision_resources(request_id, new_resources, actor=user.email)


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
