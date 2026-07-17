# 11. Deployment (DABs)

How **PAVE itself** is deployed. PAVE is shipped as a **Databricks Asset Bundle** — the app, the
provisioning Job, and the Lakebase database are declared once and deployed per environment. (Note
the distinction: DABs deploys *PAVE*; PAVE's *runtime* provisioning engine is the SDK, not a bundle
per request.)

```mermaid
flowchart TB
    subgraph bundle["Asset Bundle (databricks.yml + resources/*.yml)"]
        vars["Variables (customer setup)<br/><i>catalog · schema · warehouse_id ·<br/>lakebase_instance · parent_catalog ·<br/>provisioner_sp</i>"]:::gov
        appres["apps.pave<br/><i>source: src/app · runs as APP SP</i>"]:::ours
        jobres["jobs.pave_provisioning_job<br/><i>serverless · provision_runner.py ·<br/>runs as PROVISIONER SP</i>"]:::ours
        dbres["database (Lakebase)<br/><i>auto-injects PGHOST/PGPORT/…</i>"]:::store
    end

    appres -->|"bound: CAN_CONNECT_AND_CREATE"| dbres
    appres -->|"bound: CAN_USE (OBO FinOps)"| wh["SQL warehouse"]:::dbx
    appres -->|"bound: CAN_MANAGE_RUN"| jobres
    jobres -->|"run_as (SoD boundary)"| dbres

    subgraph envs["One bundle, many workspaces"]
        direction LR
        dev["dev workspace<br/><i>PROVISION_MODE=inprocess<br/>PAVE_ALLOW_REAL off</i>"]:::env
        test["test workspace<br/><i>validate real providers</i>"]:::env
        prod["prod workspace<br/><i>PROVISION_MODE=job<br/>PAVE_ALLOW_REAL on</i>"]:::env
    end
    bundle -.->|"databricks bundle deploy -t dev"| dev
    bundle -.->|"-t test"| test
    bundle -.->|"-t prod"| prod

    classDef ours fill:#bfdbfe,stroke:#1d4ed8,color:#1f2937
    classDef dbx fill:#bbf7d0,stroke:#15803d,color:#1f2937
    classDef store fill:#e5e7eb,stroke:#6b7280,color:#1f2937
    classDef gov fill:#fecaca,stroke:#b91c1c,color:#1f2937
    classDef env fill:#fed7aa,stroke:#c2410c,color:#1f2937
```

## How to read it

- **One bundle, three resources.** The `apps.pave` app, the `pave_provisioning_job`, and the bound
  Lakebase `database` are declared together. The app's bound resources give it exactly the grants it
  needs: connect to Lakebase, use a warehouse for FinOps (on-behalf-of), and trigger the Job.
- **The Job carries the SoD boundary.** `run_as` the provisioner SP means the app can *start*
  provisioning but only the Job's privileged identity performs `CREATE` ([07](07-identity-sod.md)).
  The Job is serverless and its entrypoint is `provision_runner.py`, which calls the same
  `provisioning_service`.
- **All workspace-specific values are variables** — catalog, schema, warehouse, Lakebase instance,
  parent catalog, and the provisioner SP. Nothing workspace-specific is hard-coded, so the same
  bundle promotes across dev → test → prod by changing the target.

## Key points

- **Promotion model.** dev is the safe default (`inprocess`, kill-switch off); test is where the
  real providers get validated; prod runs the SoD Job path with `PAVE_ALLOW_REAL` on. This is the
  recommended multi-workspace setup for a regulated customer.
- **The bound `database` resource auto-injects `PG*` connection env** — never map those manually.
- **Two DABs flavors, by design:** this bundle deploys PAVE; a separate opt-in Python-DABs showcase
  demonstrates schema provisioning. Per-request Terraform/DABs is deliberately *not* used — the
  registry + reconcile sweep replace IaC state ([10](10-reconcile-drift.md)).
