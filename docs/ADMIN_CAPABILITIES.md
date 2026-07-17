# PAVE — Admin Capabilities, Gaps & Multi-Workspace Roadmap

How PAVE looks through two lenses — the **Databricks account admin** and the **workspace
admin** — what it does today, what it deliberately *simulates*, and what a customer must
**integrate and automate themselves** to run it for real. Companion to
[`DEPLOYMENT_ROADMAP.md`](DEPLOYMENT_ROADMAP.md) (the two-plane model) and
the SDK-vs-IaC engine model.

> **Guiding principle — simulate, then document the seam.** PAVE cannot create workspaces,
> IAM roles, or S3 buckets from this environment (no account-admin identity, no cloud creds).
> For anything PAVE can't do for real, it **models the outcome** (registry row + synthetic
> handle + governed tags) *and* **emits the exact artifact/steps** the customer must run
> (Account API call, Terraform, cloud IAM). The demo stays complete; the integration seam is
> explicit, not hidden. This mirrors the existing hybrid model + `PAVE_ALLOW_REAL` kill-switch.

---

## 1. The model — two axes

PAVE models a request along two axes:

- **Axis 1 — WHERE it lands.** A hierarchy (account → region/metastore → workspace). Compute lives
  in a workspace; catalogs/schemas live in a regional metastore visible to many workspaces;
  credentials/storage/network/budget-policy are account-scoped.
- **Axis 2 — WHAT the request is.** Not only "new project": also add-to-existing, modify,
  partial-remove, reclassify, promote/clone, access change, quota change, BYO-bucket, residency.

**Status (implemented):** target-workspace routing is live — `RequestIn.target_workspace`
(`models.py`) flows through a per-host client factory `providers/_sdk.client(host)` so a request
provisions into a chosen existing workspace or the app's own. PAVE can also **vend a new serverless
workspace** (`providers/workspace.py`, account-plane). See §6 for the full status of each axis.
Remaining edge: provisioning into a *brand-new* serverless workspace needs a workspace-scoped
identity in that new workspace (an SoD boundary), so its day-0 policy bootstrap defers gracefully.

---

## 2. Account-admin lens — capability matrix

| Capability | Today | Real-world owner | PAVE's honest role |
|---|---|---|---|
| Workspace create | **Implemented** (`workspace.py`): serverless real via `AccountClient` (no cloud creds needed); classic needs credentials+storage configs; verified real end-to-end | Account admin (+ cloud for classic) | Real create behind `ALLOW_REAL`; emits Terraform for classic (record-as-code) |
| Metastore assignment (attach to new workspace) | **Implemented** (`workspace._try_create_real` assigns `METASTORE_ID` after create) | Account admin | Assign on vend so the workspace can host UC resources |
| Cloud substrate (IAM role, root/ext S3 bucket → credential/storage configs) | Missing (consumed, not created) | **Customer AWS automation** | **Document the seam**: PAVE requires the config ids; customer automates creation (boto3/TF) |
| Serverless budget policy | Missing (budget cap modeled, not enforced) | Account admin (`AccountClient`) | Simulate; note it is an account-scoped object |
| Network / PrivateLink / CMK | Missing | Account admin + cloud | Out of hot path; PAVE should *require + record* the config refs |
| Cross-workspace catalog binding | Missing | Account admin | Add: which workspaces a catalog is bound to |
| Fleet-wide tag/attribution rollup | Per-workspace only | Account admin | Extend FinOps to aggregate across workspaces |
| Account identity / SCIM groups + SPs | Assumed to exist | Account admin (IdP) | Document as a prerequisite |

**Structural need:** an **AccountClient identity** + a **workspace-fanout** model. This is the
prerequisite for most of the table.

---

## 3. Workspace-admin lens — capability matrix

| Capability | Today | PAVE's honest role |
|---|---|---|
| **Cluster policy families** (per-tier, seeded per workspace) | **Implemented** (`policies.POLICY_FAMILY` = Standard/Restricted/Dev-Cheap; `policy_for_request` binds by tier; `bootstrap_policy_family` seeds on vend) | Bind the right policy per request; seed the family at workspace bootstrap |
| SQL warehouse vending | Consumed, not vended | Add as a resource type |
| Instance pools | Missing | Add as a resource type |
| Workspace entitlements (cluster-create, SQL access, workspace access) | Partial (UC grants only) | Add workspace-level entitlement grants |
| Secret scopes | Referenced (AI gateway), not created | Add as a governed resource |
| Git folders / volumes / DBFS scaffolding | Missing | Add to project scaffolding |
| Compute limits / quotas | Autotermination default only | Add per-workspace quotas |
| Init-script / library allow-list | Missing | Security control; add later |
| IP access lists / token governance | Missing | Workspace security posture; later |

