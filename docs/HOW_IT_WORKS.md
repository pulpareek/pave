# PAVE — How It Works (UI → Backend Walkthrough)

**PAVE (Platform Asset Vending Engine)** is a governed, self-service resource-provisioning
portal built entirely on Databricks Apps. This document explains **how PAVE actually works
end to end** — what the audience sees in each UI view, and exactly what happens in the
backend when they click — so you can present it (or onboard to it) with a complete mental model.

> **Related docs:** for per-topic deep-dives + diagrams use
> [`architecture/`](architecture/README.md); for deploying in your own environment see
> [`DEPLOYMENT_ROADMAP.md`](DEPLOYMENT_ROADMAP.md). This file is the bridge: it maps every
> screen to the code path behind it.

---

## 1. The one-line story

PAVE replaces a **3–4 day ServiceNow provisioning ticket** with a **~4 minute governed
golden path**:

```
intake → risk-tiered approval → programmatic provisioning → enterprise tagging →
FinOps attribution → portable ownership → day-2 governance → decommission
```

Audience: a regulated **life-sciences platform team** (GxP / HIPAA / GDPR-aware). Second job:
a **teaching tool** for enforcing the Well-Architected Lakehouse pillars *from the moment a
resource is born*, not retrofitted afterward.

---

## 2. Architecture at a glance

```
Browser (no-build static SPA)  ──►  FastAPI backend  ──►  Provider registry (SDK-primary)
   index.html + app.js                routers/ (thin)        real | simulated per type
   persona switcher                   services/ (logic)        │
                                          │                    ▼
                                          ▼             Databricks workspace (UC, compute, AI)
                                     Lakebase (Postgres)  + append-only audit_events
                                     operational state
```

