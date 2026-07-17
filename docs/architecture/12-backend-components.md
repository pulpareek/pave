# 12. Backend Components (C4 Level 3)

Inside the FastAPI app: the modules and their responsibilities, and the one-way dependency flow.
This is "how the app is built" — the map a developer uses to find where a change goes.

```mermaid
flowchart TB
    spa["Static SPA (backend/static)"]:::ours

    subgraph routers["Routers (thin: validate → call service → return JSON)"]
        direction LR
        r["meta · assist · requests · approvals ·<br/>registry · ownership · finops · governance"]:::ours
    end

    subgraph core["Core logic (pure, testable)"]
        direction LR
        c["validation · routing (policy-as-data) ·<br/>tagging (build_tag_set) · well_architected"]:::ours
    end

    subgraph services["Services (orchestration)"]
        direction LR
        s["assistant (co-pilot) · provisioning_service (saga) ·<br/>databricks_jobs (SoD trigger) · spec (record-as-code) ·<br/>notifications (approval email + deep-link)"]:::ours
    end

    subgraph providers["Providers (only layer that touches the SDK)"]
        direction LR
        p["registry (resolve mode) · _sdk (per-host client factory) ·<br/>schema · cluster_real · ai_gateway · vector_search · app · workspace ·<br/>simulated (cluster/lakebase/catalog) · policies (cluster-policy family)"]:::ours
    end

    db[("database.py<br/><i>async pool · jsonb codec · migrations</i>")]:::store
    ext["Databricks SDK / FM API / UC / compute"]:::dbx

    spa -->|"/api/* + persona headers"| routers
    routers --> core
    routers --> services
    services --> core
    services --> providers
    services --> db
    routers --> db
    providers --> ext
    services -->|"draft"| ext

    classDef ours fill:#bfdbfe,stroke:#1d4ed8,color:#1f2937
    classDef dbx fill:#bbf7d0,stroke:#15803d,color:#1f2937
    classDef store fill:#e5e7eb,stroke:#6b7280,color:#1f2937
```

## How to read it

- **Dependencies point one way:** routers → services → providers → SDK. Routers never call the SDK
  directly, and providers never import routers. That keeps each layer independently testable.
- **Routers are thin.** Each router (one per functional area) validates input, calls a service or a
  core function, and returns JSON. No business logic lives in a router (see `.claude/rules/api.md`).
- **Core is pure.** `validation`, `routing`, `tagging`, and `well_architected` are side-effect-free
  functions — they take a request and return a decision/tag set/score. That is why the governance
  model is auditable: the rules are data + pure functions, not scattered `if`s.
- **Providers are the only SDK boundary.** Everything that mutates a workspace is behind the provider
  interface ([14](14-provider-model.md)), so the safety switch and real/simulated modes have exactly
  one place to live.

## Key points

- **`database.py` is the single persistence gateway** — async pool, the jsonb codec (pass dicts, never
  `json.dumps`), and idempotent migrations all live there.
- The same **services** run whether provisioning is in-process or on the Job — the Job entrypoint
  (`provision_runner.py`) imports `provisioning_service` too ([07](07-identity-sod.md)).
- Adding a resource type = add a provider + register it; no router or core change needed.
