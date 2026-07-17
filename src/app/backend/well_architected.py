"""Well-Architected control plane — WAF-by-default at provisioning time.

Turns the Well-Architected Lakehouse pillars from an after-the-fact scorecard into
runtime controls on the *birth* of a resource:

  * DEFAULT  — born-compliant config injected before provisioning (autotermination,
               restricted -> single-user, LLM guardrails/budget). The single source of
               truth for values previously duplicated across validation.py / cluster*.py.
  * HARD     — a true violation that blocks at intake (e.g. an explicit access mode that
               conflicts with restricted data). Kept deliberately small; broad intake
               validation still lives in validation.py.
  * SOFT     — a finding that lowers a pillar score and can be WAIVED with a logged
               justification (graduated, monitor-first posture).

SCOPE (important): PAVE is a resource *vending engine*. This module ONLY encodes WAF
practices PAVE can enforce/set at provisioning time (tags, access model, cluster policy,
autotermination/caps, classification+retention, LLM guardrails/rate-limits/budget,
sunset/ownership, budget escalation). WAF practices that are the resource *consumer's*
runtime responsibility — query/Spark tuning, Photon, streaming checkpoints, DR failover
drills, pipeline data-quality, model-drift monitoring — are intentionally OUT of scope.

Policy-as-DATA: `WAF_RULES` below is the single source of truth, mirroring the existing
data tables (`models.py` vocabularies, `providers/registry.DEFAULT_MODES`,
`providers/policies._PAVE_STANDARD_DEFINITION`).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger("pave.waf")


# ---------------------------------------------------------------------------
# Pillars — values MUST match the strings the SPA already renders (finops scorecard).
# ---------------------------------------------------------------------------
class Pillar(str, Enum):
    governance = "Data & AI Governance"
    cost = "Cost Optimization"
    security = "Security, Privacy & Compliance"
    ops = "Operational Excellence"
    reliability = "Reliability"
    performance = "Performance Efficiency"
    interop = "Interoperability & Usability"


PILLAR_ORDER = [p.value for p in Pillar]

# Born-compliant default values (single source; providers read these, saga applies them).
COMPUTE_DEFAULTS = {
    "autotermination_minutes": 30,
    "min_workers": 1,
    "max_workers": 4,
    "node_type_id": "m5d.large",
}
RESTRICTED_ACCESS_MODE = "single-user"          # config-level; providers map to SINGLE_USER
DEFAULT_LLM_GUARDRAILS = ["pii_redact", "safety"]
COMPUTE_TYPES = ("cluster", "job_cluster")
AI_TYPES = ("llm_gateway_endpoint", "vector_search")


# ---------------------------------------------------------------------------
# Rule model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WafRule:
    """One provisioning-time Well-Architected control.

    `applies_to` empty => request-level (evaluated once). Otherwise the resource types
    the rule governs. `default` returns a config patch (only absent keys are applied).
    `check` returns True when compliant (run AFTER defaults). Severity drives behaviour:
    "default" (inject only), "hard" (block), "soft" (finding + score).
    """
    id: str
    pillar: Pillar
    title: str
    severity: str                                     # "default" | "hard" | "soft"
    applies_to: tuple[str, ...] = ()
    default: Optional[Callable[[dict, dict], dict]] = None
    check: Optional[Callable[[dict, dict], bool]] = None
    remediation: str = ""
    rationale: str = ""
    source: str = "pave-code"                          # workbook cell ref once ingested


# ---------------------------------------------------------------------------
# Rule callables (kept small + pure; operate on normalized request/resource dicts)
# ---------------------------------------------------------------------------
def _restricted(req: dict) -> bool:
    return req.get("data_classification") == "restricted"


def _default_autoterm(req: dict, res: dict) -> dict:
    return {"autotermination_minutes": COMPUTE_DEFAULTS["autotermination_minutes"]}


def _default_workers(req: dict, res: dict) -> dict:
    return {"min_workers": COMPUTE_DEFAULTS["min_workers"],
            "max_workers": COMPUTE_DEFAULTS["max_workers"],
            "node_type_id": COMPUTE_DEFAULTS["node_type_id"]}


def _default_restricted_access(req: dict, res: dict) -> dict:
    return {"access_mode": RESTRICTED_ACCESS_MODE} if _restricted(req) else {}


def _default_llm_guardrails(req: dict, res: dict) -> dict:
    return {"guardrails": list(DEFAULT_LLM_GUARDRAILS)}


def _default_llm_budget(req: dict, res: dict) -> dict:
    cap = req.get("budget_monthly_cap")
    return {"monthly_cost_cap_usd": cap} if cap else {}


def _check_restricted_single_user(req: dict, res: dict) -> bool:
    """HARD: restricted data must not run on a shared/explicitly-non-single-user cluster.

    Single-user is expressed as `single-user` (legacy) or `dedicated` (current Databricks
    naming), or `auto` (Databricks picks — resolves to dedicated for restricted/ML). Only an
    explicit `standard`/shared mode on restricted data is blocked.
    """
    if not _restricted(req):
        return True
    am = (res.get("config") or {}).get("access_mode")
    return am in (None, RESTRICTED_ACCESS_MODE, "dedicated", "auto")


def _check_autoterm(req: dict, res: dict) -> bool:
    return bool((res.get("config") or {}).get("autotermination_minutes"))


def _check_sunset(req: dict, res: dict) -> bool:
    """SOFT: non-prod sandboxes should carry a sunset date (cost + lifecycle hygiene)."""
    if req.get("environment") in ("dev", "test"):
        return bool(req.get("sunset_date"))
    return True


def _check_backup_owner(req: dict, res: dict) -> bool:
    """SOFT: prod / tier1 should name a backup owner (bus-factor / reliability)."""
    if req.get("environment") == "prod" or req.get("sla_tier") == "tier1":
        return bool(req.get("backup_owner"))
    return True


def _check_budget_cap(req: dict, res: dict) -> bool:
    """SOFT: a monthly budget cap should be declared so spend is bounded from birth."""
    return req.get("budget_monthly_cap") not in (None, 0, "")


# ---------------------------------------------------------------------------
# The rule table (provisioning-scoped WAF controls PAVE already enforces / can enforce).
#
# Reconciled with the Well-Architected Lakehouse workbook: `source` cites the workbook
# identifier(s) each control realizes. ONLY provisioning-time-enforceable rows are
# included — the workbook's runtime/consumer practices (query/Spark tuning PE-02*, Photon,
# partitioning/compaction, streaming recovery R-04*, pipeline data-quality DG-03*/R-02*,
# MLOps OE-01/02*, DR drills, and account-substrate security SSO/MFA/VPC/CMK SCP-01..03)
# are intentionally EXCLUDED — they are the resource consumer's or the platform's job.
# ---------------------------------------------------------------------------
WAF_RULES: list[WafRule] = [
    # --- born-compliant DEFAULTS ---
    WafRule("COST-AUTOTERM", Pillar.cost, "Auto-terminate idle compute", "default",
            applies_to=COMPUTE_TYPES, default=_default_autoterm, source="CO-02-02",
            rationale="Idle clusters are pure waste; a fixed autotermination is set at birth."),
    WafRule("PERF-RIGHTSIZE", Pillar.performance, "Bounded, right-sized compute", "default",
            applies_to=COMPUTE_TYPES, default=_default_workers, source="CO-01-08,PE-02-07,IU-03-03",
            rationale="Worker caps + an allow-listed node type keep compute bounded by policy."),
    WafRule("SEC-SINGLEUSER-DEFAULT", Pillar.security,
            "Restricted data defaults to single-user access", "default",
            applies_to=COMPUTE_TYPES, default=_default_restricted_access, source="SCP-01-12",
            rationale="Restricted (PHI/GxP) clusters are set to single-user isolation by default."),
    WafRule("GOV-LLM-GUARDRAILS", Pillar.governance,
            "LLM endpoints get PII/safety guardrails", "default",
            applies_to=("llm_gateway_endpoint",), default=_default_llm_guardrails, source="OE-01-06",
            rationale="Every governed LLM endpoint is born with PII + safety guardrails."),
    WafRule("COST-LLM-BUDGET", Pillar.cost, "LLM endpoints inherit a spend cap", "default",
            applies_to=("llm_gateway_endpoint",), default=_default_llm_budget, source="CO-03-02",
            rationale="The request's monthly budget cap flows to the endpoint spend cap."),

    # --- HARD gate (small; broad validation stays in validation.py) ---
    WafRule("SEC-SINGLEUSER-GATE", Pillar.security,
            "No restricted data on shared compute", "hard",
            applies_to=COMPUTE_TYPES, check=_check_restricted_single_user, source="SCP-01-12",
            remediation="Set access_mode=single-user (or leave unset) for restricted data.",
            rationale="An explicit shared/multi-user access mode on restricted data is blocked."),

    # --- SOFT findings (score + waivable) ---
    WafRule("COST-AUTOTERM-PRESENT", Pillar.cost, "Compute carries an autotermination", "soft",
            applies_to=COMPUTE_TYPES, check=_check_autoterm, source="CO-02-02",
            remediation="Provide autotermination_minutes (a default is applied if omitted)."),
    WafRule("REL-SUNSET", Pillar.reliability, "Sandboxes declare a sunset date", "soft",
            check=_check_sunset, source="OE-04-01",
            remediation="Add a sunset_date for dev/test environments."),
    WafRule("REL-BACKUP-OWNER", Pillar.reliability, "Prod/tier1 names a backup owner", "soft",
            check=_check_backup_owner, source="R-04-04",
            remediation="Name a backup_owner for production / tier1 (bus-factor)."),
    WafRule("COST-BUDGET", Pillar.cost, "A monthly budget cap is declared", "soft",
            check=_check_budget_cap, source="CO-03-01,CO-03-04",
            remediation="Declare budget_monthly_cap so spend is bounded and attributable."),
]


# ---------------------------------------------------------------------------
# Normalization — one evaluate() usable for both intake (RequestIn) and provision (dict)
# ---------------------------------------------------------------------------
def _norm_request(request: Any) -> dict:
    """Uniform view of the fields rules read, from a RequestIn or a stored dict."""
    if isinstance(request, dict):
        meta = request.get("metadata") or {}
        env = request.get("environment")
        cls = request.get("data_classification")
        return {
            "data_classification": getattr(cls, "value", cls),
            "environment": getattr(env, "value", env),
            "contains_phi": bool(request.get("contains_phi")),
            "gxp_relevant": bool(request.get("gxp_relevant")),
            "sunset_date": request.get("sunset_date"),
            "ai_risk_tier": request.get("ai_risk_tier") or meta.get("ai_risk_tier"),
            "budget_monthly_cap": request.get("budget_monthly_cap")
                                  if request.get("budget_monthly_cap") is not None
                                  else meta.get("budget_monthly_cap"),
            "backup_owner": request.get("backup_owner") or meta.get("backup_owner"),
            "sla_tier": request.get("sla_tier") or meta.get("sla_tier"),
        }
    # pydantic RequestIn
    return {
        "data_classification": getattr(request.data_classification, "value",
                                       request.data_classification),
        "environment": getattr(request.environment, "value", request.environment),
        "contains_phi": bool(request.contains_phi),
        "gxp_relevant": bool(request.gxp_relevant),
        "sunset_date": request.sunset_date,
        "ai_risk_tier": request.ai_risk_tier,
        "budget_monthly_cap": request.budget_monthly_cap,
        "backup_owner": request.backup_owner,
        "sla_tier": request.sla_tier,
    }


def _norm_resource(resource: Any) -> dict:
    """Uniform {type, config} from a ResourceRequest or a stored dict."""
    if isinstance(resource, dict):
        rtype = resource.get("type")
        return {"type": getattr(rtype, "value", rtype),
                "config": dict(resource.get("config") or {})}
    return {"type": getattr(resource.type, "value", resource.type),
            "config": dict(resource.config or {})}


def _applies(rule: WafRule, rtype: Optional[str]) -> bool:
    if not rule.applies_to:
        return rtype is None                      # request-level rules evaluated once
    return rtype in rule.applies_to


# ---------------------------------------------------------------------------
# Defaults (born-compliant) — used by the provisioning saga per resource
# ---------------------------------------------------------------------------
def apply_defaults(request: Any, resource: Any) -> tuple[dict, list[dict]]:
    """Inject born-compliant defaults into a resource's config (absent keys only).

    Returns (patched_resource_dict, applied) where applied is a list of
    {rule_id, pillar, key, value} describing what was set.
    """
    req = _norm_request(request)
    res = _norm_resource(resource)
    cfg = res["config"]
    applied: list[dict] = []
    for rule in WAF_RULES:
        if rule.severity != "default" or rule.default is None:
            continue
        if not _applies(rule, res["type"]):
            continue
        for key, value in rule.default(req, res).items():
            # treat empty list / None as "absent" so a default can fill it
            if cfg.get(key) in (None, "", [], {}):
                cfg[key] = value
                applied.append({"rule_id": rule.id, "pillar": rule.pillar.value,
                                "key": key, "value": value})
    return {"type": res["type"], "config": cfg}, applied


# ---------------------------------------------------------------------------
# Evaluate (intake gate + evidence)
# ---------------------------------------------------------------------------
@dataclass
class WafDecision:
    enforced_defaults: list[dict] = field(default_factory=list)
    blocking: list[dict] = field(default_factory=list)     # hard findings -> block
    findings: list[dict] = field(default_factory=list)     # open soft findings
    waived: list[dict] = field(default_factory=list)       # soft findings with justification
    by_pillar: dict[str, dict] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return bool(self.blocking)

    def to_dict(self) -> dict:
        return {
            "enforced_defaults": self.enforced_defaults,
            "blocking": self.blocking,
            "findings": self.findings,
            "waived": self.waived,
            "by_pillar": self.by_pillar,
            "blocked": self.blocked,
        }


def _finding(rule: WafRule, rtype: Optional[str]) -> dict:
    return {"rule_id": rule.id, "pillar": rule.pillar.value, "title": rule.title,
            "severity": rule.severity, "remediation": rule.remediation,
            "resource_type": rtype, "source": rule.source}


def evaluate(request: Any, resources: list[Any],
             waivers: Optional[list[dict]] = None) -> WafDecision:
    """Run the full WAF control set over a request. Applies defaults (preview), splits
    hard/soft findings, honours waivers, and tallies per-pillar pass/total."""
    waived_ids = {w.get("rule_id") for w in (waivers or []) if w.get("rule_id")}
    waiver_by_id = {w.get("rule_id"): w.get("justification", "") for w in (waivers or [])}
    d = WafDecision()
    tally: dict[str, list[int]] = {p: [0, 0] for p in PILLAR_ORDER}  # [passed, total]

    # preview born-compliant defaults so config-dependent checks see them
    for r in resources:
        _, applied = apply_defaults(request, r)
        d.enforced_defaults.extend(applied)
    # re-normalize after defaults for accurate checks
    patched = [apply_defaults(request, r)[0] for r in resources]

    for rule in WAF_RULES:
        if rule.check is None:
            continue
        # request-level rule (applies_to empty) -> evaluate once with no resource
        targets = ([{"type": None, "config": {}}] if not rule.applies_to
                   else [p for p in patched if p["type"] in rule.applies_to])
        for res in targets:
            pillar = rule.pillar.value
            tally[pillar][1] += 1
            ok = rule.check(_norm_request(request), res)
            if ok:
                tally[pillar][0] += 1
                continue
            fnd = _finding(rule, res["type"])
            if rule.severity == "hard":
                d.blocking.append(fnd)
            elif rule.id in waived_ids:
                d.waived.append({**fnd, "justification": waiver_by_id.get(rule.id, "")})
                tally[pillar][0] += 1     # waived = accepted risk, counts as covered
            else:
                d.findings.append(fnd)

    d.by_pillar = {p: {"passed": t[0], "total": t[1],
                       "score": round(100 * t[0] / t[1]) if t[1] else 100}
                   for p, t in tally.items()}
    return d


def waivers_from_request(request: Any) -> list[dict]:
    """Pull waf_waivers off a RequestIn or a stored request dict (metadata)."""
    if isinstance(request, dict):
        meta = request.get("metadata") or {}
        return request.get("waf_waivers") or meta.get("waf_waivers") or []
    return getattr(request, "waf_waivers", None) or []


# ---------------------------------------------------------------------------
# Scorecard — real per-pillar scores computed from provisioned assets
# ---------------------------------------------------------------------------
# Descriptive controls per pillar (kept for the scorecard drill-down; the SCORE is computed).
# Each control notes the workbook identifier(s) it realizes at provisioning time.
PILLAR_CONTROLS: dict[str, list[str]] = {
    Pillar.governance.value: ["governed-tag assignment in Unity Catalog (DG-01-02)",
                              "centralized access control, grants to groups (DG-02-01)",
                              "audit logging of provisioning activities (DG-02-02, SCP-06-03)",
                              "LLM endpoints via governed AI Gateway (OE-01-06)"],
    Pillar.cost.value: ["mandatory tags for chargeback at the gate (CO-03-02, SCP-06-05)",
                        "auto-termination on all compute (CO-02-02)",
                        "compute policies control cost (CO-02-03)",
                        "budget cap + cost reporting (CO-03-01)"],
    Pillar.security.value: ["restricted -> single-user isolation (SCP-01-12)",
                            "service principals run jobs / SoD (SCP-01-13)",
                            "cluster-creation limited by policy (SCP-01-08)",
                            "append-only audit + dual approval on Tier 2"],
    Pillar.ops.value: ["standardized compute via cluster policies (OE-02-02)",
                       "dev/stage/prod environment isolation (OE-01-05)",
                       "service limits & quotas at the gate (OE-04-01)",
                       "record-as-code desired-state manifest (OE-02-10)"],
    Pillar.reliability.value: ["backup owner + RTO/RPO on prod/tier1 (R-04-04)",
                               "autoscaling on provisioned compute (R-03-01)",
                               "sunset dates on sandboxes", "PARTIAL over orphaned resources"],
    Pillar.performance.value: ["cluster-policy right-sizing / instance type (CO-01-08, PE-02-07)",
                               "bounded autoscale + node allow-list", "serverless where available (PE-01-01)"],
    Pillar.interop.value: ["single self-service provisioning portal (IU-03-01)",
                           "pre-defined compute templates / policies (IU-03-03)",
                           "central catalog + consistent tag vocabulary for billing join (IU-04-03)"],
}


def _asset_waf(asset: dict) -> dict:
    """Recorded per-asset WAF outcome, written to provenance at provision time."""
    prov = asset.get("provenance") or {}
    return prov.get("well_architected") or {}


def score(assets: list[dict], required_tag_keys: list[str]) -> dict[str, Any]:
    """Per-pillar scorecard computed from active assets.

    Governance/Cost lean on tag coverage; the rest aggregate the per-asset WAF tallies
    recorded at provision time (falling back to tag coverage when absent, e.g. legacy rows).
    """
    from .tagging import tag_coverage

    active = [a for a in assets if a.get("status") == "ACTIVE"]
    coverages = [tag_coverage(a.get("applied_tags") or {}, required_tag_keys) for a in active]
    avg_cov = round(100 * sum(coverages) / len(coverages)) if coverages else 100

    # aggregate recorded per-asset pillar tallies
    agg: dict[str, list[int]] = {p: [0, 0] for p in PILLAR_ORDER}
    open_findings: dict[str, int] = {p: 0 for p in PILLAR_ORDER}
    for a in active:
        waf = _asset_waf(a)
        for p, t in (waf.get("by_pillar") or {}).items():
            if p in agg:
                agg[p][0] += int(t.get("passed", 0))
                agg[p][1] += int(t.get("total", 0))
        for f in (waf.get("findings") or []):
            if f.get("pillar") in open_findings:
                open_findings[f["pillar"]] += 1

    pillars = []
    for p in PILLAR_ORDER:
        passed, total = agg[p]
        if total:
            s = round(100 * passed / total)
        elif p in (Pillar.governance.value, Pillar.cost.value):
            s = avg_cov                                   # tag-coverage proxy
        else:
            s = 100 if active else 100                    # nothing to fault yet
        pillars.append({"pillar": p, "score": s, "controls": PILLAR_CONTROLS.get(p, []),
                        "open_findings": open_findings[p]})

    overall = round(sum(p["score"] for p in pillars) / len(pillars)) if pillars else 100
    return {"overall_score": overall, "pillars": pillars,
            "tag_coverage_pct": avg_cov, "active_assets": len(active),
            "real_assets": sum(1 for a in active if a.get("mode") == "real")}


def record_for_asset(request: Any, resource: Any,
                     waivers: Optional[list[dict]] = None) -> dict:
    """Per-resource WAF outcome to store on asset provenance (drives spec + scorecard)."""
    single = evaluate(request, [resource], waivers)
    return {"enforced_defaults": single.enforced_defaults,
            "findings": single.findings, "waived": single.waived,
            "by_pillar": single.by_pillar}


def spec_block(request: Any, assets: Optional[list[dict]] = None) -> dict:
    """Well-Architected evidence for the desired-state manifest (append-only audit).

    Prefers the authoritative per-asset outcomes recorded at provision time; falls back
    to evaluating the request's declared resources when no provisioned assets exist yet
    (e.g. the spec is fetched before provisioning).
    """
    recorded = [(_asset_waf(a)) for a in (assets or []) if _asset_waf(a)]
    if recorded:
        enforced, findings, waived = [], [], []
        agg: dict[str, list[int]] = {p: [0, 0] for p in PILLAR_ORDER}
        for waf in recorded:
            enforced.extend(waf.get("enforced_defaults") or [])
            findings.extend(waf.get("findings") or [])
            waived.extend(waf.get("waived") or [])
            for p, t in (waf.get("by_pillar") or {}).items():
                if p in agg:
                    agg[p][0] += int(t.get("passed", 0))
                    agg[p][1] += int(t.get("total", 0))
        by_pillar = {p: {"passed": t[0], "total": t[1],
                         "score": round(100 * t[0] / t[1]) if t[1] else 100}
                     for p, t in agg.items()}
        return {"enforced": enforced, "findings": findings, "waived": waived,
                "by_pillar": by_pillar}
    # pre-provision fallback: evaluate the declared resources
    resources = request.get("resources") if isinstance(request, dict) else request.resources
    if isinstance(resources, str):
        import json
        resources = json.loads(resources)
    d = evaluate(request, resources or [], waivers_from_request(request))
    return {"enforced": d.enforced_defaults, "findings": d.findings,
            "waived": d.waived, "by_pillar": d.by_pillar}
