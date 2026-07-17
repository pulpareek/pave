"""Ownership portability: reassign a project's/owner's assets to a new owner.

Ownership is by reference (owner_id FK). Reassignment updates the registry and
re-derives tags from it, so tags + FinOps attribution follow the owner. Real
(SDK) assets also get their tags/grants re-applied by the provisioning engine in
the reassignment path; simulated assets update their recorded tag set.
"""
import logging

from fastapi import APIRouter, Depends

from ..auth import get_current_user, CurrentUser
from ..database import db
from ..exceptions import ApprovalError, ValidationError
from ..models import ReassignIn
from ..tagging import build_tag_set

logger = logging.getLogger("pave.ownership")
router = APIRouter(prefix="/api/ownership", tags=["ownership"])


@router.post("/reassign")
async def reassign(payload: ReassignIn, user: CurrentUser = Depends(get_current_user)):
    if not user.is_approver:
        raise ApprovalError("ownership reassignment requires an approver/admin")
    if not payload.esignature.strip():
        raise ValidationError("an electronic signature is required for reassignment")
    if not payload.project_id and not payload.old_owner_email:
        raise ValidationError("specify project_id or old_owner_email")

    new_oid = payload.new_owner_email.strip().lower()
    await db.upsert_owner(owner_id=new_oid, email=payload.new_owner_email,
                          group_name=payload.new_owner_group, cost_center=payload.new_cost_center)

    old_oid = payload.old_owner_email.strip().lower() if payload.old_owner_email else None
    affected = await db.reassign_owner(new_owner_id=new_oid, old_owner_id=old_oid,
                                       project_id=payload.project_id)

    # Re-derive tags from the (now updated) registry for each affected asset.
    retagged = []
    for a in affected:
        req = await db.get_request(str(a.get("request_id"))) if a.get("request_id") else {}
        new_tags = build_tag_set(req or {}, owner_email=payload.new_owner_email,
                                 owner_group=payload.new_owner_group,
                                 cost_center=payload.new_cost_center or (req or {}).get("cost_center", ""))
        # merge over existing applied tags
        merged = {**(a.get("applied_tags") or {}), **new_tags}
        await db.update_asset(a["asset_id"], applied_tags=merged)
        retagged.append(a["asset_id"])
        await db.add_audit(actor=user.email, event_type="ownership.reassigned",
                           asset_id=a["asset_id"], request_id=str(a.get("request_id") or "") or None,
                           payload={"new_owner": new_oid, "old_owner": old_oid,
                                    "esignature": payload.esignature})

    return {"new_owner": new_oid, "reassigned_assets": retagged, "count": len(retagged)}
