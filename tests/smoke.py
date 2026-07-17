"""PAVE stdlib smoke test (no pytest / no network required).

Exercises the logic + service layers in safe demo mode (in-memory store, real
providers disabled). Run: `python3 tests/smoke.py` from the repo root.
Exits non-zero on any failure.
"""
import asyncio
import os
import sys

# Force safe demo mode BEFORE importing the app.
os.environ.pop("PGHOST", None)
os.environ["PAVE_ALLOW_REAL"] = "0"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "app"))

from backend.models import RequestIn, ResourceRequest, DataClassification, Environment, RiskTier  # noqa: E402
from backend.routing import route  # noqa: E402
from backend.validation import validate_request  # noqa: E402
from backend.tagging import build_tag_set, tag_coverage  # noqa: E402
from backend.models import REQUIRED_TAG_KEYS  # noqa: E402
from backend.services.spec import build_desired_state  # noqa: E402
from backend.database import db  # noqa: E402
from backend.services.provisioning_service import provision_request, decommission_request  # noqa: E402

_fails = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        _fails.append(name)


def _req(**over):
    base = dict(
        project_name="Test Project", description="A sufficiently long description here for validation.",
        justification="A sufficiently long business justification for the audit trail and gates.",
        owner_group="platform", cost_center="CC-1001", business_domain="platform",
        data_classification=DataClassification.internal, environment=Environment.dev,
        sunset_date="2026-12-31", acknowledgements=["cost-ownership", "data-handling"],
        resources=[ResourceRequest(type="schema", config={})],
    )
    base.update(over)
    return RequestIn(**base)


def test_routing():
    r0 = route(_req())
    check("routing: dev/internal -> TIER0", r0.risk_tier == RiskTier.TIER0)
    r2 = route(_req(data_classification=DataClassification.restricted, environment=Environment.stage,
                    gxp_relevant=True, contains_phi=True, compliance_scope=["gxp"]))
    check("routing: restricted+gxp -> TIER2", r2.risk_tier == RiskTier.TIER2)
    check("routing: TIER2 dual approval", r2.requires_dual)
    check("routing: TIER2 gxp-validation gate", "gxp-validation" in r2.gates)
    r1 = route(_req(data_classification=DataClassification.confidential, environment=Environment.test))
    check("routing: confidential/test -> TIER1", r1.risk_tier == RiskTier.TIER1)


def test_validation():
    check("validation: clean request passes", validate_request(_req(), "lead@x.com") == [])
    bad_cc = validate_request(_req(cost_center="NOPE"), "lead@x.com")
    check("validation: bad cost_center blocked", any("cost_center" in e for e in bad_cc))
    short = validate_request(_req(justification="too short"), "lead@x.com")
    check("validation: short justification blocked", any("justification" in e for e in short))
    restricted_shared = validate_request(
        _req(data_classification=DataClassification.restricted,
             resources=[ResourceRequest(type="cluster", config={"access_mode": "shared"})]),
        "lead@x.com")
    check("validation: restricted requires single-user cluster",
          any("single-user" in e for e in restricted_shared))


def test_tagging():
    req = {"id": "r1", "project_id": "proj-x", "project_name": "X", "cost_center": "CC-1001",
           "business_domain": "platform", "data_classification": "internal", "environment": "dev",
           "owner_group": "platform", "owner_email": "a@x.com", "custom_tags": {"team": "core"}}
    tags = build_tag_set(req)
    check("tagging: all required keys present", all(k in tags for k in REQUIRED_TAG_KEYS))
    check("tagging: managed_by stamped", tags.get("managed_by") == "self-service-portal")
    check("tagging: no reserved Name key", "name" not in {k.lower() for k in tags})
    check("tagging: coverage 100% of required", tag_coverage(tags, REQUIRED_TAG_KEYS) == 1.0)
    # owner override (reassignment path)
    re = build_tag_set(req, owner_email="b@x.com", cost_center="CC-9999")
    check("tagging: owner/cost override flows", re["owner_email"] == "b@x.com" and re["cost_center"] == "CC-9999")


