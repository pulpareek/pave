"""NL intake co-pilot.

Turns a plain-English description into a governed request draft. Uses the
Databricks Foundation Model API when available (LLM_ENDPOINT, e.g.
databricks-claude-sonnet-4); falls back to a deterministic heuristic parser so
the feature always works offline / in demo mode. The heuristic is the reliable
core; the FM call is an enhancement that refines it.
"""
import json
import logging
import os
import re

from ..models import BUSINESS_DOMAINS, BUSINESS_TAXONOMY

logger = logging.getLogger("pave.assistant")

LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "databricks-claude-sonnet-4")

_RESOURCE_HINTS = {
    "schema": ["schema", "table", "data", "catalog data", "dataset", "delta"],
    "catalog": ["catalog", "new catalog"],
    "app": ["app", "dashboard", "portal", "ui", "frontend", "web"],
    "cluster": ["cluster", "interactive", "all-purpose", "notebook compute"],
    "job_cluster": ["job", "pipeline", "etl", "batch", "scheduled"],
    "lakebase": ["lakebase", "postgres", "oltp", "operational database", "transactional"],
}


def _heuristic(text: str) -> dict:
    t = (text or "").lower()

    # classification
    if any(w in t for w in ["phi", "clinical", "patient", "gxp", "restricted", "hipaa", "trial"]):
        classification = "restricted"
    elif any(w in t for w in ["confidential", "commercial", "ip", "proprietary", "financial"]):
        classification = "confidential"
    elif "public" in t:
        classification = "public"
    else:
        classification = "internal"

    compliance = [s for s in ["gxp", "hipaa", "gdpr", "pii"] if s in t]
    gxp = "gxp" in t or "clinical" in t or "trial" in t
    phi = "phi" in t or "patient" in t or ("clinical" in t and "data" in t)

    if any(w in t for w in ["prod", "production"]):
        environment = "prod"
    elif any(w in t for w in ["stage", "staging", "pre-prod"]):
        environment = "stage"
    elif "test" in t or "uat" in t:
        environment = "test"
    else:
        environment = "dev"

    domain = next((d for d in BUSINESS_DOMAINS if d in t), None)
    if not domain:
        if "trial" in t or "clinical" in t or "rwe" in t:
            domain = "clinical"
        elif "sales" in t or "marketing" in t:
            domain = "commercial"
        else:
            domain = "platform"

    resources = []
    for rtype, hints in _RESOURCE_HINTS.items():
        if any(h in t for h in hints):
            resources.append({"type": rtype, "config": {}})
    if not resources:
        resources = [{"type": "schema", "config": {}}]
    # restricted clusters must be single-user
    for r in resources:
        if r["type"] == "cluster" and classification == "restricted":
            r["config"]["access_mode"] = "single-user"

    # project name guess: first capitalized-ish phrase or a trimmed prefix
    name = re.sub(r"[^A-Za-z0-9 _-]", "", (text or "").strip())[:50] or "New Project"
    name = " ".join(name.split()[:6]) or "New Project"

    # business function: pick the first function for the guessed LOB (best-effort).
    functions = BUSINESS_TAXONOMY.get(domain, {})
    business_function = next(iter(functions), None)

    return {
        "project_name": name,
        "use_case_name": name,
        "description": text.strip(),
        "justification": f"Self-service request drafted from: {text.strip()}",
        "business_domain": domain,
        "business_function": business_function,
        "data_classification": classification,
        "environment": environment,
        "compliance_scope": compliance,
        "gxp_relevant": gxp,
        "contains_phi": phi,
        "resources": resources,
        "_source": "heuristic",
        "_rationale": _rationale(classification, environment, compliance, resources),
    }


def _rationale(classification, environment, compliance, resources) -> list[str]:
    out = [f"Classified **{classification}** and routed to **{environment}**."]
    if classification == "restricted":
        out.append("Restricted -> single-user compute, dual approval + compliance review.")
    if compliance:
        out.append(f"Compliance scope: {', '.join(compliance)}.")
    out.append("Resources: " + ", ".join(r["type"] for r in resources) + ".")
    return out


_SYS = """You are PAVE's intake co-pilot for a regulated life-sciences platform team.
Convert the user's plain-English request into a JSON object with keys:
project_name, use_case_name (short business-intent name), description, justification,
business_domain (one of: {domains}), business_function (a function within that LOB),
data_classification (public|internal|confidential|restricted),
environment (dev|test|stage|prod), compliance_scope (subset of gxp,hipaa,gdpr,pii),
gxp_relevant (bool), contains_phi (bool),
resources (list of {{type, config}} where type in schema,catalog,app,cluster,job_cluster,lakebase).
Anything PHI/clinical/trial -> restricted + gxp/hipaa as applicable. Return ONLY JSON."""


def _foundation_model(text: str) -> dict | None:
    """Refine with the Databricks Foundation Model API; None on any failure."""
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.serving import ChatMessage, ChatMessageRole
        w = WorkspaceClient()
        resp = w.serving_endpoints.query(
            name=LLM_ENDPOINT,
            messages=[
                ChatMessage(role=ChatMessageRole.SYSTEM,
                            content=_SYS.format(domains=", ".join(BUSINESS_DOMAINS))),
                ChatMessage(role=ChatMessageRole.USER, content=text),
            ],
            temperature=0.0, max_tokens=600,
        )
        content = resp.choices[0].message.content
        m = re.search(r"\{.*\}", content, re.DOTALL)
        draft = json.loads(m.group(0) if m else content)
        draft["_source"] = f"foundation-model:{LLM_ENDPOINT}"
        draft.setdefault("_rationale", ["Drafted by the Foundation Model API."])
        return draft
    except Exception as e:  # noqa: BLE001
        logger.info("FM intake co-pilot unavailable, using heuristic: %s", e)
        return None


def draft_request(text: str) -> dict:
    base = _heuristic(text)
    fm = _foundation_model(text)
    if fm:
        # FM refines; keep heuristic values for any keys the FM omitted.
        merged = {**base, **{k: v for k, v in fm.items() if v not in (None, "", [])}}
        merged["_source"] = fm.get("_source", base["_source"])
        return merged
    return base
