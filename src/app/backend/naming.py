"""Naming convention engine — the single source of truth for asset names.

Both name *generation* (when the requester leaves the name blank) and name
*validation* (when they override it) run through the same templates + rules, so an
auto-generated name and an accepted override always satisfy the same convention.

Templates are token-based and preset-driven so one engine serves both verticals PAVE
targets, informed by field-proven patterns:
  - Life-sciences / pharma (internal "OMOP - Unity Catalog Design" SA guidance): the
    catalog is the isolation boundary and carries the environment (e.g. `omop_prod`,
    `omop_qa`); schemas group by domain (`clinical`, `vocab`, `derived`); PHI isolated.
  - Payer / provider (field Slack thread): `{domain}_{layer}_{env}` catalogs
    (e.g. `claims_silver_prod`); schemas by subdomain / source system.

Both agree on the invariants: catalog carries env, schema = domain/subdomain, grants go
to role-based groups. They differ only in which token leads — hence tokens + presets, not
one hard-coded format.

Everything is overridable via env so a customer's documented standard wins:
  PAVE_NAMING_PRESET   life_sciences | payer_provider          (default life_sciences)
  PAVE_NAME_TEMPLATES  JSON {asset_type: template}             (overlays the preset)
  PAVE_GROUP_PREFIX    AD/security-group prefix                (default dbx)
  PAVE_GROUP_REGEX     override the owner-group format regex
  PAVE_NAME_ENFORCE    block | warn                            (default block)
"""
import json
import os
import re
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Rules: Databricks identifier constraints per asset family.
#   casing "snake" -> lowercase a-z0-9_ (Unity Catalog identifiers)
#   casing "dns"   -> lowercase a-z0-9-  (deployment names / endpoints / apps)
# ---------------------------------------------------------------------------
_SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]{1,61}$")           # 2-62, no leading digit
_DNS_RE = re.compile(r"^[a-z][a-z0-9-]{1,61}[a-z0-9]$")      # 3-63, dns label-ish

NAME_RULES: dict[str, dict[str, Any]] = {
    # Unity Catalog identifiers
    "catalog": {"casing": "snake", "regex": _SNAKE_RE, "max_len": 62,
                "reserved_prefixes": ["system", "__databricks", "information_schema", "hive_metastore"]},
    "schema":  {"casing": "snake", "regex": _SNAKE_RE, "max_len": 62,
                "reserved_prefixes": ["information_schema"]},
    # DNS-label-ish handles
    "workspace":            {"casing": "dns", "regex": _DNS_RE, "max_len": 30, "reserved_prefixes": []},
    "llm_gateway_endpoint": {"casing": "dns", "regex": _DNS_RE, "max_len": 63, "reserved_prefixes": []},
    "vector_search":        {"casing": "dns", "regex": _DNS_RE, "max_len": 63, "reserved_prefixes": []},
    "app":                  {"casing": "dns", "regex": _DNS_RE, "max_len": 30, "reserved_prefixes": []},
    "cluster":              {"casing": "dns", "regex": _DNS_RE, "max_len": 63, "reserved_prefixes": []},
    "job_cluster":          {"casing": "dns", "regex": _DNS_RE, "max_len": 63, "reserved_prefixes": []},
    "lakebase":             {"casing": "dns", "regex": _DNS_RE, "max_len": 63, "reserved_prefixes": []},
    "sql_warehouse":        {"casing": "dns", "regex": _DNS_RE, "max_len": 63, "reserved_prefixes": []},
    # AD / security group (structural: prefix - ... - role, 3-5 lowercase segments)
    "owner_group": {"casing": "dns", "max_len": 64,
                    "regex": re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+){2,4}$"),
                    "reserved_prefixes": []},
}

