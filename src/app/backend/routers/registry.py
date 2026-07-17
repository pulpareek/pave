"""Asset & ownership registry (the CMDB) + audit browser."""
from fastapi import APIRouter

from ..database import db

router = APIRouter(prefix="/api", tags=["registry"])


@router.get("/assets")
async def list_assets(owner_id: str | None = None, project_id: str | None = None,
                      status: str | None = None):
    return await db.list_assets(owner_id=owner_id, project_id=project_id, status=status)


@router.get("/assets/by-owner")
async def assets_by_owner():
    """Group active assets by owner for the ownership browser."""
    assets = await db.list_assets()
    grouped: dict[str, list] = {}
    for a in assets:
        grouped.setdefault(a.get("owner_id") or "(unassigned)", []).append(a)
    return grouped


@router.get("/audit")
async def audit(limit: int = 200):
    return await db.list_audit(limit=limit)
