"""Form metadata + golden-path templates for the intake UI."""
import asyncio
import logging

from fastapi import APIRouter

from .. import config, naming
from ..providers import _sdk

logger = logging.getLogger("pave.meta")
from ..models import (
    DataClassification, Environment, ResourceType, BUSINESS_DOMAINS, BUSINESS_TAXONOMY,
    COMPLIANCE_SCOPES, REGIONS, DATA_RETENTION_CLASSES, REQUIRED_TAG_KEYS, MEDALLION_LAYERS,
    OPTIONAL_TAG_KEYS, ALLOWED_CUSTOM_TAG_KEYS,
    DEPARTMENTS, LIFECYCLE_STAGES, SLA_TIERS, COST_TYPES, SECURITY_REVIEW_STATUSES,
    AI_PROVIDERS, ALLOWED_AI_MODELS, AI_TASKS, AI_GUARDRAILS, AIRiskTier,
    CLUSTER_ACCESS_MODES, DBR_VERSIONS, NODE_TYPES, RUNTIME_ENGINES, SPOT_POLICIES,
    CATALOG_KINDS, ISOLATION_MODES, APP_COMPUTE_SIZES, APP_BINDABLE_RESOURCES,
    LAKEBASE_OFFERINGS, LAKEBASE_CAPACITIES, PG_VERSIONS, LLM_THROUGHPUT_MODES,
    VS_INDEX_TYPES, VS_EMBEDDING_SOURCES, VS_PIPELINE_TYPES, EMBEDDING_MODELS,
    WAREHOUSE_SIZES, WAREHOUSE_TYPES,
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
        "medallion_layers": MEDALLION_LAYERS,
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
        "warehouse_sizes": WAREHOUSE_SIZES,
        "warehouse_types": WAREHOUSE_TYPES,
        # pre-approved external locations for catalog/schema (env PAVE_EXTERNAL_LOCATIONS,
        # comma-separated names). NEVER free-form s3://; requester picks from this list.
        "pre_approved_locations": config.external_locations(),
        # ---- naming convention (templates surfaced so the form can preview + hint) ----
        "naming_preset": naming._preset(),
        "group_prefix": naming.group_prefix(),
        "naming_templates": {rt: naming.convention_hint(rt) for rt in
                             [r.value for r in ResourceType]},
        "owner_group_template": naming.convention_hint("owner_group"),
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

    This is the same list validation.py enforces on submit, so the picker cannot drift
    from what the server will actually accept.
    """
    out = [{"host": "", "label": "This workspace (default)", "self": True}]
    for h in config.target_workspaces():
        label = h.replace("https://", "").split(".")[0]
        out.append({"host": h, "label": label, "self": False})
    return {"workspaces": out}


@router.get("/groups")
async def groups(workspace: str = ""):
    """Databricks groups for the owning-group picker (the principal PAVE grants to).

    Lists groups visible in the target workspace via workspace SCIM. Best-effort:
    {groups, source, resolvable}. On any failure (no perms / offline / unapproved host)
    returns an empty list with resolvable=False, and the SPA falls back to a free-text
    field + the new-group naming convention. Account-wide listing (cross-workspace /
    new-workspace vending) needs the account service principal — docs/ADMIN_CAPABILITIES §9.
    """
    names = await asyncio.to_thread(_sdk.list_groups, workspace or None)
    return {"groups": names, "source": "workspace_scim" if names else "unavailable",
            "resolvable": bool(names)}


@router.get("/templates")
async def templates():
    return TEMPLATES


@router.get("/posture")
async def posture():
    """What this deployment will and will not actually do.

    The UI renders this as a persistent banner. A governed-provisioning demo is only
    credible if a reviewer can tell, before they trust a green ACTIVE, which resource
    types are really created and which are modelled — and why.
    """
    from ..auth import identity_mode
    from ..database import db
    from ..models import ResourceType
    from ..providers import MODE_REASONS, bind

    # demo_mode resolves lazily on first connection attempt; force it so the banner is
    # accurate on a cold page load rather than optimistically claiming persistence.
    await db.health()

    types = {}
    for rt in [r.value for r in ResourceType]:
        try:
            b = bind(rt)
            types[rt] = {"mode": b.mode, "reason": b.reason,
                         "degraded": b.degraded,
                         "explanation": MODE_REASONS.get(b.reason, b.reason)}
        except Exception as e:  # noqa: BLE001 — never let the banner break the page
            types[rt] = {"mode": "unknown", "reason": "sdk_error",
                         "degraded": True, "explanation": str(e)[:200]}

    real_types = sorted(k for k, v in types.items() if v["mode"] == "real")
    return {
        "environment": config.ENVIRONMENT,
        "identity": identity_mode(),
        "storage": {
            "persistent": not db.demo_mode,
            "backend": "lakebase" if not db.demo_mode else "in-memory",
            "note": ("Requests, approvals and the registry persist in Lakebase."
                     if not db.demo_mode else
                     "Running on in-memory state: everything is lost when the app restarts."),
        },
        "provisioning": {
            "allow_real": config.ALLOW_REAL,
            "mode": config.PROVISION_MODE,
            "separation_of_duties": config.PROVISION_MODE == "job",
            "real_types": real_types,
            "summary": (f"Really creates: {', '.join(real_types)}. Everything else is modelled."
                        if real_types else
                        "Every resource type is modelled; nothing is created in the workspace."),
            "by_type": types,
        },
    }