# ---------------------------------------------------------------------------
# Templates. Tokens resolved from the request context (see build_context):
#   {domain} {layer} {env} {region} {function} {subfn} {product} {version}
#   {purpose} {role} {short}
# Empty tokens collapse cleanly (e.g. no medallion layer -> "clinical_prod").
# ---------------------------------------------------------------------------
_BASE_TEMPLATES: dict[str, str] = {
    "catalog": "{domain}_{layer}_{env}",
    "schema": "{function}",
    "workspace": "dbx-{domain}-{env}-{region}",
    "llm_gateway_endpoint": "{domain}-{purpose}-{env}",
    "vector_search": "{domain}-vs-{env}",
    "app": "{domain}-{purpose}-{env}",
    "cluster": "{domain}-{env}-{short}",
    "job_cluster": "{domain}-{env}-{short}",
    "lakebase": "{domain}-{env}-{short}",
    "sql_warehouse": "{domain}-wh-{env}",
    "owner_group": "{group_prefix}-{domain}-{env}-{role}",
}

# Presets overlay the base. Thin on purpose: the engine is one, the vocabulary (domains)
# lives in models.py/env. Presets capture the per-vertical schema-granularity difference and
# leave a documented seam for a customer's own standard via PAVE_NAME_TEMPLATES.
PRESETS: dict[str, dict[str, str]] = {
    # OMOP/pharma: schema groups by business function (clinical, biostatistics, ...).
    "life_sciences": {"schema": "{function}"},
    # Payer/provider: schema goes finer, to the sub-function / source-system grain.
    "payer_provider": {"schema": "{subfn}"},
}

DEFAULT_PURPOSE = {"llm_gateway_endpoint": "llm", "app": "app", "vector_search": "vs"}
GROUP_ROLES = ["admin", "read", "write", "rw", "eng", "svc"]


# ---------------------------------------------------------------------------
# Config resolution (env-driven, matches the rest of PAVE).
# ---------------------------------------------------------------------------
def _preset() -> str:
    return os.getenv("PAVE_NAMING_PRESET", "life_sciences")


def group_prefix() -> str:
    return os.getenv("PAVE_GROUP_PREFIX", "dbx").strip().lower()


def enforce_mode() -> str:
    return os.getenv("PAVE_NAME_ENFORCE", "block").strip().lower()


def _templates() -> dict[str, str]:
    """Base <- preset overlay <- PAVE_NAME_TEMPLATES env overlay."""
    tpl = dict(_BASE_TEMPLATES)
    tpl.update(PRESETS.get(_preset(), {}))
    raw = os.getenv("PAVE_NAME_TEMPLATES", "").strip()
    if raw:
        try:
            tpl.update({k: str(v) for k, v in json.loads(raw).items()})
        except Exception:  # noqa: BLE001 — bad JSON must never break intake; ignore the override
            pass
    return tpl


def _rules(asset_type: str) -> dict[str, Any]:
    return NAME_RULES.get(asset_type, {"casing": "snake", "regex": _SNAKE_RE,
                                       "max_len": 62, "reserved_prefixes": []})


# ---------------------------------------------------------------------------
# Token helpers.
# ---------------------------------------------------------------------------
def _sep(casing: str) -> str:
    return "_" if casing == "snake" else "-"


def _slug(value: Optional[str], casing: str) -> str:
    """Lowercase + collapse anything outside the charset to the separator."""
    if not value:
        return ""
    sep = _sep(casing)
    s = re.sub(r"[^a-z0-9]+", sep, str(value).lower()).strip(sep)
    return re.sub(sep + "{2,}", sep, s)


def _region_short(region: Optional[str]) -> str:
    """us-east-1 -> use1, eu-central-1 -> euc1. Falls back to a compacted form."""
    if not region:
        return ""
    parts = region.split("-")
    if len(parts) == 3 and parts[0].isalpha() and parts[1].isalpha():
        return f"{parts[0]}{parts[1][0]}{parts[2]}".lower()
    return re.sub(r"[^a-z0-9]", "", region.lower())


def build_context(request: dict[str, Any], cfg: Optional[dict[str, Any]] = None,
                  *, role: str = "rw") -> dict[str, str]:
    """Assemble template tokens from a request dict (+ optional per-resource config).

    Works for both callers: validation.py (RequestIn dumped to a dict) and providers
    (the persisted request dict). Values are raw here; _render slugs them per asset casing.
    """
    cfg = cfg or {}
    project_id = request.get("project_id") or ""
    return {
        "domain": request.get("business_domain") or "",
        "layer": request.get("medallion_layer") or "",
        "env": request.get("environment") or "",
        "region": _region_short(request.get("region")),
        "function": request.get("business_function") or request.get("business_domain") or "",
        "subfn": request.get("business_sub_function") or request.get("business_function") or "",
        "product": cfg.get("product") or request.get("business_domain") or "",
        "version": cfg.get("version") or "",
        "purpose": cfg.get("purpose") or "",
        "role": role,
        "short": project_id.split("-")[-1] if project_id else "",
        "group_prefix": group_prefix(),
    }


