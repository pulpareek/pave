"""Resolve which provider + mode handles each resource type, and own the fallback.

DEFAULT_MODES encodes the hybrid demo policy (schema/app real; rest simulated).
PROVIDER_MODES env JSON overrides per type. Real providers are imported lazily so
the app boots even where the SDK/credentials aren't available.

Fallback to simulated happens in exactly one place — `provision()` below — and always
records a reason code. Providers must not decide on their own to return a synthetic
handle when a real create fails; they raise ProviderUnavailable instead, so the
degradation is visible in the asset row, the audit log, and the UI.
"""
import logging
from dataclasses import dataclass
from typing import Any, Optional

from .base import Provider, ProviderUnavailable, ProvisionResult
from .simulated import SimulatedProvider
from .. import config

logger = logging.getLogger("pave.providers")

# Hybrid demo defaults: cheap/safe = real, risky/costly/slow = simulated.
# `app` defaults to simulated (real app creation provisions compute and is slow);
# flip to real with PROVIDER_MODES='{"app":"real"}'.
DEFAULT_MODES: dict[str, str] = {
    "schema": "real",
    "app": "simulated",
    "cluster": "simulated",   # policy is ensured/created even in sim; flip cluster=real to create
    "job_cluster": "simulated",
    "lakebase": "simulated",
    "catalog": "simulated",   # new-catalog is an escalation; simulated by default
    # Workspace = account-level (AccountClient). Simulated by default: real create needs
    # an account-admin identity + cloud configs PAVE doesn't hold. Self-models otherwise.
    "workspace": "simulated",
    # AI assets — real-capable (behind ALLOW_REAL), graceful fallback to modeled.
    "llm_gateway_endpoint": "real",
    "vector_search": "real",
    "sql_warehouse": "simulated",   # real create is cheap (serverless + auto-stop); flip to real
}


def resolve_mode(resource_type: str) -> str:
    overrides = config.provider_mode_overrides()
    return overrides.get(resource_type, DEFAULT_MODES.get(resource_type, "simulated"))


def _real_provider(resource_type: str) -> Optional[Provider]:
    """Import a real (SDK-backed) provider if one exists; else None."""
    try:
        if resource_type == "schema":
            from .schema import SchemaProvider
            return SchemaProvider()
        if resource_type == "catalog":
            from .catalog import CatalogProvider
            return CatalogProvider()
        if resource_type == "app":
            from .app import AppProvider
            return AppProvider()
        if resource_type == "cluster":
            from .cluster_real import RealComputeProvider
            return RealComputeProvider()
        if resource_type == "lakebase":
            from .lakebase import LakebaseProvider
            return LakebaseProvider()
        if resource_type == "sql_warehouse":
            from .sql_warehouse import SqlWarehouseProvider
            return SqlWarehouseProvider()
        if resource_type == "llm_gateway_endpoint":
            from .ai_gateway import AIGatewayEndpointProvider
            return AIGatewayEndpointProvider()
        if resource_type == "vector_search":
            from .vector_search import VectorSearchProvider
            return VectorSearchProvider()
        if resource_type == "workspace":
            from .workspace import WorkspaceProvider
            return WorkspaceProvider()
    except Exception as e:  # noqa: BLE001
        logger.warning("Real provider for %s unavailable (%s); using simulated", resource_type, e)
    return None


def _simulated_provider(resource_type: str) -> Provider:
    if resource_type in ("cluster", "job_cluster"):
        from .cluster import SimulatedComputeProvider
        return SimulatedComputeProvider(resource_type)
    # AI providers self-model their full governance when real creation is disabled,
    # so use them even in simulated mode (they skip the real SDK call when !ALLOW_REAL).
    if resource_type == "llm_gateway_endpoint":
        try:
            from .ai_gateway import AIGatewayEndpointProvider
            return AIGatewayEndpointProvider()
        except Exception:  # noqa: BLE001
            pass
    if resource_type == "vector_search":
        try:
            from .vector_search import VectorSearchProvider
            return VectorSearchProvider()
        except Exception:  # noqa: BLE001
            pass
    # Workspace self-models (records deployment name / region / config refs) even when
    # simulated, so the account-level story renders without account access.
    if resource_type == "workspace":
        try:
            from .workspace import WorkspaceProvider
            return WorkspaceProvider()
        except Exception:  # noqa: BLE001
            pass
    return SimulatedProvider(resource_type)


