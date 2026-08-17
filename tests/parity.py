"""Simulated/real provider parity harness (stdlib only, offline).

Run: `python3 tests/parity.py` from the repo root. Exits non-zero on any failure.

The hybrid demo only holds together if the simulated path is a faithful stand-in for the
real one. These checks target the specific ways the two drifted apart:

  * a real provider reporting success for something it did not create
  * a real provider deleting from the wrong workspace on decommission
  * restricted data getting single-user isolation in simulation but not for real
  * a retry duplicating resources because asset ids were random

Real providers cannot be *executed* offline (they need a live workspace), so invariants
that only show up in their source — which client they decommission against, whether they
route names through the naming service — are asserted structurally. That is deliberate:
these were real bugs, and a structural assertion that catches them is worth more than no
assertion at all.
"""
import inspect
import os
import sys

os.environ.pop("PGHOST", None)
os.environ["PAVE_ALLOW_REAL"] = "0"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "app"))

from backend import config  # noqa: E402
from backend.providers import base, registry  # noqa: E402
from backend.providers.base import ProviderUnavailable, new_asset_id  # noqa: E402

_fails = []

# Types with a real SDK-backed provider, and the module that implements it.
REAL_PROVIDERS = {
    "schema": "backend.providers.schema",
    "catalog": "backend.providers.catalog",
    "app": "backend.providers.app",
    "cluster": "backend.providers.cluster_real",
    "lakebase": "backend.providers.lakebase",
    "sql_warehouse": "backend.providers.sql_warehouse",
    "llm_gateway_endpoint": "backend.providers.ai_gateway",
    "vector_search": "backend.providers.vector_search",
    "workspace": "backend.providers.workspace",
}

# Keys every provider result must carry so the registry, saga and UI can rely on them.
RESULT_KEYS = ("asset_id", "type", "names", "external_id", "applied_tags",
               "mode", "mode_reason", "degraded", "status")


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        _fails.append(name)


def _request(**over):
    base_req = {
        "id": "11111111-1111-1111-1111-111111111111",
        "project_id": "proj-parity", "project_name": "Parity", "requester": "lead@x.com",
        "owner_email": "lead@x.com", "owner_group": "platform", "cost_center": "CC-1001",
        "business_domain": "platform", "data_classification": "internal",
        "environment": "dev", "parent_catalog": "main",
    }
    base_req.update(over)
    return base_req


def _tags():
    return {"project_id": "proj-parity", "cost_center": "CC-1001",
            "business_domain": "platform", "managed_by": "self-service-portal"}


# ------------------------------------------------------------------ contract
def test_contract():
    """Every provider, real or simulated, honours the same method contract."""
    import importlib
    for rtype, module_path in REAL_PROVIDERS.items():
        mod = importlib.import_module(module_path)
        cls = next((o for _, o in inspect.getmembers(mod, inspect.isclass)
                    if o.__module__ == module_path and hasattr(o, "provision")), None)
        check(f"contract: {rtype} exposes a real provider class", cls is not None)
        if not cls:
            continue
        for method in ("provision", "decommission"):
            fn = getattr(cls, method, None)
            check(f"contract: {rtype}.{method} exists", callable(fn))
            if not callable(fn):
                continue
            params = set(inspect.signature(fn).parameters)
            expected = ({"self", "request", "resource", "tag_set", "context"}
                        if method == "provision" else {"self", "asset", "context"})
            check(f"contract: {rtype}.{method} signature matches the protocol",
                  params == expected)


def test_result_shape():
    """Simulated results carry every key the saga and UI read."""
    for rtype in ("schema", "cluster", "job_cluster", "lakebase", "catalog", "sql_warehouse"):
        provider = registry._simulated_provider(rtype)
        res = provider.provision(request=_request(), resource={"type": rtype, "config": {}},
                                 tag_set=_tags(), context={"request_id": "r1", "resource_index": 0})
        missing = [k for k in RESULT_KEYS if k not in res]
        check(f"shape: {rtype} result has every contract key ({missing or 'ok'})", not missing)
        check(f"shape: {rtype} status is ACTIVE", res.get("status") == "ACTIVE")


