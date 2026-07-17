"""Declarative desired-state spec — the 'record as code, execute imperatively' synthesis.

PAVE provisions via the SDK (fast, scales to high request volume — no per-request
DABs/Terraform state). To keep that audit-grade for regulated shops, every request
also emits a canonical, diffable declarative manifest of its resolved desired state.
That manifest is written to the append-only audit log (immutable evidence) and is
retrievable via the API, so a platform/compliance team gets GitOps-style
reproducibility + diffability without an IaC file per request.
"""
from typing import Any, Optional

from ..tagging import build_tag_set
from ..providers import resolve_mode
from ..well_architected import spec_block as _waf_block

API_VERSION = "pave/v1"


def build_desired_state(request: dict[str, Any], assets: Optional[list[dict]] = None) -> dict:
    """Canonical declarative manifest for a request (stable key order for diffing)."""
    resources_in = request.get("resources") or []
    if isinstance(resources_in, str):
        import json
        resources_in = json.loads(resources_in)

    tag_set = build_tag_set(
        request,
        owner_email=request.get("owner_email") or "",
        owner_group=request.get("owner_group") or "",
        cost_center=request.get("cost_center") or "",
    )
    # map provisioned external ids back onto resources where known
    by_type = {}
    for a in (assets or []):
        by_type.setdefault(a.get("type"), []).append(a)

    spec_resources = []
    for r in resources_in:
        rtype = r.get("type")
        match = (by_type.get(rtype) or [None]).pop(0) if by_type.get(rtype) else None
        spec_resources.append({
            "type": rtype,
            "mode": (match or {}).get("mode") or resolve_mode(rtype),
            "config": r.get("config", {}),
            "tags": (match or {}).get("applied_tags") or tag_set,
            "external_id": (match or {}).get("external_id"),
        })

    return {
        "apiVersion": API_VERSION,
        "kind": "ProjectFootprint",
        "metadata": {
            "project_id": request.get("project_id"),
            "project_name": request.get("project_name"),
            "use_case_name": request.get("use_case_name"),
            "request_id": str(request.get("id") or request.get("request_id") or ""),
            "owner_email": request.get("owner_email"),
            "owner_group": request.get("owner_group"),
            "business_owner": request.get("business_owner"),
            "technical_lead": request.get("technical_lead"),
            "backup_owner": request.get("backup_owner"),
            "support_contact": request.get("support_contact"),
            "department": request.get("department"),
            "requested_by": request.get("requester"),
        },
        "spec": {
            "data_classification": request.get("data_classification"),
            "environment": request.get("environment"),
            "lifecycle_stage": request.get("lifecycle_stage"),
            "business_domain": request.get("business_domain"),
            "business_function": request.get("business_function"),
            "business_sub_function": request.get("business_sub_function"),
            "cost_center": request.get("cost_center"),
            "cost_type": request.get("cost_type"),
            "budget_monthly_cap": request.get("budget_monthly_cap"),
            "region": request.get("region"),
            "compliance": {
                "scope": request.get("compliance_scope") or [],
                "gxp_relevant": bool(request.get("gxp_relevant")),
                "contains_phi": bool(request.get("contains_phi")),
                "validated_system": bool(request.get("validated_system")),
                "data_retention": request.get("data_retention"),
                "dpia_ref": request.get("dpia_ref"),
            },
            "reliability": {
                "sla_tier": request.get("sla_tier"),
                "rto_hours": request.get("rto_hours"),
                "rpo_hours": request.get("rpo_hours"),
                "go_live_date": request.get("go_live_date"),
            },
            "risk_tier": request.get("risk_tier"),
            "sunset_date": str(request.get("sunset_date")) if request.get("sunset_date") else None,
            "resources": spec_resources,
        },
        "dependencies": {
            "depends_on": request.get("depends_on") or [],
            "source_systems": request.get("source_systems") or [],
            "consumed_by": request.get("consumed_by") or [],
        },
        "traceability": {
            "change_type": request.get("change_type"),
            "change_ref": request.get("change_ref"),
            "servicenow_ref": request.get("servicenow_ref"),
            "jira_epic": request.get("jira_epic"),
            "confluence_url": request.get("confluence_url"),
            "security_review_status": request.get("security_review_status"),
        },
        "ai_governance": {
            "ai_risk_tier": request.get("ai_risk_tier"),
            "intended_use": request.get("intended_use"),
            "out_of_scope_uses": request.get("out_of_scope_uses"),
            "model_card_ref": request.get("model_card_ref"),
            "human_oversight": bool(request.get("human_oversight")),
        } if any(r.get("type") in ("llm_gateway_endpoint", "vector_search")
                 for r in spec_resources) else None,
        "governance": {
            "managed_by": "self-service-portal",
            "tag_keys": sorted(tag_set.keys()),
        },
        "well_architected": _waf_block(request, assets),
    }


