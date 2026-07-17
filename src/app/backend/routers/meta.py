"""Form metadata + golden-path templates for the intake UI."""
import os

from fastapi import APIRouter

from ..models import (
    DataClassification, Environment, ResourceType, BUSINESS_DOMAINS, BUSINESS_TAXONOMY,
    COMPLIANCE_SCOPES, REGIONS, DATA_RETENTION_CLASSES, REQUIRED_TAG_KEYS,
    OPTIONAL_TAG_KEYS, ALLOWED_CUSTOM_TAG_KEYS,
    DEPARTMENTS, LIFECYCLE_STAGES, SLA_TIERS, COST_TYPES, SECURITY_REVIEW_STATUSES,
    AI_PROVIDERS, ALLOWED_AI_MODELS, AI_TASKS, AI_GUARDRAILS, AIRiskTier,
    CLUSTER_ACCESS_MODES, DBR_VERSIONS, NODE_TYPES, RUNTIME_ENGINES, SPOT_POLICIES,
    CATALOG_KINDS, ISOLATION_MODES, APP_COMPUTE_SIZES, APP_BINDABLE_RESOURCES,
    LAKEBASE_OFFERINGS, LAKEBASE_CAPACITIES, PG_VERSIONS, LLM_THROUGHPUT_MODES,
    VS_INDEX_TYPES, VS_EMBEDDING_SOURCES, VS_PIPELINE_TYPES, EMBEDDING_MODELS,
)
from ..validation import KNOWN_COST_CENTERS

router = APIRouter(prefix="/api/meta", tags=["meta"])


# Golden-path templates: one click vends a standardized footprint.
TEMPLATES = [
    {
        "id": "standard-dev-project",
        "name": "Standard dev project",
        "description": "A governed sandbox: UC schema + policy-bound job cluster + small app.",
        "defaults": {
            "data_classification": "internal",
            "environment": "dev",
        },
        "resources": [
            {"type": "schema", "config": {}},
            {"type": "job_cluster", "config": {}},
            {"type": "app", "config": {}},
        ],
    },
    {
        "id": "regulated-clinical-project",
        "name": "Regulated clinical project (GxP/PHI)",
        "description": "Restricted footprint: schema + single-user cluster, dual approval + compliance.",
        "defaults": {
            "data_classification": "restricted",
            "environment": "stage",
            "gxp_relevant": True,
            "compliance_scope": ["gxp", "hipaa"],
        },
        "resources": [
            {"type": "schema", "config": {}},
            {"type": "cluster", "config": {"access_mode": "single-user"}},
        ],
    },
    {
        "id": "analytics-lakebase-project",
        "name": "Analytics + Lakebase project",
        "description": "Schema + Lakebase (operational) + app for an analytics team.",
        "defaults": {"data_classification": "confidential", "environment": "test"},
        "resources": [
            {"type": "schema", "config": {}},
            {"type": "lakebase", "config": {}},
            {"type": "app", "config": {}},
        ],
    },
    {
        "id": "governed-genai-project",
        "name": "Governed GenAI project (per-team LLM gateway)",
        "description": "A team's governed LLM gateway endpoint (allow-listed model + PII "
                       "guardrails + rate limits + budget) plus a Vector Search index for RAG.",
        "defaults": {"data_classification": "confidential", "environment": "prod",
                     "ai_risk_tier": "high"},
        "resources": [
            {"type": "llm_gateway_endpoint",
             "config": {"provider": "databricks", "model": "databricks-claude-sonnet-4",
                        "task": "llm/v1/chat", "guardrails": ["pii_redact", "safety"],
                        "rate_limit_qpm": 100, "rate_limit_tpm": 50000,
                        "inference_logging": True, "monthly_token_budget": 5000000,
                        "monthly_cost_cap_usd": 2000}},
            {"type": "vector_search", "config": {}},
            {"type": "schema", "config": {}},
        ],
    },
    {
        "id": "new-workspace-landing-zone",
        "name": "New workspace (landing zone)",
        "description": "Account-level workspace vending: governed intake + account-admin "
                       "approval; created via the Account API / Terraform substrate under SoD.",
        "defaults": {"data_classification": "internal", "environment": "stage"},
        "resources": [
            {"type": "workspace",
             "config": {"region": "us-east-1", "pricing_tier": "ENTERPRISE"}},
        ],
    },
]


