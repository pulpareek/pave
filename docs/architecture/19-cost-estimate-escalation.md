# 19. Cost Estimate & Budget Escalation (How cost shifts left into routing)

How PAVE previews cost **before** submit and lets a budget breach change the approval path — FinOps
shifted left to the moment of request, not discovered on next month's bill.

```mermaid
flowchart TB
    draft(["draft request: resources[]"]) --> est["POST /api/finops/estimate"]:::ours
    est --> rc["rate card (est. $/month per type)<br/><i>stand-in until live billing wired</i>"]:::ours
    rc --> monthly["estimated_monthly + per-type breakdown"]:::ours

    monthly --> thresh{"monthly &gt; $2000?"}:::gov
    thresh -->|no| tier["routing uses normal tier logic"]:::ours
    thresh -->|yes| esc["escalates_on_cost = true<br/>→ TIER 2 controlled + budget rationale"]:::gov

    tier --> submit["shown in SPA before submit"]:::ours
    esc --> submit

    subgraph live["Later (Phase 6): real attribution"]
        lc["/api/finops/live-cost<br/><i>system.billing.usage ⨝ list_prices,<br/>grouped by custom_tags[project_id/cost_center]<br/>where managed_by = self-service-portal</i>"]:::dbx
    end
    monthly -.->|"rate-card estimate is the fallback"| lc

    classDef ours fill:#bfdbfe,stroke:#1d4ed8,color:#1f2937
    classDef dbx fill:#bbf7d0,stroke:#15803d,color:#1f2937
    classDef gov fill:#fecaca,stroke:#b91c1c,color:#1f2937
```

## How to read it

- **Estimate before submit.** `/api/finops/estimate` sums a per-resource-type rate card and returns
  `estimated_monthly` + a per-type breakdown, so the requester sees the cost while still editing the
  request — not after provisioning.
- **Cost can change the tier.** When the estimate exceeds the **$2000** threshold, `escalates_on_cost`
  is set and routing pulls the request up to **Tier 2 controlled** with a budget rationale
  ([05](05-risk-tiered-routing.md)). A cheap dev sandbox stays fast-lane; an expensive one earns a
  second look. This is the FinOps guardrail sitting inside the governance gate.
- **The rate card is a documented stand-in.** Until live billing is wired (Phase 6), cost is estimated
  from a simple rate card. The real path, `/api/finops/live-cost`, joins `system.billing.usage` to
  `list_prices` and groups by the `custom_tags` PAVE guarantees — falling back to the estimate when
  the warehouse/system tables are unreachable (e.g. local/demo).

## Key points

- **Same tag keys make live cost a drop-in.** Because provisioning already stamps `project_id`,
  `cost_center`, and `managed_by = self-service-portal` on both planes, the live query needs no new
  plumbing — it just groups by tags that are already there ([06](06-governance-tagging-finops.md)).
- **Attribution completeness, not cost charts.** PAVE quantifies how much spend is correctly
  attributed and flags budget breaches; it deep-links Databricks' native dashboards for the spend
  view itself.
