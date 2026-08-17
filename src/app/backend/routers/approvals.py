"""Approver console: queue + risk-tiered decision with e-signature.

Approval gating is driven by the ORDERED GATES that routing.py computes for each request
(`platform`, `security-compliance`, `gxp-validation`, `account-admin`, `llmops-validation`),
not by counting heads. Counting two distinct approvers let two platform engineers satisfy
a GxP request between them, which is exactly what a validation gate exists to prevent.

A request provisions only when every required gate is satisfied by an approver entitled to
that gate, and never by its own requester.
"""
import logging

from fastapi import APIRouter, Depends

from ..auth import get_current_user, CurrentUser
from ..database import db
from ..exceptions import ApprovalError, NotFoundError, ValidationError
from ..models import ApprovalIn, RequestStatus, RiskTier
from ..services.provisioning_service import trigger_provisioning
from ..services.signature import manifest as build_manifest

logger = logging.getLogger("pave.approvals")
router = APIRouter(prefix="/api/approvals", tags=["approvals"])

# Which groups may sign which gate. `platform` is the baseline any approver can sign;
# the rest need a compliance/admin identity. In a hardened deploy these map to real
# workspace groups — the persona switcher stands in for them during a demo.
GATE_ENTITLEMENTS: dict[str, tuple[str, ...]] = {
    "platform": ("pave-approvers", "platform-admins"),
    "security-compliance": ("platform-admins",),
    "gxp-validation": ("platform-admins", "qa-validation"),
    "account-admin": ("platform-admins", "account-admins"),
    "llmops-validation": ("platform-admins", "llmops"),
}

GATE_LABELS = {
    "platform": "Platform engineering",
    "security-compliance": "Security & compliance",
    "gxp-validation": "GxP validation (QA)",
    "account-admin": "Account administration",
    "llmops-validation": "LLMOps validation",
}


def _required_gates(req: dict) -> list[str]:
    """The gates this request must clear, recovered from the routing decision recorded
    at intake. Falls back to the tier default for rows created before gates were stored."""
    md = req.get("metadata") or {}
    gates = (md.get("routing") or {}).get("gates") or req.get("gates")
    if gates:
        return list(gates)
    return (["platform", "security-compliance"]
            if req.get("risk_tier") == RiskTier.TIER2.value else ["platform"])


def _gates_for_user(user: CurrentUser) -> set[str]:
    """Gates this identity is entitled to sign."""
    groups = {g.strip().lower() for g in (user.groups or [])}
    out = set()
    for gate, allowed in GATE_ENTITLEMENTS.items():
        if groups & set(allowed) or (user.is_admin and gate != "platform"):
            out.add(gate)
    if user.is_approver:
        out.add("platform")
    if user.is_admin:
        out |= set(GATE_ENTITLEMENTS)
    return out


def _satisfied_gates(approvals: list[dict]) -> set[str]:
    return {a.get("gate") for a in approvals if a.get("decision") == "approve" and a.get("gate")}


def _decorate(req: dict, approvals: list[dict]) -> dict:
    required = _required_gates(req)
    satisfied = _satisfied_gates(approvals)
    req["approvals"] = approvals
    req["required_gates"] = required
    req["satisfied_gates"] = sorted(g for g in satisfied if g in required)
    req["outstanding_gates"] = [g for g in required if g not in satisfied]
    req["required_approvals"] = len(required)
    return req


@router.get("/queue")
async def queue(user: CurrentUser = Depends(get_current_user)):
    if not user.is_approver:
        raise ApprovalError("not authorized to view the approval queue")
    pending = await db.list_requests(status=RequestStatus.PENDING_APPROVAL.value)
    mine = _gates_for_user(user)
    for r in pending:
        _decorate(r, await db.list_approvals(str(r["id"])))
        # What THIS approver can do about it right now.
        r["signable_gates"] = [g for g in r["outstanding_gates"] if g in mine]
        r["blocked_reason"] = ("you raised this request" if _is_requester(r, user)
                               else "" if r["signable_gates"] else
                               "awaiting an approver entitled to: " +
                               ", ".join(GATE_LABELS.get(g, g) for g in r["outstanding_gates"]))
    return pending


