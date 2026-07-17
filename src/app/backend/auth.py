"""Current-user resolution for PAVE.

Two identity sources:
  1. Databricks Apps proxy headers (X-Forwarded-Email/-User/-Preferred-Username) —
     the REAL signed-in identity in a deployed app.
  2. A demo persona (X-Pave-Persona header) — a deliberate demo affordance so one
     person can act as requester / platform approver / security-compliance and
     exercise dual approval. The Apps proxy OVERWRITES X-Forwarded-* but does NOT
     strip custom headers, so X-Pave-Persona survives the proxy (X-Forwarded-Groups
     does not — that's why role can't ride on it in prod).

Role precedence: demo persona (if DEMO_PERSONAS on) -> APPROVERS env / forwarded
groups -> DEV_ROLE. Disable personas in a hardened deploy with DEMO_PERSONAS=0.
"""
import logging
import os
from dataclasses import dataclass

from fastapi import Request

from .config import DEV_USER_EMAIL

logger = logging.getLogger("pave.auth")

DEMO_PERSONAS = os.getenv("DEMO_PERSONAS", "1") in ("1", "true", "True", "yes")
DEV_APPROVERS = {e.strip().lower() for e in os.getenv("DEV_APPROVERS", "").split(",") if e.strip()}
APPROVERS = {e.strip().lower() for e in os.getenv("APPROVERS", "").split(",") if e.strip()}
DEV_ROLE = os.getenv("DEV_ROLE", "")  # force a role locally: requester|approver|admin

# Demo persona -> synthetic (email, groups). Distinct emails enable dual approval.
PERSONA_MAP = {
    "requester":  ("lead.dev@pave.test", ["rwe-clinical", "platform"]),
    "platform":   ("platform@pave.test", ["pave-approvers"]),
    "compliance": ("compliance@pave.test", ["platform-admins"]),
}


@dataclass
class CurrentUser:
    email: str
    groups: list[str]
    is_approver: bool
    is_admin: bool
    persona: str = ""


def _forwarded_email(request: Request) -> str:
    h = request.headers
    return (
        h.get("X-Forwarded-Email")
        or h.get("X-Forwarded-User")
        or h.get("X-Forwarded-Preferred-Username")
        or ""
    ).strip()


def get_current_user(request: Request) -> CurrentUser:
    persona = (request.headers.get("X-Pave-Persona") or "").strip().lower()

    if DEMO_PERSONAS and persona in PERSONA_MAP:
        email, groups = PERSONA_MAP[persona]
    else:
        persona = ""
        email = _forwarded_email(request) or DEV_USER_EMAIL
        groups_hdr = request.headers.get("X-Forwarded-Groups", "")
        groups = [g.strip() for g in groups_hdr.split(",") if g.strip()]

    el = email.lower()
    is_admin = ("platform-admins" in groups) or DEV_ROLE == "admin"
    is_approver = (
        is_admin
        or "pave-approvers" in groups
        or el in DEV_APPROVERS
        or el in APPROVERS
        or DEV_ROLE in ("approver", "admin")
    )
    return CurrentUser(email=email, groups=groups, is_approver=is_approver,
                       is_admin=is_admin, persona=persona)
