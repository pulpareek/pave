# PAVE — Platform Asset Vending Engine

**PAVE (Platform Asset Vending Engine)** is a governed, self-service resource-provisioning
portal built entirely on Databricks Apps. It replaces a multi-day provisioning ticket with a
golden path:

```
intake → risk-tiered approval → programmatic provisioning → enterprise tagging →
FinOps attribution → portable ownership → day-2 governance → decommission
```

Governance is applied at the moment a resource is born — not retrofitted afterward. PAVE is
aimed at regulated platform teams (GxP / HIPAA / GDPR-aware) and doubles as a teaching tool for
the Well-Architected Lakehouse pillars.

- **Stack:** FastAPI backend + a no-build static SPA (vanilla JS) served by the app.
- **Engine:** SDK-primary (`WorkspaceClient`) for runtime provisioning; a YAML Databricks Asset
  Bundle deploys PAVE itself (not per-request Terraform/DABs).
- **Safety:** a `PAVE_ALLOW_REAL` kill-switch (default **off**) so nothing mutates a workspace
  until you opt in — everything degrades to simulated locally.

## Repository layout

```
databricks.yml            # DABs bundle that deploys PAVE (dev/prod targets)
resources/                # bundle resources: app, provisioning job, database binding
src/app/
  app.yaml                # Databricks Apps runtime config (env)
  requirements.txt
  backend/                # FastAPI: routers, services, providers, static SPA
docs/                     # architecture + design docs (start with HOW_IT_WORKS.md)
env.example               # copy to .env for local dev
```

## Documentation

- [`docs/HOW_IT_WORKS.md`](docs/HOW_IT_WORKS.md) — UI→backend walkthrough (best starting point).
- [`docs/architecture/`](docs/architecture/README.md) — C4 + mechanism diagrams.
- [`docs/DEPLOYMENT_ROADMAP.md`](docs/DEPLOYMENT_ROADMAP.md) — deploying in your own environment
  (the Databricks-managed vs customer-cloud two-plane model, phased plan).
- [`docs/ADMIN_CAPABILITIES.md`](docs/ADMIN_CAPABILITIES.md) — account/workspace-admin capabilities + roadmap.

## Why the SDK — not Terraform/DABs or the REST API — for provisioning?

A fair question — IaC gives you versioning, declarative diff, `plan` previews, and rollback,
and those are worth keeping. PAVE keeps them; it just doesn't put them in a state file *per
request*. It **executes imperatively (SDK) and records declaratively**: every provisioned
request emits a canonical desired-state manifest (`services/spec.py`,
`GET /api/requests/{id}/spec`) into an append-only audit log, the Lakebase **registry is the
desired-state store**, and the **governance sweep is the reconcile/drift loop** — so you get
GitOps-grade auditability, diff, and lifecycle without a `.tf` file or bundle state per request.
Generating Terraform/DABs per request would sprawl thousands of tiny state files, put the CLI in
the request hot path, and fight lock contention at vending scale — the wrong tool for
concurrent, user-driven provisioning. The **SDK is chosen over raw REST** for the same
pragmatism: it's the same underlying control plane, but with typed models, auth/paging/retry
handled, and one idiom across every resource — so there's no REST surface the SDK doesn't already
cover for our scope. IaC is still used where it earns its keep: **Terraform** for the
rarely-changing platform substrate (metastore/account) and **DABs** to deploy PAVE itself (plus
an opt-in schema showcase). See [`docs/architecture/`](docs/architecture/README.md)
(09-hybrid-provisioning, 10-reconcile-drift, 18-record-as-code) for the full rationale.

## Built to extend

Two extension directions are worth flagging explicitly — they show PAVE is a governed
*pipeline*, not a Databricks-only script:

- **Provisioning engine — SDK today, IaC-renderable.** PAVE provisions through the Databricks
  SDK/REST APIs, and every approved request also emits a declarative, tagged desired-state
  manifest (`GET /api/requests/{id}/spec`). Because the request is already captured as desired
  state, the *same* request can be rendered as a **templated Terraform script** for teams that
  standardize on IaC — without changing the intake, approval, or governance flow.
- **Beyond Databricks — a pluggable provider model.** Provisioning sits behind a provider
  abstraction (`backend/providers/`), so the same **intake → approval → governance → attribution**
  pipeline can be pointed at new asset types. The same golden path could vend not just Databricks
  assets but **cloud resources — e.g. AWS S3 buckets or IAM roles** — each born tagged, owned,
  attributed, and audited under one governed workflow.

Both are **extension points, not shipped integrations today** — but the architecture
(record-as-code + the provider interface) is deliberately shaped so adding them doesn't require
reworking the core.

## Where PAVE fits as Databricks goes serverless

A reasonable challenge: *if serverless makes spinning up compute trivial, does a provisioning
portal still matter?* It matters **more**, not less — because serverless removes the friction
PAVE was never really about, and leaves the friction it *is* about.

- **Compute config is fading, and PAVE follows it.** Node types, DBR pinning, autoscaling, spot
  policy, cluster policies — serverless removes these knobs. PAVE treats compute mode as a
  first-class choice: pick **Serverless** (the default) and those knobs disappear; only Classic
  targets show them. The vending surface is deliberately shrinking with the platform, not
  clinging to legacy machine config.