| Layer | What it is | Key files |
|-------|-----------|-----------|
| **Frontend** | Hand-written vanilla-JS SPA (no React/Vite — the offline sandbox can't run npm). Five views + a persona switcher. | `backend/static/{index.html, assets/app.js, assets/styles.css}` |
| **API** | FastAPI. `main.py` mounts routers, serves the SPA (catch-all returns `index.html`; `/api/*` never falls through), central-handles `PaveError` → `{error, code, details}`. | `backend/main.py`, `backend/routers/*` |
| **Logic** | Business logic behind thin routers: the provisioning saga, the intake co-pilot, the Job trigger. | `backend/services/*` |
| **State** | Lakebase (Postgres) for mutable operational state; **`audit_events` is append-only** (ALCOA+, never UPDATE/DELETE). No Lakebase locally → graceful in-memory `demo_mode`. | `backend/database.py` |
| **Engine** | **SDK-primary at any scale** via `WorkspaceClient`. Providers resolve real-or-simulated per resource type. | `backend/providers/*` |

**Design stance worth stating out loud:** per-request Terraform/DABs is explicitly an
*anti-pattern* here. The **Lakebase registry is the desired-state store** and the
**governance sweep is the reconcile loop** — that pair replaces IaC state files.

---

## 3. The five UI views (the demo spine)

The sidebar has five tabs and an **"Acting as"** persona dropdown
(Lead developer / Platform approver / Security & compliance). The persona is the demo
device: it injects an `X-Pave-Persona` header (`backend/auth.py`) so one person can play
requester + two *distinct* approvers and exercise dual approval. The top bar shows the env
workspace/env badge and the tagline *"Guardrails, not gatekeeping — days into minutes."*

Client-side view switching lives in `app.js` → `switchView()`; each view has a `render*()`
function that calls the API through the `api()` helper (which injects the persona headers).

### 3.1 Intake — `renderIntake()`

The self-service request form.

- **NL co-pilot.** Type a plain-English need ("I need a schema for clinical trial patient
  data in prod") and click *Draft with AI* → `POST /api/assist/intake`. The backend
  (`services/assistant.py`) calls the **Foundation Model API** (`databricks-claude-sonnet-4`)
  to fill the form, with a **deterministic heuristic parser fallback** so it always works
  offline. The heuristic detects "clinical / PHI / trial" → `restricted` classification,
  `gxp`/`hipaa` scope, `prod` environment.
- **Golden-path templates** + a multi-step form driven by controlled vocabularies
  (business domain, classification, environment, compliance scope) plus rich enterprise
  metadata (technical lead, backup owner, budget cap, SLA/RTO/RPO, AI-governance fields,
  dependencies).
- **Placement chooser** (Resources step) — provision into an **existing workspace** (a picker
  fed by `GET /api/meta/workspaces` / `PAVE_TARGET_WORKSPACES`) or request a **new workspace**
  (account-level; escalates to Tier 2 + account-admin gate). Sets `target_workspace`.
- **Per-resource governed options** — select a resource, then configure it (catalog
  managed/external + isolation, cluster access mode/sizing/DBR/Photon, lakebase
  Provisioned/Autoscaling + capacity, app compute size, AI gateway + vector-search options).
  Allow-lists come from `/api/meta/form-options`; enforced server-side in `validation.py`.
- **Custom tags** — auto-derived governed keys are always applied; the requester can add their
  own from the allow-list (merged by `tagging.build_tag_set`).
- **Live cost preview** before submit — `POST /api/finops/estimate` returns estimated
  monthly $ and flags budget escalation (> $2000).

**On submit** (`POST /api/requests`, `routers/requests.py`) the backend runs this pipeline:

1. `validate_request()` — server-side validation against the authoritative vocabularies.
2. **WAF gate** (`well_architected.evaluate`) — hard violations *block* here; soft findings
   are recorded and born-compliant defaults injected.
3. Cost estimate from the `RATE_CARD`.
4. **`route()`** (`routing.py`) — assigns the risk tier (see §4).
5. Persist the request as `PENDING_APPROVAL` and write a `request.created` audit event
   carrying the routing + WAF decision.

### 3.2 Approvals — `renderApprovals()`

The approver console (visible only to platform/compliance personas — the requester sees a
"switch persona" message). Shows the pending queue with each request's risk tier and its
**required approval count**.

- **Approval notifications** — when a request lands `PENDING_APPROVAL`, approvers (the
  `APPROVERS` list) are emailed a message with a **deep-link** to the approval
  (`{APP_URL}/#approvals/{id}`); the SPA jumps to and highlights that request. Sends via SMTP
  when `SMTP_HOST` is set, otherwise simulated + audited (`services/notifications.py`).
- **`POST /api/approvals/{id}/decision`** requires an **e-signature** (typed full name =
  21 CFR Part 11-style e-sign).
- **Dual-approval enforcement:** TIER0/TIER1 need 1 approval; **TIER2 needs 2 *distinct*
  approvers**. The code rejects the same approver signing twice (`need a distinct approver`).
- On final approval → status `APPROVED`, an audit event, and **provisioning is triggered**
  (`_trigger_provisioning`): the Job path if `PROVISION_MODE=job` (SoD — the provisioner SP
  creates), otherwise an in-process `asyncio.create_task`.

### 3.3 Registry & Ownership — `renderRegistry()`

The asset inventory. Per asset you can:

- **View the as-code spec** — `GET /api/requests/{id}/spec` returns the canonical declarative
  desired-state (YAML, plus Terraform when a workspace is involved). This is the
  **record-as-code** story: execute imperatively via the SDK, record declaratively into the
  append-only log for GitOps-grade diff/audit *without* an IaC file per request.
- **Add resources** — `POST /api/requests/{id}/resources` (approver + e-sign) amends an ACTIVE
  project with new resources; only the delta is provisioned and the as-code spec is re-emitted.
- **Reassign ownership** — `POST /api/ownership/reassign` (approver + e-sign). Because tags
  are *derived from the registry*, reassignment **re-derives the recorded tags** so FinOps
  attribution follows the new owner automatically.
- **Decommission** — classification-aware (see §5).

### 3.4 Governance — `renderGovernance()` (the day-2 story)

`GET /api/governance/sweep` + `/recertification`. This is the **reconcile loop that replaces
Terraform state**:

- **Sunset autopilot** — assets past their sunset date; an approver can *reclaim*
  (soft-delete), but **restricted/GxP assets are blocked** and require controlled change.
- **Tag drift** — assets below 100% required-tag coverage, with the missing keys listed.
- **Orphan detection** — assets with no owner.
- **Recertification** — owners re-attest assets older than 90 days.

### 3.5 FinOps & WAF — `renderFinops()`

Four panels from `/api/finops/{summary,scorecard,impact,ai}`:

- **Attribution-completeness** (the deliberate *non*-duplication of Databricks): tag-coverage
  %, untagged cost, cost mapped by cost_center / project / domain. PAVE does **not** build
  spend charts — Databricks owns that. A real `system.billing.usage` JOIN is wired at
  `/api/finops/live-cost` with graceful fallback to the rate-card estimate.
- **WAF scorecard** — a real per-pillar 0–100 score computed from the controls actually
  enforced on provisioned assets.
- **ROI / impact** — days→minutes: tickets eliminated, engineer-days saved, dollars saved,
  speedup multiplier.
- **AI FinOps** — LLM endpoint spend by team vs budget, guardrail/logging coverage.

---

## 4. Risk-tiered routing (`routing.py`) — policy as *data*

```
TIER0 (fast lane)   → dev + public/internal + low cost                 → 1 approval (platform)
TIER1 (standard)    → test/stage OR confidential                       → 1 approval + budget check
TIER2 (controlled)  → prod, restricted/PHI, GxP, new catalog,
                      new workspace, external AI model, or cost > $2000 → 2 approvers + extra gates
```

TIER2 dynamically *appends* gates: `gxp-validation`, `account-admin` (new workspace),
`llmops-validation` (risky AI). Tier-0 maps to ITIL "Standard Change" (pre-authorized,
bypasses CAB); higher tiers are "Normal Change." Every decision carries a human-readable
`rationale` list surfaced in the UI and the audit log.

---

## 5. The provisioning saga (`services/provisioning_service.py`)

On approval, the saga walks the request's resources and, **per resource**:

1. Resolve provider + mode (`get_provider`).
2. Build the canonical tag set (`tagging.build_tag_set`).
3. Record WAF evidence + inject born-compliant defaults.
4. Call `provider.provision()` — the SDK is synchronous, so it runs inside
   `asyncio.to_thread`.
5. Write **one asset row + one audit event**.

The **one sanctioned catch-and-continue** lives here: a failed resource is marked, an audit
event written, and the saga continues → final status `ACTIVE` / `PARTIAL` / `FAILED`. It then
emits the resolved **desired-state spec** into the append-only log.

**Decommission** is classification-aware: `restricted` (PHI/GxP) assets are *not* hard-deleted
— they move to `DECOMMISSION_REQUESTED` unless `controlled=true` (controlled change + retention
check done). It also refuses to tear down a project other active projects `depends_on`.

### Hybrid provisioning + the safety kill-switch

Providers resolve **real or simulated per type** (`registry.py` `DEFAULT_MODES`): `schema` is
real and the **AI types (`llm_gateway_endpoint`, `vector_search`) are real by default**
(self-model when the switch is off); `cluster`, `app`, `workspace` are real-capable behind the
switch; `cluster`/`job_cluster`/`lakebase`/`catalog` default to simulated (registry row +
synthetic handle, no real spend). Any type is flippable via `PROVIDER_MODES`. Both paths record
the *full* tag set so FinOps tells a complete story. Clusters bind a tier-appropriate member of
the **cluster-policy family** (Standard / Restricted / Dev-Cheap; `providers/policies.py`).

> **SAFETY — read before any live run.** `PAVE_ALLOW_REAL` defaults to **off**. Because
> `WorkspaceClient()` authenticates from this laptop, without the guard a "local" test *would*
> mutate the live workspace. Locally everything degrades to simulated; only the deployed app
> sets `PAVE_ALLOW_REAL=1`. **Do not enable real provisioning without explicit go-ahead** — it
> creates real resources in your configured catalog (`PARENT_CATALOG`).

---

## 6. The golden thread: tags

**Tags are the connective tissue.** One logical tag set, *derived from the registry*
(`tagging.build_tag_set`), applied identically on both planes — UC governed tags (data/AI)
and compute `custom_tags` — with the same key vocabulary (`project_id`, `cost_center`,
`business_domain`) so `system.billing.usage` joins cleanly. Mandatory tags are enforced
**at the gate** (the only reliable place). Rules: lowercase snake_case keys, no PII/secrets,
never a reserved `Name` key.

This single decision is what makes attribution, ownership-follows-tags, drift detection, and
the FinOps join all work — trace it through a demo and the whole system clicks into place.

---

## 7. Suggested 5-minute flow

1. **Intake** as *Lead developer* → NL co-pilot drafts a restricted clinical request → show
   the live cost preview → submit. Point out it auto-routed to **TIER2**.
2. **Approvals** as *Platform approver* → e-sign approve. Show it still needs a second approver.
3. Switch to *Security & compliance* → e-sign the second approval → provisioning fires.
4. **Registry** → the new asset appears with full tags → open the **as-code YAML spec** →
   do an ownership reassignment and show the tags follow.
5. **FinOps & WAF** → tag-coverage 100%, WAF scorecard, days→minutes ROI. Close on the
   **Governance** sweep showing the day-2 reconcile loop.

---

## 8. Run it locally (offline, safe)

```bash
cd src/app
python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8731
# open http://127.0.0.1:8731 — use the "Acting as" persona switcher to change roles
```

No Lakebase → in-memory `demo_mode`; the full intake → approve → provision flow still works
end to end, everything simulated (safe). See the `pave-local-dev` skill for the curl recipes.
