"""Uniqueness / collision detection for vended assets.

"We should not allow a catalog/workspace/endpoint with a name that already exists."
Two layers, registry-first:

  1. Registry (always, offline-safe): PAVE's own Lakebase `assets` store — the
     authoritative record of everything PAVE has vended. Runs in demo mode too.
  2. Live (best-effort, real mode only): query the target Databricks workspace/account
     for a pre-existing resource PAVE didn't create. Any auth/network failure degrades
     silently to registry-only — a legitimate offline submit is never blocked by it.

Collision *scope* differs per asset and is respected:
  workspace -> account-global · catalog -> metastore · schema -> parent catalog ·
  serving/vector-search/app/lakebase -> workspace.

This complements (does not replace) providers' idempotent "adopt if exists": that adoption
is for saga *retries* of the same request; this gate stops a *new* request from targeting a
name already in use.
"""
import asyncio
import logging
from typing import Any, Optional

from . import naming
from . import config

logger = logging.getLogger("pave.uniqueness")

# Resource types whose names must be unique (clusters are intentionally excluded —
# Databricks allows duplicate cluster names, so a collision there is not an error).
COLLISION_SCOPE: dict[str, str] = {
    "workspace": "account",
    "catalog": "metastore",
    "schema": "catalog",
    "llm_gateway_endpoint": "workspace",
    "vector_search": "workspace",
    "app": "workspace",
    "lakebase": "workspace",
    "sql_warehouse": "workspace",
}


def _norm(v) -> Optional[str]:
    return v.value if hasattr(v, "value") else v


def view_from_payload(payload, project_id: str) -> dict[str, Any]:
    """Request view from a RequestIn (create path)."""
    g = lambda k, d=None: getattr(payload, k, d)
    return {
        "project_id": project_id,
        "business_domain": g("business_domain"),
        "medallion_layer": g("medallion_layer"),
        "environment": _norm(g("environment")),
        "data_classification": _norm(g("data_classification")),
        "region": g("region"),
        "business_function": g("business_function"),
        "business_sub_function": g("business_sub_function"),
        "parent_catalog": g("parent_catalog") or config.PARENT_CATALOG,
        "target_workspace": g("target_workspace"),
    }


def view_from_record(rec: dict, project_id: str) -> dict[str, Any]:
    """Request view from a persisted request dict (add-to-existing path)."""
    return {
        "project_id": project_id,
        "business_domain": rec.get("business_domain"),
        "medallion_layer": rec.get("medallion_layer"),
        "environment": rec.get("environment"),
        "data_classification": rec.get("data_classification"),
        "region": rec.get("region"),
        "business_function": rec.get("business_function"),
        "business_sub_function": rec.get("business_sub_function"),
        "parent_catalog": rec.get("parent_catalog") or config.PARENT_CATALOG,
        "target_workspace": rec.get("target_workspace"),
    }


def _resource_pair(r) -> tuple[str, dict]:
    """Normalize a ResourceRequest object or a plain dict -> (type, config)."""
    if hasattr(r, "type"):
        return r.type.value, (r.config or {})
    return r.get("type"), (r.get("config") or {})


def _resolved_names(view: dict, resources) -> list[tuple[str, str, dict]]:
    """[(asset_type, effective_name, cfg), ...] for every collision-checked resource."""
    out = []
    for r in resources:
        rt, cfg = _resource_pair(r)
        if rt not in COLLISION_SCOPE:
            continue
        out.append((rt, naming.resolve_name(rt, view, cfg), cfg))
    return out


