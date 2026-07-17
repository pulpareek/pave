"""Simulated compute provider that MODELS cluster-policy governance.

Even though compute is simulated in the hybrid demo (no real spend), this records
the policy-enforced configuration a real cluster policy would impose — enforced
custom_tags, auto-termination, DBU/worker caps, and single-user access mode for
restricted data — so the governance + FinOps story is visible end-to-end.
"""
import os
import uuid
from typing import Any

from .base import Provider, ProvisionResult, new_asset_id
from ..well_architected import COMPUTE_DEFAULTS, RESTRICTED_ACCESS_MODE


class SimulatedComputeProvider(Provider):
    """Handles `cluster` and `job_cluster` with modeled policy enforcement."""

    def __init__(self, resource_type: str):
        self.resource_type = resource_type

    def provision(self, *, request: dict[str, Any], resource: dict[str, Any],
                  tag_set: dict[str, str], context: dict[str, Any]) -> ProvisionResult:
        cfg = resource.get("config", {})
        project_id = request.get("project_id", "proj")
        restricted = request.get("data_classification") == "restricted"
        # Map the requester's access mode (dedicated|standard|auto) to a data_security_mode.
        # Restricted data forces DEDICATED (single-user isolation) regardless of the pick.
        am = (cfg.get("access_mode") or "auto").lower()
        if restricted or am in ("dedicated", RESTRICTED_ACCESS_MODE, "single-user"):
            data_security_mode = "DEDICATED"        # (legacy SINGLE_USER)
        elif am == "standard":
            data_security_mode = "STANDARD"         # (legacy USER_ISOLATION)
        else:
            data_security_mode = "AUTO"

        # Resolve (and, when ALLOW_REAL, actually create) the company cluster policy so
        # the modeled story reflects the REAL policy that a real cluster would attach.
        policy_id = cfg.get("policy_id")
        if not policy_id:
            try:
                from .policies import ensure_company_cluster_policy
                policy_id = ensure_company_cluster_policy().get("policy_id")
            except Exception:  # noqa: BLE001
                policy_id = os.getenv("COMPANY_CLUSTER_POLICY_ID") or "pave-standard-policy"

        # Sizing: fixed num_workers vs autoscale range.
        if cfg.get("num_workers") is not None:
            sizing = {"num_workers": int(cfg.get("num_workers"))}
        else:
            sizing = {"autoscale": {"min_workers": int(cfg.get("min_workers", COMPUTE_DEFAULTS["min_workers"])),
                                    "max_workers": int(cfg.get("max_workers", COMPUTE_DEFAULTS["max_workers"]))}}

        # Policy-enforced configuration (what the cluster policy would impose).
        enforced = {
            "policy_id": policy_id,
            "custom_tags": tag_set,                      # enforced/fixed tags -> billing
            "max_dbu_per_hour": float(cfg.get("max_dbu_per_hour", 20)),
            "data_security_mode": data_security_mode,
            "node_type_id": cfg.get("node_type_id", COMPUTE_DEFAULTS["node_type_id"]),
            "spark_version": cfg.get("spark_version", "15.4.x-scala2.12"),
            "runtime_engine": cfg.get("runtime_engine", "PHOTON"),
            **sizing,
        }
        # job_cluster is ephemeral (no autotermination); all-purpose gets a bounded one.
        if self.resource_type == "cluster":
            enforced["autotermination_minutes"] = int(cfg.get("autotermination_minutes",
                                                              COMPUTE_DEFAULTS["autotermination_minutes"]))
        else:
            enforced["availability"] = cfg.get("availability", "SPOT_WITH_FALLBACK")
        synthetic_id = f"sim-{self.resource_type}-{uuid.uuid4().hex[:12]}"
        return ProvisionResult(
            asset_id=new_asset_id(self.resource_type, project_id),
            type=self.resource_type,
            names={"name": cfg.get("name") or f"{self.resource_type}-{project_id}", **enforced},
            external_id=synthetic_id,
            applied_tags=tag_set,
            mode="simulated",
            status="ACTIVE",
            provenance={"policy_enforced": enforced},
        )

    def decommission(self, *, asset: dict[str, Any], context: dict[str, Any]) -> None:
        return None
