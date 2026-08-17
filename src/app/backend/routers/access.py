"""Access requests — grant a principal on an already-vended resource.

This is the highest-volume ticket a real platform team handles, and it was missing
entirely: PAVE could create a schema but had no way to answer "another team needs read on
it". Without this, every access change falls back to the ticket queue the portal exists to
replace, which undercuts the whole story.

Same governance spine as provisioning, deliberately: risk-tiered (restricted data or a
write/manage privilege needs an approver signature; read on internal data is a
pre-authorized standard change), executed through the same allow-listed grant helper, and
recorded in the same append-only audit log.
"""
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import config
from ..auth import get_current_user, CurrentUser
from ..database import db
from ..exceptions import ApprovalError, NotFoundError, ValidationError
from ..services.signature import manifest as build_manifest

logger = logging.getLogger("pave.access")
router = APIRouter(prefix="/api/access", tags=["access"])

# Privileges PAVE will grant, by securable, with the risk of each. Anything not listed is
# refused: an access portal that can grant arbitrary privileges is just SQL with extra
# steps, and ALL PRIVILEGES / OWNER must never be reachable from a self-service form.
GRANTABLE = {
    "schema": {
        "read":   {"privileges": ["USE_SCHEMA", "SELECT"], "risk": "low"},
        "write":  {"privileges": ["USE_SCHEMA", "SELECT", "MODIFY"], "risk": "high"},
        "create": {"privileges": ["USE_SCHEMA", "CREATE_TABLE"], "risk": "high"},
    },
    "catalog": {
        "read":   {"privileges": ["USE_CATALOG"], "risk": "low"},
        "create": {"privileges": ["USE_CATALOG", "CREATE_SCHEMA"], "risk": "high"},
    },
}


class AccessRequestIn(BaseModel):
    asset_id: str
    principal: str                              # group (preferred) or user
    level: str = Field(default="read")          # read | write | create
    justification: str = ""
    esignature: str = ""                        # required when the grant needs approval
    duration_days: int | None = None            # optional time-bound access


def _grant_spec(asset_type: str, level: str) -> dict:
    levels = GRANTABLE.get(asset_type)
    if not levels:
        raise ValidationError(
            f"access grants are not supported on '{asset_type}' "
            f"(supported: {', '.join(sorted(GRANTABLE))})")
    spec = levels.get(level)
    if not spec:
        raise ValidationError(
            f"'{level}' is not a grantable level on a {asset_type} "
            f"(one of: {', '.join(sorted(levels))})")
    return spec


def _needs_approval(asset: dict, spec: dict) -> bool:
    """Read on internal data is a standard change; anything touching restricted data or
    granting write/create is a reviewed one."""
    classification = (asset.get("applied_tags") or {}).get("data_classification")
    return spec["risk"] == "high" or classification in ("restricted", "confidential")


@router.get("/grantable")
async def grantable():
    """What the access form may offer. Keeps the UI and the server on one vocabulary."""
    return {"grantable": GRANTABLE,
            "note": "PAVE never grants ALL PRIVILEGES or transfers ownership; those stay "
                    "with the platform team."}


@router.get("/asset/{asset_id}")
async def asset_access(asset_id: str, user: CurrentUser = Depends(get_current_user)):
    """Grants PAVE has made on an asset — who has what, and who signed for it."""
    asset = await _asset(asset_id)
    events = [e for e in await db.list_audit(limit=1000)
              if e.get("asset_id") == asset_id and e.get("event_type", "").startswith("access.")]
    return {"asset_id": asset_id, "type": asset.get("type"),
            "name": (asset.get("names") or {}).get("name"), "grants": events}


async def _asset(asset_id: str) -> dict:
    asset = next((a for a in await db.list_assets() if a.get("asset_id") == asset_id), None)
    if not asset:
        raise NotFoundError(f"asset {asset_id} not found")
    if asset.get("status") not in ("ACTIVE", "PARTIAL"):
        raise ValidationError(f"asset {asset_id} is not active (status={asset.get('status')})")
    return asset


@router.post("/request")
async def request_access(payload: AccessRequestIn,
                         user: CurrentUser = Depends(get_current_user)):
    """Request access for a principal on a vended asset.

    Low-risk grants execute immediately against policy; anything else requires an approver
    with an e-signature bound to the grant.
    """
    if not payload.principal.strip():
        raise ValidationError("a principal (group or user) is required")
    asset = await _asset(payload.asset_id)
    spec = _grant_spec(asset.get("type"), payload.level)
    needs_approval = _needs_approval(asset, spec)

    if needs_approval:
        if not user.is_approver:
            raise ApprovalError(
                f"granting {payload.level} on this asset requires an approver "
                f"(classification: "
                f"{(asset.get('applied_tags') or {}).get('data_classification', 'unknown')})")
        if not payload.esignature.strip():
            raise ValidationError("an electronic signature is required for this grant")
        if len(payload.justification.strip()) < 10:
            raise ValidationError("a justification is required for a reviewed grant")

    signature = None
    if needs_approval:
        request = await db.get_request(str(asset.get("request_id"))) if asset.get("request_id") else {}
        signature = build_manifest(
            signer=user.email, printed_name=payload.esignature, decision="approve",
            gate="access-grant", request=request or {}, reason=payload.justification,
            meaning="I authorize this access grant on the resource named in this record.")

    result = await _apply(asset, payload.principal, spec["privileges"])
    await db.add_audit(
        actor=user.email, event_type="access.granted", asset_id=payload.asset_id,
        request_id=str(asset.get("request_id")) if asset.get("request_id") else None,
        reason=payload.justification or "no justification given",
        payload={"principal": payload.principal, "level": payload.level,
                 "privileges": spec["privileges"], "risk": spec["risk"],
                 "approval": "signed" if needs_approval else "pre-authorized standard change",
                 "expires_in_days": payload.duration_days,
                 "signature": signature, "applied": result})
    return {"asset_id": payload.asset_id, "principal": payload.principal,
            "level": payload.level, "privileges": spec["privileges"],
            "approval": "signed" if needs_approval else "auto",
            "applied": result}


async def _apply(asset: dict, principal: str, privileges: list[str]) -> dict:
    """Apply the grant for real when the asset is real and the kill switch allows it;
    otherwise record it as modelled, with the reason — same honesty rule as provisioning."""
    import asyncio
    from ..providers.base import classify_error

    if asset.get("mode") != "real":
        return {"mode": "simulated", "mode_reason": "configured_simulated",
                "detail": "the target resource is modelled, so the grant is recorded "
                          "rather than applied"}
    if not config.ALLOW_REAL:
        return {"mode": "simulated", "mode_reason": "kill_switch_off",
                "detail": "PAVE_ALLOW_REAL is not set, so the grant is recorded only"}

    securable = "SCHEMA" if asset.get("type") == "schema" else "CATALOG"
    full_name = asset.get("external_id") or (asset.get("names") or {}).get("full_name")
    # Multi-workspace: grant where the asset actually lives, not wherever the app happens
    # to authenticate by default.
    host = (asset.get("names") or {}).get("target_workspace")
    try:
        from ..providers import _sdk
        out = await asyncio.to_thread(
            _sdk.apply_grants, securable, full_name, [(principal, privileges)], host)
        return {"mode": "real", "mode_reason": "real", **out}
    except Exception as e:  # noqa: BLE001
        logger.warning("grant on %s failed: %s", full_name, e)
        return {"mode": "simulated", "mode_reason": classify_error(e),
                "detail": f"the grant could not be applied: {str(e)[:200]}"}
