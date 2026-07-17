# 4. Provisioning Saga (Sequence)

What actually happens, step by step, from "user describes a project" to "resources exist, tagged and
recorded". This is PAVE's core mechanic.

```mermaid
sequenceDiagram
    autonumber
    actor U as Requester
    participant SPA as Static SPA
    participant API as FastAPI backend
    participant FM as Foundation Model API
    participant DB as Lakebase
    actor AP as Approver(s)
    participant SAGA as Provisioning saga
    participant PR as Provider (per resource)
    participant UC as Unity Catalog / compute

    U->>SPA: describe project (plain English)
    SPA->>API: POST /api/assist/intake
    API->>FM: draft structured request
    FM-->>API: resources + classification + tags (draft)
    U->>SPA: review + submit
    SPA->>API: POST /api/requests
    API->>API: validate + route (risk tier, gates)
    API->>DB: persist request + audit (PENDING_APPROVAL)
    API-->>AP: notify approvers (email + deep-link, if configured)

    AP->>API: POST /api/approvals/{id}/decision (e-sign)
    API->>DB: append approval + audit
    Note over API,DB: Tier 0/1 = 1 approval; Tier 2 = 2 distinct approvers

    API->>SAGA: provision_request(id)
    API->>DB: status PROVISIONING + audit
    loop for each resource
        SAGA->>SAGA: get_provider(type) + resolve mode
        SAGA->>SAGA: build_tag_set (from registry)
        SAGA->>SAGA: WAF record + inject born-compliant defaults
        SAGA->>PR: provision(config, tags)
        PR->>UC: create + apply governed tags / custom_tags
        UC-->>PR: external id
        alt success
            SAGA->>DB: upsert asset + audit (created)
        else failure
            SAGA->>DB: audit (resource failed) → mark PARTIAL
        end
    end
    SAGA->>DB: emit desired-state spec → audit log
    SAGA-->>API: summary (ACTIVE | PARTIAL)
    API-->>SPA: result + asset list
```

## How to read it

- **Steps 1-6: intake.** The co-pilot drafts; the human reviews and submits. The draft is never
  trusted blindly — the backend re-runs `validate` and `route` server-side.
- **Approval.** Every request waits in `PENDING_APPROVAL`; Tier 0/1 need one e-signed approval,
  Tier 2 needs two distinct approvers. Each signature is its own row ([08](08-data-model.md)). The
  provider client is routed to the request's `target_workspace` (empty = the app's own).
- **The loop is the saga.** For every resource: pick the provider, derive the tag set from the
  registry, record the Well-Architected enforcement and inject born-compliant defaults, then call
  the provider. Success writes an `asset` row; failure is caught *per resource* and marks the
  request `PARTIAL` — the one sanctioned catch-and-continue in the codebase.
- **Record-as-code:** after the loop, a canonical declarative manifest of the resolved desired state
  is written to the audit log (`GET /api/requests/{id}/spec`) — GitOps-grade diffability without an
  IaC file per request.

## Key points

- **Execute imperatively (SDK), record declaratively (spec).** No per-request Terraform/DABs state.
- **Tags are derived, never hand-built** — one `build_tag_set` call is the single source, so they
  are identical on both planes and re-derivable on ownership change ([06](06-governance-tagging-finops.md)).
- The saga is **identical** in-process and on the Job path — only the identity running it differs
  ([07](07-identity-sod.md)).
