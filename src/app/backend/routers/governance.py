"""Day-2 governance: sunset autopilot, drift/orphan sweep, recertification.

Industry pattern (AWS AFT TTL + FinOps untagged-sweeps + Cortex/Port scorecards):
keep vended resources healthy AFTER provisioning, not just at creation. All
read-only except reclaim/recertify, which are classification-aware and audited.
"""
import asyncio
import datetime
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import get_current_user, CurrentUser
from ..database import db
from ..exceptions import ApprovalError, NotFoundError, ConflictError, ValidationError
from ..models import REQUIRED_TAG_KEYS
from ..providers import get_provider
from ..tagging import tag_coverage

logger = logging.getLogger("pave.governance")
router = APIRouter(prefix="/api/governance", tags=["governance"])

RECERT_AGE_DAYS = 90      # owners re-attest assets older than this
MAX_EXTENSION_DAYS = 180  # a sunset extension is a reprieve, not a renewal


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


@router.post("/reconcile")
async def reconcile(user: CurrentUser = Depends(get_current_user)):
    """Diff the registry against the live world and report what no longer matches.

    This is the loop that makes a registry-as-desired-state design defensible without an
    IaC state file: read-only, so it runs with a low-privilege identity, and it reports
    the three cases a platform team chases — drifted tags, resources deleted out of band,
    and resources that exist with no registry row at all.
    """
    from .. import config
    from ..services import reconcile as rc

    assets = await db.list_assets()
    result = rc.reconcile_assets(assets)
    known = {a.get("external_id") for a in assets if a.get("external_id")}
    untracked = rc.find_untracked(known, parent_catalog=config.PARENT_CATALOG)

    findings = len(result["drifted"]) + len(result["missing"]) + len(untracked)
    await db.add_audit(
        actor=user.email, event_type="governance.reconciled",
        reason=f"{findings} finding(s) across {len(assets)} tracked asset(s)",
        payload={"drifted": len(result["drifted"]), "missing": len(result["missing"]),
                 "untracked": len(untracked), "in_sync": result["in_sync"],
                 "unreadable": len(result["unreadable"])})
    return {
        "checked": len(assets),
        "in_sync": result["in_sync"],
        "drifted": result["drifted"],
        "missing": result["missing"],
        "untracked": untracked,
        # Assets we could not read (no permission, or no read implemented) are reported
        # separately: "we could not check" is not the same claim as "it is fine".
        "unreadable": result["unreadable"],
        "findings": findings,
    }


@router.post("/drift/simulate/{asset_id}")
async def simulate_drift(asset_id: str, deleted: bool = False,
                         untag: str = "", user: CurrentUser = Depends(get_current_user)):
    """Inject drift into a modelled asset so the reconcile loop has something to find.

    Demo instrument: it makes the drift story tellable on a laptop with no workspace
    access. `untag` is a comma-separated list of tag keys to blank out; `deleted=true`
    makes the resource vanish.
    """
    if not user.is_approver:
        raise ApprovalError("simulating drift requires an approver/admin")
    asset = next((a for a in await db.list_assets() if a.get("asset_id") == asset_id), None)
    if not asset:
        raise NotFoundError(f"asset {asset_id} not found")
    from ..services import reconcile as rc
    tags = {k.strip(): "" for k in untag.split(",") if k.strip()} or None
    entry = rc.inject_drift(asset_id, deleted=deleted, tags=tags)
    await db.add_audit(actor=user.email, event_type="governance.drift_simulated",
                       asset_id=asset_id, reason="demo instrument: injected drift",
                       payload=entry)
    return {"asset_id": asset_id, "injected": entry}


@router.delete("/drift/simulate")
async def clear_simulated_drift(user: CurrentUser = Depends(get_current_user)):
    from ..services import reconcile as rc
    rc.clear_drift()
    return {"cleared": True}


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

    # Actually tear the resource down. Marking the row DECOMMISSION_REQUESTED and stopping
    # there was the whole bug in "sunset autopilot": the registry said reclaimed while the
    # resource kept running and kept billing.
    await db.update_asset(asset_id, status="DECOMMISSION_REQUESTED")
    request = await db.get_request(str(asset.get("request_id"))) if asset.get("request_id") else None
    try:
        provider, _ = get_provider(asset["type"], mode=asset.get("mode"))
        await asyncio.to_thread(
            provider.decommission, asset=asset,
            context={"target_workspace": (request or {}).get("target_workspace")})
    except Exception as e:  # noqa: BLE001 — leave it quarantined for a human to finish
        await db.add_audit(actor=user.email, event_type="reclaim.failed", asset_id=asset_id,
                           to_state="DECOMMISSION_REQUESTED", reason=str(e)[:400])
        return {"asset_id": asset_id, "status": "DECOMMISSION_REQUESTED",
                "error": f"teardown failed, asset quarantined for manual review: {e}"}

    await db.update_asset(asset_id, status="DECOMMISSIONED",
                          decommissioned_at=datetime.datetime.now(datetime.timezone.utc))
    await db.add_audit(actor=user.email, event_type="asset.reclaimed", asset_id=asset_id,
                       from_state="ACTIVE", to_state="DECOMMISSIONED",
                       reason="past sunset -> autopilot reclaim")
    return {"asset_id": asset_id, "status": "DECOMMISSIONED"}


class ExtendIn(BaseModel):
    sunset_date: str            # new sunset date (ISO yyyy-mm-dd)
    justification: str = ""


@router.post("/extend/{asset_id}")
async def extend_sunset(asset_id: str, payload: ExtendIn,
                        user: CurrentUser = Depends(get_current_user)):
    """Push an asset's sunset date out.

    The counterpart to reclaim: without it the only way to keep a still-needed sandbox is
    to let the autopilot take it and re-request, which teaches people to game the sunset
    date at intake. Extensions are capped and audited, so the lifecycle stays a decision
    rather than a formality.
    """
    asset = next((a for a in await db.list_assets() if a.get("asset_id") == asset_id), None)
    if not asset:
        raise NotFoundError(f"asset {asset_id} not found")
    new_date = _as_date(payload.sunset_date)
    if not new_date:
        raise ValidationError("sunset_date must be an ISO date (yyyy-mm-dd)")
    if new_date <= _today():
        raise ValidationError("the new sunset date must be in the future")
    if (new_date - _today()).days > MAX_EXTENSION_DAYS:
        raise ValidationError(
            f"a sunset extension is capped at {MAX_EXTENSION_DAYS} days "
            f"({MAX_EXTENSION_DAYS // 30} months); request a longer-lived project instead")
    owner = (asset.get("owner_id") or "").lower()
    if not (user.is_approver or owner == (user.email or "").lower()):
        raise ApprovalError("only the asset owner or an approver can extend a sunset date")

    previous = asset.get("sunset_date")
    await db.update_asset(asset_id, sunset_date=new_date)
    await db.add_audit(actor=user.email, event_type="asset.sunset_extended", asset_id=asset_id,
                       reason=payload.justification or "no justification given",
                       payload={"from": str(previous), "to": str(new_date),
                                "extended_by_days": (new_date - _today()).days})
    return {"asset_id": asset_id, "sunset_date": str(new_date), "previous": str(previous)}
