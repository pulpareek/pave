"""FinOps + Well-Architected scorecard.

Cost-by-* rollups and the tag-coverage KPI. Until real billing is wired
(Phase 6), cost is estimated per asset from a simple rate card so the dashboard
tells a complete story for simulated resources; the join shape mirrors
system.billing.usage (custom_tags) JOIN the asset registry on project_id /
cost_center / business_domain.
"""
from collections import defaultdict

from fastapi import APIRouter
from pydantic import BaseModel

from ..database import db
from ..models import REQUIRED_TAG_KEYS
from ..tagging import tag_coverage

router = APIRouter(prefix="/api/finops", tags=["finops"])

# Estimated monthly $ per resource type (stand-in for system.billing.usage).
RATE_CARD = {
    "schema": 5, "catalog": 5, "app": 80, "cluster": 400,
    "job_cluster": 150, "lakebase": 120,
    "llm_gateway_endpoint": 600, "vector_search": 250,
}

# ROI assumptions for the days->minutes story (tune for the customer).
MANUAL_PROVISION_DAYS = 3.5          # the ServiceNow status quo PAVE replaces
ENGINEER_DAY_COST = 900             # fully-loaded $/engineer-day
PAVE_MINUTES = 4                    # typical PAVE time-to-first-resource


def _est_cost(asset: dict) -> float:
    return float(RATE_CARD.get(asset.get("type"), 10))


class EstimateIn(BaseModel):
    resources: list[dict] = []


@router.get("/summary")
async def summary():
    assets = await db.list_assets()
    active = [a for a in assets if a.get("status") == "ACTIVE"]

    by_cc: dict[str, float] = defaultdict(float)
    by_project: dict[str, float] = defaultdict(float)
    by_domain: dict[str, float] = defaultdict(float)
    covered = 0

    for a in active:
        tags = a.get("applied_tags") or {}
        cost = _est_cost(a)
        by_cc[tags.get("cost_center", "(untagged)")] += cost
        by_project[a.get("project_id") or tags.get("project_id", "(none)")] += cost
        by_domain[tags.get("business_domain", "(untagged)")] += cost
        if tag_coverage(tags, REQUIRED_TAG_KEYS) >= 1.0:
            covered += 1

    coverage_pct = round(100 * covered / len(active), 1) if active else 100.0
    return {
        "total_estimated_monthly": round(sum(by_cc.values()), 2),
        "active_assets": len(active),
        "tag_coverage_pct": coverage_pct,
        "by_cost_center": dict(by_cc),
        "by_project": dict(by_project),
        "by_business_domain": dict(by_domain),
        "untagged_cost": round(by_cc.get("(untagged)", 0.0), 2),
    }


@router.get("/scorecard")
async def waf_scorecard():
    """Well-Architected Lakehouse scorecard — a REAL per-pillar score computed from the
    controls enforced on provisioned assets (see backend/well_architected.py). Each pillar
    carries a 0-100 score, the provisioning-time controls it maps to, and open findings."""
    from ..well_architected import score as waf_score
    assets = await db.list_assets()
    return waf_score(assets, REQUIRED_TAG_KEYS)


@router.post("/estimate")
async def estimate(payload: EstimateIn):
    """Cost preview BEFORE submit + budget-breach flag (FinOps-shift-left)."""
    monthly = sum(RATE_CARD.get(r.get("type"), 10) for r in payload.resources)
    return {
        "estimated_monthly": monthly,
        "breakdown": {r.get("type"): RATE_CARD.get(r.get("type"), 10) for r in payload.resources},
        "budget_threshold": 2000,
        "escalates_on_cost": monthly > 2000,
    }


# Real cost attribution over system tables (PAVE-managed spend, last 30 days).
# Joins usage -> list_prices on cloud+sku_name within the price window and groups by
# the custom_tags PAVE guarantees. Falls back to the rate-card estimate if the
# warehouse/system tables aren't reachable (e.g. local/demo).
_LIVE_COST_SQL = """
SELECT u.custom_tags['cost_center']  AS cost_center,
       u.custom_tags['project_id']   AS project_id,
       u.custom_tags['business_domain'] AS business_domain,
       SUM(u.usage_quantity * lp.pricing.effective_list.default) AS list_cost
FROM system.billing.usage u
JOIN system.billing.list_prices lp
  ON u.cloud = lp.cloud AND u.sku_name = lp.sku_name
 AND u.usage_start_time >= lp.price_start_time
 AND (u.usage_end_time <= lp.price_end_time OR lp.price_end_time IS NULL)
WHERE u.usage_date >= current_date() - INTERVAL 30 DAYS
  AND u.custom_tags['managed_by'] = 'self-service-portal'
GROUP BY 1, 2, 3
ORDER BY list_cost DESC
LIMIT 200
"""


