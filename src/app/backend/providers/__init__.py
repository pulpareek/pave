"""Pluggable provider registry for PAVE provisioning.

Each resource type maps to a Provider with provision()/decommission() and a
per-type mode (real | simulated). Real providers use the Databricks SDK; the
mode is resolved from defaults + PROVIDER_MODES overrides so an operator can flip
a type real<->simulated without code changes.
"""
from .base import MODE_REASONS, ProviderUnavailable, classify_error  # noqa: F401
from .registry import (  # noqa: F401
    Binding, DEFAULT_MODES, bind, get_provider, provision, resolve_mode,
)
