# PAVE — What Happens When You Submit a Request

A code-grounded trace of the full journey from clicking **Submit** in the intake form to a
resource going **ACTIVE**, with every file and function on the path. Read this alongside the
diagram in [`architecture/03-request-lifecycle.md`](architecture/03-request-lifecycle.md) and
[`architecture/04-provisioning-saga.md`](architecture/04-provisioning-saga.md); for the wider
UI→backend map see [`HOW_IT_WORKS.md`](HOW_IT_WORKS.md).

> **Key mental model:** submit does **not** provision anything. Submit *validates, gates,
> routes, records*. Provisioning happens **only after the required approvals land** — because
> the provisioning gate is the only place tag presence can be guaranteed.

---

## Stage A — Submit (create + route + persist)

### A0. Browser — `submitIntake()` (`static/assets/app.js:683`)

1. `collectPayload()` (`app.js:630`) assembles the form fields + `collectResources()` into the
   request body.
2. Fires **`POST /api/requests`** via the `api()` helper, which injects your persona headers
   (`X-Pave-Persona`).
3. On success: toast + shows the returned `routing` tier and `waf` result. On a `PaveError`:
   renders the `{error, code, details}` body inline.

### A1. Backend — `create_request()` (`routers/requests.py:30`)

The handler runs a **5-step pipeline** before anything is stored. If any early step fails,
nothing is persisted.

| Step | Code | What it does | On failure |
|------|------|--------------|------------|
| 1. Validate | `validate_request(payload, user.email)` | Server-side check against the authoritative vocabularies (`validation.py`). | `ValidationError` → HTTP 4xx `{errors:[...]}` |
| 2. WAF gate | `waf_evaluate(payload, resources, waivers)` (`well_architected.evaluate`) | Runs the full Well-Architected control set. **Hard** violations block. | `ValidationError` listing `rule_id: title — remediation` |
| 3. Cost estimate | `sum(RATE_CARD[...])` (`routers/finops.py`) | Estimates monthly $ so the cost-escalation branch can fire. | — |
| 4. Route | `route(payload, estimated_cost=...)` (`routing.py`) | Scores the request into a tier + ordered approval gates + rationale. | — |
| 5. Persist + audit | `db.create_request(rec)` + `db.add_audit("request.created")` | Stores the request as `PENDING_APPROVAL`; writes the routing + WAF decision to the append-only log. | — |

**The WAF gate (step 2) is the born-compliant control.** `evaluate()` (`well_architected.py:314`):
- **previews born-compliant defaults** so config-dependent checks see them (autotermination,
  restricted→single-user, LLM guardrails/budget);
- splits findings into **hard** (block now), **soft** (score + waivable), and **waived**
  (soft + logged justification, counts as covered);
- tallies a per-pillar `passed/total/score`.

Only hard findings stop the submit. Soft findings and the injected defaults are *recorded* and
carried forward to provisioning.

**Routing outcome (step 4)** — `routing.py:42`:

```
TIER0  dev + public/internal + low cost                      → 1 approval (platform)
TIER1  test/stage OR confidential                            → 1 approval + budget check
TIER2  prod, restricted/PHI, GxP, new catalog/workspace,
       external AI model, or estimated cost > $2000          → 2 DISTINCT approvers + extra gates
```

TIER2 appends gates dynamically: `gxp-validation`, `account-admin` (new workspace),
`llmops-validation` (risky AI).

**Persisted record (step 5)** — `routers/requests.py:50`. Core fields go to columns; the whole
expanded enterprise metadata set + `change_type` + the full `waf.to_dict()` go into a
`metadata` jsonb blob. Status is set to **`PENDING_APPROVAL`**, `risk_tier` to the routed tier.

### A1 result

The API returns `{request, routing, waf}`. **State now: `PENDING_APPROVAL`. No resource exists,
no tag applied.** The request is waiting in the approver queue.

---

## Stage B — Approval (the gate that unlocks provisioning)

### B1. `decide()` (`routers/approvals.py:50`) — `POST /api/approvals/{id}/decision`

- Caller must be an approver (`user.is_approver`) and must supply an **e-signature** (typed
  full name = 21 CFR Part 11-style e-sign). Missing either → `ApprovalError`/`ValidationError`.
- Request must still be `PENDING_APPROVAL`.
- **Distinct-approver rule:** the same approver cannot approve twice — enforced by checking
  existing approvals (`need a distinct approver`).
- Records the approval (`db.add_approval`) with its gate (`security-compliance` if admin, else
  `platform`) and writes an `approval.{approve|reject}` audit event.

**Reject** → status `REJECTED`, done.

**Approve** → counts *distinct* approvers. Required count = `_required_approvals(risk_tier)`
(**2 for TIER2**, else 1). If not yet met, returns `{status: PENDING_APPROVAL, approvals, required}`
and waits for the next approver.

### B2. Threshold met → provisioning triggered

