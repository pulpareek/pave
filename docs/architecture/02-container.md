# 2. Container (C4 Level 2)

Zoom into the **PAVE** box: the major runtime pieces, where each process runs, and how they talk.
"Container" here means an independently running/deployable thing, not a Docker container.

```mermaid
flowchart TB
    user["👤 Requester / approver / admin"]

    subgraph appcont["Databricks App (single deployable)"]
        spa["Static SPA<br/><i>backend/static — no-build<br/>vanilla JS + persona switcher</i>"]
        subgraph api["FastAPI backend (backend.main)"]
            routers["Routers<br/><i>meta · assist · requests · approvals ·<br/>registry · ownership · finops · governance</i>"]
            core["Core logic<br/><i>validation · routing · tagging ·<br/>well_architected</i>"]
            svc["Services<br/><i>assistant · provisioning_service (saga) ·<br/>databricks_jobs · spec</i>"]
            prov["Providers<br/><i>schema · cluster_real · ai_gateway · vector_search ·<br/>app · workspace · simulated (cluster/lakebase/catalog) ·<br/>policies (cluster-policy family) · _sdk (per-host client)</i>"]
        end
    end

    lakebase[("Lakebase (Postgres)<br/><i>owners · requests · approvals ·<br/>assets · quotas · audit_events</i>")]
    job["Provisioning Job<br/><i>provision_runner.py — runs as<br/>provisioner SP (SoD path)</i>"]
    fm["Foundation Model endpoint<br/><i>claude-sonnet-4</i>"]
    uc["Unity Catalog + compute<br/><i>schemas, governed tags,<br/>grants, clusters</i>"]

    user -->|HTTPS| spa
    spa -->|"/api/* (fetch + persona headers)"| routers
    routers --> core
    routers --> svc
    svc --> prov
    svc -->|"async pool (asyncpg)"| lakebase
    svc -->|"draft intake"| fm
    prov -->|"WorkspaceClient (SDK,<br/>wrapped in to_thread)"| uc

    svc -.->|"PROVISION_MODE=job:<br/>run_now"| job
    job -->|"same provisioning_service"| prov
    job -->|"read request / write assets + audit"| lakebase

    classDef person fill:#fde68a,stroke:#b45309,color:#1f2937
    classDef ours fill:#bfdbfe,stroke:#1d4ed8,color:#1f2937
    classDef dbx fill:#bbf7d0,stroke:#15803d,color:#1f2937
    classDef store fill:#e5e7eb,stroke:#6b7280,color:#1f2937

    class user person
    class spa,routers,core,svc,prov ours
    class job,fm,uc dbx
    class lakebase store
```

## How to read it

- **SPA → FastAPI → services → providers** is the whole request path. Routers stay thin (validate,
  call a service, return JSON); business logic lives in `core` and `services`; only **providers**
  touch the Databricks SDK.
- The **provisioning saga** (`provisioning_service`) is the one engine. It runs **in-process** in
  the backend for the demo (`PROVISION_MODE=inprocess`) or is handed to the **provisioning Job**
  (`PROVISION_MODE=job`) for separation of duties — *the same code either way* ([07](07-identity-sod.md)).
- The **Databricks SDK is synchronous**, so every provider call is wrapped in `asyncio.to_thread`
  to keep the async backend responsive.

## Key points

- **No build step on the frontend.** The SPA is hand-written static assets served by the same
  FastAPI process — one deployable, no Node toolchain.
- **Lakebase is the desired-state store.** It holds operational state and the append-only audit; it
  is what replaces per-request Terraform/DABs state files ([08](08-data-model.md)).
- **Providers are pluggable per resource type**, and each has a real and a simulated mode
  ([09](09-hybrid-provisioning.md)).
