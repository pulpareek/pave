"""Server-side validation of intake requests.

Mirrors the client-side checks but is authoritative: validates against controlled
vocabularies, formats, cross-field rules, and (stubbed) authoritative sources
(SCIM group membership, finance cost-center list). Invalid requests are blocked
before they can reach an approver.
"""
import os

from .models import (
    RequestIn, DataClassification, Environment, BUSINESS_DOMAINS, BUSINESS_TAXONOMY,
    COMPLIANCE_SCOPES,
    REGIONS, COST_CENTER_RE, PROJECT_NAME_RE, USE_CASE_NAME_RE, ALLOWED_CUSTOM_TAG_KEYS,
    DEPARTMENTS, LIFECYCLE_STAGES, SLA_TIERS, COST_TYPES, SECURITY_REVIEW_STATUSES,
    DATA_RETENTION_CLASSES, WBS_RE, EMAIL_RE,
    AI_RESOURCE_TYPES, ALLOWED_AI_MODELS, AI_PROVIDERS, AIRiskTier,
    CLUSTER_ACCESS_MODES, DBR_VERSIONS, NODE_TYPES, RUNTIME_ENGINES, SPOT_POLICIES,
    CATALOG_KINDS, ISOLATION_MODES, APP_COMPUTE_SIZES, LAKEBASE_OFFERINGS,
    LAKEBASE_CAPACITIES, PG_VERSIONS, LLM_THROUGHPUT_MODES,
    VS_INDEX_TYPES, VS_EMBEDDING_SOURCES, VS_PIPELINE_TYPES,
)

# Stubbed authoritative sources (replace with SCIM / finance API in production).
# Comma-separated overrides via env keep the demo flexible.
KNOWN_COST_CENTERS = {c.strip() for c in os.getenv(
    "KNOWN_COST_CENTERS", "CC-1001,CC-1002,CC-2034,CC-4500,CC-9100").split(",") if c.strip()}


def _member_of(requester: str, group: str) -> bool:
    """Stub SCIM membership check. In demo mode everyone is a member; set
    STRICT_GROUP_CHECK=1 + GROUP_MEMBERS_JSON to enforce."""
    if os.getenv("STRICT_GROUP_CHECK", "0") != "1":
        return True
    import json
    members = json.loads(os.getenv("GROUP_MEMBERS_JSON", "{}"))
    return requester in members.get(group, [])


