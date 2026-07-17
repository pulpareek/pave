"""Pluggable provider registry for PAVE provisioning.

Each resource type maps to a Provider with provision()/decommission() and a
per-type mode (real | simulated). Real providers use the Databricks SDK; the
mode is resolved from defaults + PROVIDER_MODES overrides so an operator can flip
a type real<->simulated without code changes.
"""
from .registry import get_provider, resolve_mode, DEFAULT_MODES  # noqa: F401
