# PAVE — Deploying in Your Environment: Capability & Phased Roadmap

PAVE is a working reference implementation. Standing it up in a real AWS-backed Databricks
account is **not** "clone and provision workspaces" — some resources depend on cloud
prerequisites (IAM roles, S3 buckets) that Databricks (and therefore PAVE) references but does
not create. This doc states plainly **what is built today, where the real work is, and a phased
plan** so effort and timelines can be set realistically.

See [`REQUEST_LIFECYCLE_WALKTHROUGH.md`](REQUEST_LIFECYCLE_WALKTHROUGH.md) for the provisioning flow.

---

## 1. The one distinction that explains everything: two planes

| Plane | Examples | Who creates it | PAVE today |
|-------|----------|----------------|------------|
| **Databricks-managed** | schemas, grants, clusters, cluster policies, serverless, managed catalog, apps | Databricks SDK | **Covered 100%** (SDK). No cloud work. |
| **Customer-managed (AWS)** | cross-account IAM roles, root S3 bucket, external S3 buckets, storage/credential configs | **Your AWS account** — Databricks only *references* these | **Not created by PAVE** — it *consumes* the ids |

Every open item below sits on the **AWS plane**. This is real, unavoidable engineering work; it
is also fully automatable (AWS SDK / boto3). PAVE's job is to orchestrate it, not to pretend it
doesn't exist.

**Concrete proof in the code:** the workspace provider (`providers/workspace.py`) creates a
**serverless** workspace for real with just name + region + `compute_mode=SERVERLESS` (no cloud
credential/storage config needed) — verified end-to-end. A **classic/hybrid** workspace additionally
requires a pre-provisioned `credentials_id` + `storage_config_id`; PAVE consumes those ids, and the
AWS resources behind them (cross-account IAM role, root S3 bucket) are the customer's to produce.

---

## 2. What is actually built today

| Resource | Status | Notes |
|----------|--------|-------|
| Schema (UC) | **Real** | Creates schema, applies governed tags, grants owning group (`providers/schema.py`). |
| Grants / tags | **Real** | `entity_tag_assignments` + SQL fallback (`providers/_sdk.py`). |
| Cluster + cluster policy | Real-capable | `providers/cluster_real.py`; simulated by default. |
| App, LLM gateway, vector search | Real-capable | Graceful fallback to modeled. |
| **Workspace** | **Real (serverless) — verified** | `AccountClient.workspaces.create`: serverless needs no cloud configs; classic needs `credentials_id` + `storage_config_id`. Assigns `METASTORE_ID` on create; seeds the cluster-policy family (defers on new serverless ws). Modeled when `ALLOW_REAL` off; emits Terraform for classic (`providers/workspace.py`, `services/spec.py`). |
| Catalog | **Simulated only** | No storage-credential → external-location → catalog chain yet. |
| Lakebase | Simulated | Registry row + synthetic handle. |

**Terraform vs Account API (the recurring question):** workspace *creation* uses the **Account
API**, not Terraform. Terraform is only the **record-as-code artifact** (emitted for an
account-admin to apply under Separation of Duties). There is no per-request `terraform apply` in
the hot path — that is a deliberate design decision, not a limitation.

---

## 3. The AWS-plane gaps (the real work), by resource

### 3a. Classic (customer-managed) workspace
To create a classic workspace for real, these must exist first:
1. **Cross-account IAM role** (AWS) → registered as a Databricks **credentials config**.
2. **Root S3 bucket** (AWS) → registered as a Databricks **storage config**.
3. (optional) network config for customer-managed VPC.

Then the existing workspace provider consumes `credentials_id` + `storage_config_id` and creates
the workspace. **Gap:** a *pre-step provider* that calls AWS (boto3) to create the IAM role +
bucket and register the configs. Net-new; does not exist today.

### 3b. External catalog on an application-team bucket
A "catalog on our bucket" is a **chain**, not one call:
```
IAM role (AWS) → storage credential → external location → catalog
```
Today catalog is simulated-only. **Gap:** model catalog as a resource whose saga runs those
ordered sub-steps, with the AWS IAM step at the front. The provisioning saga already provisions
resources in order, so this fits the existing shape.