async def check_collisions(view: dict, resources, db) -> list[str]:
    """Return collision errors (empty == all names free), with a suggested free name each.

    `view` comes from view_from_payload (create) or view_from_record (add-to-existing);
    `resources` is a list of ResourceRequest objects or plain {type, config} dicts.
    """
    errors: list[str] = []
    req = view
    catalog = req["parent_catalog"]
    seen_this_request: set[tuple[str, str]] = set()

    for asset_type, name, cfg in _resolved_names(view, resources):
        # Scope schemas to their parent catalog only when one is actually set; an empty
        # PARENT_CATALOG (demo) means "no catalog constraint" — match by type+name.
        scope_cat = catalog if (asset_type == "schema" and catalog) else None

        # in-payload dup (two resources resolving to the same name)
        key = (asset_type, name)
        if key in seen_this_request:
            errors.append(f"two {asset_type} resources resolve to the same name '{name}' — "
                          f"give them distinct names")
            continue
        seen_this_request.add(key)

        # 1) registry (authoritative for what PAVE has vended)
        hit = await db.find_active_asset_by_name(asset_type, name, catalog=scope_cat)
        exists = hit is not None
        via = "PAVE registry"

        # 2) live workspace/account (best-effort; only when real mode is enabled)
        if not exists and config.ALLOW_REAL:
            live = await _live_exists(asset_type, name, req)
            if live:
                exists, via = True, "the target workspace"

        if exists:
            suggestion = await _suggest_available(asset_type, name, req, db)
            errors.append(
                f"{asset_type} name '{name}' already exists in {via} "
                f"({COLLISION_SCOPE[asset_type]} scope) — try '{suggestion}'")
    return errors


async def _suggest_available(asset_type: str, base: str, req: dict, db) -> str:
    """base, base-2, base-3, ... (base_2 for snake_case) until the registry is free."""
    sep = "_" if naming._rules(asset_type)["casing"] == "snake" else "-"
    catalog = req["parent_catalog"] if (asset_type == "schema" and req["parent_catalog"]) else None
    max_len = naming._rules(asset_type)["max_len"]
    for i in range(2, 12):
        cand = f"{base[: max_len - len(str(i)) - 1]}{sep}{i}"
        if await db.find_active_asset_by_name(asset_type, cand, catalog=catalog) is None:
            return cand
    return f"{base}{sep}new"


async def _live_exists(asset_type: str, name: str, req: dict) -> bool:
    """Best-effort live existence check. True only if we positively confirm it exists.

    Unknown / auth failure / network error -> False (degrade to registry-only). Never raises.
    """
    try:
        return await asyncio.to_thread(_live_exists_sync, asset_type, name, req)
    except Exception as e:  # noqa: BLE001 — live check is advisory; never block on its failure
        logger.info("live collision check for %s '%s' skipped: %s", asset_type, name, e)
        return False


def _live_exists_sync(asset_type: str, name: str, req: dict) -> bool:
    """Synchronous SDK probe. A successful get() == exists; NotFound == free; any other
    error propagates to _live_exists which treats it as 'can't tell' (degrade)."""
    from databricks.sdk.errors import NotFound
    from .providers import _sdk

    host = req.get("target_workspace")

    def probe(getter) -> bool:
        try:
            getter()
            return True          # found -> collision
        except NotFound:
            return False         # confirmed free

    if asset_type == "workspace":
        # Account-scoped: needs an AccountClient (account-admin). If unavailable, the
        # outer try/except degrades to registry-only.
        from databricks.sdk import AccountClient
        a = AccountClient()
        return any((ws.deployment_name or "").lower() == name.lower()
                   for ws in a.workspaces.list())

    w = _sdk.client(host)
    if asset_type == "catalog":
        return probe(lambda: w.catalogs.get(name))
    if asset_type == "schema":
        full = f"{req['parent_catalog']}.{name}"
        return probe(lambda: w.schemas.get(full_name=full))
    if asset_type == "llm_gateway_endpoint":
        return probe(lambda: w.serving_endpoints.get(name))
    if asset_type == "vector_search":
        return probe(lambda: w.vector_search_endpoints.get_endpoint(endpoint_name=name))
    if asset_type == "app":
        return probe(lambda: w.apps.get(name))
    if asset_type == "lakebase":
        return probe(lambda: w.database.get_database_instance(name=name))
    if asset_type == "sql_warehouse":
        # Warehouse names aren't unique in Databricks, but PAVE still prevents vending a
        # second one with the same governed name.
        return any((wh.name or "").lower() == name.lower() for wh in w.warehouses.list())
    return False