When `approve_count >= required`:
1. Status → **`APPROVED`**, `request.approved` audit event.
2. `_trigger_provisioning(request_id, actor)` (`routers/approvals.py:39`):
   - if `PROVISION_MODE=job` → `trigger_provisioning_job()` (`services/databricks_jobs.py`):
     the app SP submits a run of the provisioning Job, which runs as the **privileged
     provisioner SP** (Separation of Duties). Falls back to in-process on failure.
   - else (default `inprocess`) → `asyncio.create_task(provision_request(...))` — runs the saga
     in the backend event loop.

---

## Stage C — The provisioning saga (`services/provisioning_service.py:36`)

`provision_request()` drives the request to a terminal state, writing **one asset row + one
audit event per resource**.

1. Load the request; status → **`PROVISIONING`**; `provisioning.started` audit event.
2. `_ensure_owner()` — upsert the owner into the registry and link `owner_id` to the request
   (ownership by reference, so reassignment can re-derive tags later).
3. Pull `waivers_from_request()` once.
4. **For each resource** (the loop, `provisioning_service.py:58`):
   1. `get_provider(rtype)` → `(provider, mode)` (real or simulated — see Stage D).
   2. `build_tag_set(...)` (`tagging.py`) — the canonical, registry-derived tag set.
   3. `record_for_asset(...)` — per-resource WAF outcome (defaults/findings/waived/by_pillar),
      stored on provenance.
   4. `apply_defaults(...)` — inject born-compliant config into the copy the provider receives
      (absent keys only).
   5. `provider.provision(...)` wrapped in **`asyncio.to_thread`** (the SDK is synchronous).
   6. `db.add_asset(asset)` — upsert by `asset_id` (idempotent-friendly), status `ACTIVE`,
      with `applied_tags`, `mode`, `provenance` (incl. the WAF evidence).
   7. `resource.provisioned` audit event.
5. **The one sanctioned catch-and-continue** (`provisioning_service.py:96`): a failed resource
   is captured, a `resource.failed` audit event written, and the loop continues.
6. Final status:
   - all succeeded → **`ACTIVE`**
   - some succeeded, some failed → **`PARTIAL`**
   - none succeeded → **`FAILED`**

   `provisioning.finished` audit event with the created/failed counts.
7. **Record-as-code:** `build_desired_state()` (`services/spec.py`) synthesizes the canonical,
   diffable declarative manifest (`kind: ProjectFootprint`, resolved tags, per-resource
   external ids, WAF evidence, traceability refs) and writes it as a `spec.recorded` audit
   event. This gives GitOps-grade reproducibility without an IaC file per request.

---

## Stage D — Real vs simulated (the hybrid + the safety switch)

`get_provider()` (`providers/registry.py:96`) resolves the provider per resource type.

- **`DEFAULT_MODES`** (`registry.py:20`): `schema` = real; `cluster`/`job_cluster`/`lakebase`/
  `catalog`/`workspace` = simulated; AI types real-capable with graceful fallback. Overridable
  per type via `PROVIDER_MODES` JSON.
- **Real path** (e.g. `SchemaProvider`, `providers/schema.py`): creates the UC schema
  (idempotent — adopts if it exists), applies the governed tag set via
  `entity_tag_assignments` (SQL fallback), grants the owning group baseline privileges.
- **Simulated path** (`providers/simulated.py`): records a registry row + synthetic handle, no
  real spend — but still records the **full tag set** so FinOps/tag-coverage tells a complete
  story.

> **SAFETY — `PAVE_ALLOW_REAL` (default OFF).** `get_provider()` forces real/dabs providers to
> **simulated** unless `PAVE_ALLOW_REAL=1`. Because `WorkspaceClient()` authenticates from this
> laptop, without the guard a "local" test *would* mutate the live workspace. Locally everything
> degrades to simulated; only the deployed app sets the flag. **Do not enable real provisioning
> without explicit go-ahead** — it creates real resources in your configured catalog (`PARENT_CATALOG`).

---

## State machine (the whole journey)

```
        submit                approvals met            saga
DRAFT ──────────► PENDING_APPROVAL ─────────► APPROVED ─────► PROVISIONING ─┬─► ACTIVE
                        │                                                   ├─► PARTIAL
                        │ reject                                            └─► FAILED
                        └──────────► REJECTED

  ACTIVE ── decommission (approver + e-sign) ──► DECOMMISSIONED
                                              └► DECOMMISSION_REQUESTED   (restricted/GxP held for
                                                                           controlled change)
```

Decommission is classification-aware (`provisioning_service.decommission_request`): restricted
(PHI/GxP) assets are **not** hard-deleted unless `controlled=true`; PAVE also refuses to tear
down a project that other active projects `depends_on`.

---

## Every audit event on the happy path

Because `audit_events` is append-only (ALCOA+), the full journey is reconstructable from the log:

```
request.created  →  approval.approve (×N)  →  request.approved  →  provisioning.started
→  resource.provisioned (×resources)  →  provisioning.finished  →  spec.recorded
```

---

## Try it locally (offline, safe)

```bash
cd src/app
python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8731
```

In-memory `demo_mode` (no Lakebase), everything simulated. Use the **Acting as** persona
switcher to submit as *Lead developer*, then approve as *Platform approver* (and, for TIER2,
*Security & compliance* as the second distinct approver) to watch the saga run. Curl recipes
are in the `pave-local-dev` skill.