def validate_request(payload: RequestIn, requester: str) -> list[str]:
    """Return a list of human-readable validation errors (empty == valid)."""
    errors: list[str] = []

    if not PROJECT_NAME_RE.match(payload.project_name or ""):
        errors.append("project_name must be 3-60 chars (letters, digits, space, _ or -)")
    if len(payload.description.strip()) < 20:
        errors.append("description must be at least 20 characters")
    if len(payload.justification.strip()) < 30:
        errors.append("business justification must be at least 30 characters")

    if not COST_CENTER_RE.match(payload.cost_center or ""):
        errors.append("cost_center must match CC-#### (e.g. CC-1001)")
    elif payload.cost_center not in KNOWN_COST_CENTERS:
        errors.append(f"cost_center {payload.cost_center} is not in the finance list")

    if payload.business_domain not in BUSINESS_DOMAINS:
        errors.append(f"business_domain must be one of {BUSINESS_DOMAINS}")

    # Business context: use-case name + cascading org taxonomy + accountable owner.
    if not USE_CASE_NAME_RE.match(payload.use_case_name or ""):
        errors.append("use_case_name must be 3-80 chars (letters, digits, space, _ or -)")
    if not payload.business_owner:
        errors.append("business_owner (email) is required")
    elif not EMAIL_RE.match(payload.business_owner):
        errors.append("business_owner must be a valid email")
    # business_function must belong to the chosen LOB; sub-function (when the
    # function has any) must belong to the chosen function.
    functions = BUSINESS_TAXONOMY.get(payload.business_domain, {})
    if not payload.business_function:
        errors.append("business_function is required")
    elif payload.business_function not in functions:
        errors.append(
            f"business_function '{payload.business_function}' is not valid for "
            f"line of business '{payload.business_domain}'")
    else:
        subs = functions.get(payload.business_function, [])
        if subs and not payload.business_sub_function:
            errors.append(
                f"business_sub_function is required for function "
                f"'{payload.business_function}'")
        elif payload.business_sub_function and payload.business_sub_function not in subs:
            errors.append(
                f"business_sub_function '{payload.business_sub_function}' is not valid "
                f"for function '{payload.business_function}'")

    for s in payload.compliance_scope:
        if s not in COMPLIANCE_SCOPES:
            errors.append(f"compliance_scope '{s}' invalid; allowed {COMPLIANCE_SCOPES}")

    if payload.region and payload.region not in REGIONS:
        errors.append(f"region must be one of {REGIONS}")

    if not _member_of(requester, payload.owner_group):
        errors.append(f"requester {requester} is not a member of owning group {payload.owner_group}")

    # Custom tag keys must be in the governed vocabulary (no free-form keys).
    for k in payload.custom_tags:
        if k.strip().lower() not in ALLOWED_CUSTOM_TAG_KEYS:
            errors.append(f"custom tag key '{k}' is not an allowed governed key")

    if not payload.resources:
        errors.append("at least one resource must be requested")

    # ---- cross-field rules ----
    restricted = payload.data_classification == DataClassification.restricted
    if restricted:
        if not payload.gxp_relevant and "gxp" not in payload.compliance_scope:
            # restricted often implies regulated handling; warn via error to force intent
            pass
        if payload.contains_phi and "phi-handling" not in [a.lower() for a in payload.acknowledgements]:
            errors.append("PHI handling attestation required when contains_phi is set")
        # restricted -> single-user cluster access is now owned by the WAF control plane
        # (SEC-SINGLEUSER-DEFAULT injects it; SEC-SINGLEUSER-GATE blocks explicit conflicts).

    if payload.environment in (Environment.dev, Environment.test):
        # sandboxes must declare a sunset date
        if not payload.sunset_date:
            errors.append("a sunset_date is required for dev/test (sandbox) environments")

    if any(r.type.value == "catalog" for r in payload.resources):
        # creating a new catalog is an escalation, but still must be flagged
        pass

    if "cost-ownership" not in [a.lower() for a in payload.acknowledgements]:
        errors.append("cost-ownership acknowledgement is required")

    # ---- expanded enterprise metadata: format + controlled-vocab checks ----
    for label, val in (("technical_lead", payload.technical_lead),
                       ("backup_owner", payload.backup_owner),
                       ("support_contact", payload.support_contact)):
        if val and not EMAIL_RE.match(val):
            errors.append(f"{label} must be a valid email")
    if payload.department and payload.department not in DEPARTMENTS:
        errors.append(f"department must be one of {DEPARTMENTS}")
    if payload.cost_type and payload.cost_type not in COST_TYPES:
        errors.append(f"cost_type must be one of {COST_TYPES}")
    if payload.lifecycle_stage and payload.lifecycle_stage not in LIFECYCLE_STAGES:
        errors.append(f"lifecycle_stage must be one of {LIFECYCLE_STAGES}")
    if payload.sla_tier and payload.sla_tier not in SLA_TIERS:
        errors.append(f"sla_tier must be one of {SLA_TIERS}")
    if payload.security_review_status and payload.security_review_status not in SECURITY_REVIEW_STATUSES:
        errors.append(f"security_review_status must be one of {SECURITY_REVIEW_STATUSES}")
    if payload.data_retention and payload.data_retention not in DATA_RETENTION_CLASSES:
        errors.append(f"data_retention must be one of {DATA_RETENTION_CLASSES}")
    if payload.wbs_code and not WBS_RE.match(payload.wbs_code):
        errors.append("wbs_code format invalid (uppercase letters/digits/.- , 3-30 chars)")
    if payload.budget_monthly_cap is not None and payload.budget_monthly_cap < 0:
        errors.append("budget_monthly_cap must be >= 0")

    # ---- tiered requirements (don't tax low-risk; demand more for prod/tier1/regulated) ----
    prod_or_critical = payload.environment == Environment.prod or payload.sla_tier == "tier1"
    if prod_or_critical:
        if not payload.backup_owner:
            errors.append("backup_owner is required for production / tier1 (bus-factor)")
        if payload.rto_hours is None or payload.rpo_hours is None:
            errors.append("RTO and RPO are required for production / tier1")
        if not payload.security_review_status:
            errors.append("security_review_status is required for production / tier1")
        if not payload.support_contact:
            errors.append("support_contact (on-call/escalation) is required for production / tier1")

    regulated = (payload.data_classification == DataClassification.restricted
                 or payload.gxp_relevant or "gxp" in payload.compliance_scope)
    if regulated:
        if not payload.validated_system:
            errors.append("validated_system attestation required for restricted/GxP")
        if not payload.data_retention:
            errors.append("data_retention is required for restricted/GxP")
    if "gdpr" in payload.compliance_scope and not payload.dpia_ref:
        errors.append("a DPIA reference is required when GDPR is in scope")

    # ---- AI asset governance (LLMOps + EU AI Act) ----
    ai_resources = [r for r in payload.resources if r.type.value in AI_RESOURCE_TYPES]
    if ai_resources:
        if payload.ai_risk_tier not in [t.value for t in AIRiskTier]:
            errors.append(f"ai_risk_tier is required for AI assets ({[t.value for t in AIRiskTier]})")
        if payload.ai_risk_tier == AIRiskTier.unacceptable.value:
            errors.append("ai_risk_tier 'unacceptable' is prohibited (EU AI Act) — request blocked")
        if not payload.intended_use:
            errors.append("intended_use is required for AI assets (use-case registry / model card)")
        for r in ai_resources:
            cfg = r.config or {}
            if r.type.value == "llm_gateway_endpoint":
                provider = cfg.get("provider")
                model = cfg.get("model")
                if provider not in AI_PROVIDERS:
                    errors.append(f"AI model provider must be one of {AI_PROVIDERS}")
                elif model not in ALLOWED_AI_MODELS.get(provider, []):
                    errors.append(f"model '{model}' is not allow-listed for {provider} "
                                  f"(allowed: {ALLOWED_AI_MODELS.get(provider, [])})")
                external = provider in ("openai", "anthropic")
                guardrails = cfg.get("guardrails") or []
                # external model OR PHI/high-risk -> PII guardrail required
                if (external or payload.contains_phi or payload.ai_risk_tier == "high") \
                        and not any(g in ("pii_block", "pii_redact") for g in guardrails):
                    errors.append("a PII guardrail (pii_block or pii_redact) is required for "
                                  "external-model / PHI / high-risk LLM endpoints")
            if r.type.value == "vector_search" and not cfg.get("source_table"):
                # source table optional for endpoint-only; warn-as-error only if index requested
                if cfg.get("create_index"):
                    errors.append("vector_search with create_index needs a source_table")

    # Per-resource governed-option membership checks (block hand-crafted payloads that
    # inject values outside the allow-lists; hard cost limits stay policy-side).
    errors.extend(_validate_resource_options(payload.resources))
    return errors


