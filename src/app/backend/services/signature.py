"""Electronic signature manifestation, bound to the record it signs.

21 CFR Part 11 asks two things of a signature that a typed name in a TEXT column does not
provide on its own:

  §11.50 — the signed record must display the signer's printed name, the date and time of
           signing, and the MEANING of the signature (approval, review, responsibility).
  §11.70 — the signature must be linked to its record so it cannot be excised, copied or
           transferred to falsify another record.

PAVE satisfies the link by hashing the canonical desired-state spec (services/spec.py) at
the moment of signing. Because that manifest is the exact footprint provisioning will
create, a signature is verifiable against what was actually approved: if the request is
edited afterwards the digest no longer matches, and `verify` says so.

This is a demonstrable Part 11 *pattern*, not a validated system. It is deliberately not
a cryptographic identity claim — the signer is authenticated by the workspace, and in a
demo deployment by a switchable persona.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ..models import SIGNATURE_MEANINGS

logger = logging.getLogger("pave.signature")


def record_digest(request: dict[str, Any], assets: Optional[list[dict]] = None) -> str:
    """SHA-256 over the canonical desired-state manifest for a request.

    Canonicalized with sorted keys and no insignificant whitespace so the same footprint
    always hashes the same way, independent of dict ordering or storage round-trips.
    """
    from .spec import build_desired_state
    spec = build_desired_state(request, assets)
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def manifest(*, signer: str, printed_name: str, decision: str, gate: str,
             request: dict[str, Any], reason: str = "",
             meaning: str = "") -> dict[str, Any]:
    """Build the full signature manifestation to store alongside the approval.

    Everything Part 11 §11.50 wants displayed, plus the §11.70 link to the record.
    """
    if not meaning:
        meaning = SIGNATURE_MEANINGS.get(
            "approved" if decision == "approve" else "rejected",
            SIGNATURE_MEANINGS["approved"])
    return {
        "printed_name": printed_name.strip(),
        "signer": signer,
        "meaning": meaning,
        "decision": decision,
        "gate": gate,
        "reason": reason,
        # UTC, ISO-8601, second precision — an unambiguous instant, not a local time.
        "signed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "record": {
            "request_id": str(request.get("id") or ""),
            "project_id": request.get("project_id") or "",
            "digest_algorithm": "sha256",
            "digest": record_digest(request),
            "digest_covers": "canonical desired-state spec (pave/v1)",
        },
    }


def verify(signature: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """Re-derive the digest and report whether the signed record still matches.

    A mismatch is not necessarily tampering — a request amended after approval (adding
    resources, for example) legitimately changes its footprint. It does mean the earlier
    signature no longer covers the current state, which is precisely what an auditor
    needs to be told rather than left to assume.
    """
    stored = ((signature or {}).get("record") or {}).get("digest") or ""
    if not stored:
        return {"bound": False, "matches": False,
                "detail": "signature predates record binding; it attests to an approval "
                          "decision but is not linked to a specific footprint"}
    current = record_digest(request)
    return {
        "bound": True,
        "matches": stored == current,
        "signed_digest": stored,
        "current_digest": current,
        "detail": ("the approved footprint is unchanged" if stored == current else
                   "the request footprint changed after this signature was applied; the "
                   "signature covers the earlier state"),
    }