@dataclass
class Binding:
    """Which provider will run, in which mode, and why."""
    provider: Provider
    mode: str
    reason: str

    @property
    def degraded(self) -> bool:
        """True when a real create was intended but will not happen."""
        return self.mode != "real" and self.reason not in ("configured_simulated", "real")


def bind(resource_type: str, mode: Optional[str] = None) -> Binding:
    """Choose the provider for a resource type and record why that choice was made.

    Modes: real (SDK) | dabs (Python-DABs showcase, schema only) | simulated.

    SAFETY: real/dabs only run when config.ALLOW_REAL is set (PAVE_ALLOW_REAL=1).
    Otherwise they degrade to simulated so local/demo runs never mutate the workspace.
    """
    requested = mode or resolve_mode(resource_type)
    if requested in ("real", "dabs") and not config.ALLOW_REAL:
        logger.info("PAVE_ALLOW_REAL not set -> %s modelled instead of created (was %s)",
                    resource_type, requested)
        return Binding(_simulated_provider(resource_type), "simulated", "kill_switch_off")
    if requested == "dabs" and resource_type == "schema":
        try:
            from .schema_dabs import SchemaDabsProvider
            return Binding(SchemaDabsProvider(), "dabs", "real")
        except Exception as e:  # noqa: BLE001
            logger.warning("Python-DABs schema provider unavailable (%s); using real SDK", e)
            requested = "real"
    if requested == "real":
        rp = _real_provider(resource_type)
        if rp is not None:
            return Binding(rp, "real", "real")
        return Binding(_simulated_provider(resource_type), "simulated", "provider_unavailable")
    return Binding(_simulated_provider(resource_type), "simulated", "configured_simulated")


def get_provider(resource_type: str, mode: Optional[str] = None) -> tuple[Provider, str]:
    """Back-compat 2-tuple accessor, used by decommission paths that only need to
    know which provider owns an existing asset."""
    b = bind(resource_type, mode)
    return b.provider, b.mode


def provision(resource_type: str, *, request: dict[str, Any], resource: dict[str, Any],
              tag_set: dict[str, str], context: dict[str, Any]) -> ProvisionResult:
    """Run the bound provider, degrading to a modelled result only on ProviderUnavailable.

    Synchronous (providers wrap the sync SDK); the saga calls this inside a thread.
    Any other exception is a genuine failure and propagates, so a broken provider shows
    up as a FAILED resource rather than a silently modelled one.
    """
    binding = bind(resource_type)
    try:
        result = binding.provider.provision(request=request, resource=resource,
                                            tag_set=tag_set, context=context)
        mode = result.get("mode") or binding.mode
        # A provider that self-models (AI gateway, vector search, workspace) reports its
        # own reason; anything else inherits the binding's.
        reason = result.get("mode_reason") or (binding.reason if mode == binding.mode
                                               else "configured_simulated")
        result["mode"] = mode
        result["mode_reason"] = reason
        result["degraded"] = bool(result.get("degraded",
                                             mode != "real" and reason not in
                                             ("configured_simulated", "real")))
        return result
    except ProviderUnavailable as e:
        logger.warning("%s: real provisioning unavailable (%s) -> modelling instead: %s",
                       resource_type, e.reason, e)
        fallback = _simulated_provider(resource_type)
        result = fallback.provision(request=request, resource=resource,
                                    tag_set=tag_set, context=context)
        result["mode"] = "simulated"
        result["mode_reason"] = e.reason
        result["degraded"] = True
        provenance = dict(result.get("provenance") or {})
        provenance["degraded_from_real"] = {"reason": e.reason, "detail": str(e)[:300]}
        result["provenance"] = provenance
        return result