def _render(template: str, tokens: dict[str, str], casing: str) -> str:
    sep = _sep(casing)
    filled = {}
    for k, v in tokens.items():
        # group_prefix is already a slug; region_short too — slug the rest for safety.
        filled[k] = v if k in ("group_prefix", "region") else _slug(v, casing)
    out = template
    for k, v in filled.items():
        out = out.replace("{" + k + "}", v)
    # Drop any unresolved {tokens}, then collapse/trim separators left by empty values.
    out = re.sub(r"\{[a-z_]+\}", "", out)
    out = re.sub(r"[_-]{2,}", sep, out).strip("_-")
    return out


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------
def generate_name(asset_type: str, ctx: dict[str, str]) -> str:
    """Compose a convention-compliant name for asset_type from context tokens."""
    rules = _rules(asset_type)
    casing = rules["casing"]
    tokens = dict(ctx)
    if not tokens.get("purpose") and asset_type in DEFAULT_PURPOSE:
        tokens["purpose"] = DEFAULT_PURPOSE[asset_type]
    template = _templates().get(asset_type, "{domain}-{env}-{short}")
    name = _render(template, tokens, casing)
    if not name:  # every token was empty — last-resort stable stub
        name = _slug(asset_type, casing)
    return name[: rules["max_len"]].strip("_-")


def validate_name(asset_type: str, name: str) -> list[str]:
    """Return human-readable naming-convention errors for `name` (empty == compliant)."""
    rules = _rules(asset_type)
    errs: list[str] = []
    n = (name or "").strip()
    if not n:
        return errs  # blank means "auto-generate" — not an override to validate
    casing = rules["casing"]
    kind = "lowercase letters, digits and underscores (snake_case)" if casing == "snake" \
        else "lowercase letters, digits and hyphens (DNS-style)"
    if len(n) > rules["max_len"]:
        errs.append(f"{asset_type} name '{n}' exceeds {rules['max_len']} characters")
    if not rules["regex"].match(n):
        errs.append(f"{asset_type} name '{n}' must use {kind}, start with a letter, "
                    f"and not end with a separator")
    for pfx in rules.get("reserved_prefixes", []):
        if n == pfx or n.startswith(pfx + _sep(casing)) or n.startswith(pfx):
            errs.append(f"{asset_type} name '{n}' uses reserved prefix '{pfx}'")
            break
    return errs


def suggest_name(asset_type: str, ctx: dict[str, str]) -> str:
    """A compliant name to offer the requester when their override is rejected."""
    return generate_name(asset_type, ctx)


def resolve_name(asset_type: str, request: dict[str, Any], cfg: dict[str, Any]) -> str:
    """The effective name a provider will use: the override if present, else generated."""
    override = (cfg or {}).get("name") or (cfg or {}).get("deployment_name")
    if override:
        return str(override)
    return generate_name(asset_type, build_context(request, cfg))


def owner_group_regex() -> re.Pattern:
    raw = os.getenv("PAVE_GROUP_REGEX", "").strip()
    if raw:
        try:
            return re.compile(raw)
        except re.error:
            pass
    return NAME_RULES["owner_group"]["regex"]


def validate_owner_group(name: str) -> list[str]:
    """Enforce the AD/security-group naming standard on the owning group."""
    n = (name or "").strip()
    if not n:
        return ["owner_group (AD group) is required"]
    if not owner_group_regex().match(n):
        return [f"owner_group '{n}' does not match the AD-group convention "
                f"'{group_prefix()}-<domain>-<env>-<role>' (lowercase, 3-5 segments, "
                f"role one of {GROUP_ROLES})"]
    return []


def convention_hint(asset_type: str) -> str:
    """Human-readable template for the intake form ({group_prefix} shown resolved)."""
    tpl = _templates().get(asset_type, "{domain}-{env}-{short}")
    return tpl.replace("{group_prefix}", group_prefix())
