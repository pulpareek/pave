"""PAVE domain models, controlled vocabularies, and the tagging taxonomy.

These are the single source of truth for the intake form, server-side
validation, risk-tiered routing, and the tag-set builder.
"""
import re
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------
class DataClassification(str, Enum):
    public = "public"
    internal = "internal"
    confidential = "confidential"
    restricted = "restricted"   # PHI / clinical / GxP


class Environment(str, Enum):
    dev = "dev"
    test = "test"
    stage = "stage"
    prod = "prod"


class ResourceType(str, Enum):
    catalog = "catalog"
    schema = "schema"
    app = "app"
    cluster = "cluster"
    job_cluster = "job_cluster"
    lakebase = "lakebase"
    llm_gateway_endpoint = "llm_gateway_endpoint"   # governed LLM serving endpoint (AI Gateway)
    vector_search = "vector_search"                 # vector search endpoint (+ index)
    workspace = "workspace"                         # account-level landing zone (Account API / Terraform substrate)


AI_RESOURCE_TYPES = {"llm_gateway_endpoint", "vector_search"}


class AIRiskTier(str, Enum):
    minimal = "minimal"
    limited = "limited"
    high = "high"
    unacceptable = "unacceptable"   # blocked (EU AI Act)


class RequestStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    PROVISIONING = "PROVISIONING"
    ACTIVE = "ACTIVE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    DECOMMISSION_REQUESTED = "DECOMMISSION_REQUESTED"
    DECOMMISSIONED = "DECOMMISSIONED"


class RiskTier(str, Enum):
    TIER0 = "TIER0"   # fast lane
    TIER1 = "TIER1"   # standard
    TIER2 = "TIER2"   # controlled (dual approval + compliance)


# Controlled value sets surfaced to the form + enforced on the server.
# BUSINESS_DOMAINS is the top level of the org taxonomy — surfaced in the UI as
# "Line of Business" (LOB). Kept as `business_domain` on the wire + tag key for
# backward compatibility (project_id derivation, existing tags, FinOps joins).
BUSINESS_DOMAINS = [
    "clinical", "commercial", "manufacturing", "regulatory",
    "research", "safety", "supply_chain", "platform",
]

# Cascading org taxonomy: Line of Business -> Business Function -> Sub-Function.
# Keyed by BUSINESS_DOMAINS. Snake_case values (become governed tag values). A
# function mapping to [] has no sub-functions (sub-function then optional/hidden).
# Representative life-sciences content; edit to match the org chart.
BUSINESS_TAXONOMY: dict[str, dict[str, list[str]]] = {
    "clinical": {
        "clinical_operations": ["site_management", "trial_master_file", "patient_recruitment"],
        "clinical_data_management": ["edc", "data_review", "coding"],
        "biostatistics": ["statistical_programming", "statistical_analysis"],
        "medical_affairs": ["medical_information", "msl_field", "publications"],
    },
    "commercial": {
        "sales": ["field_sales", "inside_sales", "sales_operations"],
        "marketing": ["brand_marketing", "digital_marketing", "market_access"],
        "market_analytics": ["forecasting", "commercial_insights", "targeting"],
        "customer_engagement": ["crm", "hcp_engagement", "patient_services"],
    },
    "manufacturing": {
        "production": ["drug_substance", "drug_product", "packaging"],
        "process_development": ["upstream", "downstream", "formulation"],
        "manufacturing_science": ["tech_transfer", "process_analytics"],
        "maintenance": ["reliability", "calibration"],
    },
    "regulatory": {
        "regulatory_operations": ["submissions", "publishing", "labeling"],
        "regulatory_affairs": ["cmc", "clinical_regulatory", "post_market"],
        "regulatory_intelligence": ["policy", "health_authority_liaison"],
    },
    "research": {
        "discovery": ["target_id", "lead_optimization", "computational_biology"],
        "translational": ["biomarkers", "pharmacology"],
        "preclinical": ["toxicology", "pk_pd", "bioanalytical"],
        "bioinformatics": ["genomics", "data_science"],
    },
    "safety": {
        "pharmacovigilance": ["case_processing", "signal_detection", "aggregate_reporting"],
        "drug_safety": ["risk_management", "benefit_risk"],
        "epidemiology": ["rwe", "observational_studies"],
    },
    "supply_chain": {
        "planning": ["demand_planning", "supply_planning", "inventory"],
        "logistics": ["distribution", "cold_chain", "serialization"],
        "procurement": ["direct_procurement", "indirect_procurement"],
        "quality_supply": ["gdp", "supplier_quality"],
    },
    "platform": {
        "data_platform": ["data_engineering", "data_governance", "lakehouse_ops"],
        "ml_platform": ["mlops", "feature_store", "model_serving"],
        "analytics_platform": ["bi", "self_service_analytics"],
        "infrastructure": ["cloud_ops", "security", "identity"],
    },
}
COMPLIANCE_SCOPES = ["gxp", "gdpr", "hipaa", "pii", "sox", "pci", "none"]
REGIONS = ["us-east-1", "us-west-2", "eu-west-1", "eu-central-1"]
DATA_RETENTION_CLASSES = ["transient", "1y", "7y", "10y", "gxp-retention", "permanent"]