- **The hard parts are untouched by serverless.** *Who* may create a resource and who approved
  it (SoD, risk-tiered routing); is it **tagged so spend is attributable** — which serverless
  usage-based billing makes *harder*, since there's no node to tag and `system.billing.usage`
  attribution rests entirely on tags-at-birth; who owns it, when it sunsets, and the audit
  trail; and whether it is born compliant (classification, retention, PHI). All of this is
  compute-agnostic, and it is exactly PAVE's lane.
- **Serverless-native assets need governance most.** LLM gateway endpoints, Vector Search, model
  serving, Lakebase, Apps — the growth areas — are serverless already and carry the highest
  governance/attribution stakes.
- **Complement, don't duplicate.** Where Databricks ships it natively (budget policies, usage
  dashboards, tag policies), PAVE defers and deep-links; it does not re-implement it.

The through-line: **serverless makes it easy to *create* resources; PAVE governs, attributes,
and manages the lifecycle of what gets created** — a layer that gets more valuable as compute
gets easier.

## Run locally (offline-friendly, safe)

No Lakebase required — the app falls back to an in-memory demo store, and with
`PAVE_ALLOW_REAL` unset everything is simulated (no workspace mutation).

```bash
cd src/app
python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8731
# open http://127.0.0.1:8731
```

Use the **Acting as** persona switcher (top-right) to move between requester, platform approver,
and security/compliance and exercise the full intake → approve → provision flow.

Before committing, run the checks. They are offline, need no `pip install`, and force
real providers off, so they can never touch a live workspace:

```bash
make check     # smoke tests + simulated/real provider parity + SPA syntax
```

## Setup (before deploying to your workspace)

**Fastest path — run the guided setup:**

```bash
./setup.sh
```

It prompts for the handful of values below, writes a local `.env` (gitignored), and prints the
exact `databricks bundle deploy … --var …` command — so you don't hand-edit YAML. (Set your
workspace host once in `databricks.yml` → `targets.<t>.workspace.host`.)

**Prerequisites in your workspace** (create these first — the deploy binds to them):
a **SQL warehouse**, a **Lakebase (managed Postgres) instance**, and a **UC catalog**.

Prefer to do it manually? Every workspace-specific value is intentionally blank.

**Required — the deploy fails or loses data if these are unset:**

| Value | Where | What it is |
|-------|-------|-----------|
| Workspace host | `databricks.yml` → `targets.<t>.workspace.host` | Your `https://<workspace>.cloud.databricks.com` |
| `catalog` / `parent_catalog` | `databricks.yml` variables | UC catalog for PAVE's tables + the catalog it provisions into |
| `warehouse_id` | `databricks.yml` | SQL warehouse (the app resource binds to it — blank fails deploy) |
| `lakebase_instance_name` | `databricks.yml` | Lakebase instance (the app resource binds to it — blank fails deploy) |
| **`LAKEBASE_INSTANCE`** | **`src/app/app.yaml`** | **Same instance name. `app.yaml` is uploaded as-is (no `${var}` substitution), so you MUST set this here too — if blank, the app silently runs in in-memory demo mode and loses all data on restart.** |
| `PARENT_CATALOG` / `AUDIT_CATALOG` | `src/app/app.yaml` | UC catalog(s) the deployed app uses (same reason — set in app.yaml). |

**Optional — features stay inert until set (all in `src/app/app.yaml`):**

| Value | Enables |
|-------|---------|
| `PAVE_ALLOW_REAL=1` | Real provisioning (default off = everything simulated) |
| `APPROVERS`, `APP_URL`, `SMTP_*` | Approval email notifications with a deep-link to the approval |
| `PAVE_TARGET_WORKSPACES` | Multi-workspace targeting in the intake picker |
| `PAVE_EXTERNAL_LOCATIONS` | External-location catalogs/schemas |
| `METASTORE_ID` | Metastore attach when vending a new workspace |
| `LLM_ENDPOINT` | The Foundation Model endpoint the intake co-pilot calls |

See [`env.example`](env.example) for the full variable list (local dev) and `src/app/app.yaml`
for the deployed-app env (that's the authoritative place for deploy-time values).

## Deploy

Deploying is **two steps** — the bundle uploads the app; then the app must be deployed to
compute and started:

```bash
# 1. upload the bundle (app + provisioning job + database binding)
databricks bundle validate -t dev
databricks bundle deploy -t dev

# 2. deploy the app to compute + start it (bundle deploy alone does NOT run the app)
databricks bundle run pave -t dev
# then find the running URL:
databricks apps get pave | grep -i url
```

> **Real provisioning creates real resources.** Keep `PAVE_ALLOW_REAL` off until you have
> confirmed the target catalog and are ready. See
> [`docs/DEPLOYMENT_ROADMAP.md`](docs/DEPLOYMENT_ROADMAP.md) for the phased rollout (start
> serverless-first; classic workspaces + external storage need cloud-side IAM/bucket setup).
