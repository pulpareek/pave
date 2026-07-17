"""Canonical tag-set builder.

A single logical tag set is derived from the request + owner registry and applied
identically across both planes (UC governed tags for data/AI assets; compute
custom_tags for clusters/jobs/serverless). Because tags are *derived* from the
registry, ownership reassignment automatically re-emits correct tags.

Rules enforced here:
  - lowercase snake_case keys
  - no PII/secrets in tags (we only emit governed keys)
  - cost_center format validated upstream (models.COST_CENTER_RE)
  - never emit a reserved `Name` key (breaks cluster auto-termination tracking)
"""
import datetime
from typing import Any

from .models import MANAGED_BY_VALUE, ALLOWED_CUSTOM_TAG_KEYS

RESERVED_KEYS = {"name"}  # case-insensitive guard


def _today() -> str:
    return datetime.date.today().isoformat()


def build_tag_set(request: dict[str, Any], *, owner_email: str = "", owner_group: str = "",
                  cost_center: str = "") -> dict[str, str]:
    """Build the full enterprise tag set for a resource from a request record.

    `owner_*` / `cost_center` overrides let the reassignment job re-derive tags
    for a new owner without mutating the original request.
    """
    custom = request.get("custom_tags") or {}
    if isinstance(custom, str):  # demo-mode jsonb may arrive as text
        import json
        try:
            custom = json.loads(custom)
        except Exception:  # noqa: BLE001
            custom = {}

    tags: dict[str, str] = {
        "cost_center": cost_center or request.get("cost_center") or "",
        "business_domain": request.get("business_domain") or "",
        "data_classification": request.get("data_classification") or "",
        "environment": request.get("environment") or "",
        "project_id": request.get("project_id") or "",
        "project_name": request.get("project_name") or "",
        "owner_group": owner_group or request.get("owner_group") or "",
        "owner_email": owner_email or request.get("owner_email") or "",
        "managed_by": MANAGED_BY_VALUE,
        "request_id": str(request.get("id") or request.get("request_id") or ""),
        "provisioned_date": _today(),
    }

    # Conditional / optional governed keys
    scope = request.get("compliance_scope") or []
    if scope:
        tags["compliance_scope"] = ",".join(scope)
    if request.get("gxp_relevant"):
        tags["gxp_relevant"] = "true"
    if request.get("region"):
        tags["region"] = request["region"]
    if request.get("sunset_date"):
        tags["sunset_date"] = str(request["sunset_date"])
    # expanded enterprise metadata that belongs on the resource for FinOps/ops
    for k in ("sla_tier", "lifecycle_stage", "data_retention", "cost_type", "ai_risk_tier",
              "use_case_name", "business_function", "business_sub_function", "business_owner"):
        if request.get(k):
            tags[k] = str(request[k])

    # Allow-listed custom tags (keys must be in the governed vocabulary)
    for k, v in custom.items():
        lk = str(k).strip().lower()
        if lk in RESERVED_KEYS:
            continue
        if lk in ALLOWED_CUSTOM_TAG_KEYS and v not in (None, ""):
            tags[lk] = str(v)

    # Drop empties + enforce lowercase keys; never emit reserved keys.
    return {k.lower(): v for k, v in tags.items() if v not in (None, "") and k.lower() not in RESERVED_KEYS}


def tag_coverage(applied: dict[str, str], required: list[str]) -> float:
    """Fraction of required keys present (the FinOps north-star KPI)."""
    if not required:
        return 1.0
    present = sum(1 for k in required if applied.get(k))
    return round(present / len(required), 3)