# ---- expanded enterprise metadata vocabularies ----
DEPARTMENTS = ["r&d", "clinical-dev", "commercial", "manufacturing", "quality",
               "regulatory-affairs", "pharmacovigilance", "it-platform", "data-office"]
LIFECYCLE_STAGES = ["poc", "pilot", "production", "sunset"]
SLA_TIERS = ["tier1", "tier2", "tier3"]          # tier1 = mission-critical
COST_TYPES = ["opex", "capex"]
SECURITY_REVIEW_STATUSES = ["not-started", "in-review", "approved", "exempt"]

# ---- AI governance vocabularies (model allow-list, tasks, guardrails) ----
# Platform-approved models. Databricks-hosted FMs need no external key; external
# providers require a secret (AI_GATEWAY_SECRET_*). Editing this list = the allow-list.
ALLOWED_AI_MODELS = {
    "databricks": ["databricks-claude-sonnet-4", "databricks-meta-llama-3-3-70b-instruct",
                   "databricks-gte-large-en"],
    "openai": ["gpt-4o", "gpt-4o-mini"],
    "anthropic": ["claude-3-5-sonnet"],
}
AI_PROVIDERS = list(ALLOWED_AI_MODELS.keys())
AI_TASKS = ["llm/v1/chat", "llm/v1/completions", "llm/v1/embeddings"]
AI_GUARDRAILS = ["pii_redact", "pii_block", "safety", "jailbreak"]

# ---- per-resource governed option vocabularies (surfaced to the intake form) ----
# Compute (cluster / job_cluster). Access modes use the current Databricks naming
# (Dedicated/Standard/Auto); providers map to data_security_mode.
CLUSTER_ACCESS_MODES = ["auto", "dedicated", "standard"]   # auto|DEDICATED(single-user)|STANDARD(shared)
DBR_VERSIONS = ["15.4.x-scala2.12", "16.4.x-scala2.12", "17.3.x-scala2.13"]  # LTS only
NODE_TYPES = ["m-fleet.xlarge", "m-fleet.2xlarge", "m5d.large", "m5d.xlarge",
              "r-fleet.xlarge", "c-fleet.xlarge", "i3.xlarge"]
RUNTIME_ENGINES = ["PHOTON", "STANDARD"]
SPOT_POLICIES = ["SPOT_WITH_FALLBACK", "ON_DEMAND", "SPOT"]

# Unity Catalog
CATALOG_KINDS = ["managed", "external"]
ISOLATION_MODES = ["auto", "OPEN", "ISOLATED"]

# Databricks Apps
APP_COMPUTE_SIZES = ["MEDIUM", "LARGE", "XLARGE"]
APP_BINDABLE_RESOURCES = ["sql_warehouse", "serving_endpoint", "secret", "database"]

# Lakebase (two offerings — Provisioned is the governed default; Autoscaling is newer)
LAKEBASE_OFFERINGS = ["provisioned", "autoscaling"]
LAKEBASE_CAPACITIES = ["CU_1", "CU_2", "CU_4", "CU_8"]
PG_VERSIONS = ["16", "17"]

# AI Gateway / serving
LLM_THROUGHPUT_MODES = ["pay_per_token", "provisioned"]

# Vector Search
VS_INDEX_TYPES = ["DELTA_SYNC", "DIRECT_ACCESS"]
VS_EMBEDDING_SOURCES = ["managed", "self_managed"]
VS_PIPELINE_TYPES = ["TRIGGERED", "CONTINUOUS"]
EMBEDDING_MODELS = ["databricks-gte-large-en", "databricks-bge-large-en"]

COST_CENTER_RE = re.compile(r"^CC-[0-9]{4,6}$")
PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{2,59}$")
USE_CASE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{2,79}$")
WBS_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{2,29}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# Tag taxonomy (dual-plane: same keys on UC governed tags + compute custom_tags)
# ---------------------------------------------------------------------------
REQUIRED_TAG_KEYS = [
    "cost_center", "business_domain", "data_classification", "environment",
    "project_id", "project_name", "owner_group", "owner_email",
    "managed_by", "request_id", "provisioned_date",
]
OPTIONAL_TAG_KEYS = [
    "compliance_scope", "gxp_relevant", "data_retention_class",
    "sla_tier", "sunset_date", "region",
    # business-context dimensions (org taxonomy + accountable owner) for FinOps
    "use_case_name", "business_function", "business_sub_function", "business_owner",
]
MANAGED_BY_VALUE = "self-service-portal"
# Keys allowed for free-form custom tags (governed-policy vocabulary stand-in).
ALLOWED_CUSTOM_TAG_KEYS = set(OPTIONAL_TAG_KEYS) | {
    "application", "team", "criticality", "chargeback_code",
}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class ResourceRequest(BaseModel):
    type: ResourceType
    # free-form per-resource config (schema name, cluster size, policy id, etc.)
    config: dict[str, Any] = Field(default_factory=dict)


