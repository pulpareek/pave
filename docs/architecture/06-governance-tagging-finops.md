# 6. Governance, Tagging & FinOps (Data Flow)

How **one logical tag set**, derived from the registry, flows onto both governance planes and lands
as **cost attribution** in the billing system. This is what makes spend traceable to a project and
owner — the "attribution completeness" story.

```mermaid
flowchart LR
    reg[("Registry (Lakebase)<br/><i>request + owner =<br/>desired state</i>")]:::store
    build["build_tag_set()<br/><i>single source, snake_case keys</i>"]:::ours

    reg --> build

    build --> uc["Unity Catalog<br/><i>governed tags on schema/catalog</i>"]:::dbx
    build --> compute["Compute<br/><i>custom_tags on cluster/job</i>"]:::dbx

    uc --> billing[("system.billing.usage<br/><i>usage.custom_tags")]:::store
    compute --> billing
    billing --> finops["FinOps lens (/api/finops)<br/><i>tag-coverage % · untagged spend ·<br/>cost mapped to project / owner</i>"]:::ours
    finops -.->|deep-link| dash["Native cost dashboards<br/><i>AI/BI Usage · budgets</i>"]:::ext

    subgraph keys["One key vocabulary (both planes)"]
        direction LR
        k["project_id · cost_center · business_domain ·<br/>data_classification · environment · owner_group ·<br/>owner_email · managed_by · request_id · provisioned_date"]:::gov
    end
    build -.-> keys

    classDef ours fill:#bfdbfe,stroke:#1d4ed8,color:#1f2937
    classDef dbx fill:#bbf7d0,stroke:#15803d,color:#1f2937
    classDef store fill:#e5e7eb,stroke:#6b7280,color:#1f2937
    classDef ext fill:#fed7aa,stroke:#c2410c,color:#1f2937
    classDef gov fill:#fecaca,stroke:#b91c1c,color:#1f2937
```

## How to read it

- **The registry is the source of truth.** `build_tag_set()` reads the request + owner and produces
  the tag dictionary once. That same dictionary is applied as **UC governed tags** (data plane) and
  **compute `custom_tags`** (compute plane) — identical keys, so nothing drifts between planes.
- Because both planes carry the **same keys**, `system.billing.usage.custom_tags` joins cleanly on
  `project_id` / `cost_center` / `business_domain`. Spend becomes attributable without any manual
  mapping.
- PAVE's FinOps surface is **attribution completeness** (how much spend is correctly tagged, what is
  untagged, which project/owner owns it) — it **complements** Databricks' native cost reporting and
  deep-links to it, rather than rebuilding cost charts.

## Key points

- **Tags follow the owner.** Ownership reassignment re-runs `build_tag_set()` with the new owner, so
  `owner_email` / `owner_group` / `cost_center` re-derive automatically — no stale attribution.
- **Rules:** lowercase snake_case keys; no PII or secrets in tags; never emit a `Name` key.
- `managed_by = self-service-portal` marks every PAVE-provisioned asset, so a sweep can instantly
  tell PAVE-governed resources from hand-created ones ([10](10-reconcile-drift.md)).