def _is_requester(req: dict, user: CurrentUser) -> bool:
    email = (user.email or "").strip().lower()
    return email and email in {
        (req.get("requester") or "").strip().lower(),
        (req.get("owner_email") or "").strip().lower(),
    }


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

    # Separation of duties: you cannot authorize your own request, whatever your groups.
    if _is_requester(req, user):
        raise ApprovalError("you raised this request; a different approver must sign it")

    required = _required_gates(req)
    existing = await db.list_approvals(request_id)
    satisfied = _satisfied_gates(existing)

    if payload.decision == "reject":
        return await _reject(request_id, req, payload, user)

    # Which gate is this signature for? Honour an explicit choice, else take the first
    # outstanding gate this approver is entitled to sign.
    mine = _gates_for_user(user)
    outstanding = [g for g in required if g not in satisfied]
    requested_gate = (getattr(payload, "gate", "") or "").strip()
    if requested_gate:
        if requested_gate not in required:
            raise ApprovalError(f"'{requested_gate}' is not a gate on this request "
                                f"(required: {required})")
        if requested_gate not in mine:
            raise ApprovalError(
                f"you are not entitled to sign the {GATE_LABELS.get(requested_gate, requested_gate)} "
                f"gate (needs one of: {', '.join(GATE_ENTITLEMENTS[requested_gate])})")
        gate = requested_gate
    else:
        gate = next((g for g in outstanding if g in mine), "")
    if not gate:
        raise ApprovalError(
            "you are not entitled to sign any outstanding gate on this request "
            f"(outstanding: {', '.join(GATE_LABELS.get(g, g) for g in outstanding) or 'none'})")
    if gate in satisfied:
        raise ApprovalError(f"the {GATE_LABELS.get(gate, gate)} gate is already signed")

    # Bind the signature to a digest of the exact footprint being approved, so it cannot
    # later be read as approval of something else (Part 11 §11.70).
    signature = build_manifest(
        signer=user.email, printed_name=payload.esignature, decision="approve",
        gate=gate, request=req, reason=payload.reason, meaning=payload.meaning)
    await db.add_approval({
        "request_id": request_id, "approver": user.email,
        "decision": payload.decision, "reason": payload.reason,
        "esignature": payload.esignature, "gate": gate, "signature": signature,
    })
    await db.add_audit(actor=user.email, event_type="approval.approve",
                       request_id=request_id,
                       payload={"gate": gate, "reason": payload.reason,
                                "signature": signature})

    approvals = await db.list_approvals(request_id)
    satisfied = _satisfied_gates(approvals)
    outstanding = [g for g in required if g not in satisfied]
    if outstanding:
        return {"status": RequestStatus.PENDING_APPROVAL.value,
                "signed_gate": gate,
                "satisfied_gates": sorted(satisfied & set(required)),
                "outstanding_gates": outstanding,
                "message": "awaiting " + ", ".join(GATE_LABELS.get(g, g) for g in outstanding)}

    # Every gate is signed. Compare-and-swap so two concurrent final approvals cannot both
    # trigger provisioning.
    moved = await db.transition_request_status(
        request_id, expected=RequestStatus.PENDING_APPROVAL.value,
        new=RequestStatus.APPROVED.value)
    if moved is None:
        logger.info("request %s was already advanced by a concurrent approval", request_id)
        current = await db.get_request(request_id)
        return {"status": (current or {}).get("status"), "signed_gate": gate,
                "provisioning": "already_triggered"}

    await db.add_audit(actor=user.email, event_type="request.approved",
                       request_id=request_id, to_state=RequestStatus.APPROVED.value,
                       payload={"gates": required,
                                "signatures": [{"gate": a.get("gate"), "approver": a.get("approver")}
                                               for a in approvals if a.get("decision") == "approve"]})
    outcome = await trigger_provisioning(request_id, user.email)
    if outcome == "failed":
        return {"status": RequestStatus.FAILED.value, "provisioning": "trigger_failed",
                "error": "the provisioning Job could not be triggered; the request was not "
                         "provisioned in-process because that would bypass separation of duties"}
    return {"status": RequestStatus.APPROVED.value, "signed_gate": gate,
            "provisioning": outcome}


async def _reject(request_id: str, req: dict, payload: ApprovalIn, user: CurrentUser) -> dict:
    """A rejection at any gate stops the request; no gate entitlement is needed to say no."""
    gate = next(iter(_gates_for_user(user) & set(_required_gates(req))), "platform")
    signature = build_manifest(
        signer=user.email, printed_name=payload.esignature, decision="reject",
        gate=gate, request=req, reason=payload.reason, meaning=payload.meaning)
    await db.add_approval({
        "request_id": request_id, "approver": user.email, "decision": "reject",
        "reason": payload.reason, "esignature": payload.esignature,
        "gate": gate, "signature": signature,
    })
    await db.add_audit(actor=user.email, event_type="approval.reject",
                       request_id=request_id, to_state=RequestStatus.REJECTED.value,
                       reason=payload.reason, payload={"signature": signature})
    moved = await db.transition_request_status(
        request_id, expected=RequestStatus.PENDING_APPROVAL.value,
        new=RequestStatus.REJECTED.value)
    if moved is None:
        current = await db.get_request(request_id)
        return {"status": (current or {}).get("status")}
    return {"status": RequestStatus.REJECTED.value}