# --------------------------------------------------------------- idempotency
def test_idempotent_asset_ids():
    """A retry must adopt the existing registry row, not create a second one."""
    ctx = {"request_id": "req-abc", "resource_index": 0}
    first = new_asset_id("schema", "proj-x", ctx)
    second = new_asset_id("schema", "proj-x", ctx)
    check("idempotency: same request+index -> same asset id", first == second)

    other_index = new_asset_id("schema", "proj-x", {**ctx, "resource_index": 1})
    check("idempotency: a different resource index -> different id", first != other_index)

    other_req = new_asset_id("schema", "proj-x", {**ctx, "request_id": "req-zzz"})
    check("idempotency: a different request -> different id", first != other_req)

    # No context (callers outside the saga) must still produce something unique.
    check("idempotency: without context ids stay unique",
          new_asset_id("schema", "proj-x") != new_asset_id("schema", "proj-x"))


def test_idempotent_across_providers():
    """The id is a property of the saga slot, not of which provider filled it."""
    ctx = {"request_id": "req-abc", "resource_index": 2}
    sim = registry._simulated_provider("lakebase").provision(
        request=_request(), resource={"type": "lakebase", "config": {}},
        tag_set=_tags(), context=ctx)
    sim_again = registry._simulated_provider("lakebase").provision(
        request=_request(), resource={"type": "lakebase", "config": {}},
        tag_set=_tags(), context=ctx)
    check("idempotency: re-provisioning the same slot reuses the asset id",
          sim["asset_id"] == sim_again["asset_id"])


# ----------------------------------------------------------------- honesty
def test_kill_switch_is_honest():
    """With the kill switch off, a type configured real must say why it is modelled."""
    binding = registry.bind("schema")
    check("honesty: kill switch forces schema to simulated", binding.mode == "simulated")
    check("honesty: kill switch records the reason",
          binding.reason == "kill_switch_off")
    check("honesty: kill-switched schema is flagged degraded", binding.degraded)

    # A type that is *meant* to be simulated is not degraded — the distinction the UI needs.
    cluster = registry.bind("cluster")
    check("honesty: a deliberately simulated type is not degraded", not cluster.degraded)
    check("honesty: deliberate simulation records its own reason",
          cluster.reason == "configured_simulated")


def test_fallback_records_reason():
    """A real provider that cannot complete degrades to modelled WITH the reason."""
    class Failing:
        resource_type = "schema"

        def provision(self, **_):
            raise ProviderUnavailable("no create for you", reason="no_permission")

        def decommission(self, **_):
            return None

    original_real, original_allow = registry._real_provider, config.ALLOW_REAL
    try:
        registry._real_provider = lambda rt: Failing()
        config.ALLOW_REAL = True
        res = registry.provision("schema", request=_request(),
                                 resource={"type": "schema", "config": {}},
                                 tag_set=_tags(),
                                 context={"request_id": "r1", "resource_index": 0})
    finally:
        registry._real_provider, config.ALLOW_REAL = original_real, original_allow

    check("fallback: degraded result is simulated", res["mode"] == "simulated")
    check("fallback: carries the specific reason", res["mode_reason"] == "no_permission")
    check("fallback: flagged degraded", res["degraded"] is True)
    check("fallback: keeps the detail for the audit trail",
          "degraded_from_real" in (res.get("provenance") or {}))
    check("fallback: still emits a usable asset", bool(res.get("asset_id")))


def test_real_failures_are_not_swallowed():
    """A genuine bug in a provider must fail the resource, not silently model it."""
    class Broken:
        resource_type = "schema"

        def provision(self, **_):
            raise KeyError("a genuine bug")

        def decommission(self, **_):
            return None

    original_real, original_allow = registry._real_provider, config.ALLOW_REAL
    raised = False
    try:
        registry._real_provider = lambda rt: Broken()
        config.ALLOW_REAL = True
        registry.provision("schema", request=_request(),
                           resource={"type": "schema", "config": {}},
                           tag_set=_tags(), context={})
    except KeyError:
        raised = True
    except Exception:  # noqa: BLE001
        raised = False
    finally:
        registry._real_provider, config.ALLOW_REAL = original_real, original_allow
    check("fallback: an unexpected error propagates instead of being modelled", raised)