@router.get("/live-cost")
async def live_cost():
    """Real PAVE-attributed cost from system.billing.usage (graceful fallback)."""
    import asyncio
    from .. import config

    if not config.WAREHOUSE_ID:
        return {"source": "fallback", "reason": "no warehouse configured",
                "note": "real cost lives in the native AI/BI Usage Dashboard; PAVE guarantees the tags"}

    def _run():
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        r = w.statement_execution.execute_statement(
            statement=_LIVE_COST_SQL, warehouse_id=config.WAREHOUSE_ID, wait_timeout="50s")
        rows = (r.result.data_array if r.result else None) or []
        return [{"cost_center": x[0], "project_id": x[1], "business_domain": x[2],
                 "list_cost": float(x[3]) if x[3] else 0.0} for x in rows]

    try:
        rows = await asyncio.to_thread(_run)
        return {"source": "system.billing.usage", "window_days": 30, "rows": rows,
                "total_list_cost": round(sum(r["list_cost"] for r in rows), 2)}
    except Exception as e:  # noqa: BLE001
        return {"source": "fallback", "reason": str(e)[:200],
                "note": "could not reach system tables; use /api/finops/summary for the registry estimate"}


@router.get("/ai")
async def ai_finops():
    """AI spend by team/domain vs budget + guardrail/logging coverage (the multi-team
    enterprise-platform story). Estimated spend stands in for system.serving.endpoint_usage."""
    assets = [a for a in await db.list_assets()
              if a.get("status") == "ACTIVE" and a.get("type") in ("llm_gateway_endpoint", "vector_search")]
    by_team: dict[str, dict] = defaultdict(lambda: {"endpoints": 0, "est_spend": 0.0, "budget": 0.0})
    rows, llm, guardrailed, logged = [], 0, 0, 0
    for a in assets:
        t = a.get("applied_tags") or {}
        n = a.get("names") or {}
        team = t.get("owner_group") or t.get("business_domain") or "(none)"
        spend = _est_cost(a)
        budget = float(n.get("gateway_budget_usd") or 0) if a["type"] == "llm_gateway_endpoint" else 0.0
        rec = by_team[team]
        rec["endpoints"] += 1; rec["est_spend"] += spend; rec["budget"] += budget
        if a["type"] == "llm_gateway_endpoint":
            llm += 1
            if n.get("gateway_guardrails"):
                guardrailed += 1
            if str(n.get("gateway_logging")).lower() == "true":
                logged += 1
        rows.append({"endpoint": n.get("name"), "type": a["type"], "team": team,
                     "model": t.get("ai_model"), "ai_risk_tier": t.get("ai_risk_tier"),
                     "est_monthly": spend, "budget_usd": budget})
    return {
        "ai_assets": len(assets), "llm_endpoints": llm,
        "guardrail_coverage_pct": round(100 * guardrailed / llm, 1) if llm else 100.0,
        "logging_coverage_pct": round(100 * logged / llm, 1) if llm else 100.0,
        "by_team": {k: {**v, "over_budget": v["budget"] > 0 and v["est_spend"] > v["budget"]}
                    for k, v in by_team.items()},
        "endpoints": rows,
    }


@router.get("/impact")
async def impact():
    """Days->minutes ROI: tickets eliminated, engineer-days + $ saved, time-to-provision."""
    requests = await db.list_requests(limit=1000)
    provisioned = [r for r in requests if r.get("status") in ("ACTIVE", "PARTIAL", "DECOMMISSIONED")]
    n = len(provisioned)
    days_saved = round(n * (MANUAL_PROVISION_DAYS - PAVE_MINUTES / (60 * 8)), 1)
    return {
        "tickets_eliminated": n,
        "manual_baseline_days": MANUAL_PROVISION_DAYS,
        "pave_minutes": PAVE_MINUTES,
        "engineer_days_saved": days_saved,
        "dollars_saved": round(days_saved * ENGINEER_DAY_COST),
        "speedup_x": round((MANUAL_PROVISION_DAYS * 24 * 60) / PAVE_MINUTES),
    }