async def _flow():
    # create -> provision -> assets tagged (simulated, real disabled)
    rec = await db.create_request({
        "project_id": "proj-smoke", "project_name": "Smoke", "requester": "lead@x.com",
        "owner_email": "lead@x.com", "owner_group": "platform", "cost_center": "CC-1001",
        "business_domain": "platform", "data_classification": "internal", "environment": "dev",
        "resources": [{"type": "schema", "config": {}}, {"type": "cluster", "config": {}}],
        "status": "APPROVED", "risk_tier": "TIER0",
    })
    rid = str(rec["id"])
    res = await provision_request(rid, actor="smoke")
    check("flow: provisioning ACTIVE", res["status"] == "ACTIVE")
    check("flow: 2 assets created", len(res["created"]) == 2)
    assets = await db.list_assets(project_id="proj-smoke")
    check("flow: assets simulated (real disabled)", all(a["mode"] == "simulated" for a in assets))
    check("flow: assets fully tagged", all(tag_coverage(a["applied_tags"], REQUIRED_TAG_KEYS) == 1.0 for a in assets))
    # spec
    spec = build_desired_state(rec, assets)
    check("flow: spec kind ProjectFootprint", spec["kind"] == "ProjectFootprint")
    check("flow: spec has 2 resources", len(spec["spec"]["resources"]) == 2)
    # decommission (internal -> hard)
    dc = await decommission_request(rid, actor="smoke")
    check("flow: internal decommissioned (hard)", len(dc["decommissioned"]) == 2 and not dc["held_for_controlled_change"])
    # audit append-only present
    audit = await db.list_audit(request_id=rid)
    types = {e["event_type"] for e in audit}
    check("flow: audit has provisioning + spec", "provisioning.finished" in types and "spec.recorded" in types)


async def _restricted_hold():
    rec = await db.create_request({
        "project_id": "proj-phi", "project_name": "PHI", "requester": "lead@x.com",
        "owner_email": "lead@x.com", "owner_group": "rwe", "cost_center": "CC-2034",
        "business_domain": "clinical", "data_classification": "restricted", "environment": "stage",
        "resources": [{"type": "schema", "config": {}}], "status": "APPROVED", "risk_tier": "TIER2",
    })
    rid = str(rec["id"])
    await provision_request(rid, actor="smoke")
    dc = await decommission_request(rid, actor="smoke")
    check("flow: restricted held for controlled change", len(dc["held_for_controlled_change"]) == 1 and not dc["decommissioned"])


def test_metadata():
    # ITIL change-type mapping
    check("metadata: TIER0 -> standard change", route(_req()).change_type == "standard")
    check("metadata: TIER2 -> normal change",
          route(_req(data_classification=DataClassification.restricted)).change_type == "normal")
    # tiered: prod requires backup_owner/rto/rpo/security/support
    prod_errs = validate_request(_req(environment=Environment.prod, sla_tier="tier1"), "lead@x.com")
    for need in ("backup_owner", "RTO and RPO", "security_review_status", "support_contact"):
        check(f"metadata: prod requires {need}", any(need in e for e in prod_errs))
    # regulated requires validated_system + data_retention
    reg_errs = validate_request(_req(data_classification=DataClassification.restricted,
                                     environment=Environment.stage,
                                     resources=[ResourceRequest(type="schema", config={})]), "lead@x.com")
    check("metadata: restricted requires validated_system", any("validated_system" in e for e in reg_errs))
    check("metadata: restricted requires data_retention", any("data_retention" in e for e in reg_errs))
    # gdpr requires dpia
    gdpr_errs = validate_request(_req(compliance_scope=["gdpr"]), "lead@x.com")
    check("metadata: gdpr requires DPIA", any("DPIA" in e for e in gdpr_errs))
    # bad email + bad wbs
    bad = validate_request(_req(technical_lead="not-an-email", wbs_code="bad code"), "lead@x.com")
    check("metadata: bad technical_lead email blocked", any("technical_lead" in e for e in bad))
    check("metadata: bad wbs_code blocked", any("wbs_code" in e for e in bad))


async def _dependency_guard():
    # project A depends on project B; decommissioning B is blocked while A is active
    b = await db.create_request({"project_id": "proj-b", "project_name": "B", "requester": "x@x.com",
        "owner_email": "x@x.com", "owner_group": "g", "cost_center": "CC-1001", "business_domain": "platform",
        "data_classification": "internal", "environment": "dev",
        "resources": [{"type": "schema", "config": {}}], "status": "APPROVED", "risk_tier": "TIER0"})
    await provision_request(str(b["id"]), actor="smoke")
    a = await db.create_request({"project_id": "proj-a", "project_name": "A", "requester": "x@x.com",
        "owner_email": "x@x.com", "owner_group": "g", "cost_center": "CC-1001", "business_domain": "platform",
        "data_classification": "internal", "environment": "dev",
        "resources": [{"type": "schema", "config": {}}], "status": "APPROVED", "risk_tier": "TIER0",
        "metadata": {"depends_on": ["proj-b"]}})
    await provision_request(str(a["id"]), actor="smoke")
    dc = await decommission_request(str(b["id"]), actor="smoke")
    check("deps: decommission blocked by dependents", dc.get("blocked_by_dependents") == ["proj-a"])
    # spec carries enterprise metadata
    spec = build_desired_state(await db.get_request(str(a["id"])))
    check("deps: spec has dependencies block", "dependencies" in spec and "traceability" in spec)