# ------------------------------------------------------- structural invariants
def _source(module_path: str) -> str:
    import importlib
    return inspect.getsource(importlib.import_module(module_path))


def test_decommission_targets_the_right_workspace():
    """Decommission must hit the workspace the asset was vended INTO.

    Calling _sdk.client() with no host tears down (or fails to find) the resource in the
    app's own workspace instead of the target.
    """
    for rtype, module_path in REAL_PROVIDERS.items():
        src = _source(module_path)
        body = src.split("def decommission", 1)
        if len(body) < 2:
            continue
        body = body[1]
        if "_sdk.client(" not in body:
            continue  # nothing to route (e.g. workspace teardown is account-level)
        check(f"routing: {rtype}.decommission targets the request's workspace",
              "_sdk.client()" not in body)


def test_real_providers_use_the_naming_service():
    """Names must come from naming.resolve_name so simulated and real agree."""
    for rtype, module_path in REAL_PROVIDERS.items():
        src = _source(module_path)
        check(f"naming: {rtype} resolves names through the naming service",
              "naming.resolve_name" in src)


def test_restricted_isolation_parity():
    """Restricted data gets single-user isolation in BOTH providers.

    The real provider used to key only off the resolved access_mode, so a restricted
    request whose config bypassed the WAF defaults got USER_ISOLATION for real while
    simulation showed DEDICATED.
    """
    sim = registry._simulated_provider("cluster").provision(
        request=_request(data_classification="restricted"),
        resource={"type": "cluster", "config": {}},
        tag_set=_tags(), context={"request_id": "r", "resource_index": 0})
    check("isolation: simulated restricted cluster is single-user",
          sim["names"].get("data_security_mode") == "DEDICATED")
    real_src = _source("backend.providers.cluster_real")
    check("isolation: real cluster keys single-user off the classification too",
          'data_classification") == "restricted"' in real_src)


def test_sql_identifiers_are_quoted():
    """Tag values reach DDL that takes no bind parameters, so quoting is the only defence."""
    from backend.providers._sdk import _sql_identifier, _sql_literal
    check("sql: literals escape embedded quotes",
          _sql_literal("O'Brien") == "'O\\'Brien'")
    check("sql: identifiers are backtick-quoted per part",
          _sql_identifier("main.my_schema") == "`main`.`my_schema`")
    rejected = False
    try:
        _sql_identifier("main.evil` DROP TABLE x --")
    except ValueError:
        rejected = True
    check("sql: a hostile identifier is rejected outright", rejected)


def test_target_workspace_is_allow_listed():
    """An unapproved host must never be handed service-principal credentials."""
    from backend.providers import _sdk
    refused = False
    try:
        _sdk.client("https://attacker.example.com")
    except ValueError:
        refused = True
    except Exception:  # noqa: BLE001 — any other error means it got further than it should
        refused = False
    check("routing: an unapproved target workspace is refused", refused)


def main():
    print("PAVE provider parity harness (offline, real providers disabled)\n")
    print("contract:");     test_contract()
    print("result shape:"); test_result_shape()
    print("idempotency:");  test_idempotent_asset_ids(); test_idempotent_across_providers()
    print("honesty:");      test_kill_switch_is_honest(); test_fallback_records_reason()
    test_real_failures_are_not_swallowed()
    print("structure:");    test_decommission_targets_the_right_workspace()
    test_real_providers_use_the_naming_service()
    test_restricted_isolation_parity()
    print("safety:");       test_sql_identifiers_are_quoted(); test_target_workspace_is_allow_listed()
    print()
    if _fails:
        print(f"FAILED ({len(_fails)}): {_fails}")
        sys.exit(1)
    print("ALL PARITY CHECKS PASSED")


if __name__ == "__main__":
    main()
