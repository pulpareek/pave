"""Reconcile the registry against reality — the loop that replaces an IaC state file.

PAVE provisions imperatively through the SDK and treats the Lakebase registry as the
desired-state store. That is a defensible choice only if something continuously checks
that the registry still describes the world; otherwise it is just a database of things
somebody once intended. This module is that check, and it is the honest answer to "why
didn't you use Terraform".

Three findings, matching what a platform team actually chases:

  drifted    the resource exists but its governance tags no longer match the registry
             (someone edited them by hand, so cost attribution has quietly broken)
  missing    the registry says ACTIVE but the resource is gone (deleted out of band)
  untracked  the resource exists in the workspace with no registry row (shadow IT, the
             thing the whole portal exists to eliminate)

All SDK calls here are READ-ONLY (`get`/`list`), so this works with a low-privilege
identity — reconciliation is useful precisely where provisioning rights are not granted.
Simulated assets are reconciled against an in-process world model that accepts INJECTED
drift, so the loop demonstrates end to end on a laptop with no workspace access at all.
"""
import logging
from typing import Any, Optional

logger = logging.getLogger("pave.reconcile")

# Injected drift for simulated assets, keyed by asset_id:
#   {"deleted": True}            -> the modelled resource "disappeared"
#   {"tags": {"cost_center": ""}} -> the modelled resource's tags were "edited"
# Process-local and deliberately ephemeral: it is a demo instrument, not state.
_INJECTED: dict[str, dict] = {}


def inject_drift(asset_id: str, *, deleted: bool = False,
                 tags: Optional[dict] = None) -> dict:
    """Make a simulated asset drift, so the reconcile loop has something to find."""
    entry = _INJECTED.setdefault(asset_id, {})
    if deleted:
        entry["deleted"] = True
    if tags is not None:
        entry["tags"] = dict(tags)
    return entry


def clear_drift(asset_id: Optional[str] = None) -> None:
    if asset_id:
        _INJECTED.pop(asset_id, None)
    else:
        _INJECTED.clear()


def injected() -> dict[str, dict]:
    return dict(_INJECTED)


# How to READ each resource type's live state. Every entry is a get/list, never a write.
def _observe_real(asset: dict[str, Any]) -> dict[str, Any]:
    """Read a real resource's current existence + tags. Raises on transport failure."""
    from ..providers import _sdk
    rtype = asset.get("type")
    ext = asset.get("external_id") or ""
    names = asset.get("names") or {}
    host = (asset.get("provenance") or {}).get("target_workspace") or None
    w = _sdk.client(host)

    if rtype == "schema":
        s = w.schemas.get(full_name=ext or names.get("full_name"))
        return {"exists": True, "tags": _tags_of(s)}
    if rtype == "catalog":
        c = w.catalogs.get(ext or names.get("name"))
        return {"exists": True, "tags": _tags_of(c)}
    if rtype == "sql_warehouse":
        wh = w.warehouses.get(id=ext)
        return {"exists": True, "tags": {t.key: t.value for t in
                                         (getattr(getattr(wh, "tags", None), "custom_tags", None) or [])}}
    if rtype == "cluster":
        c = w.clusters.get(cluster_id=ext)
        return {"exists": True, "tags": dict(getattr(c, "custom_tags", None) or {})}
    if rtype == "app":
        w.apps.get(name=ext)
        return {"exists": True, "tags": {}}
    if rtype == "lakebase":
        w.database.get_database_instance(name=ext)
        return {"exists": True, "tags": {}}
    if rtype == "llm_gateway_endpoint":
        w.serving_endpoints.get(name=ext)
        return {"exists": True, "tags": {}}
    if rtype == "vector_search":
        w.vector_search_endpoints.get_endpoint(endpoint_name=ext)
        return {"exists": True, "tags": {}}
    return {"exists": None, "tags": {}}   # no read implemented for this type


def _tags_of(obj) -> dict:
    """UC objects expose properties/tags inconsistently across SDK versions."""
    for attr in ("properties", "tags"):
        val = getattr(obj, attr, None)
        if isinstance(val, dict):
            return {k: str(v) for k, v in val.items()}
    return {}