---

## 4. Cluster policies — where they belong (implemented)

A cluster policy is a **workspace-scoped, admin-managed template** — not a per-request resource.
PAVE handles both concerns (`providers/policies.py`):

1. **Bootstrap (workspace create-time).** `bootstrap_policy_family()` seeds the family into a
   freshly-vended workspace: `PAVE Standard`, `PAVE Restricted` (single-user, locked), `PAVE
   Dev-Cheap` (capped). Policy-as-data (`POLICY_FAMILY`), cached per `(workspace_host, name)`.
   Best-effort: on a brand-new serverless workspace the seed *defers* (needs a workspace-scoped
   identity there — an SoD boundary), rather than failing the create.
2. **Bind (request-time).** `policy_for_request(classification, environment)` picks the right member
   — restricted → `PAVE Restricted` (single-user); dev/test → `PAVE Dev-Cheap`; else `PAVE Standard`.

Both belong to the workspace layer (Layer 1 bootstrap + Layer 2 bind — see §6).

---

## 5. Request-intent catalog (Axis 2) — what to build vs document

| # | Intent | Status | Plan |
|---|--------|--------|------|
| 1 | New project → existing workspace | **Implemented** (target-workspace picker + routing) | done |
| 2 | New project → new workspace | **Implemented** (real serverless vend + metastore assign; policy seed defers) | done |
| 3 | Add resources to existing project | **Implemented** (`POST /api/requests/{id}/resources`, delta provision) | done |
| 4 | Modify a resource (resize, budget↑, autoterm) | Missing | Phase 3 |
| 5 | Remove one resource (partial decommission) | Only whole-project decommission | Phase 3 |
| 6 | Reclassify (e.g. now holds PHI) → re-tag/re-route | Missing | Phase 3 |
| 7 | Promote/clone across envs (replay the record-as-code spec) | Missing | Phase 3 (spec already exists) |
| 8 | Access / ownership grant change | Reassign only | Phase 2 |
| 9 | Quota / budget change | Missing | Phase 3 |
| 10 | BYO-bucket → external-location chain | Simulated catalog only | Phase 2 (needs cloud seam) |
| 11 | Data residency (region → metastore → workspaces) | `region` captured, not enforced | Phase 2 |

---

## 6. The layered target model + phased build

Three layers mapping to the personas:

- **Layer 0 — Account substrate** (Terraform / customer cloud automation): network, CMK,
  credentials, storage, metastore. PAVE **requires + records**, does not own.
- **Layer 1 — Workspace vending** (account admin via PAVE): create workspace → **bootstrap**
  (metastore attach + **cluster-policy family** + default groups + budget policy + secret scope).
- **Layer 2 — In-workspace vending** (workspace admin via PAVE): today's PAVE — schemas,
  clusters, apps, grants — now **targeted at a chosen workspace** and **bound to the right seeded
  policy**.

### Phase 1 (keystone) — target-workspace routing — **IMPLEMENTED**
- `target_workspace` (host) is on `RequestIn` (stored in `metadata`, surfaced by `_flatten`) with
  a workspace **picker** in intake (step 1) fed by `GET /api/meta/workspaces`.
- `providers/_sdk.client(target_workspace)` is now a **factory keyed by target host** (cached per
  host); every real provider (schema, cluster, ai_gateway, vector_search, app) + the tag/grant/SQL
  helpers resolve their client from the request's target. Decommission carries it via `context`.
- **Simulated cleanly** when the target isn't reachable (no creds) — model + record, per the
  guiding principle. Verified end-to-end in demo mode.
- **How a customer enables real multi-workspace routing:** see the `client()` docstring in
  `providers/_sdk.py` — either (a) per-target OAuth SP env vars (uncomment the block), or (b)
  account identity federation. List targets for the picker via `PAVE_TARGET_WORKSPACES` (demo) or
  the Account API scaffold in `routers/meta.workspaces()`.

### Approval notifications (email + deep-link) — **IMPLEMENTED**
- When a request lands `PENDING_APPROVAL`, `services/notifications.notify_approvers` emails the
  approvers (`APPROVERS`) a message with a **clickable deep-link** to the approval
  (`{APP_URL}/#approvals/{id}`). The SPA reads that hash on boot and jumps to the highlighted
  request (`app.js` `handleDeepLink`).
- **Databricks Apps have no built-in email**, so mail SENDS only when `SMTP_HOST` is configured;
  otherwise PAVE **simulates** (logs the rendered email + writes a `notification.approval_requested`
  audit event). Fire-and-forget — a notification failure never breaks request creation.