def _validate_resource_options(resources) -> list[str]:
    errs: list[str] = []
    _in = lambda v, allowed, label: None if (v in (None, "") or v in allowed) else \
        errs.append(f"{label} '{v}' is not allowed (one of {allowed})")
    for r in resources:
        rt = r.type.value
        cfg = r.config or {}
        if rt == "catalog":
            _in(cfg.get("kind"), CATALOG_KINDS, "catalog kind")
            _in(cfg.get("isolation_mode"), ISOLATION_MODES, "isolation_mode")
        elif rt in ("cluster", "job_cluster"):
            _in(cfg.get("spark_version"), DBR_VERSIONS, "spark_version")
            _in(cfg.get("node_type_id"), NODE_TYPES, "node_type_id")
            _in(cfg.get("runtime_engine"), RUNTIME_ENGINES, "runtime_engine")
            if rt == "cluster":
                _in(cfg.get("access_mode"), CLUSTER_ACCESS_MODES + ["single-user"], "access_mode")
            else:
                _in(cfg.get("availability"), SPOT_POLICIES, "availability")
        elif rt == "app":
            _in(cfg.get("compute_size"), APP_COMPUTE_SIZES, "compute_size")
        elif rt == "lakebase":
            _in(cfg.get("offering"), LAKEBASE_OFFERINGS, "lakebase offering")
            _in(cfg.get("pg_version"), PG_VERSIONS, "pg_version")
            _in(cfg.get("capacity"), LAKEBASE_CAPACITIES, "lakebase capacity")
        elif rt == "llm_gateway_endpoint":
            _in(cfg.get("throughput_mode"), LLM_THROUGHPUT_MODES, "throughput_mode")
        elif rt == "vector_search":
            _in(cfg.get("index_type"), VS_INDEX_TYPES, "index_type")
            _in(cfg.get("embedding_source"), VS_EMBEDDING_SOURCES, "embedding_source")
            _in(cfg.get("pipeline_type"), VS_PIPELINE_TYPES, "pipeline_type")
    return errs
