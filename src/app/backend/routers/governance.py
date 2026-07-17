"""Day-2 governance: sunset autopilot, drift/orphan sweep, recertification.

Industry pattern (AWS AFT TTL + FinOps untagged-sweeps + Cortex/Port scorecards):
keep vended resources healthy AFTER provisioning, not just at creation. All
read-only except reclaim/recertify, which are classification-aware and audited.
"""
import datetime
import logging

from fastapi import APIRouter, Depends

from ..auth import get_current_user, CurrentUser
from ..database import db
from ..exceptions import ApprovalError, NotFoundError, ConflictError
from ..models import REQUIRED_TAG_KEYS
from ..tagging import tag_coverage

logger = logging.getLogger("pave.governance")
router = APIRouter(prefix="/api/governance", tags=["governance"])

RECERT_AGE_DAYS = 90  # owners re-attest assets older than this


def _today() -> datetime.date:
    return datetime.date.today()


def _as_date(v) -> datetime.date | None:
    if not v:
        return None
    # NOTE: datetime.datetime is a SUBCLASS of datetime.date, so this check must come
    # first — otherwise a Postgres TIMESTAMPTZ (datetime) falls through unchanged and
    # `date - datetime` raises TypeError (only on Lakebase; in-memory uses float ts).
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except Exception:  # noqa: BLE001
        return None


def _age_days(asset: dict) -> int | None:
    p = asset.get("provisioned_at")
    if isinstance(p, (int, float)):
        return int((datetime.datetime.now().timestamp() - p) / 86400)
    d = _as_date(p)
    return (_today() - d).days if d else None


@router.get("/sweep")
async def sweep():
    """Find resources that need attention: past sunset, tag drift, orphaned."""
    assets = [a for a in await db.list_assets() if a.get("status") == "ACTIVE"]
    today = _today()
    past_sunset, drift, orphaned = [], [], []
    for a in assets:
        tags = a.get("applied_tags") or {}
        sd = _as_date(a.get("sunset_date"))
        if sd and sd < today:
            past_sunset.append({"asset_id": a["asset_id"], "type": a["type"],
                                "sunset_date": str(sd), "owner_id": a.get("owner_id"),
                                "classification": tags.get("data_classification")})
        cov = tag_coverage(tags, REQUIRED_TAG_KEYS)
        if cov < 1.0:
            missing = [k for k in REQUIRED_TAG_KEYS if not tags.get(k)]
            drift.append({"asset_id": a["asset_id"], "type": a["type"],
                          "coverage": cov, "missing": missing})
        if not a.get("owner_id"):
            orphaned.append({"asset_id": a["asset_id"], "type": a["type"]})
    return {
        "active_assets": len(assets),
        "past_sunset": past_sunset,
        "tag_drift": drift,
        "orphaned": orphaned,
        "clean": len(assets) - len({x["asset_id"] for x in past_sunset + drift + orphaned}),
    }


@router.get("/recertification")
async def recertification():
    """Assets whose owner should re-attest (older than RECERT_AGE_DAYS)."""
    assets = [a for a in await db.list_assets() if a.get("status") == "ACTIVE"]
    due = []
    for a in assets:
        age = _age_days(a)
        if age is not None and age >= RECERT_AGE_DAYS:
            due.append({"asset_id": a["asset_id"], "type": a["type"],
                        "owner_id": a.get("owner_id"), "age_days": age})
    return {"recert_age_days": RECERT_AGE_DAYS, "due": due, "due_count": len(due)}


@router.post("/recertify/{asset_id}")
async def recertify(asset_id: str, user: CurrentUser = Depends(get_current_user)):
    """Owner attests an asset is still needed + correctly classified."""
    asset = await db.update_asset(asset_id, recertified_at=datetime.datetime.now().isoformat())
    if not asset:
        raise NotFoundError(f"asset {asset_id} not found")
    await db.add_audit(actor=user.email, event_type="asset.recertified", asset_id=asset_id,
                       payload={"by": user.email})
    return {"asset_id": asset_id, "recertified_by": user.email}


@router.post("/reclaim/{asset_id}")
async def reclaim(asset_id: str, user: CurrentUser = Depends(get_current_user)):
    """Sunset autopilot: reclaim an expired asset. Classification-aware — restricted
    (PHI/GxP) requires controlled change and is NOT auto-reclaimed."""
    if not user.is_approver:
        raise ApprovalError("reclaim requires an approver/admin")
    assets = await db.list_assets()
    asset = next((a for a in assets if a.get("asset_id") == asset_id), None)
    if not asset:
        raise NotFoundError(f"asset {asset_id} not found")
    classification = (asset.get("applied_tags") or {}).get("data_classification")
    if classification == "restricted":
        await db.add_audit(actor=user.email, event_type="reclaim.blocked", asset_id=asset_id,
                           reason="restricted -> controlled change + retention check required")
        raise ConflictError("restricted/GxP asset requires controlled change + retention check",
                            {"asset_id": asset_id, "classification": classification})
    # Soft-delete first (quarantine), classification-aware.
    await db.update_asset(asset_id, status="DECOMMISSION_REQUESTED")
    await db.add_audit(actor=user.email, event_type="asset.reclaimed", asset_id=asset_id,
                       from_state="ACTIVE", to_state="DECOMMISSION_REQUESTED",
                       reason="past sunset -> autopilot reclaim (soft-delete)")
    return {"asset_id": asset_id, "status": "DECOMMISSION_REQUESTED"}
