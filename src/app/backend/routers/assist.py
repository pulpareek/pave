"""AI co-pilot endpoints: NL intake drafting."""
from fastapi import APIRouter
from pydantic import BaseModel

from ..services.assistant import draft_request

router = APIRouter(prefix="/api/assist", tags=["assist"])


class DraftIn(BaseModel):
    text: str


@router.post("/intake")
async def intake(payload: DraftIn):
    """Turn plain English into a governed request draft (FM API + heuristic)."""
    return draft_request(payload.text)
