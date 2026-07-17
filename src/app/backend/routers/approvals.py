"""Approver console: queue + risk-tiered decision with e-signature.

Approval gating:
  - TIER0 / TIER1  -> 1 approval
  - TIER2          -> 2 distinct approvers (dual approval / compliance)
On final approval the provisioning engine is triggered (in-process or Job).
"""
import asyncio
import logging

from fastapi import APIRouter, Depends

from ..auth import get_current_user, CurrentUser
from ..config import PROVISION_MODE
from ..database import db
from ..exceptions import ApprovalError, NotFoundError, ValidationError
from ..models import ApprovalIn, RequestStatus, RiskTier
from ..services.provisioning_service import provision_request

logger = logging.getLogger("pave.approvals")
router = APIRouter(prefix="/api/approvals", tags=["approvals"])


def _required_approvals(risk_tier: str | None) -> int:
    return 2 if risk_tier == RiskTier.TIER2.value else 1


@router.get("/queue")
async def queue(user: CurrentUser = Depends(get_current_user)):
    if not user.is_approver:
        raise ApprovalError("not authorized to view the approval queue")
    pending = await db.list_requests(status=RequestStatus.PENDING_APPROVAL.value)
    for r in pending:
        r["approvals"] = await db.list_approvals(str(r["id"]))
        r["required_approvals"] = _required_approvals(r.get("risk_tier"))
    return pending


async def _trigger_provisioning(request_id: str, actor: str):
    if PROVISION_MODE == "job":
        from ..services.databricks_jobs import trigger_provisioning_job
        try:
            await trigger_provisioning_job(request_id, action="provision")
            return
        except Exception as e:  # noqa: BLE001 — fall back to in-process
            logger.warning("Job trigger failed (%s); running in-process", e)
    asyncio.create_task(provision_request(request_id, actor=actor))


@router.post("/{request_id}/decision")
async def decide(request_id: str, payload: ApprovalIn,
                 user: CurrentUser = Depends(get_current_user)):
    if not user.is_approver:
        raise ApprovalError("not authorized to approve/reject requests")
    if not payload.esignature.strip():
        raise ValidationError("an electronic signature is required")

    req = await db.get_request(request_id)
    if not req:
        raise NotFoundError(f"request {request_id} not found")
    if req.get("status") != RequestStatus.PENDING_APPROVAL.value:
        raise ApprovalError(f"request is not pending approval (status={req.get('status')})")

    existing = await db.list_approvals(request_id)
    if any(a.get("approver") == user.email and a.get("decision") == "approve"
           for a in existing) and payload.decision == "approve":
        raise ApprovalError("you have already approved this request (need a distinct approver)")

    await db.add_approval({
        "request_id": request_id, "approver": user.email,
        "decision": payload.decision, "reason": payload.reason,
        "esignature": payload.esignature,
        "gate": "security-compliance" if user.is_admin else "platform",
    })
    await db.add_audit(actor=user.email,
                       event_type=f"approval.{payload.decision}",
                       request_id=request_id,
                       payload={"esignature": payload.esignature, "reason": payload.reason})

    if payload.decision == "reject":
        await db.update_request_status(request_id, RequestStatus.REJECTED.value)
        return {"status": RequestStatus.REJECTED.value}

    approvals = await db.list_approvals(request_id)
    approve_count = len({a["approver"] for a in approvals if a.get("decision") == "approve"})
    required = _required_approvals(req.get("risk_tier"))
    if approve_count >= required:
        await db.update_request_status(request_id, RequestStatus.APPROVED.value)
        await db.add_audit(actor=user.email, event_type="request.approved",
                           request_id=request_id, to_state=RequestStatus.APPROVED.value,
                           payload={"approvals": approve_count, "required": required})
        await _trigger_provisioning(request_id, user.email)
        return {"status": RequestStatus.APPROVED.value, "provisioning": "triggered"}

    return {"status": RequestStatus.PENDING_APPROVAL.value,
            "approvals": approve_count, "required": required}
