"""Shared Databricks SDK helpers for real providers.

Centralizes the researched mechanisms so providers stay small:
  - WorkspaceClient (auto-auths as the app/Job SP)
  - UC tag assignment via entity_tag_assignments, with SQL ALTER...SET TAGS fallback
  - UC grants via grants.update (a DIFF of add/remove)
All calls are synchronous; the service layer runs them via asyncio.to_thread.
"""
import logging
from typing import Optional

logger = logging.getLogger("pave.sdk")

# Cache of WorkspaceClients keyed by target host. The default (app's own workspace)
# is stored under the empty-string key. See client() for the multi-workspace story.
_clients: dict[str, object] = {}


def client(target_workspace: Optional[str] = None):
    """Build/cache a WorkspaceClient, optionally for a TARGET workspace.

    MULTI-WORKSPACE ROUTING (Phase 1 keystone — see docs/ADMIN_CAPABILITIES.md):
    - `target_workspace` is None/empty  -> the app's OWN workspace (auto-auths as the
      app/Job service principal; the default and only path that works out of the box).
    - `target_workspace` is a host      -> provision INTO a different workspace, e.g.
      "https://ws-123.cloud.databricks.com". This requires an identity that can auth to
      that host. Two supported ways (pick one; both are standard SDK auth):

        (a) A per-target OAuth service principal. Set env vars named by a slug of the
            host, then UNCOMMENT the block below. The customer creates one SP per target
            workspace and grants it provisioning rights there (SoD boundary).
        (b) Account-level identity federation (single SP entitled to many workspaces).

    If the target client cannot be built or authed, the caller's provider catches the
    error and falls back to SIMULATED (registry row + synthetic handle) so a demo/local
    run never breaks. That is why this is safe to ship untested against real targets.
    """
    from databricks.sdk import WorkspaceClient

    host = (target_workspace or "").strip()
    if host in _clients:
        return _clients[host]

    if not host:
        # Default: the app's own workspace (no host arg -> ambient auth).
        _clients[host] = WorkspaceClient()
        return _clients[host]

    # ---- Target a DIFFERENT workspace --------------------------------------------------
    # OUT OF THE BOX this just points a client at the host and relies on ambient auth
    # (works if the app's credentials are valid on that host, e.g. same account + the SP
    # is entitled there). For explicit per-target credentials, see option (a) below.
    #
    # (a) PER-TARGET SERVICE PRINCIPAL — uncomment + set env vars to enable:
    #     Env var names are derived from the host slug, e.g. host
    #     "https://ws-123.cloud.databricks.com" -> WS_123_CLOUD_DATABRICKS_COM_CLIENT_ID / _SECRET
    #
    # import os
    # slug = host.replace("https://", "").replace("http://", "").rstrip("/")
    # slug = "".join(c.upper() if c.isalnum() else "_" for c in slug)
    # cid = os.getenv(f"{slug}_CLIENT_ID")
    # sec = os.getenv(f"{slug}_CLIENT_SECRET")
    # if cid and sec:
    #     _clients[host] = WorkspaceClient(host=host, client_id=cid, client_secret=sec)
    #     return _clients[host]
    #
    # (b) ACCOUNT IDENTITY FEDERATION — a single SP entitled to many workspaces:
    #     the ambient auth below already works once the SP is federated + entitled.

    _clients[host] = WorkspaceClient(host=host)   # ambient auth against the target host
    return _clients[host]


def apply_uc_tags(entity_type: str, entity_name: str, tags: dict[str, str],
                  target_workspace: Optional[str] = None) -> dict:
    """Assign UC tags to a securable.

    Tries the entity_tag_assignments API first (covers governed + standard tags),
    then falls back to SQL ALTER ... SET TAGS via the statement-execution API.
    Returns {"applied": [...], "via": "api|sql|none", "errors": [...]}.
    entity_type: e.g. "catalogs" | "schemas" | "tables" (assignment API) ->
                 the SQL fallback maps schema->SCHEMA, etc.
    entity_name: full name (catalog or catalog.schema).
    target_workspace: optional host to run against (see client()); None = app's own.
    """
    w = client(target_workspace)
    applied, errors = [], []

    # 1) entity_tag_assignments API
    try:
        svc = getattr(w, "entity_tag_assignments", None)
        if svc is not None:
            for k, v in tags.items():
                svc.create(
                    entity_type=entity_type, entity_name=entity_name,
                    tag_key=k, tag_value=str(v),
                )
                applied.append(k)
            return {"applied": applied, "via": "api", "errors": errors}
    except Exception as e:  # noqa: BLE001
        logger.warning("entity_tag_assignments failed for %s (%s); trying SQL", entity_name, e)
        errors.append(f"api: {e}")

    # 2) SQL fallback
    try:
        sql_type = {"schemas": "SCHEMA", "catalogs": "CATALOG", "tables": "TABLE"}.get(
            entity_type, "SCHEMA")
        pairs = ", ".join(f"'{k}' = '{str(v)}'" for k, v in tags.items())
        run_sql(f"ALTER {sql_type} {entity_name} SET TAGS ({pairs})",
                target_workspace=target_workspace)
        return {"applied": list(tags), "via": "sql", "errors": errors}
    except Exception as e:  # noqa: BLE001
        errors.append(f"sql: {e}")
        return {"applied": applied, "via": "none", "errors": errors}


def run_sql(statement: str, warehouse_id: Optional[str] = None,
            target_workspace: Optional[str] = None) -> None:
    """Execute a SQL statement via the statement-execution API."""
    from .. import config
    wid = warehouse_id or config.WAREHOUSE_ID
    if not wid:
        raise RuntimeError("no warehouse_id for SQL execution (DATABRICKS_WAREHOUSE_ID)")
    w = client(target_workspace)
    w.statement_execution.execute_statement(statement=statement, warehouse_id=wid, wait_timeout="30s")


def apply_grants(securable_type: str, full_name: str,
                 grants: list[tuple[str, list[str]]],
                 target_workspace: Optional[str] = None) -> dict:
    """Apply UC grants. `grants` = [(principal, [privileges...]), ...].

    grants.update is a DIFF (add/remove); we only add here.
    """
    from databricks.sdk.service.catalog import PermissionsChange, Privilege
    w = client(target_workspace)

    # grants.update wants the securable type as a STRING ("SCHEMA"), not the enum object
    # (which serializes to the wrong repr). Privileges are Privilege enums, looked up by name.
    sec = securable_type.value if hasattr(securable_type, "value") else str(securable_type).upper()

    def _priv(p):
        if isinstance(p, Privilege):
            return p
        return getattr(Privilege, str(p).upper(), p)

    changes = [PermissionsChange(principal=p, add=[_priv(x) for x in privs])
               for p, privs in grants if privs]
    if not changes:
        return {"granted": 0}
    w.grants.update(securable_type=sec, full_name=full_name, changes=changes)
    return {"granted": len(changes)}