- **Customer enablement:** set `APPROVERS`, `APP_URL`, and `SMTP_HOST/PORT/USER/PASSWORD/FROM`
  (see `env.example`). No relay = simulated, fully demoable.

### Per-resource governed options + custom tags — **IMPLEMENTED**
- The intake Resources step is **select-then-configure**: pick resources, then a config panel per
  selection. Each resource exposes a governed subset of real 2025-2026 Databricks options:
  catalog (managed/external + isolation), schema (managed location), cluster/job_cluster (access
  mode → data_security_mode, node type, sizing, Photon, DBR LTS, autotermination/spot), app
  (compute size + least-priv resource bindings), lakebase (Provisioned vs Autoscaling, capacity,
  retention, scale-to-zero), LLM gateway (throughput mode, guardrails, rate limits, budget,
  fallbacks), vector search (endpoint/index type, embedding source, pipeline).
- **Governance seams:** external storage comes from a **pre-approved location dropdown**
  (`PAVE_EXTERNAL_LOCATIONS`) — never a raw `s3://`; allow-lists are enforced server-side in
  `validation._validate_resource_options`; hard cost limits stay in the cluster policy.
- **Custom tags:** auto-derived governed keys are always applied; requesters can add their own
  from the allow-listed vocabulary (`ALLOWED_CUSTOM_TAG_KEYS`), merged by `tagging.build_tag_set`.

### Also implemented alongside Phase 1
- **Add resources to an existing project** (`POST /api/requests/{id}/resources`, approver+e-sign):
  WAF-checks the delta, provisions ONLY the new resources, appends them to the request, re-emits
  the as-code spec. UI: registry → Lifecycle → "Add resources to project".
- **Cluster-policy family + bind** (`providers/policies.py`): `POLICY_FAMILY`
  (Standard/Restricted/Dev-Cheap), `policy_for_request()` binds by tier/classification, and
  `bootstrap_policy_family()` seeds all three when a workspace is vended
  (`providers/workspace._bootstrap`). Policy cache is now per-`(target_host, policy_name)`.
- **Workspace bootstrap scaffold**: `_bootstrap()` seeds policies (real-capable) and leaves the
  account-level **metastore-attach** as a ready-to-enable commented block (set `METASTORE_ID`).

### Phase 2 — workspace vending made real-ish + add-to-existing
- Workspace **bootstrap** (policy family, metastore attach) — simulated, with emitted
  Terraform/Account-API steps for the customer to run.
- **Add-to-existing project** (Gap 1): amend endpoint + DB method + a registry "Add resources"
  action that reuses intake scoped to a `project_id`, re-routing approval for **just the delta**.
- BYO-bucket external-location chain (needs the cloud seam documented in Layer 0).

### Phase 3 — lifecycle intents
- Modify / partial-remove / reclassify / promote-clone / quota-change (intents 4-9).

---

## 7. What the customer MUST integrate & automate themselves

PAVE stops at the Databricks control plane. These are the customer's to build (PAVE simulates and
documents each):

1. **Cloud IAM + storage automation** — cross-account IAM role + root/external S3 bucket, then
   register as Databricks credential/storage configs (boto3 or Terraform). Prereq for real
   classic workspaces and external catalogs.
2. **Account-admin identity** — PAVE needs an `AccountClient` identity to create workspaces,
   assign metastores, and set budget policies. Provide it (and scope it tightly).
3. **Network / CMK / PrivateLink** — regulated deployments; provision as account substrate
   (Terraform), hand the config ids to PAVE.
4. **Per-target provisioner SP + grants** — provisioning into workspace X needs an identity with
   rights *in X* (SoD). Multi-workspace = a federated identity or per-workspace SPs.
5. **IdP / SCIM groups** — PAVE assumes owner groups exist; wire your identity provider.
6. **CMDB / ServiceNow / CID registration** — post-provision registration; PAVE stores the refs
   and can open tickets, but the sync is the customer's integration.

---

## 8. Cross-cutting requirements (both personas will demand these)

1. **Entitlement-scoped self-service** — pickers (workspace, catalog, policy) show only what the
   requester's team owns. Without it, self-service exposes the whole account. A rollout blocker.
2. **Bootstrap vs bind** — a new workspace needs a day-0 bundle (metastore, policy family,
   groups, budget policy, secret scope) before it accepts any project.
3. **Fleet-wide governance rollup** — tag coverage / untagged spend / drift **across all
   workspaces**, not one. PAVE's current views are single-workspace.