@router.get("/form-options")
async def form_options():
    return {
        "data_classifications": [c.value for c in DataClassification],
        "environments": [e.value for e in Environment],
        "resource_types": [r.value for r in ResourceType],
        "business_domains": BUSINESS_DOMAINS,
        "business_taxonomy": BUSINESS_TAXONOMY,
        "compliance_scopes": COMPLIANCE_SCOPES,
        "regions": REGIONS,
        "data_retention_classes": DATA_RETENTION_CLASSES,
        "departments": DEPARTMENTS,
        "lifecycle_stages": LIFECYCLE_STAGES,
        "sla_tiers": SLA_TIERS,
        "cost_types": COST_TYPES,
        "security_review_statuses": SECURITY_REVIEW_STATUSES,
        "ai_providers": AI_PROVIDERS,
        "allowed_ai_models": ALLOWED_AI_MODELS,
        "ai_tasks": AI_TASKS,
        "ai_guardrails": AI_GUARDRAILS,
        "ai_risk_tiers": [t.value for t in AIRiskTier],
        "ai_resource_types": ["llm_gateway_endpoint", "vector_search"],
        "cost_centers": sorted(KNOWN_COST_CENTERS),
        "required_tag_keys": REQUIRED_TAG_KEYS,
        "optional_tag_keys": OPTIONAL_TAG_KEYS,
        "allowed_custom_tag_keys": sorted(ALLOWED_CUSTOM_TAG_KEYS),
        # ---- per-resource governed option vocabularies ----
        "cluster_access_modes": CLUSTER_ACCESS_MODES,
        "dbr_versions": DBR_VERSIONS,
        "node_types": NODE_TYPES,
        "runtime_engines": RUNTIME_ENGINES,
        "spot_policies": SPOT_POLICIES,
        "catalog_kinds": CATALOG_KINDS,
        "isolation_modes": ISOLATION_MODES,
        "app_compute_sizes": APP_COMPUTE_SIZES,
        "app_bindable_resources": APP_BINDABLE_RESOURCES,
        "lakebase_offerings": LAKEBASE_OFFERINGS,
        "lakebase_capacities": LAKEBASE_CAPACITIES,
        "pg_versions": PG_VERSIONS,
        "llm_throughput_modes": LLM_THROUGHPUT_MODES,
        "vs_index_types": VS_INDEX_TYPES,
        "vs_embedding_sources": VS_EMBEDDING_SOURCES,
        "vs_pipeline_types": VS_PIPELINE_TYPES,
        "embedding_models": EMBEDDING_MODELS,
        # pre-approved external locations for catalog/schema (env PAVE_EXTERNAL_LOCATIONS,
        # comma-separated names). NEVER free-form s3://; requester picks from this list.
        "pre_approved_locations": [x.strip() for x in
                                   os.getenv("PAVE_EXTERNAL_LOCATIONS", "").split(",") if x.strip()],
        "acknowledgements": [
            {"key": "cost-ownership", "label": "I accept cost ownership for these resources"},
            {"key": "data-handling", "label": "I will handle data per its classification"},
            {"key": "phi-handling", "label": "I attest to PHI handling controls (if applicable)"},
        ],
    }


@router.get("/workspaces")
async def workspaces():
    """Target workspaces a request can provision INTO (multi-workspace routing).

    Returns [{host, label, self}]. The first entry (empty host) is always the app's own
    workspace — the default and the only one guaranteed to work out of the box.

    ENTITLEMENT SCOPING (production): this list should be filtered to the workspaces the
    REQUESTER's groups are entitled to — do NOT expose the whole account. Enumerate via the
    Account API and intersect with the caller's entitlements. Scaffold below (commented):

        # from databricks.sdk import AccountClient
        # a = AccountClient()                       # needs account-admin identity
        # for ws in a.workspaces.list():
        #     extra.append({"host": f"https://{ws.deployment_name}.cloud.databricks.com",
        #                   "label": ws.workspace_name, "self": False})

    Offline/demo: the app's own workspace plus any hosts in PAVE_TARGET_WORKSPACES
    (comma-separated) so the picker is demoable without account access.
    """
    import os
    out = [{"host": "", "label": "This workspace (default)", "self": True}]
    for h in [x.strip() for x in os.getenv("PAVE_TARGET_WORKSPACES", "").split(",") if x.strip()]:
        label = h.replace("https://", "").split(".")[0]
        out.append({"host": h, "label": label, "self": False})
    return {"workspaces": out}


@router.get("/templates")
async def templates():
    return TEMPLATES
