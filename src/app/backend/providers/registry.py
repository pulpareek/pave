"""Resolve which provider + mode handles each resource type.

DEFAULT_MODES encodes the hybrid demo policy (schema/app real; rest simulated).
PROVIDER_MODES env JSON overrides per type. Real providers are imported lazily so
the app boots even where the SDK/credentials aren't available; if a real provider
fails to import we fall back to simulated and note it.
"""
import logging
from typing import Optional

from .base import Provider
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
        if resource_type == "app":
            from .app import AppProvider
            return AppProvider()
        if resource_type == "cluster":
            from .cluster_real import RealComputeProvider
            return RealComputeProvider()
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


def get_provider(resource_type: str, mode: Optional[str] = None) -> tuple[Provider, str]:
    """Return (provider, effective_mode) for a resource type.

    Modes: real (SDK) | dabs (Python-DABs showcase, schema only) | simulated.

    SAFETY: real/dabs only run when config.ALLOW_REAL is set (PAVE_ALLOW_REAL=1).
    Otherwise they degrade to simulated so local/demo runs never mutate the workspace.
    """
    mode = mode or resolve_mode(resource_type)
    if mode in ("real", "dabs") and not config.ALLOW_REAL:
        logger.info("PAVE_ALLOW_REAL not set -> %s forced to simulated (mode was %s)",
                    resource_type, mode)
        return _simulated_provider(resource_type), "simulated"
    if mode == "dabs" and resource_type == "schema":
        try:
            from .schema_dabs import SchemaDabsProvider
            return SchemaDabsProvider(), "dabs"
        except Exception as e:  # noqa: BLE001
            logger.warning("Python-DABs schema provider unavailable (%s); using real SDK", e)
            mode = "real"
    if mode == "real":
        rp = _real_provider(resource_type)
        if rp is not None:
            return rp, "real"
        return _simulated_provider(resource_type), "simulated"
    return _simulated_provider(resource_type), "simulated"
