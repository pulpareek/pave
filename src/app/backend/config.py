"""PAVE configuration + Lakebase credential helper.

All workspace-specific values come from environment variables injected by the
Databricks App runtime (app.yaml) or the bound resources. Locally, sensible
defaults keep the app runnable in demo mode.
"""
import functools
import logging
import os
import time

logger = logging.getLogger("pave.config")

# ---- Deployment posture ----
# demo | production. Production forbids the demo affordances (persona switching);
# see auth.py, which refuses to honour X-Pave-Persona when this is production.
ENVIRONMENT = os.getenv("ENVIRONMENT", "demo").strip().lower()
IS_PRODUCTION = ENVIRONMENT == "production"

# ---- App state plane ----
PAVE_SCHEMA = os.getenv("PAVE_SCHEMA", "pave")          # Lakebase schema PAVE owns
LAKEBASE_INSTANCE = os.getenv("LAKEBASE_INSTANCE", "")  # for generate_database_credential

# ---- Provisioning target plane ----
PARENT_CATALOG = os.getenv("PARENT_CATALOG", "")   # SETUP: UC catalog PAVE provisions into
AUDIT_CATALOG = os.getenv("AUDIT_CATALOG", "")     # SETUP: UC catalog for the audit/registry mirror
AUDIT_SCHEMA = os.getenv("AUDIT_SCHEMA", "pave")
WAREHOUSE_ID = os.getenv("DATABRICKS_WAREHOUSE_ID", "")

# ---- Engine ----
PROVISION_MODE = os.getenv("PROVISION_MODE", "inprocess")   # inprocess | job
PROVISIONING_JOB_ID = os.getenv("PROVISIONING_JOB_ID", "")
PROVIDER_MODES = os.getenv("PROVIDER_MODES", "")            # JSON overrides, optional
# Honour the Tier-0 fast lane: a pre-authorized standard change provisions without a
# human approver (routing.STANDARD_CHANGE_POLICY). Set to 0 to require an approver for
# every tier — useful if a customer's change policy has no pre-authorized category.
AUTO_APPROVE_TIER0 = os.getenv("PAVE_AUTO_APPROVE_TIER0", "1") in ("1", "true", "True", "yes")
# Kill-switch: real/dabs providers only run when this is truthy. Default OFF so
# local/demo runs NEVER mutate the workspace (everything degrades to simulated).
# The deployed app sets PAVE_ALLOW_REAL=1 to enable real provisioning.
ALLOW_REAL = os.getenv("PAVE_ALLOW_REAL", "0") in ("1", "true", "True", "yes")

# ---- Naming convention + uniqueness (read directly in naming.py; listed here for
#      discoverability). All optional — sensible pharma/healthcare defaults apply. ----
#   PAVE_NAMING_PRESET   life_sciences | payer_provider   (default life_sciences)
#   PAVE_NAME_TEMPLATES  JSON {asset_type: template} overriding the preset
#   PAVE_GROUP_PREFIX    AD/security-group prefix          (default dbx)
#   PAVE_GROUP_REGEX     override the owner-group format regex
#   PAVE_NAME_ENFORCE    block | warn                      (default block)

# ---- Multi-workspace routing + external storage allow-lists ----
# Both are ENFORCED server-side (validation.py, providers/_sdk.py), not merely used to
# populate the UI pickers: a request body can name any host or location, and an
# unvalidated target host means pointing service-principal credentials at an arbitrary
# endpoint.
def _csv(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


def target_workspaces() -> list[str]:
    """Hosts a request may provision INTO. Empty = only the app's own workspace."""
    return _csv("PAVE_TARGET_WORKSPACES")


def external_locations() -> list[str]:
    """Pre-approved UC external location NAMES usable as a storage_root."""
    return _csv("PAVE_EXTERNAL_LOCATIONS")


# ---- Postgres connection (auto-injected by the bound `database` resource) ----
PGHOST = os.getenv("PGHOST", "")
PGPORT = int(os.getenv("PGPORT", "5432"))
PGDATABASE = os.getenv("PGDATABASE", "databricks_postgres")
PGUSER = os.getenv("PGUSER", "")

# Local dev identity (when not running behind the Apps proxy)
DEV_USER_EMAIL = os.getenv("DEV_USER_EMAIL", "local-dev@pave.databricks.com")

# ---- Notifications (approval emails + deep-links) ----
# Base URL of the deployed app, used to build the clickable approval deep-link
# (e.g. https://pave-<id>.aws.databricksapps.com). Empty -> a relative "#approvals/{id}".
APP_URL = os.getenv("APP_URL", "").rstrip("/")
# SMTP relay for outbound email. Databricks Apps have NO built-in mail, so email only
# actually SENDS when SMTP_HOST is set; otherwise PAVE simulates (logs + audits) the
# notification so the flow is complete without a relay. See services/notifications.py.
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "pave-noreply@databricks.com")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "1") in ("1", "true", "True", "yes")

# Slack Incoming Webhook — a tokenless URL PAVE POSTs approval notifications to. Databricks
# Apps can't call the platform mailer, but they can POST to a webhook, so this is the
# no-SMTP notification path. Empty = Slack notifications off (email/simulated still apply).
# Create one at api.slack.com/apps -> Incoming Webhooks -> Add to a channel.
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "").strip()


@functools.lru_cache(maxsize=1)
def _workspace_client():
    """Lazily build a WorkspaceClient. In a deployed App / Job this auto-auths
    as the service principal; locally it uses the default profile."""
    from databricks.sdk import WorkspaceClient  # imported lazily so import never blocks boot
    return WorkspaceClient()


def get_db_password() -> str:
    """Return a Lakebase OAuth token to use as the Postgres password.

    Prefers a static PGPASSWORD if present (local dev); otherwise mints a
    short-lived credential via the SDK for the bound Lakebase instance.
    """
    static = os.getenv("PGPASSWORD")
    if static:
        return static
    if not LAKEBASE_INSTANCE:
        raise RuntimeError("LAKEBASE_INSTANCE not set and no PGPASSWORD provided")
    cred = _workspace_client().database.generate_database_credential(
        request_id=str(int(time.time())),
        instance_names=[LAKEBASE_INSTANCE],
    )
    return cred.token


def provider_mode_overrides() -> dict:
    """Parse PROVIDER_MODES JSON ({"cluster": "real", ...}); empty -> {}."""
    if not PROVIDER_MODES.strip():
        return {}
    import json
    try:
        return json.loads(PROVIDER_MODES)
    except Exception as e:  # noqa: BLE001
        logger.warning("Invalid PROVIDER_MODES JSON ignored: %s", e)
        return {}
