# 1. System Context (C4 Level 1)

The highest-level view: **PAVE** as a single black box, the people who use it, and the external
systems it depends on. Use this to explain "what is this thing and what does it touch" to anyone,
including non-engineers.

```mermaid
flowchart TB
    requester["👤 Requester<br/><i>Lead developer / data scientist.<br/>Asks for a project footprint<br/>in plain English</i>"]
    approver["👤 Platform approver<br/><i>Approves standard requests,<br/>e-signs the platform gate</i>"]
    compliance["👤 Security &amp; compliance<br/><i>Second signature on regulated<br/>(PHI / GxP / prod) requests</i>"]
    admin["👤 Platform / account admin<br/><i>Owns policy-as-data, catalogs,<br/>account-level workspace vending</i>"]

    subgraph pave["PAVE — Platform Asset Vending Engine"]
        app["Governed self-service portal<br/><i>Intake, approval, provisioning,<br/>tagging, FinOps, decommission</i>"]
    end

    fm["Foundation Model API<br/><i>databricks-claude-sonnet-4:<br/>NL intake co-pilot</i>"]
    uc["Unity Catalog<br/><i>Governed tags, grants,<br/>tag policies, schemas</i>"]
    compute["Compute plane<br/><i>Clusters, job clusters,<br/>policies</i>"]
    lakebase[("Lakebase (Postgres)<br/><i>Requests, approvals, owners,<br/>assets, append-only audit</i>")]
    billing["system.billing.usage<br/><i>Cost attribution source</i>"]
    account["Account API / IaC substrate<br/><i>Workspace landing-zone vending</i>"]
    notify["Email / Slack<br/><i>Optional approval + result<br/>notifications</i>"]

    requester -->|"describe project,<br/>submit request (HTTPS)"| app
    approver -->|"review + e-sign"| app
    compliance -->|"second e-sign"| app
    admin -->|"configure policy,<br/>catalogs, quotas"| app

    app -->|"draft request"| fm
    app -->|"create schemas,<br/>apply governed tags, grant"| uc
    app -->|"create clusters +<br/>custom_tags"| compute
    app -->|"read / write state<br/>(asyncpg)"| lakebase
    app -->|"join tags to spend"| billing
    app -.->|"escalate workspace vending"| account
    app -.->|"notify"| notify

    classDef person fill:#fde68a,stroke:#b45309,color:#1f2937
    classDef ours fill:#bfdbfe,stroke:#1d4ed8,color:#1f2937
    classDef dbx fill:#bbf7d0,stroke:#15803d,color:#1f2937
    classDef store fill:#e5e7eb,stroke:#6b7280,color:#1f2937
    classDef ext fill:#fed7aa,stroke:#c2410c,color:#1f2937

    class requester,approver,compliance,admin person
    class app ours
    class fm,uc,compute,billing dbx
    class lakebase store
    class account,notify ext
```

## How to read it

- Everything inside the **PAVE** box is one deployable: a Databricks App. Users never touch the SDK,
  the provisioner, or Postgres directly — only the portal UI.
- The **requester** describes what they need; the **co-pilot** (Foundation Model API) turns plain
  English into a structured, tagged request. Approvers and compliance sign at gates that depend on
  the request's risk (see [05](05-risk-tiered-routing.md)).
- The dependency that matters most is **Unity Catalog** — that's where governed tags, grants, and
  schemas are actually created, and where the tag vocabulary joins back to **`system.billing.usage`**
  for cost attribution ([06](06-governance-tagging-finops.md)).

## Key points

- **Governance at birth.** Tags, owner, classification, and audit are attached at creation, not
  bolted on later. There is no "ungoverned" state a resource can exist in.
- **Account-level vending** (new workspaces) is an escalation path, not the common case — it hands
  off to the Account API (real serverless create) under a stricter gate, and emits Terraform for the
  classic path.
- **Multi-workspace targeting** — a request can name a `target_workspace`; PAVE provisions into that
  workspace (or the app's own) via a per-host SDK client, so it is not limited to the workspace it
  runs in.
- **Approval-request email** with a deep-link back to the approval is a built, wired feature (SMTP
  when configured, otherwise simulated + audited). The default record of every action remains the
  append-only audit log in Lakebase; Slack is not implemented.
