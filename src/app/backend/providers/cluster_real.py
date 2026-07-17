"""Real all-purpose cluster provider (Databricks SDK) — opt-in (cluster=real).

Guarantees the company cluster policy is applied: resolves/creates the company policy,
creates the cluster WITH `policy_id` + `apply_policy_default_values=True` + enforced
custom_tags, then grants the requester CAN_MANAGE. Restricted data → SINGLE_USER mode.
Behind PAVE_ALLOW_REAL (the registry guard). Job clusters stay policy-modeled (a job
cluster only exists within a job) — handled by the simulated compute provider.
"""
import logging
from typing import Any

from . import _sdk
from .base import Provider, ProvisionResult, new_asset_id
from .policies import ensure_company_cluster_policy, policy_for_request
from ..well_architected import COMPUTE_DEFAULTS, RESTRICTED_ACCESS_MODE

logger = logging.getLogger("pave.provider.cluster_real")

DEFAULT_SPARK_VERSION = "15.4.x-scala2.12"


class RealComputeProvider(Provider):
    resource_type = "cluster"

    def provision(self, *, request: dict[str, Any], resource: dict[str, Any],
                  tag_set: dict[str, str], context: dict[str, Any]) -> ProvisionResult:
        cfg = resource.get("config", {})
        project_id = request.get("project_id", "proj")
        # single-user is set by the WAF born-compliant defaults (restricted data); shared
        # user-isolation otherwise.
        single_user = cfg.get("access_mode") == RESTRICTED_ACCESS_MODE
        target = request.get("target_workspace")
        # Bind the right policy-family member for this request's tier/classification.
        policy_name = policy_for_request(request.get("data_classification"),
                                         request.get("environment"))
        policy = ensure_company_cluster_policy(policy_name=policy_name, target_workspace=target)

        # Route to the request's TARGET workspace (empty -> the app's own).
        w = _sdk.client(target)
        from databricks.sdk.service.compute import DataSecurityMode

        name = cfg.get("name") or f"{request.get('business_domain', 'proj')}-{project_id.split('-')[-1]}"

        # Create with a hard timeout. `clusters.create()` returns a Wait carrying the
        # cluster_id while the cluster is PENDING (we do NOT call .response — that would block
        # until RUNNING). We still bound the create call itself: on a serverless-only workspace
        # the classic-cluster API can hang, and the saga must never block indefinitely.
        import concurrent.futures as _cf

        def _create():
            return w.clusters.create(
                cluster_name=name,
                policy_id=policy["policy_id"],
                apply_policy_default_values=True,
                spark_version=cfg.get("spark_version", DEFAULT_SPARK_VERSION),
                node_type_id=cfg.get("node_type_id", COMPUTE_DEFAULTS["node_type_id"]),
                autotermination_minutes=int(cfg.get("autotermination_minutes",
                                                    COMPUTE_DEFAULTS["autotermination_minutes"])),
                num_workers=int(cfg.get("num_workers", COMPUTE_DEFAULTS["min_workers"])),
                custom_tags=tag_set,
                data_security_mode=DataSecurityMode.SINGLE_USER if single_user else DataSecurityMode.USER_ISOLATION,
            )

        with _cf.ThreadPoolExecutor(max_workers=1) as ex:
            try:
                waiter = ex.submit(_create).result(timeout=45)
            except _cf.TimeoutError:
                raise RuntimeError(
                    "cluster create did not return within 45s — the target workspace may be "
                    "serverless-only (classic all-purpose clusters unsupported); use serverless compute")
        cluster_id = waiter.cluster_id

        # grant the requester CAN_MANAGE on the cluster
        perm = {"granted": []}
        try:
            from databricks.sdk.service.iam import AccessControlRequest, PermissionLevel
            requester = request.get("owner_email") or request.get("requester")
            if requester:
                w.clusters.set_permissions(
                    cluster_id=cluster_id,
                    access_control_list=[AccessControlRequest(
                        user_name=requester, permission_level=PermissionLevel.CAN_MANAGE)])
                perm = {"granted": [requester]}
        except Exception as e:  # noqa: BLE001
            logger.warning("set cluster permissions failed: %s", e)
            perm = {"granted": [], "error": str(e)}

        return ProvisionResult(
            asset_id=new_asset_id("cluster", project_id),
            type="cluster",
            names={"name": name, "cluster_id": cluster_id, "policy_id": policy["policy_id"],
                   "policy_name": policy["name"],
                   "data_security_mode": "SINGLE_USER" if single_user else "USER_ISOLATION"},
            external_id=cluster_id,
            applied_tags=tag_set,
            mode="real",
            status="ACTIVE",
            provenance={"policy": policy, "permissions": perm},
        )

    def decommission(self, *, asset: dict[str, Any], context: dict[str, Any]) -> None:
        cid = asset.get("external_id")
        if cid:
            _sdk.client(context.get("target_workspace")).clusters.permanent_delete(cluster_id=cid)
