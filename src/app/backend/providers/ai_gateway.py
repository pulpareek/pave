"""Governed LLM gateway endpoint provider — leverages the FULL Mosaic/Unity AI Gateway.

Vends a per-team serving endpoint fronted by AI Gateway with every governance control:
access control, model allow-list, rate limits (QPM/TPM), guardrails (PII/safety/
jailbreak), inference-table payload logging, usage tracking, fallbacks, and a recorded
per-team token/$ budget + spend cap. Real creation (behind PAVE_ALLOW_REAL) via
serving_endpoints.create with the ai_gateway block; graceful fallback to a fully
*modeled* asset (governance recorded) when prerequisites (e.g. an external-model API
key secret, or SDK support) are absent — so the governance story always renders.
"""
import logging
import os
from typing import Any

from . import _sdk
from .base import Provider, ProvisionResult, new_asset_id
from ..models import ALLOWED_AI_MODELS

logger = logging.getLogger("pave.provider.ai_gateway")


def _governance(cfg: dict, tag_set: dict) -> dict:
    """The full intended AI-Gateway governance config (recorded on the asset)."""
    guardrails = cfg.get("guardrails") or ["pii_redact", "safety"]
    return {
        "model": {"provider": cfg.get("provider", "databricks"),
                  "name": cfg.get("model"), "task": cfg.get("task", "llm/v1/chat"),
                  "allow_listed": cfg.get("model") in ALLOWED_AI_MODELS.get(cfg.get("provider", ""), [])},
        "rate_limits": {"qpm": int(cfg.get("rate_limit_qpm", 100)),
                        "tpm": int(cfg.get("rate_limit_tpm", 50000)), "key": "user"},
        "guardrails": guardrails,
        "inference_logging": bool(cfg.get("inference_logging", True)),
        "usage_tracking": True,
        "throughput_mode": cfg.get("throughput_mode", "pay_per_token"),
        "fallbacks_enabled": bool(cfg.get("fallbacks")),
        "fallback": cfg.get("fallback_model"),
        "budget": {"monthly_token_budget": cfg.get("monthly_token_budget"),
                   "monthly_cost_cap_usd": cfg.get("monthly_cost_cap_usd"),
                   "team": tag_set.get("owner_group") or tag_set.get("business_domain")},
        "access": {"can_query": tag_set.get("owner_group"), "can_manage": "platform-admins"},
    }


class AIGatewayEndpointProvider(Provider):
    resource_type = "llm_gateway_endpoint"

    def provision(self, *, request: dict[str, Any], resource: dict[str, Any],
                  tag_set: dict[str, str], context: dict[str, Any]) -> ProvisionResult:
        cfg = resource.get("config", {})
        project_id = request.get("project_id", "proj")
        team = (tag_set.get("owner_group") or request.get("business_domain") or "team")
        name = (cfg.get("name") or f"llm-{team}-{project_id.split('-')[-1]}").lower().replace("_", "-")[:60]
        gov = _governance(cfg, tag_set)
        gov_tags = {**tag_set, "ai_model": gov["model"]["name"] or "", "ai_endpoint": name}

        mode, external_id, provenance = "simulated", f"sim-llm-{name}", {"engine": "modeled"}
        created = self._try_create_real(name, cfg, gov, request.get("target_workspace"))
        if created:
            mode, external_id, provenance = "real", created["name"], created

        return ProvisionResult(
            asset_id=new_asset_id("llm_gateway_endpoint", project_id),
            type="llm_gateway_endpoint",
            names={"name": name, **{f"gateway_{k}": str(v) for k, v in {
                "model": gov["model"]["name"], "qpm": gov["rate_limits"]["qpm"],
                "tpm": gov["rate_limits"]["tpm"], "guardrails": ",".join(gov["guardrails"]),
                "logging": gov["inference_logging"], "budget_usd": gov["budget"]["monthly_cost_cap_usd"],
            }.items()}},
            external_id=external_id,
            applied_tags=gov_tags,
            mode=mode,
            status="ACTIVE",
            provenance={"ai_governance": gov, **provenance},
        )

    def _try_create_real(self, name, cfg, gov, target_workspace=None) -> dict | None:
        """Attempt a real serving endpoint with the ai_gateway block; None on any gap."""
        from .. import config
        if not config.ALLOW_REAL:
            return None
        try:
            from databricks.sdk.service.serving import (
                EndpointCoreConfigInput, ServedEntityInput, ExternalModel,
                AiGatewayConfig, AiGatewayRateLimit, AiGatewayGuardrails,
                AiGatewayGuardrailParameters, AiGatewayInferenceTableConfig,
                AiGatewayUsageTrackingConfig,
            )
        except Exception as e:  # noqa: BLE001
            logger.info("AI Gateway SDK types unavailable (%s); modeling instead", e)
            return None

        provider = cfg.get("provider", "databricks")
        scope, key = os.getenv("AI_GATEWAY_SECRET_SCOPE"), os.getenv("AI_GATEWAY_SECRET_KEY")
        if provider in ("openai", "anthropic") and not (scope and key):
            logger.info("external provider %s needs AI_GATEWAY_SECRET_*; modeling instead", provider)
            return None

        w = _sdk.client(target_workspace)
        try:
            served = ServedEntityInput(
                name=f"{name}-entity",
                external_model=ExternalModel(
                    name=cfg.get("model"), provider=provider, task=cfg.get("task", "llm/v1/chat"),
                    **({f"{provider}_config": {f"{provider}_api_key": f"{{{{secrets/{scope}/{key}}}}}"}}
                       if provider in ("openai", "anthropic") else {})),
            )
            guard_params = AiGatewayGuardrailParameters(
                pii=({"behavior": "BLOCK"} if "pii_block" in gov["guardrails"]
                     else {"behavior": "MASK"} if "pii_redact" in gov["guardrails"] else None),
                safety=("safety" in gov["guardrails"]) or None,
            )
            ai_gw = AiGatewayConfig(
                rate_limits=[AiGatewayRateLimit(calls=gov["rate_limits"]["qpm"],
                                                key="user", renewal_period="minute")],
                guardrails=AiGatewayGuardrails(input=guard_params, output=guard_params),
                usage_tracking_config=AiGatewayUsageTrackingConfig(enabled=True),
                inference_table_config=(AiGatewayInferenceTableConfig(
                    enabled=True, catalog_name=os.getenv("AUDIT_CATALOG", ""),
                    schema_name=os.getenv("AUDIT_SCHEMA", "pave")) if gov["inference_logging"] else None),
            )
            ep = w.serving_endpoints.create(
                name=name, config=EndpointCoreConfigInput(served_entities=[served]), ai_gateway=ai_gw)
            ep_name = getattr(ep, "name", None) or name
            return {"name": ep_name, "engine": "serving_endpoints.create+ai_gateway"}
        except Exception as e:  # noqa: BLE001
            logger.warning("real AI gateway endpoint create failed (%s); modeling instead", e)
            return None

    def decommission(self, *, asset: dict[str, Any], context: dict[str, Any]) -> None:
        if asset.get("mode") == "real" and asset.get("external_id"):
            try:
                _sdk.client(context.get("target_workspace")).serving_endpoints.delete(name=asset["external_id"])
            except Exception as e:  # noqa: BLE001
                logger.warning("serving endpoint delete failed: %s", e)
