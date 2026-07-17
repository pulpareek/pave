"""OPT-IN Python-DABs schema provider — the 'as-code, destroyable' showcase.

Demonstrates provisioning a UC schema via the Python bundles library
(`databricks-bundles`, CLI >= 0.275.0) instead of a direct SDK call: PAVE
generates a tiny bundle whose resources.load_resources() emits a typed
databricks.bundles.schemas.Schema built from the request, then shells
`databricks bundle deploy`. Decommission shells `databricks bundle destroy`.

This is OPT-IN (PROVIDER_MODES='{"schema":"dabs"}') because it requires the
Databricks CLI on PATH and writes a bundle to the workspace; the default schema
path is the SDK provider. See docs/BEST_PRACTICES.md section 4.
"""
import logging
import os
import subprocess
import tempfile
from typing import Any

from .base import Provider, ProvisionResult, new_asset_id
from .. import config

logger = logging.getLogger("pave.provider.schema_dabs")

# Generated bundle files. resources.py uses the typed Python-DABs API per
# https://docs.databricks.com/aws/en/dev-tools/bundles/python
_DATABRICKS_YML = """\
bundle:
  name: pave-vend-{slug}
include: []
targets:
  {target}:
    default: true
    mode: development
    workspace:
      host: {host}
python:
  resources:
    - "resources:load_resources"
variables: {{}}
"""

_RESOURCES_PY = """\
from databricks.bundles.core import Bundle, Resources, load_resources_from_current_package_module
from databricks.bundles.schemas import Schema


def load_resources(bundle: Bundle) -> Resources:
    resources = load_resources_from_current_package_module()
    resources.add_resource(
        "vended_schema",
        Schema(
            name="{schema_name}",
            catalog_name="{catalog}",
            comment="PAVE (Python-DABs): {project_name} ({project_id})",
        ),
    )
    return resources
"""


class SchemaDabsProvider(Provider):
    resource_type = "schema"

    def _write_bundle(self, d: str, *, request: dict[str, Any], catalog: str,
                      schema_name: str, target: str) -> None:
        slug = str(request.get("project_id", "proj")).replace("_", "-")[:40]
        host = os.getenv("DATABRICKS_HOST", "")
        with open(os.path.join(d, "databricks.yml"), "w") as f:
            f.write(_DATABRICKS_YML.format(slug=slug, target=target, host=host))
        with open(os.path.join(d, "resources.py"), "w") as f:
            f.write(_RESOURCES_PY.format(
                schema_name=schema_name, catalog=catalog,
                project_name=str(request.get("project_name", ""))[:80],
                project_id=request.get("project_id", "")))

    def _run(self, args: list[str], cwd: str) -> str:
        logger.info("bundle: %s", " ".join(args))
        out = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=600)
        if out.returncode != 0:
            raise RuntimeError(f"`{' '.join(args)}` failed: {out.stderr[-500:]}")
        return out.stdout

    def provision(self, *, request: dict[str, Any], resource: dict[str, Any],
                  tag_set: dict[str, str], context: dict[str, Any]) -> ProvisionResult:
        cfg = resource.get("config", {})
        project_id = request.get("project_id", "proj")
        catalog = request.get("parent_catalog") or config.PARENT_CATALOG
        schema_name = cfg.get("name") or f"{request.get('business_domain','proj')}_{project_id.split('-')[-1]}"
        full_name = f"{catalog}.{schema_name}"
        target = "dev"

        d = tempfile.mkdtemp(prefix="pave-dabs-")
        self._write_bundle(d, request=request, catalog=catalog,
                           schema_name=schema_name, target=target)
        self._run(["databricks", "bundle", "validate", "-t", target], d)
        self._run(["databricks", "bundle", "deploy", "-t", target], d)

        # Tags/grants still applied via SDK (not bundle-expressible).
        tag_result = {"via": "skipped"}
        try:
            from . import _sdk
            tag_result = _sdk.apply_uc_tags("schemas", full_name, tag_set)
        except Exception as e:  # noqa: BLE001
            logger.warning("post-deploy tagging failed for %s: %s", full_name, e)

        return ProvisionResult(
            asset_id=new_asset_id("schema", project_id),
            type="schema",
            names={"name": schema_name, "catalog": catalog, "full_name": full_name,
                   "bundle_dir": d, "bundle_target": target},
            external_id=full_name,
            applied_tags=tag_set,
            mode="dabs",
            status="ACTIVE",
            provenance={"engine": "python-dabs", "tags": tag_result},
        )

    def decommission(self, *, asset: dict[str, Any], context: dict[str, Any]) -> None:
        names = asset.get("names", {})
        d = names.get("bundle_dir")
        target = names.get("bundle_target", "dev")
        if d and os.path.isdir(d):
            self._run(["databricks", "bundle", "destroy", "-t", target, "--auto-approve"], d)
