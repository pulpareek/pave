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

# Ensure `backend` is importable when run as a workspace file in the Job.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


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


def main():
    params = _parse_params(sys.argv[1:])
    action = _resolve(params, "action", "provision")
    request_id = _resolve(params, "request_id")
    if not request_id:
        raise SystemExit("request_id is required")

    from backend.services.provisioning_service import provision_request, decommission_request

    if action == "decommission":
        result = asyncio.run(decommission_request(request_id, actor="provisioner-sp"))
    else:
        result = asyncio.run(provision_request(request_id, actor="provisioner-sp"))
    print(f"[pave] {action} result: {result}")


if __name__ == "__main__":
    main()