class RequestIn(BaseModel):
    # ---- core ----
    project_name: str
    description: str = ""
    justification: str = ""
    owner_group: str
    cost_center: str
    business_domain: str
    data_classification: DataClassification
    environment: Environment
    region: Optional[str] = None
    parent_catalog: Optional[str] = None
    # Target workspace to provision INTO (host, e.g. https://ws.cloud.databricks.com).
    # Empty/None = the workspace PAVE itself runs in (the default, works out of the box).
    # See docs/ADMIN_CAPABILITIES.md for the multi-workspace routing story.
    target_workspace: Optional[str] = None
    compliance_scope: list[str] = Field(default_factory=list)
    gxp_relevant: bool = False
    contains_phi: bool = False
    sunset_date: Optional[str] = None
    custom_tags: dict[str, str] = Field(default_factory=dict)
    resources: list[ResourceRequest] = Field(default_factory=list)
    acknowledgements: list[str] = Field(default_factory=list)
    template: Optional[str] = None
    # WAF waivers: soft Well-Architected findings the requester accepts with a
    # logged justification -> [{"rule_id": "REL-SUNSET", "justification": "..."}]
    waf_waivers: list[dict[str, str]] = Field(default_factory=list)

    # ---- expanded enterprise metadata (curated high-value set) ----
    # business context (org taxonomy + business identity; business_domain above = LOB)
    use_case_name: Optional[str] = None          # business intent (vs project_name = asset label)
    business_function: Optional[str] = None      # LOB -> function (BUSINESS_TAXONOMY)
    business_sub_function: Optional[str] = None  # function -> sub-function (when present)
    business_owner: Optional[str] = None         # accountable person (email); distinct from owner_group
    # ownership & accountability
    technical_lead: Optional[str] = None        # email
    backup_owner: Optional[str] = None          # email (bus-factor)
    department: Optional[str] = None
    # financial / FinOps
    budget_monthly_cap: Optional[float] = None   # $/month
    cost_type: Optional[str] = None              # opex | capex
    wbs_code: Optional[str] = None               # chargeback (integration-ready)
    # lifecycle & reliability
    lifecycle_stage: Optional[str] = None        # poc | pilot | production | sunset
    sla_tier: Optional[str] = None               # tier1 | tier2 | tier3
    rto_hours: Optional[float] = None            # recovery time objective
    rpo_hours: Optional[float] = None            # recovery point objective
    go_live_date: Optional[str] = None
    # compliance (regulated)
    validated_system: bool = False               # GxP computer-system validation
    dpia_ref: Optional[str] = None               # GDPR DPIA reference
    data_retention: Optional[str] = None
    # support & on-call (research: most consistent field PAVE lacked)
    support_contact: Optional[str] = None        # email / on-call / escalation
    # dependencies & lineage (bidirectional for impact analysis)
    depends_on: list[str] = Field(default_factory=list)      # upstream project_ids / systems
    source_systems: list[str] = Field(default_factory=list)
    consumed_by: list[str] = Field(default_factory=list)     # downstream consumers
    # AI governance (LLMOps + EU AI Act + model card; shown when an AI asset is requested)
    ai_risk_tier: Optional[str] = None           # minimal | limited | high | unacceptable
    intended_use: Optional[str] = None
    out_of_scope_uses: Optional[str] = None
    model_card_ref: Optional[str] = None         # link to model card / eval results
    human_oversight: bool = False                # human-in-the-loop attestation
    # process / traceability (integration-ready references; no live sync yet)
    change_ref: Optional[str] = None             # ServiceNow CHG / CAB record
    servicenow_ref: Optional[str] = None         # CMDB CI / request item
    jira_epic: Optional[str] = None
    confluence_url: Optional[str] = None
    security_review_status: Optional[str] = None  # not-started|in-review|approved|exempt


class RequestOut(BaseModel):
    id: str
    project_id: str
    project_name: str
    requester: str
    owner_id: Optional[str] = None
    owner_group: Optional[str] = None
    owner_email: Optional[str] = None
    cost_center: Optional[str] = None
    business_domain: Optional[str] = None
    data_classification: Optional[str] = None
    environment: Optional[str] = None
    region: Optional[str] = None
    compliance_scope: list[str] = Field(default_factory=list)
    custom_tags: dict[str, Any] = Field(default_factory=dict)
    resources: list[dict[str, Any]] = Field(default_factory=list)
    justification: Optional[str] = None
    status: str
    risk_tier: Optional[str] = None
    created_at: Optional[Any] = None
    updated_at: Optional[Any] = None


class ApprovalIn(BaseModel):
    decision: str                       # "approve" | "reject"
    reason: str = ""
    esignature: str                     # typed full name = 21 CFR Part 11-style e-sign


class ReassignIn(BaseModel):
    project_id: Optional[str] = None
    old_owner_email: Optional[str] = None
    new_owner_email: str
    new_owner_group: str = ""
    new_cost_center: str = ""
    esignature: str = ""
