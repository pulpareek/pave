# PAVE — Architecture

**PAVE (Platform Asset Vending Engine)** is a governed, self-service resource-provisioning
portal built entirely on Databricks Apps. It replaces a multi-day provisioning ticket with a
golden path: **intake → risk-tiered approval → programmatic provisioning → enterprise tagging →
FinOps attribution → portable ownership → decommission** — with governance applied at the moment
a resource is born, not retrofitted afterward.

This folder is a set of complementary architecture views, each answering a different question.
Diagrams are **Mermaid** (text-based, render in GitHub and most markdown viewers, easy to diff and
maintain — the source *is* the diagram, nothing to re-export).

Read them roughly top to bottom: they zoom from "the platform in its world" down to "what happens
on one request", then out to "how it is deployed and kept honest over time".

> New to PAVE? Start with [`../HOW_IT_WORKS.md`](../HOW_IT_WORKS.md) — a plain-language
> UI→backend walkthrough that maps every screen to the code path behind it, then come back
> here for the mechanism-level views.

| # | View | Type | Answers |
|---|------|------|---------|
| 01 | [System context](01-system-context.md) | C4 L1 | Who uses PAVE and every external system it depends on. |
| 02 | [Container](02-container.md) | C4 L2 | The major runtime pieces and how they talk. |
| 03 | [Request lifecycle](03-request-lifecycle.md) | State machine | The states a request moves through, from draft to decommissioned. |
| 04 | [Provisioning saga](04-provisioning-saga.md) | Sequence | Step by step, what happens when a request is approved and provisioned. |
| 05 | [Risk-tiered routing](05-risk-tiered-routing.md) | Decision flow | How a request is scored into a tier and which approval gates it hits. |
| 06 | [Governance, tagging & FinOps](06-governance-tagging-finops.md) | Data flow | How one tag set flows onto both planes and lands as cost attribution. |
| 07 | [Identity & separation of duties](07-identity-sod.md) | Flow | The two service principals and why submit is split from create. |
| 08 | [Data model](08-data-model.md) | ER | The operational + evidence tables that are PAVE's system of record. |
| 09 | [Hybrid provisioning](09-hybrid-provisioning.md) | Comparison | Which resource types are provisioned for real vs simulated, and the safety switch. |
| 10 | [Reconcile & drift sweep](10-reconcile-drift.md) | Flow | How PAVE stays the source of truth: sunset, drift, orphan, recertify. |
| 11 | [Deployment (DABs)](11-deployment.md) | Deployment | How PAVE itself is deployed across dev/test/prod workspaces. |

### Deep dives — how each part actually works

Mechanism-level views (same style + legend) that drill into a single feature's internals.

| # | View | Type | Answers |
|---|------|------|---------|
| 12 | [Backend components](12-backend-components.md) | C4 L3 | How the FastAPI app is built inside: routers → core → services → providers → DB. |
| 13 | [NL intake co-pilot](13-intake-copilot.md) | Flow | How plain English becomes a governed draft (Foundation Model + heuristic fallback). |
| 14 | [Provider model](14-provider-model.md) | Flow | How a resource type resolves to a real or simulated provider behind one interface. |
| 15 | [Approval & e-signature](15-approval-esignature.md) | Sequence | How ordered gates, distinct-approver dual approval, and e-sign are enforced. |
| 16 | [Well-Architected enforcement](16-well-architected.md) | Flow | How born-compliant defaults are injected and the 7 pillars score/block/waive. |
| 17 | [Ownership reassignment](17-ownership-reassignment.md) | Sequence | How tags and cost attribution follow the owner on reassignment. |
| 18 | [Record-as-code spec](18-record-as-code.md) | Flow | How the diffable desired-state manifest is built and stored for audit. |
| 19 | [Cost estimate & escalation](19-cost-estimate-escalation.md) | Flow | How a pre-submit cost estimate shifts FinOps left into routing. |

> **Cross-cutting features** woven through several views above: **multi-workspace target routing**
> (a request's `target_workspace` → per-host SDK client — see [02](02-container.md), [04](04-provisioning-saga.md),
> [08](08-data-model.md)); **approval email + deep-link** ([04](04-provisioning-saga.md), [15](15-approval-esignature.md));
> **per-resource governed options + custom tags** at intake; **add-resources-to-existing-project**
> ([03](03-request-lifecycle.md)); and the **cluster-policy family** + serverless workspace vending
> ([09](09-hybrid-provisioning.md), [../ADMIN_CAPABILITIES.md](../ADMIN_CAPABILITIES.md)).

## Conventions used in these diagrams

- **Solid arrows** = synchronous request/response. **Dashed arrows** = async / scheduled / event.
- Color legend (consistent across every view):

| Color | Meaning |
|-------|---------|
| 🟦 Blue | **PAVE** — the components we own and deploy |
| 🟩 Green | **Databricks-managed** services (Unity Catalog, Jobs, Foundation Model API, compute) |
| ⬜ Grey | **Data stores** (Lakebase Postgres, Delta) |
| 🟧 Orange | **External / out-of-platform** (email, Account API, human-facing) |
| 🟨 Amber | **People** — the personas who interact with PAVE |
| 🟥 Red | **Governance-critical** control points (gates, audit, tag enforcement) |

- **App SP** = the Databricks App's service principal (can *submit and read*). **Provisioner SP**
  = the privileged principal that actually *creates* resources on the Job path. The split is the
  heart of PAVE's separation-of-duties model — see [07](07-identity-sod.md).

## How this maps to your environment

These views are deliberately **account-neutral**. To land PAVE in a regulated platform team's
environment, the only substitutions are:

- **Workspaces** — PAVE deploys per environment (dev / test / prod), each its own Databricks
  workspace under one account. See [11](11-deployment.md).
- **Catalogs & schemas** — the target Unity Catalog catalog(s) PAVE provisions into (e.g. a
  landing/sandbox catalog per business domain). PAVE never hard-codes them; they are inputs.
- **AD / SCIM groups** — the owning groups a requester must belong to, and the approver groups
  that back each gate (platform, security-compliance, GxP-validation, LLMOps, account-admin).
- **Compliance scope** — GxP / HIPAA / GDPR flags on a request drive tier and gates; the tag
  vocabulary and mandatory-tag set are configuration, not code.

Nothing in this design is specific to one company: it is the Well-Architected Lakehouse governance
model expressed as a vending machine.

## Related docs

- [../HOW_IT_WORKS.md](../HOW_IT_WORKS.md) — UI→backend walkthrough (best starting point).
- [../DEPLOYMENT_ROADMAP.md](../DEPLOYMENT_ROADMAP.md) — deploying in your own environment.
- [../ADMIN_CAPABILITIES.md](../ADMIN_CAPABILITIES.md) — account/workspace-admin capabilities + roadmap.
