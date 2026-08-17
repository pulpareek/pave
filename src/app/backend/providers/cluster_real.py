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
from .base import Provider, ProviderUnavailable, ProvisionResult, classify_error, new_asset_id
from .. import naming
from .policies import ensure_company_cluster_policy, policy_for_request, policy_sizing
from ..well_architected import COMPUTE_DEFAULTS, RESTRICTED_ACCESS_MODE

logger = logging.getLogger("pave.provider.cluster_real")

DEFAULT_SPARK_VERSION = "15.4.x-scala2.12"


class RealComputeProvider(Provider):
    resource_type = "cluster"

    def provision(self, *, request: dict[str, Any], resource: dict[str, Any],
                  tag_set: dict[str, str], context: dict[str, Any]) -> ProvisionResult:
        cfg = resource.get("config", {})
        project_id = request.get("project_id", "proj")
        # Single-user isolation for restricted data. Keyed off the classification as well as
        # the resolved access mode so this matches the simulated provider exactly — relying on
        # the WAF defaults alone meant a restricted request whose config bypassed them got
        # USER_ISOLATION here but DEDICATED in simulation.
        single_user = (request.get("data_classification") == "restricted"
                       or cfg.get("access_mode") in (RESTRICTED_ACCESS_MODE, "dedicated",
                                                     "single-user"))
        target = request.get("target_workspace")
        # Bind the right policy-family member for this request's tier/classification.
        policy_name = policy_for_request(request.get("data_classification"),
                                         request.get("environment"))
        policy = ensure_company_cluster_policy(policy_name=policy_name, target_workspace=target)

        # Route to the request's TARGET workspace (empty -> the app's own).
        w = _sdk.client(target)
        from databricks.sdk.service.compute import DataSecurityMode, AutoScale

        name = naming.resolve_name(self.resource_type, request, cfg)

        # Create with a hard timeout. `clusters.create()` returns a Wait carrying the
        # cluster_id while the cluster is PENDING (we do NOT call .response — that would block
        # until RUNNING). We still bound the create call itself: on a serverless-only workspace
        # the classic-cluster API can hang, and the saga must never block indefinitely.
        import concurrent.futures as _cf

        # The bound policy enforces autoscale, so provide AutoScale(min,max) — never a fixed
        # num_workers (the merged spec would otherwise leave autoscale.min/max unset and the
        # create is rejected). Clamp the requested size into the policy's own bounds
        # (policy_sizing reads the SAME definition the policy was created from) so a request
        # can't ask for more workers than the bound tier allows.
        sizing = policy_sizing(policy_name)
        min_w = max(int(cfg.get("min_workers") or sizing["min_workers"]), sizing["min_workers"])
        req_max = int(cfg.get("max_workers") or cfg.get("num_workers") or sizing["max_workers"])
        max_w = min(max(req_max, min_w), sizing["max_workers"])

        def _create():
            # NOTE: do NOT pass autotermination_minutes — every family policy FIXES it, and
            # sending a different value trips policy validation. apply_policy_default_values
            # applies the policy's fixed value.
            return w.clusters.create(
                cluster_name=name,
                policy_id=policy["policy_id"],
                apply_policy_default_values=True,
                spark_version=cfg.get("spark_version", DEFAULT_SPARK_VERSION),
                node_type_id=cfg.get("node_type_id", COMPUTE_DEFAULTS["node_type_id"]),
                autoscale=AutoScale(min_workers=min_w, max_workers=max_w),
                custom_tags=tag_set,
                data_security_mode=DataSecurityMode.SINGLE_USER if single_user else DataSecurityMode.USER_ISOLATION,
            )

        with _cf.ThreadPoolExecutor(max_workers=1) as ex:
            try:
                waiter = ex.submit(_create).result(timeout=45)
            except _cf.TimeoutError as e:
                raise ProviderUnavailable(
                    "cluster create did not return within 45s — the target workspace may be "
                    "serverless-only (classic all-purpose clusters unsupported); use serverless "
                    "compute", reason="unsupported_here") from e
            except Exception as e:  # noqa: BLE001
                raise ProviderUnavailable(f"cluster create failed: {e}",
                                          reason=classify_error(e)) from e
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
            asset_id=new_asset_id("cluster", project_id, context),
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
