"""Budget-breach SQL Alert — Databricks-native cost alerting with NO SMTP relay.

When a request carries a budget, PAVE provisions a Databricks SQL Alert (AlertsV2) over
`system.billing.usage` that emails the owner/lead when the project's month-to-date attributed
cost crosses the budget. Alert subscriptions email users directly through the Databricks
control-plane mailer — the same path Jobs/Alerts use — so budget alerts work WITHOUT PAVE
running its own SMTP. The cost is attributable at all because PAVE guarantees the
`custom_tags` (project_id / cost_center / business_domain) on every asset it vends.

Behind PAVE_ALLOW_REAL + a configured warehouse (config.WAREHOUSE_ID). Simulated otherwise —
it records the alert spec + the emails it would notify, so the story is complete without a
warehouse, and honestly reports the reason.
"""
import logging
from typing import Any, Optional

from . import _sdk
from .base import ProvisionResult, classify_error, new_asset_id
from .. import config
from ..models import EMAIL_RE

logger = logging.getLogger("pave.provider.budget_alert")

# One row, one column `list_cost` = month-to-date cost attributed to THIS project via the
# tags PAVE guarantees, joined to list_prices within the price window (mirrors
# routers/finops.py::_LIVE_COST_SQL). The alert fires when list_cost > budget.
_BUDGET_SQL = """SELECT COALESCE(SUM(u.usage_quantity * lp.pricing.effective_list.default), 0) AS list_cost
FROM system.billing.usage u
JOIN system.billing.list_prices lp
  ON u.cloud = lp.cloud AND u.sku_name = lp.sku_name
 AND u.usage_start_time >= lp.price_start_time
 AND (u.usage_end_time <= lp.price_end_time OR lp.price_end_time IS NULL)
WHERE u.usage_date >= date_trunc('MONTH', current_date())
  AND u.custom_tags['project_id'] = '{project_id}'"""


def budget_for_request(request: dict) -> Optional[float]:
    """The monthly budget cap on the request, or None when there is no positive budget."""
    try:
        b = float(request.get("budget_monthly_cap"))
    except (TypeError, ValueError):
        return None
    return b if b > 0 else None


def budget_recipients(request: dict) -> list[str]:
    """Who receives the breach email: owner + business owner + technical lead + requester
    (valid emails, de-duplicated, order-preserving)."""
    out, seen = [], set()
    for e in (request.get("owner_email"), request.get("business_owner"),
              request.get("technical_lead"), request.get("requester")):
        e = (e or "").strip().lower()
        if e and e not in seen and EMAIL_RE.match(e):
            seen.add(e)
            out.append(e)
    return out


def build_alert_sql(project_id: str) -> str:
    return _BUDGET_SQL.format(project_id=str(project_id).replace("'", "''"))


def provision_budget_alert(*, request: dict, tag_set: dict,
                           context: dict) -> Optional[ProvisionResult]:
    """Create (or model) the budget-breach SQL Alert. Returns None when the request has no
    budget. Never raises — a failed alert create degrades to a modelled asset with a reason."""
    budget = budget_for_request(request)
    if budget is None:
        return None
    project_id = request.get("project_id", "proj")
    recipients = budget_recipients(request)
    name = f"pave-budget-{project_id}"
    sql = build_alert_sql(project_id)
    aid = new_asset_id("budget_alert", project_id, {**context, "resource_index": "budget"})
    spec = {"name": name, "threshold_usd": budget, "comparison": "GREATER_THAN",
            "notify": recipients, "schedule": "daily 13:00 UTC",
            "source": "system.billing.usage", "query": sql}

    def _modelled(reason: str, extra: dict | None = None) -> ProvisionResult:
        return ProvisionResult(
            asset_id=aid, type="budget_alert", names={**spec, **(extra or {})},
            external_id=f"sim-budget-alert-{project_id}", applied_tags=tag_set,
            mode="simulated", status="ACTIVE", mode_reason=reason, degraded=True,
            provenance={"alert": spec, **(extra or {})})

    if not config.ALLOW_REAL:
        return _modelled("kill_switch_off")
    if not config.WAREHOUSE_ID:
        return _modelled("missing_prerequisite")   # an alert needs a warehouse to run on
    if not recipients:
        return _modelled("missing_prerequisite", {"note": "no valid recipient email to notify"})

    try:
        w = _sdk.client(request.get("target_workspace"))
        from databricks.sdk.service.sql import (
            AlertV2, AlertV2Evaluation, AlertV2Notification, AlertV2Operand,
            AlertV2OperandColumn, AlertV2OperandValue, AlertV2Subscription,
            ComparisonOperator, CronSchedule)
        evaluation = AlertV2Evaluation(
            source=AlertV2OperandColumn(name="list_cost"),
            comparison_operator=ComparisonOperator.GREATER_THAN,
            threshold=AlertV2Operand(value=AlertV2OperandValue(double_value=budget)),
            notification=AlertV2Notification(
                notify_on_ok=False,
                subscriptions=[AlertV2Subscription(user_email=e) for e in recipients]))
        alert = w.alerts_v2.create_alert(alert=AlertV2(
            display_name=name, query_text=sql, warehouse_id=config.WAREHOUSE_ID,
            evaluation=evaluation,
            custom_description=(f"PAVE budget alert for {project_id}: emails "
                                f"{', '.join(recipients)} when month-to-date cost > "
                                f"${budget:,.0f}."),
            schedule=CronSchedule(quartz_cron_schedule="0 0 13 * * ?", timezone_id="UTC")))
        return ProvisionResult(
            asset_id=aid, type="budget_alert", names={**spec, "alert_id": alert.id},
            external_id=alert.id or name, applied_tags=tag_set, mode="real", status="ACTIVE",
            provenance={"alert": spec, "alert_id": alert.id})
    except Exception as e:  # noqa: BLE001
        logger.warning("budget alert create failed for %s: %s", project_id, e)
        return _modelled(classify_error(e), {"error": str(e)[:200]})


def decommission_budget_alert(external_id: str, target_workspace: str | None = None) -> None:
    """Trash the alert on project decommission (best-effort; simulated ids are skipped)."""
    if not external_id or str(external_id).startswith("sim-"):
        return
    _sdk.client(target_workspace).alerts_v2.trash_alert(id=external_id)
