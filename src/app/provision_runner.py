"""PAVE provisioning Job entrypoint (the SoD-hardened, run-as-provisioner path).

Triggered by the app via run_now with job parameters. Parses params, then calls
the SAME backend.services.provisioning_service used by the in-process path so the
engine has one implementation.

Job parameters (named): action, request_id, catalog, schema, parent_catalog,
lakebase_instance. Passed by Databricks Jobs; we parse argv defensively and fall
back to environment variables / dbutils widgets.
"""
import asyncio
import os
import sys


def _ensure_backend_importable() -> None:
    """Put the directory that holds `backend/` on sys.path.

    A serverless spark_python_task execs this file inside a kernel where `__file__` is
    NOT defined, so resolving the path from it raises NameError before the job does any
    work. Try the module path when it exists, then argv[0], then the working directory,
    and pick the first that actually contains the package.
    """
    candidates = []
    here = globals().get("__file__")
    if here:
        candidates.append(os.path.dirname(os.path.abspath(here)))
    if sys.argv and sys.argv[0]:
        candidates.append(os.path.dirname(os.path.abspath(sys.argv[0])))
    candidates.append(os.getcwd())

    for d in candidates:
        if d and os.path.isdir(os.path.join(d, "backend")):
            sys.path.insert(0, d)
            return
    # Nothing matched: keep the candidates on the path so the ImportError names them.
    for d in candidates:
        if d:
            sys.path.insert(0, d)


_ensure_backend_importable()


def _parse_params(argv: list[str]) -> dict:
    params: dict[str, str] = {}
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("--"):
            key = tok[2:]
            if "=" in key:
                k, v = key.split("=", 1)
                params[k] = v
                i += 1
            elif i + 1 < len(argv):
                params[key] = argv[i + 1]
                i += 2
            else:
                params[key] = ""
                i += 1
        else:
            i += 1
    return params


def _resolve(params: dict, key: str, default: str = "") -> str:
    if params.get(key):
        return params[key]
    env = os.getenv(key.upper())
    if env:
        return env
    try:  # Databricks notebook/job widget fallback
        from pyspark.dbutils import DBUtils  # type: ignore
        from pyspark.sql import SparkSession  # type: ignore
        dbutils = DBUtils(SparkSession.builder.getOrCreate())
        return dbutils.widgets.get(key)
    except Exception:  # noqa: BLE001
        return default


async def _mark_failed(request_id: str, action: str, error: str) -> None:
    """Park the request as FAILED so a dead Job is visible instead of silent.

    Without this the app triggers the Job, returns "job_triggered", and nothing ever
    watches: if the Job dies the request sits in APPROVED forever with no asset, no
    error, and no retry affordance. The request is the only place a user looks.
    """
    from backend.database import db
    from backend.models import RequestStatus

    await db.update_request_status(request_id, RequestStatus.FAILED.value)
    await db.add_audit(
        actor="provisioner-sp", event_type="provisioning.job_failed",
        request_id=request_id, to_state=RequestStatus.FAILED.value,
        reason=f"the provisioning Job raised during {action}",
        payload={"error": error[:2000], "action": action})


def _run(coro):
    """Drive a coroutine to completion whether or not the host already has a running loop.
    Databricks serverless execs this file INSIDE a live event loop, so a bare asyncio.run()
    raises "cannot be called from a running event loop"; in that case run it on a dedicated
    worker thread (which has no loop of its own)."""
    import concurrent.futures
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)   # no loop here (e.g. classic/local) — run directly
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()


def _configure_lakebase_env(instance: str) -> None:
    """Export PG* env for the app's Lakebase BEFORE any backend import.

    The serverless job gets none of the PG* env the app receives from its bound `database`
    resource, so backend.database.py would flip to in-memory demo_mode and never see the
    request the app persisted ("request not found"). Resolve the instance endpoint + our own
    identity via the SDK and set the env; config.get_db_password() then mints an OAuth token
    for the same instance. Without this the SoD job path cannot share the app's state store.
    """
    if not instance:
        return
    os.environ.setdefault("LAKEBASE_INSTANCE", instance)
    os.environ.setdefault("PGPORT", "5432")
    os.environ.setdefault("PGDATABASE", "databricks_postgres")
    os.environ.setdefault("PGSSLMODE", "require")
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        inst = w.database.get_database_instance(name=instance)
        host = getattr(inst, "read_write_dns", None) or getattr(inst, "read_only_dns", None)
        if host:
            os.environ.setdefault("PGHOST", host)
        try:
            os.environ.setdefault("PGUSER", w.current_user.me().user_name or "")
        except Exception as e:  # noqa: BLE001
            print(f"[pave] could not resolve current user for PGUSER: {e}")
    except Exception as e:  # noqa: BLE001
        print(f"[pave] could not resolve Lakebase endpoint for {instance}: {e}")


def _configure_provisioning_env(params: dict) -> None:
    """Export the provisioning-plane env the Job needs BEFORE backend.config imports it.

    A serverless Job inherits none of the app.yaml env, so without this config reads
    PARENT_CATALOG="" (schema create targets no catalog) and PAVE_ALLOW_REAL=0 (every
    real provider silently degrades to simulated). The app passes its own live values
    through as job parameters so the Job provisions with the SAME policy as the app that
    triggered it — no second source of truth to drift."""
    passthrough = {
        "PARENT_CATALOG": _resolve(params, "parent_catalog"),
        "AUDIT_CATALOG": _resolve(params, "audit_catalog") or _resolve(params, "parent_catalog"),
        "PAVE_ALLOW_REAL": _resolve(params, "allow_real"),
        "PROVIDER_MODES": _resolve(params, "provider_modes"),
        "DATABRICKS_WAREHOUSE_ID": _resolve(params, "warehouse_id"),
    }
    for k, v in passthrough.items():
        if v:
            os.environ[k] = v
            # PROVIDER_MODES may carry secrets? no — it's a mechanism map; log key only.
            print(f"[pave] provisioning env {k} set ({'json' if k == 'PROVIDER_MODES' else v})")


def main():
    params = _parse_params(sys.argv[1:])
    action = _resolve(params, "action", "provision")
    request_id = _resolve(params, "request_id")
    if not request_id:
        raise SystemExit("request_id is required")

    # Wire the job to the app's Lakebase + provisioning policy (must happen before importing
    # backend.config/db, which read these env vars at import time).
    _configure_lakebase_env(_resolve(params, "lakebase_instance"))
    _configure_provisioning_env(params)

    from backend.services.provisioning_service import (
        provision_request, decommission_request, provision_resources)

    try:
        if action == "decommission":
            result = _run(decommission_request(request_id, actor="provisioner-sp"))
        elif action == "add_resources":
            import json as _json
            resources = _json.loads(_resolve(params, "resources") or "[]")
            result = _run(provision_resources(request_id, resources, actor="provisioner-sp"))
        else:
            result = _run(provision_request(request_id, actor="provisioner-sp"))
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        try:
            _run(_mark_failed(request_id, action, f"{type(e).__name__}: {e}"))
        except Exception as inner:  # noqa: BLE001
            print(f"[pave] could not record the failure on {request_id}: {inner}")
        raise
    print(f"[pave] {action} result: {result}")


# The serverless task execs this file rather than importing it, and the module name it
# lands under is not guaranteed to be __main__, so call main() directly.
main()
