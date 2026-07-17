# 8. Data Model (ER)

PAVE's system of record. Operational state lives in **Lakebase (Postgres)** with an async pool;
the **`audit_events`** table is append-only immutable evidence. Together these replace per-request
IaC state files as the desired-state store.

```mermaid
erDiagram
    owners ||--o{ requests : "owns"
    owners ||--o{ assets : "owns"
    requests ||--o{ approvals : "gated by"
    requests ||--o{ assets : "provisions"
    requests ||--o{ audit_events : "records"
    assets ||--o{ audit_events : "records"

    owners {
        text owner_id PK
        text email
        text group_name
        text cost_center
        bool active
    }
    requests {
        uuid id PK
        text project_id
        text project_name
        text requester
        text owner_id FK
        text data_classification
        text environment
        text_array compliance_scope
        jsonb resources
        bool gxp_relevant
        bool contains_phi
        date sunset_date
        text status
        text risk_tier
        jsonb metadata
    }
    approvals {
        bigint id PK
        uuid request_id FK
        text approver
        text decision
        text esignature
        text gate
        timestamptz signed_at
    }
    assets {
        text asset_id PK
        uuid request_id FK
        text type
        jsonb names
        text external_id
        text owner_id FK
        text project_id
        jsonb applied_tags
        text mode
        text status
        date sunset_date
        jsonb provenance
        timestamptz decommissioned_at
    }
    quotas {
        text principal PK
        text resource_type PK
        int used
        int limit_val
    }
    audit_events {
        bigint event_id PK
        timestamptz ts
        text actor
        uuid request_id
        text asset_id
        text event_type
        text from_state
        text to_state
        jsonb payload
    }
```

## How to read it

- **`requests`** is the intake record (what was asked for, its classification, its risk tier, the
  resources as JSONB). The **`metadata`** jsonb column holds the expanded enterprise fields and the
  request's **`target_workspace`** (which workspace to provision into); `database._flatten` surfaces
  those keys to the top level on read, so providers see `request["target_workspace"]` uniformly.
  **`assets`** is one row per *provisioned* resource, carrying its `applied_tags`, `mode`
  (real/simulated), `external_id`, and lifecycle dates.
- **`owners`** is referenced by both requests and assets, so **ownership is by reference** — reassign
  the owner in one place and tags/attribution re-derive ([06](06-governance-tagging-finops.md)).
- **`approvals`** is one row per gate signature (with `esignature` + `gate`), so a Tier-2 request has
  multiple approval rows — the full sign-off chain.
- **`audit_events`** is the immutable spine: every state change, provision, and decommission is an
  append. Code only ever calls `add_audit` — never `UPDATE`/`DELETE` (ALCOA+).

## Key points

- **Operational vs evidence split.** Mutable app state → the operational tables; immutable evidence
  → `audit_events` (append-only). The record-as-code spec manifests are written into that audit log.
- `quotas` enforces per-principal caps per resource type at the gate.
- JSONB columns (`resources`, `applied_tags`, `names`, `payload`, `provenance`) store structured
  detail without a rigid schema, via a registered jsonb codec.