def _observe_simulated(asset: dict[str, Any]) -> dict[str, Any]:
    """Read a modelled resource from the in-process world (plus any injected drift)."""
    drift = _INJECTED.get(asset.get("asset_id") or "", {})
    if drift.get("deleted"):
        return {"exists": False, "tags": {}}
    tags = dict(asset.get("applied_tags") or {})
    tags.update(drift.get("tags") or {})
    return {"exists": True, "tags": tags}


def observe(asset: dict[str, Any]) -> dict[str, Any]:
    """Current live state of one asset: {exists, tags, source, error}."""
    if asset.get("mode") != "real":
        return {**_observe_simulated(asset), "source": "modelled"}
    try:
        return {**_observe_real(asset), "source": "sdk"}
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        # A "not found" is a genuine finding; anything else means we could not look.
        if any(s in msg for s in ("does not exist", "not found", "no such", "404")):
            return {"exists": False, "tags": {}, "source": "sdk"}
        logger.info("could not read %s: %s", asset.get("asset_id"), e)
        return {"exists": None, "tags": {}, "source": "sdk", "error": str(e)[:200]}


def diff_tags(expected: dict, actual: dict) -> list[dict]:
    """Governance tags that no longer match. Only keys PAVE set are compared — extra
    tags added by a team are not drift, they are just extra."""
    out = []
    for key, want in (expected or {}).items():
        got = (actual or {}).get(key)
        if got is None:
            out.append({"key": key, "expected": want, "actual": None, "issue": "removed"})
        elif str(got) != str(want):
            out.append({"key": key, "expected": want, "actual": got, "issue": "changed"})
    return out


def reconcile_assets(assets: list[dict]) -> dict[str, Any]:
    """Diff every tracked asset against its live state."""
    drifted, missing, unreadable, in_sync = [], [], [], 0
    for a in assets:
        if a.get("status") not in ("ACTIVE", "PARTIAL"):
            continue
        state = observe(a)
        base = {"asset_id": a.get("asset_id"), "type": a.get("type"),
                "name": (a.get("names") or {}).get("name"),
                "mode": a.get("mode"), "owner_id": a.get("owner_id"),
                "project_id": a.get("project_id"), "source": state.get("source")}
        if state.get("exists") is None:
            unreadable.append({**base, "error": state.get("error", "no read implemented")})
            continue
        if not state["exists"]:
            missing.append({**base, "detail": "registry says ACTIVE but the resource is gone"})
            continue
        tag_diff = diff_tags(a.get("applied_tags") or {}, state.get("tags") or {})
        # Modelled assets have no external tag plane to drift unless drift was injected,
        # so an empty observed tag set is not evidence of drift for them.
        if tag_diff and (a.get("mode") == "real" or a.get("asset_id") in _INJECTED):
            drifted.append({**base, "tag_drift": tag_diff,
                            "impact": "cost attribution and access policy keyed on these "
                                      "tags no longer match the registry"})
            continue
        in_sync += 1
    return {"drifted": drifted, "missing": missing, "unreadable": unreadable,
            "in_sync": in_sync}


def find_untracked(known_external_ids: set[str], *, parent_catalog: str) -> list[dict]:
    """Schemas in the governed catalog with no registry row — resources created outside
    the paved road. Read-only, and best-effort: no permission to list simply means we
    report nothing rather than claiming a clean estate."""
    from .. import config
    from ..providers import _sdk
    if not (config.ALLOW_REAL and parent_catalog):
        return []
    out = []
    try:
        for s in _sdk.client().schemas.list(catalog_name=parent_catalog):
            full = getattr(s, "full_name", "") or f"{parent_catalog}.{getattr(s, 'name', '')}"
            if getattr(s, "name", "") in ("information_schema", "default"):
                continue
            if full not in known_external_ids:
                out.append({"type": "schema", "name": full,
                            "detail": "exists in the governed catalog with no PAVE registry "
                                      "row: created outside the paved road"})
    except Exception as e:  # noqa: BLE001
        logger.info("untracked scan skipped: %s", e)
    return out