def _tf_slug(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in str(s)).strip("_").lower() or "ws"


def to_terraform(spec: dict) -> str:
    """Emit applyable Terraform for account-level workspace resources in the spec.

    Workspace creation is account-scoped (databricks_mws_* under the accounts provider),
    so PAVE records it as code for an ACCOUNT-ADMIN identity to apply — the SoD boundary.
    Returns "" when the request has no workspace resource.
    """
    resources = (spec.get("spec") or {}).get("resources") or []
    workspaces = [r for r in resources if r.get("type") == "workspace"]
    if not workspaces:
        return ""
    region_default = (spec.get("spec") or {}).get("region") or "us-east-1"
    L = [
        "# ---------------------------------------------------------------------------",
        "# Generated by PAVE — account-level workspace landing zone (record-as-code).",
        "# Apply with an ACCOUNT-ADMIN identity (SoD boundary). Requires pre-provisioned",
        "# credentials / storage / (optional) network configurations in the account.",
        "# ---------------------------------------------------------------------------",
        'terraform { required_providers { databricks = { source = "databricks/databricks" } } }',
        'provider "databricks" {',
        '  host       = "https://accounts.cloud.databricks.com"',
        "  account_id = var.databricks_account_id",
        "}",
        'variable "databricks_account_id" { type = string }',
        "",
    ]
    for i, r in enumerate(workspaces):
        cfg = r.get("config") or {}
        tags = r.get("tags") or {}
        name = cfg.get("deployment_name") or cfg.get("name") or f"pave-ws-{i+1}"
        rn = _tf_slug(name)
        region = cfg.get("region") or region_default
        cred = f'"{cfg["credentials_id"]}"' if cfg.get("credentials_id") else "var.credentials_id"
        stor = f'"{cfg["storage_config_id"]}"' if cfg.get("storage_config_id") else "var.storage_configuration_id"
        tag_lines = "".join(f'\n    {k} = "{v}"' for k, v in tags.items() if v)
        L += [
            f'resource "databricks_mws_workspaces" "{rn}" {{',
            "  account_id      = var.databricks_account_id",
            f'  workspace_name  = "{name}"',
            f'  deployment_name = "{name}"',
            f'  aws_region      = "{region}"',
            f'  pricing_tier    = "{cfg.get("pricing_tier", "ENTERPRISE")}"',
            f"  credentials_id           = {cred}",
            f"  storage_configuration_id = {stor}",
        ]
        if cfg.get("network_id"):
            L.append(f'  network_id      = "{cfg["network_id"]}"')
        L.append(f"  custom_tags = {{{tag_lines}\n  }}")
        L.append("}")
        L.append("")
    return "\n".join(L)


def to_yaml(spec: dict) -> str:
    """Minimal YAML rendering (no PyYAML dependency) for the as-code view."""
    def emit(obj, indent=0):
        pad = "  " * indent
        lines = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (dict, list)) and v:
                    lines.append(f"{pad}{k}:")
                    lines.append(emit(v, indent + 1))
                else:
                    lines.append(f"{pad}{k}: {_scalar(v)}")
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    inner = emit(item, indent + 1).lstrip()
                    lines.append(f"{pad}- {inner}")
                else:
                    lines.append(f"{pad}- {_scalar(item)}")
        return "\n".join(lines)

    def _scalar(v):
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (list, dict)) and not v:
            return "[]" if isinstance(v, list) else "{}"
        return str(v)

    return emit(spec)
