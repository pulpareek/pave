"""Company cluster-policy resolution (create-if-missing then attach).

Guarantees governed compute: PAVE attaches a company cluster policy to every cluster
it vends. Resolution order:
  1. COMPANY_CLUSTER_POLICY_ID env (explicit id)
  2. lookup by COMPANY_CLUSTER_POLICY_NAME via cluster_policies.list()
  3. create a baseline "PAVE Standard" policy (fixed autotermination, DBU cap,
     node-type allowlist, data_security_mode, required custom_tag keys)

The provisioner SP must hold CAN_USE on the policy (admin/build-time step). Cached
after first resolution. Only runs real SDK when config.ALLOW_REAL is set; otherwise
returns a synthetic id so the simulated provider can still show an accurate story.
"""
import json
import logging
import os

from . import _sdk
from .. import config

logger = logging.getLogger("pave.policies")

POLICY_NAME = os.getenv("COMPANY_CLUSTER_POLICY_NAME", "PAVE Standard")
POLICY_ID_ENV = os.getenv("COMPANY_CLUSTER_POLICY_ID", "")

# Cache keyed by (target_workspace_host, policy_name) — policies are workspace-scoped,
# so a per-workspace key is required once multi-workspace routing is in play.
_cached_ids: dict[tuple[str, str], str] = {}

# ---------------------------------------------------------------------------
# POLICY FAMILY — the tiered, governed set every workspace should carry.
# `ensure_company_cluster_policy` uses PAVE Standard by default; a request can pick a
# stricter/cheaper member by tier/classification via `policy_name`. On workspace vend,
# bootstrap_policy_family() seeds all three (see providers/workspace.py).
# ---------------------------------------------------------------------------
_BASE_TAGS = {
    "custom_tags.cost_center": {"type": "regex", "pattern": ".+"},
    "custom_tags.project_id": {"type": "regex", "pattern": ".+"},
    "custom_tags.managed_by": {"type": "fixed", "value": "self-service-portal"},
}
POLICY_FAMILY: dict[str, dict] = {
    # Baseline governed policy: fixed auto-termination + DBU cap, node allow-list, tags.
    "PAVE Standard": {
        "autotermination_minutes": {"type": "fixed", "value": 30, "hidden": False},
        "dbus_per_hour": {"type": "range", "maxValue": 50},
        "node_type_id": {"type": "allowlist",
                         "values": ["m5d.large", "m5d.xlarge", "i3.xlarge", "rd-fleet.xlarge"],
                         "defaultValue": "m5d.large"},
        "autoscale.min_workers": {"type": "fixed", "value": 1, "hidden": True},
        "autoscale.max_workers": {"type": "range", "maxValue": 8, "defaultValue": 4},
        **_BASE_TAGS,
    },
    # Restricted (PHI/GxP): single-user isolation locked on, tighter caps.
    "PAVE Restricted": {
        "autotermination_minutes": {"type": "fixed", "value": 20, "hidden": False},
        "data_security_mode": {"type": "fixed", "value": "SINGLE_USER"},
        "dbus_per_hour": {"type": "range", "maxValue": 30},
        "node_type_id": {"type": "allowlist", "values": ["m5d.large", "m5d.xlarge"],
                         "defaultValue": "m5d.large"},
        "autoscale.max_workers": {"type": "range", "maxValue": 4, "defaultValue": 2},
        **_BASE_TAGS,
    },
    # Dev sandbox: cheapest, single small node, aggressive auto-termination.
    "PAVE Dev-Cheap": {
        "autotermination_minutes": {"type": "fixed", "value": 15, "hidden": False},
        "dbus_per_hour": {"type": "range", "maxValue": 15},
        "node_type_id": {"type": "fixed", "value": "m5d.large"},
        "num_workers": {"type": "fixed", "value": 0},   # single-node
        **_BASE_TAGS,
    },
}


def policy_for_request(data_classification: str | None, environment: str | None) -> str:
    """Pick the policy-family member for a request (bind-time selection)."""
    if data_classification == "restricted":
        return "PAVE Restricted"
    if environment in ("dev", "test"):
        return "PAVE Dev-Cheap"
    return POLICY_NAME


def policy_definition_json(policy_name: str = POLICY_NAME) -> str:
    return json.dumps(POLICY_FAMILY.get(policy_name, POLICY_FAMILY[POLICY_NAME]))


def ensure_company_cluster_policy(policy_name: str | None = None,
                                  target_workspace: str | None = None) -> dict:
    """Resolve (create-if-missing) a cluster policy IN THE TARGET workspace.

    Returns {'policy_id', 'name', 'source': env|cached|existing|created|simulated}.
    `policy_name` selects a POLICY_FAMILY member (default PAVE Standard).
    `target_workspace` routes to a specific workspace (empty = the app's own).
    """
    name = policy_name or POLICY_NAME
    host = (target_workspace or "").strip()
    key = (host, name)
    if key in _cached_ids:
        return {"policy_id": _cached_ids[key], "name": name, "source": "cached"}

    # Explicit env id only applies to the default policy in the app's own workspace.
    if POLICY_ID_ENV and name == POLICY_NAME and not host:
        _cached_ids[key] = POLICY_ID_ENV
        return {"policy_id": POLICY_ID_ENV, "name": name, "source": "env"}

    if not config.ALLOW_REAL:
        # modeled: synthetic id so the simulated provider story stays accurate
        return {"policy_id": f"sim-policy-{name.lower().replace(' ', '-')}",
                "name": name, "source": "simulated"}

    w = _sdk.client(host)
    # lookup by name
    try:
        for p in w.cluster_policies.list():
            if p.name == name:
                _cached_ids[key] = p.policy_id
                return {"policy_id": p.policy_id, "name": name, "source": "existing"}
    except Exception as e:  # noqa: BLE001
        logger.warning("cluster_policies.list failed: %s", e)

    # create the family member
    created = w.cluster_policies.create(name=name, definition=policy_definition_json(name))
    _cached_ids[key] = created.policy_id
    logger.info("created cluster policy '%s' = %s (ws=%s)", name, created.policy_id, host or "self")
    return {"policy_id": created.policy_id, "name": name, "source": "created"}


def bootstrap_policy_family(target_workspace: str | None = None) -> list[dict]:
    """Seed the FULL policy family into a (usually freshly-vended) workspace.

    Called at workspace bootstrap so the workspace is born governed — every tier has a
    policy to bind to. Returns one resolution dict per family member. Simulated unless
    PAVE_ALLOW_REAL (each ensure_* call self-guards).

    BEST-EFFORT per policy: cluster policies are a WORKSPACE-scoped API, but a workspace is
    created by an ACCOUNT-admin identity, and a brand-new serverless workspace may not yet be
    reachable with a workspace-admin token. A failure here must not abort the workspace
    create — capture it as 'deferred' so an admin (or a later reconcile) seeds policies once
    the workspace has a workspace-scoped identity. This is a real SoD boundary, not an error."""
    out = []
    for n in POLICY_FAMILY:
        try:
            out.append(ensure_company_cluster_policy(policy_name=n, target_workspace=target_workspace))
        except Exception as e:  # noqa: BLE001
            logger.warning("policy '%s' bootstrap deferred (%s)", n, type(e).__name__)
            out.append({"name": n, "source": "deferred",
                        "note": "seed with a workspace-admin identity in the new workspace",
                        "error": str(e)[:160]})
    return out
