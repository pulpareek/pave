"""Workspace provider — account-level landing-zone vending.

Creating a Databricks workspace is an ACCOUNT-level operation (AccountClient + the
accounts API: POST /accounts/{id}/workspaces), a different privilege plane from
everything else PAVE vends. It requires an account-admin identity + pre-provisioned
cloud infra (credentials / storage / network configurations). PAVE therefore treats a
workspace like any other resource in the hybrid model but keeps the account tier behind
its own SoD boundary:

  * By default it MODELS the workspace (registry row + synthetic handle + governed tags,
    no account mutation) — fully demoable without account access.
  * It creates for real only when PAVE_ALLOW_REAL is set AND account credentials +
    config ids are present; otherwise it gracefully falls back to modeled (like the
    AI-gateway provider).
  * Either way the canonical desired-state emits applyable Terraform (services/spec.py),
    so an account-admin team can apply it — the "record-as-code, execute under SoD" path.
"""
import logging
import uuid
from typing import Any

from .base import Provider, ProvisionResult, new_asset_id

logger = logging.getLogger("pave.provider.workspace")


class WorkspaceProvider(Provider):
    resource_type = "workspace"

    def provision(self, *, request: dict[str, Any], resource: dict[str, Any],
                  tag_set: dict[str, str], context: dict[str, Any]) -> ProvisionResult:
        cfg = resource.get("config", {})
        project_id = request.get("project_id", "proj")
        region = cfg.get("region") or request.get("region") or "us-east-1"
        deployment_name = (cfg.get("deployment_name") or cfg.get("name")
                           or f"{request.get('business_domain', 'proj')}-{project_id.split('-')[-1]}"
                           ).lower().replace("_", "-")[:30]
        modeled = {
            "deployment_name": deployment_name,
            "region": region,
            "pricing_tier": cfg.get("pricing_tier", "ENTERPRISE"),
            "credentials_id": cfg.get("credentials_id", ""),
            "storage_config_id": cfg.get("storage_config_id", ""),
            "network_id": cfg.get("network_id", ""),
            "workspace_url": f"https://{deployment_name}.cloud.databricks.com",
        }
        mode = "simulated"
        external_id = f"sim-workspace-{uuid.uuid4().hex[:10]}"
        provenance: dict[str, Any] = {
            "engine": "modeled",
            "note": "account-level create runs under an account-admin SoD identity; "
                    "PAVE emits Terraform for the account team to apply",
        }
        created = self._try_create_real(deployment_name, region, cfg)
        if created:
            mode, external_id = "real", str(created.get("workspace_id"))
            provenance = created

        # BOOTSTRAP: a freshly vended workspace is born governed — seed the cluster-policy
        # family. BEST-EFFORT: a bootstrap hiccup must NOT fail the (successful) workspace
        # create — capture it and move on. Policies are a WORKSPACE API, so this targets the
        # new workspace's host, not the account client used to create it.
        try:
            provenance["bootstrap"] = self._bootstrap(created)
        except Exception as e:  # noqa: BLE001
            logger.warning("workspace bootstrap failed (non-fatal): %s", e)
            provenance["bootstrap"] = {"status": "deferred", "error": str(e)[:200]}

        return ProvisionResult(
            asset_id=new_asset_id("workspace", project_id),
            type="workspace",
            names={"name": deployment_name, **{k: str(v) for k, v in modeled.items()}},
            external_id=external_id,
            applied_tags=tag_set,
            mode=mode,
            status="ACTIVE",
            provenance={"workspace": modeled, **provenance},
        )

    def _try_create_real(self, deployment_name: str, region: str, cfg: dict) -> dict | None:
        """Real create via the Account API — only when ALLOW_REAL + account-admin auth.

        Two modes (compute_mode config, default 'serverless'):
          * SERVERLESS — no cloud credential/storage config needed (Databricks manages
            infra). The governed default (matches the account console "serverless + default
            storage"). Just workspace_name + region + compute_mode.
          * CLASSIC/HYBRID — customer-managed; requires a pre-provisioned credentials_id +
            storage_configuration_id (account-admin registers these). Modeled if absent.

        Returns the created workspace dict, or None to fall back to modeled.
        """
        from .. import config
        if not config.ALLOW_REAL:
            return None
        compute_mode = (cfg.get("compute_mode") or "serverless").lower()
        cred, stor = cfg.get("credentials_id"), cfg.get("storage_config_id")
        try:
            from databricks.sdk import AccountClient  # requires ACCOUNT host + account-admin creds
            from databricks.sdk.service.provisioning import CustomerFacingComputeMode
            a = AccountClient()
            kwargs = {"workspace_name": deployment_name, "aws_region": region}
            if compute_mode == "serverless":
                kwargs["compute_mode"] = CustomerFacingComputeMode.SERVERLESS
            else:
                if not (cred and stor):
                    logger.info("classic workspace needs credentials_id + storage_config_id; modeling")
                    return None
                kwargs.update(deployment_name=deployment_name, credentials_id=cred,
                              storage_configuration_id=stor,
                              network_id=cfg.get("network_id") or None,
                              pricing_tier=cfg.get("pricing_tier") or None)
            ws = a.workspaces.create(**kwargs).result()   # wait for provisioning
            out = {"workspace_id": ws.workspace_id, "deployment_name": ws.deployment_name,
                   "compute_mode": compute_mode, "workspace_status": str(getattr(ws, "workspace_status", "")),
                   "engine": "AccountClient.workspaces.create"}
            # Assign the requested UC metastore so the workspace can vend UC resources.
            ms = cfg.get("metastore_id") or config.__dict__.get("METASTORE_ID") or None
            import os as _os
            ms = ms or _os.getenv("METASTORE_ID")
            if ms:
                try:
                    a.metastore_assignments.create(workspace_id=ws.workspace_id, metastore_id=ms)
                    out["metastore_id"] = ms
                except Exception as me:  # noqa: BLE001
                    out["metastore_assign_error"] = str(me)[:200]
            return out
        except Exception as e:  # noqa: BLE001 — no account access in this env => model
            logger.warning("real workspace create failed (%s); modeling instead", e)
            return None

    def _bootstrap(self, created: dict | None) -> dict:
        """Day-0 setup for a newly vended workspace so it is born usable + governed.

        Two steps. Both SIMULATE by default and emit the exact action a customer must run
        for real (see docs/ADMIN_CAPABILITIES.md).

        1) Cluster-policy family — seeded via the workspace SDK. This runs for real once
           PAVE_ALLOW_REAL is set AND the new workspace is reachable (it targets the new
           host when we have it).
        2) Metastore attachment — ACCOUNT-level (AccountClient.metastore_assignments), so
           it needs an account-admin identity PAVE may not hold. Left as a documented,
           ready-to-enable scaffold: uncomment + provide METASTORE_ID to activate.
        """
        from .policies import bootstrap_policy_family

        new_host = ""
        if created and created.get("deployment_name"):
            new_host = f"https://{created['deployment_name']}.cloud.databricks.com"

        # 1) policy family (self-guards to simulated unless ALLOW_REAL)
        policies = bootstrap_policy_family(target_workspace=new_host or None)

        # 2) metastore attach — ACCOUNT-level scaffold. UNCOMMENT + set METASTORE_ID to enable.
        metastore = {"status": "manual",
                     "note": "attach a regional UC metastore to the new workspace before "
                             "vending UC resources; account-admin step (AccountClient)."}
        # import os
        # from .. import config
        # ms_id = os.getenv("METASTORE_ID")
        # if config.ALLOW_REAL and created and ms_id:
        #     try:
        #         from databricks.sdk import AccountClient
        #         from databricks.sdk.service.catalog import CreateMetastoreAssignment
        #         a = AccountClient()  # requires ACCOUNT host + account-admin creds
        #         a.metastore_assignments.create(
        #             workspace_id=int(created["workspace_id"]), metastore_id=ms_id,
        #             metastore_assignment=CreateMetastoreAssignment(
        #                 metastore_id=ms_id, workspace_id=int(created["workspace_id"])))
        #         metastore = {"status": "attached", "metastore_id": ms_id}
        #     except Exception as e:  # noqa: BLE001
        #         metastore = {"status": "failed", "error": str(e)}

        return {"cluster_policies": policies, "metastore": metastore}

    def decommission(self, *, asset: dict[str, Any], context: dict[str, Any]) -> None:
        # Account-level teardown is out of PAVE's workspace-tier SoD scope in the demo.
        return None
