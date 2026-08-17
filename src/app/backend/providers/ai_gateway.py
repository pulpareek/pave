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
from .base import Provider, ProvisionResult, classify_error, new_asset_id
from .. import naming
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
        name = naming.resolve_name("llm_gateway_endpoint", request, cfg)
        gov = _governance(cfg, tag_set)
        gov_tags = {**tag_set, "ai_model": gov["model"]["name"] or "", "ai_endpoint": name}

        mode, external_id, provenance = "simulated", f"sim-llm-{name}", {"engine": "modeled"}
        created, reason = self._try_create_real(request, name, cfg, gov)
        if created:
            mode, external_id, provenance = "real", created["name"], created

        return ProvisionResult(
            asset_id=new_asset_id("llm_gateway_endpoint", project_id, context),
            mode_reason=reason,
            degraded=mode != "real" and reason != "configured_simulated",
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

    def _try_create_real(self, request, name, cfg, gov) -> tuple[dict | None, str]:
        """Create the real governed gateway. Databricks-hosted FMs go through the Unity
        Catalog AI Gateway (a `model service`: governed UC securable + rate limits +
        inference-table logging + routing to a pay-per-token FM). External models
        (OpenAI/Anthropic) go through a serving endpoint + AI Gateway block (needs a provider
        API-key secret). Returns (created, reason); a None create always carries the reason."""
        from .. import config
        if not config.ALLOW_REAL:
            return None, "kill_switch_off"
        provider = cfg.get("provider", "databricks")
        if provider in ("openai", "anthropic"):
            return self._create_external_serving(request, name, cfg, gov)
        return self._create_uc_model_service(request, name, cfg, gov)

    def _resolve_fm_model(self, w, requested: str) -> tuple[str, str]:
        """Map the requested model to a `models/system.ai.<name>` reference for a REGISTERED
        Databricks FM — preferring the request's choice, falling back to a known chat model
        when it isn't registered (older allow-list entries can be retired)."""
        want = (requested or "").replace("system.ai.", "").strip()
        names = set()
        try:
            names = {m.name for m in w.registered_models.list(catalog_name="system", schema_name="ai")}
        except Exception:  # noqa: BLE001
            pass
        if want and (want in names or not names):
            return f"models/system.ai.{want}", want
        for d in ("databricks-claude-opus-4-8", "databricks-claude-sonnet-4", "databricks-claude-opus-5"):
            if not names or d in names:
                return f"models/system.ai.{d}", d
        d = want or "databricks-claude-opus-4-8"
        return f"models/system.ai.{d}", d

    def _create_uc_model_service(self, request, name, cfg, gov) -> tuple[dict | None, str]:
        """POST /api/2.1/unity-catalog/model-services — the Unity Catalog AI Gateway. The
        governed gateway lives in the project's UC schema and routes to a pay-per-token FM.
        NOTE: PII/safety guardrails are recorded as governance intent but are NOT part of the
        model-service config in this API version — rate limits, inference-table logging,
        governed UC access, and routing ARE enforced."""
        from .. import config, naming
        catalog = (request.get("parent_catalog") or config.PARENT_CATALOG or "").strip()
        if not catalog:
            return None, "missing_prerequisite"           # no catalog to place the gateway in
        schema = naming.resolve_name("schema", request, {})
        parent = f"schemas/{catalog}.{schema}"
        w = _sdk.client(request.get("target_workspace"))
        model_ref, resolved = self._resolve_fm_model(w, cfg.get("model"))
        qpm = int(gov["rate_limits"]["qpm"] or 100)
        body = {
            "config": {
                "rate_limits": [{"key": "RATE_LIMIT_KEY_SERVICE",
                                 "renewal_period": "RATE_LIMIT_RENEWAL_PERIOD_MINUTE",
                                 "requests": str(qpm)}],
                "routing": {"destinations": [{
                    "name": "primary",
                    "destination_type": "DESTINATION_TYPE_PAY_PER_TOKEN_FOUNDATION_MODEL",
                    "pay_per_token_config": {"model": model_ref},
                    "traffic_percentage": 100}]},
            },
            "comment": f"PAVE governed AI gateway for {request.get('project_id')} -> {resolved}",
        }
        if gov["inference_logging"]:
            # The inference (payload-logging) table lives in a UC schema too — `parent` is
            # required and must be `schemas/{catalog}.{schema}` (same schema as the service).
            body["config"]["inference_table"] = {
                "parent": parent, "table_name_prefix": name[:40], "disabled": False}
        try:
            r = w.api_client.do("POST", "/api/2.1/unity-catalog/model-services",
                                query={"parent": parent, "model_service_id": name}, body=body)
            full = (r.get("name") or "").replace("model-services/", "") or f"{catalog}.{schema}.{name}"
            return {"name": full, "id": r.get("id"), "model": resolved,
                    "engine": "uc_ai_gateway.model_service",
                    "api_types": r.get("supported_api_types"),
                    "guardrails_note": "recorded intent (not enforced by model-service config)"}, "real"
        except Exception as e:  # noqa: BLE001
            reason = classify_error(e)
            logger.warning("UC AI gateway model-service create failed (%s: %s); modelling instead",
                           reason, e)
            return None, reason

    def _create_external_serving(self, request, name, cfg, gov) -> tuple[dict | None, str]:
        """External model (OpenAI/Anthropic) via a serving endpoint + AI Gateway block — needs
        a provider API-key secret (AI_GATEWAY_SECRET_SCOPE/KEY)."""
        scope, key = os.getenv("AI_GATEWAY_SECRET_SCOPE"), os.getenv("AI_GATEWAY_SECRET_KEY")
        if not (scope and key):
            return None, "missing_prerequisite"
        provider = cfg.get("provider")
        try:
            from databricks.sdk.service.serving import (
                EndpointCoreConfigInput, ServedEntityInput, ExternalModel,
                AiGatewayConfig, AiGatewayRateLimit, AiGatewayGuardrails,
                AiGatewayGuardrailParameters, AiGatewayInferenceTableConfig,
                AiGatewayUsageTrackingConfig, AiGatewayGuardrailPiiBehavior,
                AiGatewayGuardrailPiiBehaviorBehavior, AiGatewayRateLimitKey,
                AiGatewayRateLimitRenewalPeriod,
            )
        except Exception:  # noqa: BLE001
            return None, "sdk_unavailable"
        w = _sdk.client(request.get("target_workspace"))
        try:
            served = ServedEntityInput(
                name=f"{name}-entity",
                external_model=ExternalModel(
                    name=cfg.get("model"), provider=provider, task=cfg.get("task", "llm/v1/chat"),
                    **{f"{provider}_config": {f"{provider}_api_key": f"{{{{secrets/{scope}/{key}}}}}"}}))
            _pii = (AiGatewayGuardrailPiiBehavior(behavior=AiGatewayGuardrailPiiBehaviorBehavior.BLOCK)
                    if "pii_block" in gov["guardrails"]
                    else AiGatewayGuardrailPiiBehavior(behavior=AiGatewayGuardrailPiiBehaviorBehavior.MASK)
                    if "pii_redact" in gov["guardrails"] else None)
            ai_gw = AiGatewayConfig(
                rate_limits=[AiGatewayRateLimit(calls=gov["rate_limits"]["qpm"],
                                                key=AiGatewayRateLimitKey.USER,
                                                renewal_period=AiGatewayRateLimitRenewalPeriod.MINUTE)],
                guardrails=AiGatewayGuardrails(input=AiGatewayGuardrailParameters(
                    pii=_pii, safety=("safety" in gov["guardrails"]) or None)),
                usage_tracking_config=AiGatewayUsageTrackingConfig(enabled=True),
                inference_table_config=(AiGatewayInferenceTableConfig(
                    enabled=True, catalog_name=os.getenv("AUDIT_CATALOG", ""),
                    schema_name=os.getenv("AUDIT_SCHEMA", "pave")) if gov["inference_logging"] else None))
            ep = w.serving_endpoints.create(
                name=name, config=EndpointCoreConfigInput(name=name, served_entities=[served]),
                ai_gateway=ai_gw)
            return {"name": getattr(ep, "name", None) or name,
                    "engine": "serving_endpoints.create+ai_gateway"}, "real"
        except Exception as e:  # noqa: BLE001
            reason = classify_error(e)
            logger.warning("external AI gateway endpoint create failed (%s: %s); modelling", reason, e)
            return None, reason

    def decommission(self, *, asset: dict[str, Any], context: dict[str, Any]) -> None:
        if asset.get("mode") != "real" or not asset.get("external_id"):
            return
        ext = asset["external_id"]
        w = _sdk.client(context.get("target_workspace"))
        try:
            if str(ext).count(".") >= 2:      # catalog.schema.leaf -> UC AI Gateway model service
                w.api_client.do("DELETE", f"/api/2.1/unity-catalog/model-services/{ext}")
            else:                             # serving endpoint (external-model path)
                w.serving_endpoints.delete(name=ext)
        except Exception as e:  # noqa: BLE001
            logger.warning("AI gateway delete failed: %s", e)
