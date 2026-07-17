# 7. Identity & Separation of Duties (Flow)

Who authenticates as what, and why **submitting** a request is deliberately split from **creating**
resources. Separation of duties (SoD) is a hard requirement for regulated platform teams, and PAVE
models it with two service principals.

```mermaid
flowchart TB
    subgraph users["People"]
        req["👤 Requester"]:::person
        apr["👤 Approver / compliance"]:::person
    end

    subgraph app["Databricks App (runs as APP SP)"]
        api["FastAPI backend"]:::ours
    end

    job["Provisioning Job<br/><i>runs as PROVISIONER SP</i>"]:::dbx
    uc["Unity Catalog + compute<br/><i>create schema, tags, grants,<br/>clusters</i>"]:::dbx
    db[("Lakebase")]:::store

    req -->|"identity headers<br/>(X-Forwarded-Email / -Groups)"| api
    apr -->|"e-sign at gate"| api

    api -->|"APP SP: read + submit + record"| db
    api -->|"APP SP: CANNOT create prod resources"| uc

    api -.->|"PROVISION_MODE=job:<br/>run_now(request_id)"| job
    job -->|"PROVISIONER SP: create resources"| uc
    job -->|"write assets + audit"| db

    classDef person fill:#fde68a,stroke:#b45309,color:#1f2937
    classDef ours fill:#bfdbfe,stroke:#1d4ed8,color:#1f2937
    classDef dbx fill:#bbf7d0,stroke:#15803d,color:#1f2937
    classDef store fill:#e5e7eb,stroke:#6b7280,color:#1f2937
```

## How to read it

- **User identity** comes from Databricks Apps: the platform injects `X-Forwarded-Email` /
  `-Groups`, which `auth.get_current_user` reads. Role (requester / approver / admin) derives from
  group membership. There is no separate PAVE login.
- The **App SP** is the identity the running app uses. It can read state, submit requests, record
  audit — but on the hardened path it is **not** the identity that creates resources.
- The **Provisioner SP** is privileged and runs the **provisioning Job**. When `PROVISION_MODE=job`,
  the app SP merely *triggers* the job (`run_now`); the job — running as the provisioner SP — does
  the actual `CREATE`. Submit and create are therefore different identities.

## Key points

- **Two modes, one engine.** `inprocess` (default, demo) runs the saga inside the backend as the app
  SP; `job` (SoD-hardened, deployed) offloads creation to the provisioner SP. The saga code is the
  same ([04](04-provisioning-saga.md)).
- **Least privilege.** The app SP holds only submit/read grants; only the provisioner SP holds
  create grants. A compromise of the app cannot silently mint prod infrastructure.
- Locally, a **persona switcher** in the SPA sets the identity headers so one machine can demo
  requester → approver → compliance without separate logins.
