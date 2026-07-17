"""Risk-tiered approval routing — policy as DATA, not buried code.

Evaluates a request and returns its risk tier + the required approval gates.
Keeping the rules declarative makes the governance model auditable and tweakable
without touching handler code.
"""
from dataclasses import dataclass, field

from .models import RequestIn, DataClassification, Environment, RiskTier


@dataclass
class RoutingDecision:
    risk_tier: RiskTier
    gates: list[str] = field(default_factory=list)   # ordered approval gates
    rationale: list[str] = field(default_factory=list)

    @property
    def requires_dual(self) -> bool:
        return len(self.gates) > 1

    @property
    def change_type(self) -> str:
        """ITIL change-type mapping: Tier-0 golden paths are pre-authorized
        Standard Changes (bypass CAB); higher tiers are Normal Changes."""
        return "standard" if self.risk_tier == RiskTier.TIER0 else "normal"

    def to_dict(self) -> dict:
        return {
            "risk_tier": self.risk_tier.value,
            "gates": self.gates,
            "requires_dual": self.requires_dual,
            "change_type": self.change_type,
            "rationale": self.rationale,
        }


# Threshold (estimated monthly $) above which a request escalates to controlled.
COST_ESCALATION_THRESHOLD = 2000


def route(payload: RequestIn, *, estimated_cost: float = 0.0) -> RoutingDecision:
    rationale: list[str] = []
    tier = RiskTier.TIER0
    gates = ["platform"]

    cls = payload.data_classification
    env = payload.environment
    new_catalog = any(r.type.value == "catalog" for r in payload.resources)
    new_workspace = any(r.type.value == "workspace" for r in payload.resources)

    # AI assets: high-risk, external model, or PHI -> controlled (LLMOps gate)
    ai_types = {"llm_gateway_endpoint", "vector_search"}
    ai_resources = [r for r in payload.resources if r.type.value in ai_types]
    ai_external = any((r.config or {}).get("provider") in ("openai", "anthropic")
                      for r in ai_resources)
    ai_controlled = bool(ai_resources) and (
        payload.ai_risk_tier in ("high", "unacceptable") or ai_external or payload.contains_phi)

    controlled = (
        env == Environment.prod
        or cls == DataClassification.restricted
        or payload.gxp_relevant
        or payload.contains_phi
        or "gxp" in payload.compliance_scope
        or new_catalog
        or new_workspace
        or ai_controlled
        or estimated_cost > COST_ESCALATION_THRESHOLD
    )
    standard = (
        env in (Environment.test, Environment.stage)
        or cls == DataClassification.confidential
    )

    if controlled:
        tier = RiskTier.TIER2
        gates = ["platform", "security-compliance"]
        if env == Environment.prod:
            rationale.append("production environment")
        if cls == DataClassification.restricted:
            rationale.append("restricted (PHI/GxP) classification")
        if payload.gxp_relevant or "gxp" in payload.compliance_scope:
            rationale.append("GxP relevant -> validation gate")
            gates.append("gxp-validation")
        if payload.contains_phi:
            rationale.append("contains PHI")
        if new_catalog:
            rationale.append("new catalog -> platform-admin escalation")
        if new_workspace:
            rationale.append("new workspace -> account-admin escalation (account-level SoD)")
            gates.append("account-admin")
        if ai_controlled:
            rationale.append("AI asset (high-risk / external model / PHI) -> LLMOps gate")
            gates.append("llmops-validation")
        if estimated_cost > COST_ESCALATION_THRESHOLD:
            rationale.append(f"estimated cost ${estimated_cost:.0f} over threshold")
    elif standard:
        tier = RiskTier.TIER1
        gates = ["platform"]
        rationale.append("standard tier (test/stage or confidential) + budget check")
    else:
        tier = RiskTier.TIER0
        gates = ["platform"]
        rationale.append("fast lane (dev sandbox, public/internal, low cost)")

    return RoutingDecision(risk_tier=tier, gates=gates, rationale=rationale)