### 3c. Post-provision registration (CMDB / CID)
Registering a deployment id / CMDB CI after a workspace is live is a genuine post-provision step
and will be **semi-manual at first**. Model it as a day-2 action (saga tail or governance-sweep
action) that writes the id back onto the asset once available.

---

## 4. Governance / blast-radius (a policy decision, not code)

Concern: if PAVE owns workspace + catalog + policy creation, it accumulates broad control. Two
levers already in the codebase to bound this:

- **Scope by environment.** New-workspace and new-catalog already force **TIER2 + an
  `account-admin` gate** (`routing.py`). Phase 1 can restrict PAVE to **non-prod / PoC** by
  rejecting or hard-routing prod/stage requests to a manual path.
- **Separation of Duties.** The app SP only *submits*; a distinct provisioner SP *creates*
  (`PROVISION_MODE=job`). Keep the provisioner SP's grants deliberately narrow so PAVE
  *cannot* over-grant, regardless of what a request asks for.

Decide the intended scope (non-prod only? stage? prod?) before enabling real mode.

---

## 5. Phased plan

Phasing is native to PAVE: `PAVE_ALLOW_REAL` + per-type `PROVIDER_MODES` flip resource types
real **one at a time**, so each phase ships independently.

### Phase 1 — Serverless-first, no AWS integration (fastest to demo) — **DONE**
- Serverless workspace + managed schema / grants → **real** (verified end-to-end).
- Classic / external → stay simulated (need the AWS seam, Phase 2).
- Serverless vs classic is a per-request config (`compute_mode`, default serverless) — **built**.
- **Approval email notification** with a deep-link — **built** (`services/notifications.py`, fired
  at `PENDING_APPROVAL`; SMTP-or-simulated). (Note: fires on request-pending, not
  `provisioning.finished` — an approval nudge, not a completion email.)
- Scope to non-prod / PoC.
- **Why first:** serverless needs no AWS work, so it is real-capable now with the least effort.

### Phase 2 — AWS integration (classic + external storage)
- Build the AWS pre-step: IAM role + root/external S3 bucket via boto3 → register
  credential/storage configs.
- Turn on **real classic workspace** (consumes those configs — provider already exists).
- Build the **external-catalog chain** (§3b).

### Phase 3 — Enterprise integrations
- **ServiceNow (bidirectional):** PAVE already stores `servicenow_ref` / `change_ref`
  ("integration-ready, no live sync yet" in `models.py`). Add one outbound call in
  `create_request` to open a ticket on submit and store the returned sys_id in the existing
  field — so ticket-closure metrics still flow. Low effort, high value; can be its own mini-phase.
- **CMDB / CID registration** (§3c), initially semi-manual.

---

## 6. Effort snapshot (for expectation-setting)

| Item | Effort | Status / depends on |
|------|--------|-----------|
| Serverless workspace real | Low | **done** (verified) |
| serverless vs classic per-request | Low | **done** (`compute_mode`) |
| Approval email + deep-link | Low | **done** (needs SMTP relay to actually send) |
| AWS IAM + bucket automation (boto3) | **Medium–High** | AWS access + role-creation policy |
| Real classic workspace | Low *(once §3a exists)* | Phase-2 AWS work |
| External-catalog chain | Medium | Phase-2 AWS work |
| ServiceNow open-on-submit | Low–Medium | ServiceNow API creds |
| CMDB / CID registration | Medium *(semi-manual first)* | CMDB API |

---

## 7. Summary for stakeholders

- PAVE covers the **Databricks-managed plane** end to end today; the **AWS-managed plane** (IAM
  roles, buckets) is real, expected work that PAVE orchestrates but cannot skip.
- **Workspace create is via the Account API** (Terraform is only the audit artifact).
- Ship **serverless-first** to demo value with zero AWS integration, then layer classic +
  external storage, then ServiceNow / CMDB.
- Bound blast radius with **environment scoping + a narrow provisioner SP** before enabling real
  mode.