def _ai_req(**over):
    base = dict(ai_risk_tier="high", intended_use="protocol QA over approved docs",
                human_oversight=True, environment=Environment.prod, sla_tier="tier1",
                backup_owner="b@x.com", support_contact="o@x.com", rto_hours=24, rpo_hours=4,
                security_review_status="approved",
                resources=[ResourceRequest(type="llm_gateway_endpoint", config={
                    "provider": "databricks", "model": "databricks-claude-sonnet-4",
                    "task": "llm/v1/chat", "guardrails": ["pii_redact", "safety"],
                    "rate_limit_qpm": 100, "monthly_cost_cap_usd": 2000, "inference_logging": True})])
    return _req(**{**base, **over})


def test_ai():
    d = route(_ai_req())
    check("ai: high-risk LLM -> TIER2", d.risk_tier == RiskTier.TIER2)
    check("ai: LLMOps validation gate added", "llmops-validation" in d.gates)
    check("ai: clean AI request passes", validate_request(_ai_req(), "lead@x.com") == [])
    bad = validate_request(_ai_req(resources=[ResourceRequest(type="llm_gateway_endpoint",
        config={"provider": "databricks", "model": "gpt-4o", "guardrails": ["pii_redact"]})]), "lead@x.com")
    check("ai: non-allowlisted model blocked", any("allow-listed" in e for e in bad))
    unacc = validate_request(_ai_req(ai_risk_tier="unacceptable"), "lead@x.com")
    check("ai: unacceptable risk blocked", any("unacceptable" in e for e in unacc))
    noguard = validate_request(_ai_req(resources=[ResourceRequest(type="llm_gateway_endpoint",
        config={"provider": "openai", "model": "gpt-4o", "guardrails": []})]), "lead@x.com")
    check("ai: external model requires PII guardrail", any("PII guardrail" in e for e in noguard))


async def _ai_flow():
    rec = await db.create_request({
        "project_id": "proj-ai", "project_name": "AI", "requester": "x@x.com",
        "owner_email": "x@x.com", "owner_group": "rwe-clinical", "cost_center": "CC-2034",
        "business_domain": "clinical", "data_classification": "confidential", "environment": "prod",
        "ai_risk_tier": "high", "status": "APPROVED", "risk_tier": "TIER2",
        "resources": [{"type": "llm_gateway_endpoint", "config": {
            "provider": "databricks", "model": "databricks-claude-sonnet-4",
            "guardrails": ["pii_redact", "safety"], "monthly_cost_cap_usd": 2000,
            "inference_logging": True}}]})
    res = await provision_request(str(rec["id"]), actor="smoke")
    check("ai: endpoint provisioned (modeled, real off)", res["status"] == "ACTIVE")
    a = (await db.list_assets(project_id="proj-ai"))[0]
    check("ai: gateway governance modeled on asset",
          a["applied_tags"].get("ai_model") == "databricks-claude-sonnet-4"
          and "gateway_guardrails" in a["names"])


async def _cluster_policy():
    rec = await db.create_request({
        "project_id": "proj-clu", "project_name": "Clu", "requester": "x@x.com",
        "owner_email": "x@x.com", "owner_group": "platform", "cost_center": "CC-1001",
        "business_domain": "platform", "data_classification": "internal", "environment": "dev",
        "status": "APPROVED", "risk_tier": "TIER0",
        "resources": [{"type": "cluster", "config": {}}]})
    await provision_request(str(rec["id"]), actor="smoke")
    a = (await db.list_assets(project_id="proj-clu"))[0]
    check("cluster: company policy_id attached/modeled", bool(a["names"].get("policy_id")))


def main():
    print("PAVE smoke test (demo mode, real providers disabled)\n")
    print("routing:");    test_routing()
    print("validation:"); test_validation()
    print("tagging:");    test_tagging()
    print("metadata:");   test_metadata()
    print("ai:");         test_ai()
    print("flow:");       asyncio.run(_flow())
    print("restricted:"); asyncio.run(_restricted_hold())
    print("deps:");       asyncio.run(_dependency_guard())
    print("ai-flow:");    asyncio.run(_ai_flow())
    print("cluster:");    asyncio.run(_cluster_policy())
    print()
    if _fails:
        print(f"FAILED ({len(_fails)}): {_fails}")
        sys.exit(1)
    print("ALL SMOKE CHECKS PASSED")


if __name__ == "__main__":
    main()
