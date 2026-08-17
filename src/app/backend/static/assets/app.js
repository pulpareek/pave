// PAVE — Platform Asset Vending Engine (no-build static SPA)
"use strict";

// ---- persona -> identity headers (local demo; real identity comes from the Apps proxy)
const PERSONAS = {
  requester:  { email: "lead.dev@pave.test",   groups: "rwe-clinical,platform" },
  platform:   { email: "platform@pave.test",   groups: "pave-approvers" },
  compliance: { email: "compliance@pave.test", groups: "platform-admins" },
};
let persona = localStorage.getItem("pave_persona") || "requester";

async function api(path, opts = {}) {
  const p = PERSONAS[persona];
  const headers = Object.assign(
    {
      "Content-Type": "application/json",
      // X-Pave-Persona survives the Databricks Apps proxy (X-Forwarded-* is
      // overwritten by the proxy in a deployed app). Local dev has no proxy, so
      // the X-Forwarded-* values are honored there.
      "X-Pave-Persona": persona,
      "X-Forwarded-Email": p.email,
      "X-Forwarded-Groups": p.groups,
    },
    opts.headers || {}
  );
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  let body = null;
  try { body = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) throw { status: res.status, body };
  return body;
}

// Everything that reaches innerHTML from the API goes through esc(). Requesters control
// justification text, resource names and tag values, and approvers render them in a
// privileged session — so unescaped interpolation there is stored XSS aimed at approvers.
function esc(v) {
  if (v === null || v === undefined) return "";
  return String(v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// Parse a timestamp that may be an epoch (seconds or ms) OR an ISO string. Lakebase's
// TIMESTAMPTZ serializes to ISO, while demo mode uses time.time() floats — the old
// `v * 1000` math turned ISO strings into NaN ("Invalid Date"). Returns a Date or null.
function toDate(v) {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v === "number") return new Date(v > 1e12 ? v : v * 1000);
  const d = new Date(v);
  return isNaN(d.getTime()) ? null : d;
}

function toast(msg, ok = true) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.style.borderColor = ok ? "var(--accent-2)" : "var(--red)";
  t.classList.remove("hidden");
  setTimeout(() => t.classList.add("hidden"), 3800);
}
const el = (tag, attrs = {}, html = "") => {
  const e = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => (k === "class" ? (e.className = v) : e.setAttribute(k, v)));
  if (html) e.innerHTML = html;
  return e;
};
function showModal(title, body) {
  document.getElementById("modal-title").textContent = title;
  document.getElementById("modal-body").textContent = body;
  document.getElementById("modal").classList.remove("hidden");
}

// Show full request detail in modal (for approvals, registry, governance)
async function showRequestDetail(requestId) {
  try {
    const request = await api(`/api/requests/${requestId}`);
    const fmt = (v) => v === null || v === undefined || v === "" ? "—" : esc(String(v));
    const yes = (v) => v ? "✓" : "—";
    const arr = (v) => Array.isArray(v) && v.length ? v.map(esc).join(", ") : "—";

    let html = `<div style="font-size:13px; line-height:1.6">
      <h4 style="margin:12px 0 8px 0; border-bottom:1px solid var(--line); padding-bottom:6px">Request</h4>
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px 20px; margin-bottom:14px">
        <div><b>ID:</b> ${fmt(request.project_id)}</div>
        <div><b>Use case:</b> ${fmt(request.use_case_name)}</div>
        <div><b>Asset name:</b> ${fmt(request.project_name)}</div>
        <div><b>Requester:</b> ${fmt(request.requester)}</div>
        <div><b>Business owner:</b> ${fmt(request.business_owner)}</div>
        <div><b>Owner group:</b> ${fmt(request.owner_group)}</div>
        <div><b>Technical lead:</b> ${fmt(request.technical_lead)}</div>
        <div><b>Backup owner:</b> ${fmt(request.backup_owner)}</div>
        <div><b>Support / on-call:</b> ${fmt(request.support_contact)}</div>
        <div><b>Department:</b> ${fmt(request.department)}</div>
      </div>

      <h4 style="margin:12px 0 8px 0; border-bottom:1px solid var(--line); padding-bottom:6px">Governance &amp; compliance</h4>
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px 20px; margin-bottom:14px">
        <div><b>Data classification:</b> ${fmt(request.data_classification)}</div>
        <div><b>Environment:</b> ${fmt(request.environment)}</div>
        <div><b>SLA tier:</b> ${fmt(request.sla_tier)}</div>
        <div><b>Lifecycle stage:</b> ${fmt(request.lifecycle_stage)}</div>
        <div><b>RTO (hours):</b> ${fmt(request.rto_hours)}</div>
        <div><b>RPO (hours):</b> ${fmt(request.rpo_hours)}</div>
        <div><b>Data retention:</b> ${fmt(request.data_retention)}</div>
        <div><b>Sunset date:</b> ${fmt(request.sunset_date)}</div>
        <div><b>Compliance scope:</b> ${arr(request.compliance_scope)}</div>
        <div><b>GxP relevant:</b> ${yes(request.gxp_relevant)}</div>
        <div><b>Contains PHI:</b> ${yes(request.contains_phi)}</div>
        <div><b>Validated system:</b> ${yes(request.validated_system)}</div>
        <div><b>Security review:</b> ${fmt(request.security_review_status)}</div>
        <div><b>DPIA ref:</b> ${fmt(request.dpia_ref)}</div>
      </div>

      <h4 style="margin:12px 0 8px 0; border-bottom:1px solid var(--line); padding-bottom:6px">Cost &amp; ownership</h4>
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px 20px; margin-bottom:14px">
        <div><b>Cost center:</b> ${fmt(request.cost_center)}</div>
        <div><b>Cost type:</b> ${fmt(request.cost_type)}</div>
        <div><b>Monthly budget cap:</b> ${fmt(request.budget_monthly_cap)}</div>
        <div><b>WBS code:</b> ${fmt(request.wbs_code)}</div>
        <div><b>Business domain:</b> ${fmt(request.business_domain)}</div>
        <div><b>Business function:</b> ${fmt(request.business_function)}</div>
      </div>

      <h4 style="margin:12px 0 8px 0; border-bottom:1px solid var(--line); padding-bottom:6px">Justification</h4>
      <p style="font-style:italic; margin:8px 0; color:var(--muted)">${fmt(request.justification)}</p>
      <p style="margin:8px 0">${fmt(request.description)}</p>

      ${(request.resources || []).length ? `
        <h4 style="margin:12px 0 8px 0; border-bottom:1px solid var(--line); padding-bottom:6px">Resources (${request.resources.length})</h4>
        <div style="margin-bottom:14px">
          ${(request.resources || []).map((r, i) => {
            const cfg = r.config || {};
            const cfgEntries = Object.entries(cfg).filter(([k, v]) => v !== null && v !== undefined && v !== "");
            return `<div style="background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:10px; margin-bottom:8px">
              <b>${fmt(r.type)}</b>${cfgEntries.length ? `<div style="font-size:12px; margin-top:6px">
                ${cfgEntries.map(([k, v]) => `<div><span class="mono">${esc(k)}</span>: ${fmt(JSON.stringify(v))}</div>`).join("")}
              </div>` : ""}
            </div>`;
          }).join("")}
        </div>
      ` : ""}

      ${(request.assets || []).length ? `
        <h4 style="margin:12px 0 8px 0; border-bottom:1px solid var(--line); padding-bottom:6px">Provisioned assets (${request.assets.length})</h4>
        <div style="margin-bottom:14px">
          ${(request.assets || []).map(a => `
            <div style="background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:8px; margin-bottom:6px; font-size:12px">
              <span class="mono">${fmt(a.external_id)}</span> · ${fmt(a.type)} · ${modePill(a.mode)} · <span class="pill">${fmt(a.status)}</span>
              ${a.applied_tags ? `<div style="margin-top:6px">${tagsHtml(a.applied_tags)}</div>` : ""}
            </div>
          `).join("")}
        </div>
      ` : ""}`;

    // ---- routing & risk (why it was tiered the way it was) ----
    const routing = request.routing || (request.metadata || {}).routing || {};
    if (routing.risk_tier || (routing.rationale || []).length) {
      html += `<h4 style="margin:12px 0 8px 0; border-bottom:1px solid var(--line); padding-bottom:6px">Routing &amp; risk</h4>
        <div style="margin-bottom:6px">${tierPill(routing.risk_tier || request.risk_tier)}
          <span class="pill">${fmt(routing.change_type || (request.metadata || {}).change_type)} change</span>
          ${(routing.gates || []).map(g => `<span class="kv">${esc(g)}</span>`).join("")}</div>
        ${(routing.rationale || []).length ? `<ul style="font-size:12px; margin:6px 0 14px 18px; color:var(--muted)">
          ${(routing.rationale || []).map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}`;
    }

    // ---- approvals & e-signatures (with the stored decision comment) ----
    const apps = request.approvals || [];
    if (apps.length) {
      html += `<h4 style="margin:12px 0 8px 0; border-bottom:1px solid var(--line); padding-bottom:6px">Approvals &amp; e-signatures (${apps.length})</h4>
        <div style="margin-bottom:14px">
          ${apps.map(a => {
            const sig = a.signature || {};
            const wd = toDate(a.signed_at); const when = wd ? wd.toLocaleString() : "";
            return `<div style="background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:10px; margin-bottom:8px; font-size:12px">
              <div><b>${a.decision === "approve" ? "✓ Approved" : "✗ Rejected"}</b> · gate <span class="kv">${fmt(a.gate)}</span> · ${fmt(a.approver)} ${when ? `· ${esc(when)}` : ""}</div>
              <div style="margin-top:4px"><b>Signed:</b> ${fmt(a.esignature)}</div>
              <div style="margin-top:4px"><b>Comment:</b> <span style="font-style:italic">${fmt(a.reason)}</span></div>
              ${sig.digest ? `<div class="mono muted" style="margin-top:4px; font-size:11px">manifest ${esc(String(sig.digest).slice(0, 24))}…</div>` : ""}
            </div>`;
          }).join("")}
        </div>`;
    }

    // ---- lineage & references ----
    const refs = [["Source systems", request.source_systems], ["Consumed by", request.consumed_by],
                  ["Depends on", request.depends_on]];
    const refLine = [["Change ref", request.change_ref], ["ServiceNow", request.servicenow_ref],
                     ["Jira", request.jira_epic], ["Confluence", request.confluence_url]]
                    .filter(([, v]) => v);
    if (refs.some(([, v]) => (v || []).length) || refLine.length) {
      html += `<h4 style="margin:12px 0 8px 0; border-bottom:1px solid var(--line); padding-bottom:6px">Lineage &amp; references</h4>
        <div style="font-size:12px; margin-bottom:14px">
          ${refs.filter(([, v]) => (v || []).length).map(([k, v]) => `<div><b>${k}:</b> ${arr(v)}</div>`).join("")}
          ${refLine.map(([k, v]) => `<div><b>${k}:</b> ${fmt(v)}</div>`).join("")}
        </div>`;
    }

    // ---- audit timeline ----
    let audit = [];
    try { audit = await api(`/api/requests/${requestId}/audit`); } catch (e) { /* non-fatal */ }
    if (audit.length) {
      html += `<h4 style="margin:12px 0 8px 0; border-bottom:1px solid var(--line); padding-bottom:6px">Audit trail (${audit.length})</h4>
        <div style="font-size:12px; margin-bottom:14px">
          ${audit.map(e => {
            const td = toDate(e.at || e.created_at || e.signed_at); const ts = td ? td.toLocaleString() : "";
            return `<div class="audit-ev"><span class="kv">${esc(e.event_type)}</span> ${esc(e.actor || "")} ${ts ? `· ${esc(ts)}` : ""}${e.reason ? ` — ${esc(e.reason)}` : ""}</div>`;
          }).join("")}
        </div>`;
    }

    // ---- actions (persona / status aware) ----
    const st = request.status;
    const canRetry = st === "FAILED" || st === "PARTIAL";
    html += `<h4 style="margin:12px 0 8px 0; border-bottom:1px solid var(--line); padding-bottom:6px">Actions</h4>
      <div class="row" style="gap:10px; flex-wrap:wrap">
        <button class="btn small" id="act-amend">Amend / new change request</button>
        ${canRetry ? `<button class="btn ghost small" id="act-retry">Retry failed resources</button>` : ""}
        <button class="btn ghost small" id="act-spec">View as-code spec</button>
      </div>
      <p class="muted" style="font-size:11px; margin-top:8px">Amend re-opens this request in the intake form to edit any field; submitting re-tags the project's assets and provisions net-new resources (approver + e-signature). Decommission lives in Registry &amp; Ownership.</p>
    </div>`;

    const modal = document.getElementById("modal");
    document.getElementById("modal-title").textContent = `Request · ${fmt(request.project_id)}`;
    const body = document.getElementById("modal-body");
    body.innerHTML = html;
    body.style.whiteSpace = "normal";
    modal.classList.remove("hidden");

    body.querySelector("#act-amend").onclick = () => openAmend(request);
    const specBtn = body.querySelector("#act-spec");
    if (specBtn) specBtn.onclick = async () => {
      try { const r = await api(`/api/requests/${requestId}/spec`); showModal(`As-code spec · ${fmt(request.project_id)}`, r.yaml); }
      catch (e) { toast((e.body && e.body.error) || "spec failed", false); }
    };
    const retryBtn = body.querySelector("#act-retry");
    if (retryBtn) retryBtn.onclick = async () => {
      try { await api(`/api/requests/${requestId}/retry`, { method: "POST" }); toast("Retrying failed resources…"); modal.classList.add("hidden"); }
      catch (e) { toast((e.body && e.body.error) || "retry failed", false); }
    };
  } catch (e) {
    toast((e.body && e.body.error) || "Could not load request details", false);
  }
}

// Pill classes come from a fixed vocabulary; anything unrecognised renders unclassed so a
// hostile value can never inject a class (or break out of the attribute).
const KNOWN_PILLS = ["tier0", "tier1", "tier2", "real", "simulated", "dabs", "degraded"];
const pill = (value, extraClass = "") => {
  const key = String(value || "").toLowerCase();
  const cls = KNOWN_PILLS.includes(key) ? key : "";
  return `<span class="pill ${cls} ${extraClass}">${esc(value || "-")}</span>`;
};
const tierPill = (t) => pill(t);
const modePill = (m) => pill(m);
const tagsHtml = (tags) => `<div class="tagset">${Object.entries(tags || {})
  .map(([k, v]) => `<span class="kv">${esc(k)}=${esc(v)}</span>`).join("")}</div>`;

let OPTS = null, TEMPLATES = [];
let WORKSPACES = [{ host: "", label: "This workspace (default)", self: true }];
let POSTURE = null;

// ============================================================ POSTURE BANNER
// A governed-provisioning demo is only credible if the viewer can tell which resources
// are really created and which are modelled, before they trust a green ACTIVE.
async function renderPosture() {
  const bar = document.getElementById("posture-banner");
  if (!bar) return;
  try { POSTURE = await api("/api/meta/posture"); }
  catch (e) { bar.classList.add("hidden"); return; }

  const p = POSTURE.provisioning, s = POSTURE.storage, id = POSTURE.identity;
  const notes = [];
  if (!p.allow_real) {
    notes.push(`<b>Nothing is created in the workspace.</b> Every resource type is modelled (PAVE_ALLOW_REAL is off).`);
  } else if (p.real_types.length) {
    notes.push(`<b>Really created:</b> ${p.real_types.map(esc).join(", ")}. All other types are modelled.`);
  }
  if (!s.persistent) notes.push(`<b>In-memory storage.</b> ${esc(s.note)}`);
  if (id.personas_enabled) notes.push(`<b>Demo identity.</b> ${esc(id.note)}`);
  if (!p.separation_of_duties) notes.push(`Provisioning runs in-process as the app service principal (no separation of duties).`);

  if (!notes.length) { bar.classList.add("hidden"); return; }
  bar.className = "posture" + (p.allow_real && s.persistent ? " soft" : "");
  bar.innerHTML = `<span class="posture-tag">Demo posture</span>
    <span class="posture-notes">${notes.join(" · ")}</span>
    <button class="btn ghost small" id="posture-detail">Details</button>`;
  bar.querySelector("#posture-detail").onclick = () => {
    const lines = Object.entries(p.by_type).map(([t, v]) =>
      `${t.padEnd(22)} ${v.mode.padEnd(10)} ${v.explanation}`).join("\n");
    showModal("What this deployment actually does",
      `environment: ${POSTURE.environment}\nidentity:    ${id.source}\n` +
      `storage:     ${s.backend}\nprovisioning:${p.mode} (allow_real=${p.allow_real})\n\n${lines}`);
  };
}

// ===================================================================== INTAKE
const STEPS = [
  { key: "project", title: "Project & Ownership" },
  { key: "compliance", title: "Classification & Compliance" },
  { key: "cost", title: "Cost & Lifecycle" },
  { key: "deps", title: "Dependencies & Traceability" },
  { key: "resources", title: "Resources" },
  { key: "review", title: "Review & Submit" },
];
let _step = 0;
const opt = (k) => OPTS[k] || [];

function renderIntake() {
  _step = 0;
  const v = document.getElementById("view-intake");
  v.innerHTML = "";
  v.appendChild(el("h1", {}, "Request a project footprint"));
  v.appendChild(el("p", { class: "sub" },
    "Describe it in plain English or step through the guided intake. Everything is governed, tagged, and attributed from creation — and becomes the system-of-record for the asset's life."));

  // AI co-pilot
  const cop = el("div", { class: "card" });
  cop.style.borderColor = "var(--lava)";
  cop.innerHTML = `
    <div class="flex"><h3 style="margin:0">✨ Intake co-pilot</h3>
      <span class="right pill">Foundation Model API · heuristic fallback</span></div>
    <p class="muted" style="font-size:12px;margin:6px 0">e.g. "I need a stage sandbox for an oncology RWE project that touches PHI — a schema, a single-user cluster, and a small app."</p>
    <textarea id="cop-text" placeholder="Describe your project..."></textarea>
    <div class="flex" style="margin-top:8px"><button class="btn small" id="cop-go">Draft with AI</button>
      <span class="right muted" id="cop-src"></span></div>
    <div id="cop-rationale" class="tagset"></div>`;
  cop.querySelector("#cop-go").onclick = draftWithAI;
  v.appendChild(cop);

  // templates
  const tWrap = el("div", { class: "grid cols-3" });
  TEMPLATES.forEach((t) => {
    const c = el("div", { class: "card click" });
    c.innerHTML = `<h3>${t.name}</h3><p class="muted" style="font-size:12px">${t.description}</p>
      <div class="tagset">${t.resources.map(r => `<span class="kv">${r.type}</span>`).join("")}</div>`;
    c.onclick = () => applyTemplate(t, c);
    tWrap.appendChild(c);
  });
  v.appendChild(el("div", { class: "section-title" }, "<h2>Golden-path templates</h2>"));
  v.appendChild(tWrap);

  // stepper
  v.appendChild(el("hr", { class: "sep" }));
  const wrap = el("div", {}); wrap.id = "intake-form";
  wrap.innerHTML = `
    <div class="completion">
      <div class="completion-bar"><span id="cbar"></span></div>
      <span id="cpct" class="muted"></span>
      <span class="muted req-legend"><span class="req">*</span> required</span>
    </div>
    <div class="steps-ind">${STEPS.map((s, i) =>
      `<span class="step-chip" data-i="${i}"><b>${i + 1}</b> ${s.title}</span>`).join("")}</div>
    <div id="intake-errors"></div>
    ${stepPanels()}
    <div class="flex" style="margin-top:16px; gap:10px">
      <button class="btn ghost" id="step-back">Back</button>
      <button class="btn" id="step-next">Next</button>
      <button class="btn ghost" id="step-preview" style="display:none">Preview cost</button>
      <button class="btn" id="step-submit" style="display:none">Submit request</button>
      <span id="cost-out" class="muted"></span>
    </div>`;
  v.appendChild(wrap);

  // wire resource picker -> (re)render the stacked config panels for selected resources
  wrap.querySelectorAll("#resource-picker .rpick .rtype").forEach(cb => {
    cb.onchange = () => {
      cb.closest(".rpick").classList.toggle("selected", cb.checked);
      renderResourceConfigs();
      refreshAI(); updateCompletion();
    };
  });
  // wire placement chooser (existing vs new workspace)
  wrap.querySelectorAll("input[name='placement']").forEach(r => {
    r.onchange = () => {
      const isNew = wrap.querySelector("input[name='placement']:checked").value === "new";
      document.getElementById("placement-new").classList.toggle("hidden", !isNew);
      document.getElementById("placement-existing").classList.toggle("hidden", isNew);
    };
  });
  // per-resource config toggles (panels re-render, so delegate on the container)
  document.getElementById("resource-configs").addEventListener("change", onResourceConfigToggle);
  // custom-tags "+ Add tag" + initial render
  document.getElementById("add-tag").onclick = addCustomTagRow;
  renderResourceConfigs();
  // conditional hints
  ["f-data_classification", "f-environment", "f-sla_tier", "f-gxp_relevant"].forEach(id => {
    const e = document.getElementById(id); if (e) e.onchange = refreshHints;
  });
  // cascading org taxonomy: LOB -> Function -> Sub-Function
  const lobEl = document.getElementById("f-business_domain");
  if (lobEl) lobEl.addEventListener("change", () => { refreshTaxonomy("lob"); updateCompletion(); });
  const fnEl = document.getElementById("f-business_function");
  if (fnEl) fnEl.addEventListener("change", () => { refreshTaxonomy("function"); updateCompletion(); });
  refreshTaxonomy("lob");
  wrap.querySelectorAll("#f-compliance input").forEach(cb => cb.onchange = refreshHints);
  document.getElementById("step-back").onclick = () => showStep(_step - 1);
  document.getElementById("step-next").onclick = nextStep;
  document.getElementById("step-preview").onclick = previewCost;
  document.getElementById("step-submit").onclick = submitIntake;
  loadOwnerGroups();
  // live validation + completion %
  wrap.addEventListener("input", onIntakeInput);
  wrap.addEventListener("change", onIntakeInput);
  refreshAI();
  showStep(0);
  if (AMEND) injectAmendBanner();   // re-show the amend context if a re-render wiped it
}

// Currently-required fields (dynamic by tier) -> drives completion % + step checks.
function requiredNow() {
  const v = (id) => { const e = document.getElementById(id); return e ? e.value.trim() : ""; };
  const chk = (id) => { const e = document.getElementById(id); return e ? e.checked : false; };
  const cls = v("f-data_classification"), env = v("f-environment"), sla = v("f-sla_tier");
  const gdpr = [...document.querySelectorAll("#f-compliance input:checked")].some(c => c.value === "gdpr");
  const regulated = cls === "restricted" || chk("f-gxp_relevant");
  const prodCrit = env === "prod" || sla === "tier1";
  // Each entry carries {step, id, label, ok}. `id` is the input element (or a container for
  // composite requirements); `label` drives the blocking error list. `ok` is a live boolean.
  const req = [
    { step: 0, id: "f-use_case_name", label: "Use case name (min 3 chars)", ok: v("f-use_case_name").length >= 3 },
    { step: 0, id: "f-project_name", label: "Technical asset name (min 3 chars)", ok: v("f-project_name").length >= 3 },
    { step: 0, id: "f-description", label: "Description (min 20 chars)", ok: v("f-description").length >= 20 },
    { step: 0, id: "f-justification", label: "Business justification (min 30 chars)", ok: v("f-justification").length >= 30 },
    { step: 0, id: "f-owner_group", label: "Owning AD group", ok: !!v("f-owner_group") },
    { step: 0, id: "f-business_owner", label: "Business owner (email)", ok: !!v("f-business_owner") },
    { step: 0, id: "f-business_domain", label: "Line of Business", ok: !!v("f-business_domain") },
    { step: 0, id: "f-business_function", label: "Business function", ok: !!v("f-business_function") },
    { step: 1, id: "f-data_classification", label: "Data classification", ok: !!cls },
    { step: 1, id: "f-environment", label: "Environment", ok: !!env },
    { step: 2, id: "f-cost_center", label: "Cost center", ok: !!v("f-cost_center") },
    { step: 4, id: "resource-picker", label: "At least one resource", ok: document.querySelectorAll("#resource-picker .rtype:checked").length > 0 },
    { step: 5, id: null, label: "Cost-ownership acknowledgement", ok: !!document.querySelector(".ack[value='cost-ownership']:checked") },
  ];
  if (env === "dev" || env === "test") req.push({ step: 1, id: "f-sunset_date", label: "Sunset date (dev/test)", ok: !!v("f-sunset_date") });
  // Sub-function is required only when the chosen function actually has sub-functions.
  const _tax = (OPTS && OPTS.business_taxonomy) || {};
  const _subs = ((_tax[v("f-business_domain")] || {})[v("f-business_function")]) || [];
  if (_subs.length) req.push({ step: 0, id: "f-business_sub_function", label: "Business sub-function", ok: !!v("f-business_sub_function") });
  const anyAI = [...document.querySelectorAll("#resource-picker .rtype:checked")]
    .some(cb => ["llm_gateway_endpoint", "vector_search"].includes(cb.value));
  if (anyAI) {
    req.push({ step: 4, id: "f-ai_risk_tier", label: "AI risk tier (not 'unacceptable')", ok: !!v("f-ai_risk_tier") && v("f-ai_risk_tier") !== "unacceptable" });
    req.push({ step: 4, id: "f-intended_use", label: "Intended use (AI)", ok: !!v("f-intended_use") });
  }
  if (regulated) {
    req.push({ step: 1, id: "f-validated_system", label: "Validated system (restricted/GxP)", ok: chk("f-validated_system") });
    req.push({ step: 1, id: "f-data_retention", label: "Data retention (restricted/GxP)", ok: !!v("f-data_retention") });
  }
  if (gdpr) req.push({ step: 1, id: "f-dpia_ref", label: "DPIA reference (GDPR)", ok: !!v("f-dpia_ref") });
  if (prodCrit) {
    req.push({ step: 0, id: "f-backup_owner", label: "Backup owner (prod/tier1)", ok: !!v("f-backup_owner") });
    req.push({ step: 0, id: "f-support_contact", label: "Support / on-call (prod/tier1)", ok: !!v("f-support_contact") });
    req.push({ step: 2, id: "f-rto_hours", label: "RTO hours (prod/tier1)", ok: !!v("f-rto_hours") });
    req.push({ step: 2, id: "f-rpo_hours", label: "RPO hours (prod/tier1)", ok: !!v("f-rpo_hours") });
    req.push({ step: 3, id: "f-security_review_status", label: "Security review status (prod/tier1)", ok: !!v("f-security_review_status") });
  }
  return req;
}

// Shared gate helpers (reused by submit + step advancement). ---------------------
function reqErrorsHtml(missing, heading) {
  return `<div class="errors"><b>${heading}</b><ul>${
    missing.map(m => `<li>${m.label}</li>`).join("")}</ul></div>`;
}

// Flash the offending inputs so the user can see WHERE the gaps are, not just a list.
function markMissing(missing) {
  missing.forEach(m => {
    if (!m.id) return;
    const e = document.getElementById(m.id);
    if (!e || !e.classList || e.id === "resource-picker") return;
    e.classList.add("invalid");
    const sib = e.nextElementSibling;
    if (sib && sib.classList && sib.classList.contains("field-err")) sib.textContent = "Required.";
  });
}

function updateCompletion() {
  const req = requiredNow();
  const done = req.filter(r => r.ok).length;
  const pct = Math.round(100 * done / req.length);
  const bar = document.getElementById("cbar"), txt = document.getElementById("cpct");
  if (bar) bar.style.width = pct + "%";
  if (txt) txt.textContent = `${pct}% complete · ${done}/${req.length} required fields`;
  // per-step completeness check mark
  document.querySelectorAll("#intake-form .step-chip").forEach(chip => {
    const s = Number(chip.dataset.i);
    const stepReqs = req.filter(r => r.step === s);
    const complete = stepReqs.length > 0 && stepReqs.every(r => r.ok);
    chip.classList.toggle("done", complete);
  });
}

const EMAIL_RX = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
function checkField(elm) {
  if (!elm || !elm.id) return;
  const sib = elm.nextElementSibling;
  const err = (sib && sib.classList && sib.classList.contains("field-err")) ? sib : null;
  let msg = "";
  const val = (elm.value || "").trim();
  if (val) {
    if (elm.type === "email" && !EMAIL_RX.test(val)) msg = "Enter a valid email.";
    else if (elm.type === "url" && !/^https?:\/\/.+/.test(val)) msg = "Enter a URL (https://…).";
    else if (elm.dataset && elm.dataset.pattern && !new RegExp(elm.dataset.pattern).test(val)) msg = "Invalid format.";
    else if (elm.tagName === "TEXTAREA" && elm.dataset.min && val.length < Number(elm.dataset.min)) msg = `${val.length}/${elm.dataset.min} min`;
    else if (elm.type === "number" && Number(val) < 0) msg = "Must be ≥ 0.";
  } else {
    // Empty + currently required (per the live requiredNow set) -> flag it.
    const missingIds = new Set(requiredNow().filter(r => !r.ok && r.id).map(r => r.id));
    if (missingIds.has(elm.id)) msg = "Required.";
  }
  // textarea live counter even when valid
  if (!msg && elm.tagName === "TEXTAREA" && elm.dataset.min) {
    msg = `${val.length}/${elm.dataset.min} min ✓`;
  }
  elm.classList.toggle("invalid", !!msg && !msg.includes("✓"));
  if (err) { err.textContent = msg; err.classList.toggle("ok", msg.includes("✓")); }
}

function onIntakeInput(e) {
  if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) checkField(e.target);
  if (e.target && e.target.classList && e.target.classList.contains("ai-provider")) refreshAI();
  refreshHints();
  updateCompletion();
}

function stepPanels() {
  const sel = (id, list, blank, h, required) =>
    `${L(labelFor(id), h, required)}${selectHtml(id, blank ? ["", ...list] : list)}`;
  const checks = (cls, list) => list.map(s =>
    `<label class="check"><input type="checkbox" class="${cls}" value="${s}"/> ${s}</label>`).join("");
  // L(label, help) -> label with a help (?) icon; inp(...) -> constrained input.
  return `
  <div class="step-panel" data-step="0"><div class="grid cols-2">
    <div class="card">
      <h3 style="margin:0 0 10px">The use case</h3>
      ${L("Use case name", "The business intent, 3–80 chars. e.g. \"Oncology Trial Data Mart\". Distinct from the technical asset name below.", true)}
      ${inp("f-use_case_name", { placeholder: "Oncology Trial Data Mart", maxlength: 80, pattern: "^[A-Za-z0-9][A-Za-z0-9 _-]{2,79}$" })}
      ${L("Business justification", "Why it's needed (business case). Min 30 chars — captured for audit / GxP.", true)}
      ${area("f-justification", { min: 30, maxlength: 2000, placeholder: "Business case / what it replaces…" })}
      ${L("Description", "What this delivers. Min 20 chars — shown to approvers and in the catalog.", true)}
      ${area("f-description", { min: 20, maxlength: 1000, placeholder: "What this project delivers…" })}
      ${L("Technical asset name", "Human-readable asset label, 3–60 chars: letters, digits, space, _ or -.", true)}
      ${inp("f-project_name", { placeholder: "onco-trial-rwe", maxlength: 60, pattern: "^[A-Za-z0-9][A-Za-z0-9 _-]{2,59}$" })}
    </div>
    <div class="card">
      <h3 style="margin:0 0 10px">Organization &amp; ownership</h3>
      <div>${sel("f-business_domain", opt("business_domains"), false, "Line of Business — top of the org taxonomy. Drives attribution, discovery + the function list below.", true)}</div>
      <div class="row">
        <div>${sel("f-business_function", [""], false, "Business function within the selected Line of Business.", true)}</div>
        <div>${sel("f-business_sub_function", [""], false, "Sub-function within the selected function (when applicable).")}</div>
      </div>
      ${L("Business owner (email)", "Accountable business owner. Distinct from the technical owning group.", true)}
      ${inp("f-business_owner", { type: "email", placeholder: "owner@co.com" })}
      ${L("Owning group (Databricks)", "The Databricks group that owns this and receives the grants. Pick an existing group, or type a new name to have it created.", true)}
      <input id="f-owner_group" list="owner-group-dl" placeholder="${opt("owner_group_template") || "dbx-<domain>-<env>-<role>"}" maxlength="80" autocomplete="off" /><span class="field-err"></span>
      <datalist id="owner-group-dl"></datalist>
      <div id="owner-group-note" class="muted" style="font-size:11px;margin-top:-4px">New-group convention: <code>${opt("owner_group_template") || "dbx-{domain}-{env}-{role}"}</code> (e.g. dbx-clinical-prod-rw)</div>
      <div class="row">
        <div>${L("Technical lead (email)", "Day-to-day technical owner's email.")}${inp("f-technical_lead", { type: "email", placeholder: "lead@co.com" })}</div>
        <div>${L("Backup owner (email)", "Secondary owner (bus-factor). Required for prod / tier1.")}${inp("f-backup_owner", { type: "email", placeholder: "backup@co.com" })}</div>
      </div>
      <div class="row">
        <div>${sel("f-department", opt("departments"), true, "Owning department / business unit.")}</div>
        <div>${L("Support / on-call (email)", "Escalation / on-call contact. Required for prod / tier1.")}${inp("f-support_contact", { type: "email", placeholder: "oncall@co.com" })}</div>
      </div>
    </div>
  </div></div>

  <div class="step-panel hidden" data-step="1">
    <div id="hint-compliance" class="muted" style="font-size:12px;margin-bottom:8px"></div>
    <div class="grid cols-2">
    <div class="card">
      <div class="row">
        <div>${sel("f-data_classification", opt("data_classifications"), false, "Drives controls, routing + access policy. restricted = PHI / clinical / GxP.", true)}</div>
        <div>${sel("f-environment", opt("environments"), false, "Lifecycle environment. prod adds change-control gates.", true)}</div>
      </div>
      <div class="row">
        <div>${sel("f-medallion_layer", opt("medallion_layers"), true, "Medallion layer. Part of the catalog naming convention {domain}_{layer}_{env} (required when creating a catalog).")}</div>
        <div>${sel("f-region", opt("regions"), true, "Data residency region (if multi-region).")}</div>
      </div>
      <div class="row">
        <div>${sel("f-data_retention", opt("data_retention_classes"), true, "Retention class. Required for restricted / GxP.")}</div>
      </div>
      ${L("Sunset date", "Auto-decommission reminder. Required for dev/test sandboxes.")}
      ${inp("f-sunset_date", { type: "date", min: futureDateMin() })}
    </div>
    <div class="card">
      ${L("Compliance scope", "Regulatory frameworks in scope — drives gates + ABAC.")}
      <div id="f-compliance">${checks("", opt("compliance_scopes").filter(s => s !== "none"))}</div>
      <div class="row" style="margin-top:6px">
        <label class="check"><input type="checkbox" id="f-gxp_relevant"/> GxP relevant ${help("GxP system → validation gate + controlled change.")}</label>
        <label class="check"><input type="checkbox" id="f-contains_phi"/> Contains PHI ${help("PHI → HIPAA handling + attestation.")}</label>
      </div>
      <label class="check"><input type="checkbox" id="f-validated_system"/> Validated system (CSV) ${help("Computer System Validation per GAMP 5. Required for restricted/GxP.")}</label>
      ${L("DPIA reference", "Data Protection Impact Assessment ref. Required if GDPR in scope.")}
      ${inp("f-dpia_ref", { placeholder: "DPIA-2026-001", maxlength: 60 })}
    </div>
  </div></div>

  <div class="step-panel hidden" data-step="2">
    <div id="hint-cost" class="muted" style="font-size:12px;margin-bottom:8px"></div>
    <div class="grid cols-2">
    <div class="card">
      <div class="row">
        <div>${sel("f-cost_center", opt("cost_centers"), false, "Chargeback cost center (from finance list).", true)}</div>
        <div>${sel("f-cost_type", opt("cost_types"), true, "Opex vs Capex (capitalization).")}</div>
      </div>
      <div class="row">
        <div>${L("Monthly budget cap ($)", "Spend cap; drives alerts. Over $2000 escalates approval.")}${inp("f-budget_monthly_cap", { type: "number", min: 0, placeholder: "2000" })}
          <div id="budget-rec" class="muted" style="font-size:11px;margin-top:4px"></div>
        </div>
        <div>${L("WBS / chargeback code", "Uppercase letters/digits/.- , 3–30 chars.")}${inp("f-wbs_code", { placeholder: "WBS-1234.5", maxlength: 30, pattern: "^[A-Z0-9][A-Z0-9.\\-]{2,29}$" })}</div>
      </div>
    </div>
    <div class="card">
      <div class="row">
        <div>${sel("f-lifecycle_stage", opt("lifecycle_stages"), true, "POC / pilot / production / sunset.")}</div>
        <div>${sel("f-sla_tier", opt("sla_tiers"), true, "tier1 = mission-critical (strict RTO/RPO).")}</div>
      </div>
      <div class="row">
        <div>${L("RTO (hours)", "Recovery Time Objective. Required for prod/tier1.")}${inp("f-rto_hours", { type: "number", min: 0, placeholder: "24" })}</div>
        <div>${L("RPO (hours)", "Recovery Point Objective. Required for prod/tier1.")}${inp("f-rpo_hours", { type: "number", min: 0, placeholder: "4" })}</div>
      </div>
      ${L("Target go-live date", "Planned production go-live.")}${inp("f-go_live_date", { type: "date", min: futureDateMin() })}
    </div>
  </div></div>

  <div class="step-panel hidden" data-step="3">
    <div class="card" style="border-color:var(--teal)">
      <span class="pill simulated">Integration-ready</span>
      <span class="muted" style="font-size:12px;margin-left:8px">References captured now; bidirectional ServiceNow / Jira / CMDB sync is a future enhancement.</span>
    </div>
    <div class="grid cols-2" style="margin-top:14px">
    <div class="card">
      ${L("Depends on (upstream)", "Upstream projects/systems this needs. Comma separated.")}${inp("f-depends_on", { placeholder: "proj-platform-edw, EDW", maxlength: 300 })}
      ${L("Source systems", "Systems feeding this. Comma separated.")}${inp("f-source_systems", { placeholder: "Veeva, SAP, LIMS", maxlength: 300 })}
      ${L("Consumed by (downstream)", "Downstream consumers — used for decommission impact. Comma separated.")}${inp("f-consumed_by", { placeholder: "proj-commercial-xyz", maxlength: 300 })}
    </div>
    <div class="card">
      <div class="row">
        <div>${L("Change record (ServiceNow CHG)", "Existing change ticket, e.g. CHG0012345.")}${inp("f-change_ref", { placeholder: "CHG0012345", maxlength: 40 })}</div>
        <div>${L("ServiceNow CI / RITM", "CMDB CI or request-item reference.")}${inp("f-servicenow_ref", { placeholder: "CI / RITM…", maxlength: 60 })}</div>
      </div>
      <div class="row">
        <div>${L("Jira epic", "Delivery epic key, e.g. PLAT-678.")}${inp("f-jira_epic", { placeholder: "PLAT-678", maxlength: 40 })}</div>
        <div>${sel("f-security_review_status", opt("security_review_statuses"), true, "Security review state. Required for prod/tier1.")}</div>
      </div>
      ${L("Confluence / design doc URL", "Link to design doc or runbook.")}${inp("f-confluence_url", { type: "url", placeholder: "https://wiki/…", maxlength: 300 })}
    </div>
  </div></div>

  <div class="step-panel hidden" data-step="4">
    ${placementHtml()}
    ${L("Resources to provision", "Pick the footprint, then configure each below. Restricted data forces single-user clusters; AI assets get governed by the AI Gateway.", true)}
    <div id="resource-picker" class="grid cols-3" style="margin-top:8px">${opt("resource_types").filter(rt => rt !== "workspace" && rt !== "job_cluster").map(rt =>
      `<label class="card click rpick" data-rtype="${rt}">
        <span class="check"><input type="checkbox" class="rtype" value="${rt}"/> <b>${rt}</b></span>
      </label>`).join("")}</div>
    <div id="resource-configs" style="margin-top:12px"></div>
    ${tagsPanelHtml()}
    <div id="ai-gov" class="card hidden" style="border-color:var(--lava); margin-top:14px">
      <div class="flex"><h3 style="margin:0">🛡️ AI governance (use-case registry + EU AI Act)</h3>
        <span class="right pill tier2">required for AI assets</span></div>
      <p class="muted" style="font-size:12px;margin:6px 0">Captured as the AI use-case registry entry + model card; drives the LLMOps approval gate.</p>
      <div class="row">
        <div>${L("AI risk tier", "EU AI Act tier. high -> dual approval + LLMOps gate; unacceptable -> blocked.")}${selectHtml("f-ai_risk_tier", ["", ...opt("ai_risk_tiers")])}</div>
        <div>${L("Model card / eval ref", "Link to model card or evaluation results.")}<input id="f-model_card_ref" maxlength="200" placeholder="https://… or MLflow run" /></div>
      </div>
      ${L("Intended use", "What this AI is for — the use-case registry entry.")}${area("f-intended_use", { maxlength: 500, placeholder: "e.g. clinical-protocol Q&A for field medical…" })}
      ${L("Out-of-scope uses", "Explicitly prohibited uses (prevents mission creep).")}<input id="f-out_of_scope_uses" maxlength="300" placeholder="e.g. no diagnosis, no patient-facing output" />
      <label class="check" style="margin-top:8px"><input type="checkbox" id="f-human_oversight"/> Human-in-the-loop oversight in place ${help("Attest a human reviews and can override AI outputs.")}</label>
    </div>
  </div>

  <div class="step-panel hidden" data-step="5"><div class="card">
    <h3>Acknowledgements</h3>
    ${opt("acknowledgements").map(a =>
      `<label class="check"><input type="checkbox" class="ack" value="${a.key}"/> ${a.label}</label>`).join("")}
    <p class="muted" style="font-size:12px;margin-top:10px">On submit, PAVE validates against authoritative sources, routes to the right approval tier, and (on approval) provisions + tags + records an as-code spec in the audit log.</p>
  </div></div>`;
}

// ---- field helpers (help tooltips + input constraints) ----
const FIELD_LABELS = {
  "f-department": "Department", "f-business_domain": "Line of Business",
  "f-business_function": "Business function", "f-business_sub_function": "Business sub-function",
  "f-data_classification": "Data classification", "f-environment": "Environment",
  "f-region": "Region / residency", "f-data_retention": "Data retention",
  "f-cost_center": "Cost center", "f-cost_type": "Cost type",
  "f-lifecycle_stage": "Lifecycle stage", "f-sla_tier": "SLA tier",
  "f-security_review_status": "Security review",
};
const labelFor = (id) => FIELD_LABELS[id] || id;

function help(t) {
  const safe = (t || "").replace(/"/g, "&quot;");
  // data-tip drives a reliable CSS tooltip; title is an accessibility fallback.
  return `<span class="help" data-tip="${safe}" title="${safe}" tabindex="0">i</span>`;
}
function L(label, h, required) {
  const star = required ? `<span class="req" title="Required">*</span>` : "";
  return `<label class="field">${label}${star}${h ? " " + help(h) : ""}</label>`;
}
// Earliest selectable date = tomorrow (dates must always be in the future).
function futureDateMin() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10);
}
function inp(id, o = {}) {
  const a = [`id="${id}"`];
  if (o.type) a.push(`type="${o.type}"`);
  if (o.placeholder) a.push(`placeholder="${o.placeholder}"`);
  if (o.maxlength) a.push(`maxlength="${o.maxlength}"`);
  if (o.min != null) a.push(`min="${o.min}"`);
  if (o.pattern) a.push(`data-pattern="${o.pattern}"`);
  return `<input ${a.join(" ")} /><span class="field-err"></span>`;
}
function area(id, o = {}) {
  const a = [`id="${id}"`];
  if (o.placeholder) a.push(`placeholder="${o.placeholder}"`);
  if (o.maxlength) a.push(`maxlength="${o.maxlength}"`);
  if (o.min) a.push(`data-min="${o.min}"`);
  return `<textarea ${a.join(" ")}></textarea><span class="field-err" data-counter="${id}"></span>`;
}

// ---- per-resource config (select-then-configure) --------------------------------
// A small <select> builder scoped to a resource panel (class-based, not id).
function optSelect(cls, values, opts = {}) {
  const cur = opts.selected;
  const os = values.map(v => `<option value="${v}"${v === cur ? " selected" : ""}>${opts.labels ? (opts.labels[v] || v) : v}</option>`).join("");
  return `<select class="${cls}">${os}</select>`;
}

// The list of currently-checked resource types (order follows the picker).
function selectedResourceTypes() {
  return [...document.querySelectorAll("#resource-picker .rtype:checked")].map(c => c.value);
}

// The naming-convention template for a resource type (from /api/meta/form-options).
function namingHint(rt) {
  const t = opt("naming_templates") || {};
  return t[rt] || "{domain}-{env}-{short}";
}

// Radio `name` is document-global, so two panels rendered at once (intake + the
// add-to-existing-project flow) would share one radio group and silently uncheck each
// other. Every panel render gets its own scope token.
let PANEL_SEQ = 0;

// (Re)render one config panel per selected resource into #resource-configs.
function renderResourceConfigs() {
  const host = document.getElementById("resource-configs");
  if (!host) return;
  const types = selectedResourceTypes();
  host.innerHTML = types.length
    ? types.map(rt => `<div class="card rcfg" data-rtype="${rt}">
        <div class="flex"><b>${rt}</b><span class="right muted" style="font-size:11px">configuration</span></div>
        ${L("Name (optional)", "Override the auto-generated name. Must follow the naming convention below.")}<input class="rname" maxlength="60" placeholder="auto: ${namingHint(rt)}" />
        <div class="muted" style="font-size:11px;margin-top:-4px">Convention: <code>${namingHint(rt)}</code></div>
        ${resourceConfigHtml(rt, `p${++PANEL_SEQ}`)}
      </div>`).join("")
    : `<p class="muted" style="font-size:12px">Select one or more resources above to configure them.</p>`;
  refreshAI();
}

// Show/hide conditional sub-fields within a resource panel (delegated).
function onResourceConfigToggle(e) {
  const t = e.target;
  if (!t.classList) return;
  const panel = t.closest(".rcfg");
  if (!panel) return;
  if (t.classList.contains("cat-kind")) {
    panel.querySelector(".cat-ext")?.classList.toggle("hidden",
      panel.querySelector(".cat-kind:checked")?.value !== "external");
  }
  if (t.classList.contains("cl-mode")) {
    const classic = panel.querySelector(".cl-mode:checked")?.value === "classic";
    panel.querySelector(".cl-classic")?.classList.toggle("hidden", !classic);
    panel.querySelector(".cl-serverless-note")?.classList.toggle("hidden", classic);
  }
  if (t.classList.contains("cl-sizemode")) {
    const fixed = panel.querySelector(".cl-sizemode:checked")?.value === "fixed";
    panel.querySelector(".cl-fixed")?.classList.toggle("hidden", !fixed);
    panel.querySelector(".cl-autoscale")?.classList.toggle("hidden", fixed);
  }
  if (t.classList.contains("lb-offer")) {
    const auto = panel.querySelector(".lb-offer:checked")?.value === "autoscaling";
    panel.querySelectorAll(".lb-auto").forEach(el => el.classList.toggle("hidden", !auto));
    panel.querySelectorAll(".lb-prov").forEach(el => el.classList.toggle("hidden", auto));
  }
  if (t.classList.contains("vs-embsrc")) {
    panel.querySelector(".vs-managed")?.classList.toggle("hidden",
      panel.querySelector(".vs-embsrc")?.value !== "managed");
  }
}

// Custom-tags repeater. Keys come from the governed allow-list; server drops others.
function addCustomTagRow() {
  const host = document.getElementById("custom-tags");
  if (!host) return;
  const keys = opt("allowed_custom_tag_keys") || [];
  const row = el("div", { class: "row tag-row" });
  row.innerHTML = `
    <select class="tag-key">${keys.map(k => `<option value="${k}">${k}</option>`).join("")}</select>
    <input class="tag-val" maxlength="120" placeholder="value" />
    <button type="button" class="btn ghost small tag-del">×</button>`;
  row.querySelector(".tag-del").onclick = () => row.remove();
  host.appendChild(row);
}

function collectCustomTags() {
  const out = {};
  document.querySelectorAll("#custom-tags .tag-row").forEach(r => {
    const k = (r.querySelector(".tag-key")?.value || "").trim();
    const v = (r.querySelector(".tag-val")?.value || "").trim();
    if (k && v) out[k] = v;
  });
  return out;
}

// The governed option set per resource type (2025-2026 Databricks options).
function resourceConfigHtml(rt, scope = `p${++PANEL_SEQ}`) {
  const locs = opt("pre_approved_locations") || [];
  const locOptions = ["", ...locs];

  if (rt === "catalog") {
    return `
      ${L("Catalog kind", "Managed = Databricks-governed storage (default). External = a pre-approved external location.")}
      <div class="row" style="gap:16px">
        <label class="check"><input type="radio" name="cat-kind-${scope}" class="cat-kind" value="managed" checked/> managed</label>
        <label class="check"><input type="radio" name="cat-kind-${scope}" class="cat-kind" value="external"/> external</label>
      </div>
      <div class="cat-ext hidden">
        ${L("External location", "Pick a pre-approved external location (admin-registered). Never a raw s3:// path.")}
        ${optSelect("cat-location", locOptions, { labels: { "": locs.length ? "(select)" : "(none configured)" } })}
      </div>
      ${L("Isolation mode", "auto -> ISOLATED for restricted data. ISOLATED binds the catalog to specific workspaces.")}
      ${optSelect("cat-isolation", opt("isolation_modes"))}
      ${L("Comment (optional)", "Describes the catalog in Unity Catalog.")}<input class="cat-comment" maxlength="200" placeholder="e.g. Oncology RWE governed catalog" />`;
  }
  if (rt === "schema") {
    return `
      ${L("Managed location", "Inherit the catalog's managed storage (recommended) or a pre-approved external location.")}
      ${optSelect("sc-location", locOptions, { labels: { "": "inherit catalog storage" } })}
      ${L("Comment (optional)", "Describes the schema in Unity Catalog.")}<input class="sc-comment" maxlength="200" placeholder="e.g. curated silver tables" />`;
  }
  if (rt === "cluster") {
    return computeConfigHtml(true, scope);
  }
  if (rt === "job_cluster") {
    return computeConfigHtml(false, scope);
  }
  if (rt === "app") {
    const binds = (opt("app_bindable_resources") || []).map(r =>
      `<label class="check" style="font-size:12px"><input type="checkbox" class="app-bind" value="${r}"/> ${r}</label>`).join(" ");
    return `
      ${L("Compute size", "MEDIUM (default, ~2 vCPU/6GB) -> LARGE -> XLARGE. Drives cost per hour.")}
      ${optSelect("app-size", opt("app_compute_sizes"))}
      ${L("Resource bindings (optional)", "Grant the app least-privilege access to these. Owner/manage perms are not offered here.")}
      <div>${binds}</div>`;
  }
  if (rt === "lakebase") {
    return `
      ${L("Offering", "Provisioned = FinOps tags + Apps binding (governed default). Autoscaling = scale-to-zero + branching (newer; no billing tags yet).")}
      <div class="row" style="gap:16px">
        <label class="check"><input type="radio" name="lb-offer-${scope}" class="lb-offer" value="provisioned" checked/> provisioned</label>
        <label class="check"><input type="radio" name="lb-offer-${scope}" class="lb-offer" value="autoscaling"/> autoscaling</label>
      </div>
      <div class="row">
        <div>${L("PG version", "Postgres major version.")}${optSelect("lb-pg", opt("pg_versions"), { selected: "16" })}</div>
        <div class="lb-prov">${L("Capacity", "Compute units (16 GB/CU). Capped by risk tier server-side.")}${optSelect("lb-capacity", opt("lakebase_capacities"), { selected: "CU_2" })}</div>
      </div>
      <div class="lb-prov">${L("Retention (days)", "Point-in-time restore window, 2-35. Higher = more cost + more coverage.")}<input type="number" min="2" max="35" class="lb-retention" value="7" /></div>
      <div class="lb-auto hidden">
        <div class="row">
          <div>${L("Min CU", "Autoscale floor (0.5-32).")}<input type="number" min="0" step="0.5" class="lb-mincu" value="0.5" /></div>
          <div>${L("Max CU", "Autoscale ceiling. max - min must be <= 8 CU.")}<input type="number" min="0" step="0.5" class="lb-maxcu" value="4" /></div>
        </div>
        <label class="check"><input type="checkbox" class="lb-stz" checked/> Scale to zero when idle ${help("Cheapest; adds cold-start latency. Disabled on production branches.")}</label>
      </div>`;
  }
  if (rt === "llm_gateway_endpoint") {
    const providers = opt("ai_providers");
    const checks = (opt("ai_guardrails")).map(g =>
      `<label class="check" style="font-size:12px"><input type="checkbox" class="ai-guardrail" value="${g}" ${["pii_redact", "safety"].includes(g) ? "checked" : ""}/> ${g}</label>`).join("");
    return `
      <div class="row">
        <div>${L("Provider", "Databricks-hosted models need no key; external providers need a configured secret (admin-managed).")}${optSelect("ai-provider", providers)}</div>
        <div>${L("Throughput", "pay-per-token (default) or provisioned throughput (reserved capacity, higher fixed cost).")}${optSelect("ai-throughput", opt("llm_throughput_modes"))}</div>
      </div>
      ${L("Model (allow-listed)", "Only platform-approved models can be vended.")}<select class="ai-model"></select>
      ${L("Task", "")}${optSelect("ai-task", opt("ai_tasks"))}
      ${L("Guardrails", "PII redaction/blocking, safety, jailbreak. Required for external/PHI/high-risk.")}
      <div>${checks}</div>
      <div class="row">
        <div>${L("Rate QPM", "Queries per minute per user.")}<input type="number" min="0" class="ai-qpm" value="100" /></div>
        <div>${L("Rate TPM", "Tokens per minute per user.")}<input type="number" min="0" class="ai-tpm" value="50000" /></div>
      </div>
      <div class="row">
        <div>${L("Monthly token budget", "Per-team token budget for chargeback/forecast.")}<input type="number" min="0" class="ai-tokbudget" value="5000000" /></div>
        <div>${L("Monthly $ cap", "Hard spend cap for this endpoint.")}<input type="number" min="0" class="ai-costcap" value="2000" /></div>
      </div>
      <label class="check"><input type="checkbox" class="ai-logging" checked/> Inference logging (audit) ${help("Log prompts/responses to a UC inference table for audit.")}</label>
      <label class="check"><input type="checkbox" class="ai-fallback"/> Enable provider fallbacks ${help("Route to a backup provider on failure (external models).")}</label>`;
  }
  if (rt === "vector_search") {
    return `
      <div class="row">
        <div>${L("Endpoint type", "STANDARD = low latency, higher cost. STORAGE_OPTIMIZED = 1B+ vectors, ~7x cheaper.")}${optSelect("vs-type", ["STANDARD", "STORAGE_OPTIMIZED"])}</div>
        <div>${L("Index type", "DELTA_SYNC auto-syncs from a UC table (recommended). DIRECT_ACCESS = manual upsert.")}${optSelect("vs-index", opt("vs_index_types"))}</div>
      </div>
      ${L("Source table (optional)", "UC Delta table to index for RAG. Inherits the table's data scope — gated on UC grants.")}<input class="vs-source" maxlength="120" placeholder="catalog.schema.table" />
      <div class="row">
        <div>${L("Embedding source", "managed = Databricks embedding model. self-managed = you supply vectors.")}${optSelect("vs-embsrc", opt("vs_embedding_sources"))}</div>
        <div>${L("Pipeline", "TRIGGERED = batch (cheaper). CONTINUOUS = near-real-time (higher cost).")}${optSelect("vs-pipeline", opt("vs_pipeline_types"))}</div>
      </div>
      <div class="vs-managed">${L("Embedding model", "Databricks-hosted embedding endpoint (managed embeddings).")}${optSelect("vs-embmodel", opt("embedding_models"))}</div>`;
  }
  if (rt === "sql_warehouse") {
    return `
      <div class="row">
        <div>${L("Type", "serverless (governed default — instant start, no infra) · pro · classic.")}${optSelect("wh-type", opt("warehouse_types"))}</div>
        <div>${L("Size", "Cluster size. Larger = more concurrency/throughput + cost. Capped by governance.")}${optSelect("wh-size", opt("warehouse_sizes"), { selected: "Small" })}</div>
      </div>
      <div class="row">
        <div>${L("Auto-stop (mins)", "Idle minutes before the warehouse stops (cost control). Lower = cheaper.")}<input type="number" min="1" max="120" class="wh-autostop" value="10" /></div>
        <div>${L("Max clusters", "Upper bound for multi-cluster load balancing (scale-out). 1 = no scale-out.")}<input type="number" min="1" max="10" class="wh-maxclusters" value="1" /></div>
      </div>`;
  }
  return "";
}

// Shared cluster / job_cluster config. `interactive` -> all-purpose (has autotermination).
function computeConfigHtml(interactive, scope = `p${++PANEL_SEQ}`) {
  const dbr = optSelect("cl-dbr", opt("dbr_versions"), { selected: "15.4.x-scala2.12" });
  const nodes = optSelect("cl-node", opt("node_types"));
  const engine = optSelect("cl-engine", opt("runtime_engines"));
  const access = interactive
    ? `${L("Access mode", "Dedicated = single-user (forced for restricted data). Standard = shared isolation. Auto = Databricks picks.")}${optSelect("cl-access", opt("cluster_access_modes"))}`
    : "";
  const autoterm = interactive
    ? `${L("Auto-termination (min)", "Idle shutdown, 10-60. Enforced by the cluster policy.")}<input type="number" min="10" max="60" class="cl-autoterm" value="30" />`
    : "";
  const spot = interactive ? "" :
    `${L("Spot policy", "SPOT_WITH_FALLBACK (cheapest, recommended) | ON_DEMAND | SPOT.")}${optSelect("cl-spot", opt("spot_policies"))}`;
  const sizing = interactive
    ? `${L("Sizing", "Fixed workers or autoscale range.")}
       <div class="row" style="gap:16px;margin-bottom:4px">
         <label class="check"><input type="radio" name="cl-sizemode-${scope}" class="cl-sizemode" value="autoscale" checked/> autoscale</label>
         <label class="check"><input type="radio" name="cl-sizemode-${scope}" class="cl-sizemode" value="fixed"/> fixed</label>
       </div>
       <div class="cl-autoscale"><div class="row">
         <div>${L("Min workers", "")}<input type="number" min="0" class="cl-min" value="1" /></div>
         <div>${L("Max workers", "Autoscale ceiling; capped by policy.")}<input type="number" min="1" class="cl-max" value="4" /></div>
       </div></div>
       <div class="cl-fixed hidden">${L("Workers", "0 = single node.")}<input type="number" min="0" class="cl-workers" value="2" /></div>`
    : `${L("Workers", "Fixed worker count for the job cluster (0 = single node).")}<input type="number" min="0" class="cl-workers" value="2" />`;
  // Compute-mode chooser. Serverless (default) needs NO classic sizing knobs — node type,
  // DBR, spot, autoscaling all vanish; governance (tags, policy, access) is what still
  // matters. Classic reveals the full knob set. This makes PAVE's positioning visible:
  // as compute goes serverless, the vending surface is governance, not machine config.
  const modeChooser = `
    ${L("Compute mode", "Serverless = no infra to size (Databricks manages it); PAVE still governs tags, access + lifecycle. Classic = you pick node type / runtime / sizing.")}
    <div class="row" style="gap:18px;margin-bottom:8px">
      <label class="check"><input type="radio" name="cl-mode-${scope}" class="cl-mode" value="serverless" checked/> Serverless</label>
      <label class="check"><input type="radio" name="cl-mode-${scope}" class="cl-mode" value="classic"/> Classic</label>
    </div>
    <div class="cl-serverless-note muted" style="font-size:12px;margin-bottom:6px">
      Serverless: no node type, runtime, or sizing to choose — Databricks manages the compute.
      PAVE still applies governed tags, the access policy, and lifecycle. Switch to Classic to size infra yourself.
    </div>`;
  // The classic-only knobs are wrapped so serverless can hide them wholesale.
  const classicKnobs = `
    ${access}
    <div class="row">
      <div>${L("Node type", "Fleet types auto-resolve the best instance. Allow-listed for cost.")}${nodes}</div>
      <div>${L("Databricks Runtime", "LTS versions only.")}${dbr}</div>
    </div>
    ${sizing}
    <div class="row">
      <div>${L("Engine", "Photon accelerates SQL/DataFrame workloads.")}${engine}</div>
      ${interactive ? `<div>${autoterm}</div>` : `<div>${spot}</div>`}
    </div>`;
  return `
    ${modeChooser}
    <div class="cl-classic hidden">${classicKnobs}</div>`;
}

// Tags panel: auto-derived defaults (shown read-only) + user-added allow-listed tags.
function tagsPanelHtml() {
  const req = opt("required_tag_keys") || [];
  return `<div class="card" id="tags-panel" style="margin-top:14px">
    <div class="flex"><b>Tags</b><span class="right muted" style="font-size:11px">applied to every resource on both planes</span></div>
    <p class="muted" style="font-size:12px;margin:6px 0">These governed keys are applied automatically from your request: ${
      req.map(k => `<span class="kv">${k}</span>`).join(" ")}</p>
    <label class="field">Add your own tags ${help("Optional, from the governed vocabulary. Keys outside the allow-list are dropped server-side.")}</label>
    <div id="custom-tags"></div>
    <button type="button" class="btn ghost small" id="add-tag">+ Add tag</button>
  </div>`;
}

// Placement chooser: WHERE the footprint lands. Default = an existing workspace (the
// common case); "new workspace" is a deliberate, separate account-level path (SoD).
function placementHtml() {
  const regions = (opt("regions") || []).filter(Boolean);
  const wsOptions = WORKSPACES.map(w =>
    `<option value="${w.host}">${w.label}${w.self ? "" : " · " + w.host}</option>`).join("");
  return `<div class="card" id="placement" style="margin-bottom:14px">
    <label class="field">Where should this land? ${help("Provision into an existing workspace (common), or request a brand-new workspace (account-level, separate approval).")}</label>
    <div class="row" style="gap:18px;margin:6px 0">
      <label class="check"><input type="radio" name="placement" value="existing" checked/> Existing workspace</label>
      <label class="check"><input type="radio" name="placement" value="new"/> New workspace (account-level)</label>
    </div>
    <div id="placement-existing">
      <label class="field">Target workspace ${help("Which workspace to provision INTO. 'This workspace' = where PAVE runs (default).")}</label>
      <select id="f-target_workspace">${wsOptions}</select>
    </div>
    <div id="placement-new" class="hidden">
      <label class="field">New workspace name (optional)</label>
      <input id="ws-name" maxlength="60" placeholder="auto" />
      <div class="row">
        <div><label class="field">Region ${help("Cloud region for the new workspace.")}</label>
          <select class="ws-region">${regions.map(r => `<option value="${r}">${r}</option>`).join("")}</select></div>
        <div><label class="field">Pricing tier</label>
          <select class="ws-tier"><option value="ENTERPRISE">ENTERPRISE</option><option value="PREMIUM">PREMIUM</option></select></div>
      </div>
      <label class="field">Credentials config id ${help("Pre-provisioned account cross-account IAM role config. Blank -> emitted as a Terraform variable.")}</label>
      <input class="ws-cred" maxlength="80" placeholder="account credentials_id (optional)" />
      <label class="field">Storage config id ${help("Pre-provisioned account root S3 bucket config.")}</label>
      <input class="ws-stor" maxlength="80" placeholder="account storage_configuration_id (optional)" />
      <label class="field">Network config id (optional) ${help("Customer-managed VPC config for the workspace.")}</label>
      <input class="ws-net" maxlength="80" placeholder="account network_id (optional)" />
      <p class="muted" style="font-size:11px;margin-top:6px">Account-level: created under an account-admin identity (SoD), and forces a Tier-2 approval. PAVE also emits applyable Terraform in the request spec. Requires account access to run for real; otherwise modeled.</p>
    </div>
  </div>`;
}

function refreshAI() {
  // populate model selects from provider; show/hide the AI governance panel
  document.querySelectorAll("#resource-configs .rcfg[data-rtype='llm_gateway_endpoint']").forEach(c => {
    const prov = c.querySelector(".ai-provider"), modelSel = c.querySelector(".ai-model");
    if (prov && modelSel) {
      const models = (OPTS.allowed_ai_models || {})[prov.value] || [];
      if (modelSel.dataset.prov !== prov.value) {
        modelSel.innerHTML = models.map(m => `<option value="${m}">${m}</option>`).join("");
        modelSel.dataset.prov = prov.value;
      }
    }
  });
  const anyAI = selectedResourceTypes().some(t => ["llm_gateway_endpoint", "vector_search"].includes(t));
  const panel = document.getElementById("ai-gov");
  if (panel) panel.classList.toggle("hidden", !anyAI);
}

// Guarded advance: block leaving the current step if its required fields are incomplete.
function nextStep() {
  const errBox = document.getElementById("intake-errors");
  const missingHere = requiredNow().filter(r => !r.ok && r.step === _step);
  if (missingHere.length) {
    if (errBox) errBox.innerHTML = reqErrorsHtml(missingHere,
      `Complete this step before continuing (${missingHere.length} required)`);
    markMissing(missingHere);
    toast(`${missingHere.length} required field${missingHere.length !== 1 ? 's' : ''} on this step`, false);
    return;
  }
  if (errBox) errBox.innerHTML = "";
  showStep(_step + 1);
}

function showStep(i) {
  _step = Math.max(0, Math.min(STEPS.length - 1, i));
  document.querySelectorAll("#intake-form .step-panel").forEach(p =>
    p.classList.toggle("hidden", Number(p.dataset.step) !== _step));
  document.querySelectorAll("#intake-form .step-chip").forEach(c =>
    c.classList.toggle("active", Number(c.dataset.i) === _step));
  const last = _step === STEPS.length - 1;
  document.getElementById("step-back").style.visibility = _step === 0 ? "hidden" : "visible";
  document.getElementById("step-next").style.display = last ? "none" : "";
  document.getElementById("step-submit").style.display = last ? "" : "none";
  document.getElementById("step-preview").style.display = last ? "" : "none";
  if (STEPS[_step] && STEPS[_step].key === "cost") refreshBudgetRec();
  refreshHints();
  updateCompletion();
}

function refreshHints() {
  const cls = (document.getElementById("f-data_classification") || {}).value;
  const env = (document.getElementById("f-environment") || {}).value;
  const sla = (document.getElementById("f-sla_tier") || {}).value;
  const gxp = (document.getElementById("f-gxp_relevant") || {}).checked;
  const gdpr = [...document.querySelectorAll("#f-compliance input:checked")].some(c => c.value === "gdpr");
  const hc = document.getElementById("hint-compliance");
  if (hc) {
    const need = [];
    if (cls === "restricted" || gxp) need.push("validated-system + data-retention");
    if (gdpr) need.push("DPIA reference");
    hc.innerHTML = need.length
      ? `🔒 Regulated request → also required: <b>${need.join(", ")}</b>. Routes to dual approval + compliance.`
      : "Classification drives controls, routing, and access policy.";
  }
  const hcost = document.getElementById("hint-cost");
  if (hcost) {
    hcost.innerHTML = (env === "prod" || sla === "tier1")
      ? "🛡️ Production / Tier-1 → also required: <b>backup owner, RTO/RPO, security review, on-call contact</b>."
      : "Lifecycle + SLA drive reliability expectations and decommission reminders.";
  }
}

// Cascading org taxonomy: Line of Business -> Function -> Sub-Function.
// `which` = "lob" (LOB changed -> repopulate function + sub-function) or
// "function" (function changed -> repopulate sub-function only). Preserve the
// current selection when it is still valid (so co-pilot/template pre-fill sticks).
function refreshTaxonomy(which) {
  const tax = (OPTS && OPTS.business_taxonomy) || {};
  const lob = (document.getElementById("f-business_domain") || {}).value || "";
  const fnSel = document.getElementById("f-business_function");
  const subSel = document.getElementById("f-business_sub_function");
  if (!fnSel || !subSel) return;
  const functions = tax[lob] || {};

  if (which === "lob") {
    const keep = functions[fnSel.value] ? fnSel.value : "";
    fnSel.innerHTML = ["", ...Object.keys(functions)]
      .map(o => `<option value="${o}">${o || "(none)"}</option>`).join("");
    fnSel.value = keep;
  }

  const subs = functions[fnSel.value] || [];
  const keepSub = subs.includes(subSel.value) ? subSel.value : "";
  subSel.innerHTML = ["", ...subs]
    .map(o => `<option value="${o}">${o || "(none)"}</option>`).join("");
  subSel.value = keepSub;
  // Hide the sub-function control when the chosen function has no sub-functions.
  const wrap = subSel.closest("div");
  if (wrap) wrap.classList.toggle("hidden", subs.length === 0);
}

// Populate the owning-group picker with REAL Databricks groups (Phase A: the app's own
// workspace). Free-text is still allowed (type a new name -> PAVE creates it), so this is
// an enhancement, not a hard gate.
async function loadOwnerGroups() {
  const dl = document.getElementById("owner-group-dl");
  const note = document.getElementById("owner-group-note");
  if (!dl) return;
  const conv = opt("owner_group_template") || "dbx-{domain}-{env}-{role}";
  try {
    const r = await api("/api/meta/groups");
    dl.innerHTML = (r.groups || []).map(g => `<option value="${g}"></option>`).join("");
    if (note) note.innerHTML = (r.resolvable && r.groups.length)
      ? `${r.groups.length} Databricks group(s) — pick one, or type a new name to create it (convention: <code>${conv}</code>).`
      : `Couldn't load workspace groups — type the group name. New-group convention: <code>${conv}</code>.`;
  } catch (e) { /* keep free-text fallback + convention note as authored */ }
}

// Live cost estimate + recommended budget cap on the Cost & Lifecycle step, so the
// requester sets the cap against a number instead of guessing (addresses the "how do
// customers know a realistic budget?" ask). Coarse rate-card stand-in until real billing.
async function refreshBudgetRec() {
  const el = document.getElementById("budget-rec");
  if (!el) return;
  const res = collectResources();
  if (!res.length) { el.innerHTML = "Add resources (step 5) to get an estimate & a recommended cap."; return; }
  try {
    const r = await api("/api/finops/estimate", { method: "POST", body: JSON.stringify({ resources: res }) });
    const rec = r.recommended_budget || 0;
    el.innerHTML = `Est. <b>$${r.estimated_monthly}/mo</b> for the selected footprint · recommended cap <b>$${rec}</b> `
      + `<a href="#" id="budget-use">Use $${rec}</a>`
      + (r.escalates_on_cost ? ` · <span class="pill tier2">over $${r.budget_threshold} → controlled approval</span>` : "");
    const use = document.getElementById("budget-use");
    if (use) use.onclick = (e) => {
      e.preventDefault();
      const b = document.getElementById("f-budget_monthly_cap");
      if (b) { b.value = rec; if (typeof updateCompletion === "function") updateCompletion(); }
    };
  } catch (e) { el.textContent = "estimate unavailable (rate-card)"; }
}

async function previewCost() {
  const out = document.getElementById("cost-out");
  try {
    const r = await api("/api/finops/estimate", { method: "POST",
      body: JSON.stringify({ resources: collectResources() }) });
    const rec = r.recommended_budget ? ` · recommended cap <b>$${r.recommended_budget}</b>` : "";
    out.innerHTML = `Est. <b>$${r.estimated_monthly}/mo</b>` + rec +
      (r.escalates_on_cost ? ` <span class="pill tier2">over $${r.budget_threshold} → controlled approval</span>` : "");
  } catch (e) { out.textContent = "estimate failed"; }
}

function selectHtml(id, options) {
  return `<select id="${id}">${options.map(o => `<option value="${o}">${o || "(none)"}</option>`).join("")}</select>`;
}

function applyTemplate(t, card) {
  document.querySelectorAll("#view-intake .grid.cols-3 > .card.click").forEach(c => c.classList.remove("selected"));
  card.classList.add("selected");
  const d = t.defaults || {};
  const set = (id, val) => { const e = document.getElementById(id); if (e && val != null) e.value = val; };
  set("f-data_classification", d.data_classification);
  set("f-environment", d.environment);
  const g = document.getElementById("f-gxp_relevant"); if (g) g.checked = !!d.gxp_relevant;
  if (d.compliance_scope)
    document.querySelectorAll("#f-compliance input").forEach(cb => { cb.checked = d.compliance_scope.includes(cb.value); });
  selectResources(new Set(t.resources.map(r => r.type)));
  refreshHints();
  updateCompletion();
  showStep(0);   // jump to step 1 so the pre-filled form + progress are visible
  toast(`Template applied: ${t.name} — step through to complete`);
}

async function draftWithAI() {
  const text = document.getElementById("cop-text").value.trim();
  if (!text) { toast("Describe your project first", false); return; }
  const btn = document.getElementById("cop-go"); btn.textContent = "Drafting…"; btn.disabled = true;
  try {
    const d = await api("/api/assist/intake", { method: "POST", body: JSON.stringify({ text }) });
    applyDraft(d);
    document.getElementById("cop-src").textContent = "source: " + (d._source || "heuristic");
    document.getElementById("cop-rationale").innerHTML =
      (d._rationale || []).map(x => `<span class="kv">${esc(x).replace(/\*\*/g, "")}</span>`).join("");
    toast("Draft ready — step through and submit");
  } catch (e) { toast("Co-pilot failed", false); }
  finally { btn.textContent = "Draft with AI"; btn.disabled = false; }
}

function applyDraft(d) {
  const set = (id, val) => { const e = document.getElementById(id); if (e != null && val != null) e.value = val; };
  set("f-project_name", d.project_name);
  set("f-use_case_name", d.use_case_name || d.project_name);
  set("f-description", d.description);
  set("f-justification", d.justification);
  set("f-business_domain", d.business_domain);
  // repopulate the function list for the drafted LOB, then set function + sub-function
  refreshTaxonomy("lob");
  set("f-business_function", d.business_function);
  refreshTaxonomy("function");
  set("f-business_sub_function", d.business_sub_function);
  set("f-data_classification", d.data_classification);
  set("f-environment", d.environment);
  const g = document.getElementById("f-gxp_relevant"); if (g) g.checked = !!d.gxp_relevant;
  const p = document.getElementById("f-contains_phi"); if (p) p.checked = !!d.contains_phi;
  document.querySelectorAll("#f-compliance input").forEach(cb => {
    cb.checked = (d.compliance_scope || []).includes(cb.value);
  });
  selectResources(new Set((d.resources || []).map(r => r.type)));
  refreshHints();
}

function selectResources(want) {
  document.querySelectorAll("#resource-picker .rpick").forEach(c => {
    const cb = c.querySelector(".rtype");
    cb.checked = want.has(c.dataset.rtype);
    c.classList.toggle("selected", cb.checked);
  });
  renderResourceConfigs();
  refreshAI();
}

function collectResources() {
  const out = [];
  document.querySelectorAll("#resource-configs .rcfg").forEach(c => {
    const rt = c.dataset.rtype;
    const q = (sel) => c.querySelector(sel);
    const v = (sel) => (q(sel)?.value || "").trim();
    const numv = (sel) => Number(q(sel)?.value || 0);
    const name = v(".rname");
    const cfg = name ? { name } : {};

    if (rt === "catalog") {
      const kind = c.querySelector(".cat-kind:checked")?.value || "managed";
      cfg.kind = kind;
      if (kind === "external") cfg.storage_root = v(".cat-location") || undefined;
      cfg.isolation_mode = v(".cat-isolation");
      const cm = v(".cat-comment"); if (cm) cfg.comment = cm;
    }
    if (rt === "schema") {
      const loc = v(".sc-location"); if (loc) cfg.storage_root = loc;
      const cm = v(".sc-comment"); if (cm) cfg.comment = cm;
    }
    if (rt === "cluster" || rt === "job_cluster") {
      const computeMode = c.querySelector(".cl-mode:checked")?.value || "serverless";
      cfg.compute_mode = computeMode;
      // Serverless: Databricks manages the infra — no node/runtime/sizing knobs to collect.
      // Governance (tags, access, lifecycle) still applies. Classic collects the full set.
      if (computeMode === "classic") {
        cfg.node_type_id = v(".cl-node");
        cfg.spark_version = v(".cl-dbr");
        cfg.runtime_engine = v(".cl-engine");
        if (rt === "cluster") {
          cfg.access_mode = v(".cl-access");
          cfg.autotermination_minutes = numv(".cl-autoterm");
          const mode = c.querySelector(".cl-sizemode:checked")?.value || "autoscale";
          if (mode === "fixed") { cfg.num_workers = numv(".cl-workers"); }
          else { cfg.min_workers = numv(".cl-min"); cfg.max_workers = numv(".cl-max"); }
        } else {
          cfg.num_workers = numv(".cl-workers");
          cfg.availability = v(".cl-spot");
        }
      }
    }
    if (rt === "app") {
      cfg.compute_size = v(".app-size");
      cfg.resource_bindings = [...c.querySelectorAll(".app-bind:checked")].map(x => x.value);
    }
    if (rt === "lakebase") {
      const offer = c.querySelector(".lb-offer:checked")?.value || "provisioned";
      cfg.offering = offer;
      cfg.pg_version = v(".lb-pg");
      if (offer === "provisioned") {
        cfg.capacity = v(".lb-capacity");
        cfg.retention_days = numv(".lb-retention");
      } else {
        cfg.min_cu = numv(".lb-mincu");
        cfg.max_cu = numv(".lb-maxcu");
        cfg.scale_to_zero = !!q(".lb-stz")?.checked;
      }
    }
    if (rt === "llm_gateway_endpoint") {
      cfg.provider = v(".ai-provider");
      cfg.throughput_mode = v(".ai-throughput");
      cfg.model = v(".ai-model");
      cfg.task = v(".ai-task");
      cfg.guardrails = [...c.querySelectorAll(".ai-guardrail:checked")].map(x => x.value);
      cfg.rate_limit_qpm = numv(".ai-qpm");
      cfg.rate_limit_tpm = numv(".ai-tpm");
      cfg.monthly_token_budget = numv(".ai-tokbudget");
      cfg.monthly_cost_cap_usd = numv(".ai-costcap");
      cfg.inference_logging = !!q(".ai-logging")?.checked;
      cfg.fallbacks = !!q(".ai-fallback")?.checked;
    }
    if (rt === "sql_warehouse") {
      cfg.warehouse_type = v(".wh-type");
      cfg.cluster_size = v(".wh-size");
      cfg.auto_stop_mins = numv(".wh-autostop");
      cfg.max_num_clusters = numv(".wh-maxclusters");
    }
    if (rt === "vector_search") {
      cfg.endpoint_type = v(".vs-type");
      cfg.index_type = v(".vs-index");
      cfg.source_table = v(".vs-source") || undefined;
      cfg.embedding_source = v(".vs-embsrc");
      cfg.pipeline_type = v(".vs-pipeline");
      if (v(".vs-embsrc") === "managed") cfg.embedding_model = v(".vs-embmodel");
    }
    out.push({ type: rt, config: cfg });
  });
  // Placement = "new workspace" -> add a workspace resource from the placement panel.
  if (placementMode() === "new") {
    const q = (sel) => document.querySelector("#placement-new " + sel);
    const cfg = { region: q(".ws-region")?.value, pricing_tier: q(".ws-tier")?.value };
    const name = document.getElementById("ws-name")?.value.trim(); if (name) cfg.name = name;
    const cred = q(".ws-cred")?.value.trim(); if (cred) cfg.credentials_id = cred;
    const stor = q(".ws-stor")?.value.trim(); if (stor) cfg.storage_config_id = stor;
    const net = q(".ws-net")?.value.trim(); if (net) cfg.network_id = net;
    out.push({ type: "workspace", config: cfg });
  }
  return out;
}

function placementMode() {
  const r = document.querySelector("input[name='placement']:checked");
  return r ? r.value : "existing";
}

function collectPayload() {
  const val = (id) => { const e = document.getElementById(id); return e ? e.value : ""; };
  const num = (id) => { const x = val(id); return x === "" ? null : Number(x); };
  const chk = (id) => { const e = document.getElementById(id); return e ? e.checked : false; };
  const list = (id) => val(id).split(",").map(s => s.trim()).filter(Boolean);
  return {
    project_name: val("f-project_name"),
    use_case_name: val("f-use_case_name"),
    description: val("f-description"),
    justification: val("f-justification"),
    business_function: val("f-business_function") || null,
    business_sub_function: val("f-business_sub_function") || null,
    business_owner: val("f-business_owner") || null,
    owner_group: val("f-owner_group"),
    technical_lead: val("f-technical_lead") || null,
    backup_owner: val("f-backup_owner") || null,
    support_contact: val("f-support_contact") || null,
    department: val("f-department") || null,
    cost_center: val("f-cost_center"),
    cost_type: val("f-cost_type") || null,
    budget_monthly_cap: num("f-budget_monthly_cap"),
    wbs_code: val("f-wbs_code") || null,
    business_domain: val("f-business_domain"),
    data_classification: val("f-data_classification"),
    environment: val("f-environment"),
    medallion_layer: val("f-medallion_layer") || null,
    region: val("f-region") || null,
    target_workspace: placementMode() === "existing" ? (val("f-target_workspace") || null) : null,
    data_retention: val("f-data_retention") || null,
    lifecycle_stage: val("f-lifecycle_stage") || null,
    sla_tier: val("f-sla_tier") || null,
    rto_hours: num("f-rto_hours"),
    rpo_hours: num("f-rpo_hours"),
    go_live_date: val("f-go_live_date") || null,
    compliance_scope: [...document.querySelectorAll("#f-compliance input:checked")].map(c => c.value),
    gxp_relevant: chk("f-gxp_relevant"),
    contains_phi: chk("f-contains_phi"),
    validated_system: chk("f-validated_system"),
    dpia_ref: val("f-dpia_ref") || null,
    sunset_date: val("f-sunset_date") || null,
    depends_on: list("f-depends_on"),
    source_systems: list("f-source_systems"),
    consumed_by: list("f-consumed_by"),
    ai_risk_tier: val("f-ai_risk_tier") || null,
    intended_use: val("f-intended_use") || null,
    out_of_scope_uses: val("f-out_of_scope_uses") || null,
    model_card_ref: val("f-model_card_ref") || null,
    human_oversight: chk("f-human_oversight"),
    change_ref: val("f-change_ref") || null,
    servicenow_ref: val("f-servicenow_ref") || null,
    jira_epic: val("f-jira_epic") || null,
    confluence_url: val("f-confluence_url") || null,
    security_review_status: val("f-security_review_status") || null,
    custom_tags: collectCustomTags(),
    resources: collectResources(),
    acknowledgements: [...document.querySelectorAll(".ack:checked")].map(c => c.value),
  };
}

async function submitIntake() {
  const errBox = document.getElementById("intake-errors");
  errBox.innerHTML = "";
  // Client-side gate: block the POST if any required field is incomplete, jump to the
  // earliest incomplete step, and flag the offending fields. (Server still re-validates.)
  const missing = requiredNow().filter(r => !r.ok);
  if (missing.length) {
    errBox.innerHTML = reqErrorsHtml(missing, `Complete ${missing.length} required field${missing.length !== 1 ? 's' : ''} before submitting`);
    markMissing(missing);
    toast(`${missing.length} required field${missing.length !== 1 ? 's' : ''} incomplete`, false);
    showStep(Math.min(...missing.map(m => m.step)));
    return;
  }
  if (AMEND) { await submitAmendment(errBox); return; }
  try {
    const r = await api("/api/requests", { method: "POST", body: JSON.stringify(collectPayload()) });
    const w = r.waf || {};
    const nDef = (w.enforced_defaults || []).length, nFind = (w.findings || []).length;
    const wafMsg = nDef || nFind
      ? ` — WAF: ${nDef} default${nDef !== 1 ? 's' : ''} enforced${nFind ? `, ${nFind} finding${nFind !== 1 ? 's' : ''}` : ''}`
      : " — WAF: born compliant";
    toast(`Submitted ${r.request.project_id} — ${r.routing.risk_tier} · ${r.routing.change_type} change${wafMsg}`);
    switchView("approvals");
  } catch (e) {
    const errs = (e.body && e.body.details && e.body.details.errors) || [e.body && e.body.error || "request failed"];
    errBox.innerHTML = `<div class="errors"><b>Request blocked (${esc(e.status)})</b><ul>${errs.map(x => `<li>${esc(x)}</li>`).join("")}</ul></div>`;
    showStep(0);
  }
}

// ------------------------------------------------------------ amend (change request)
// AMEND is set when the intake form is opened to edit an existing request. Submitting then
// posts to /amend (approver + e-signature) instead of creating a brand-new project.
let AMEND = null;

function openAmend(request) {
  AMEND = { request_id: String(request.id), project_id: request.project_id,
            label: request.use_case_name || request.project_name || request.project_id };
  document.getElementById("modal").classList.add("hidden");
  switchView("intake");
  // renderIntake() runs synchronously via switchView; prefill immediately after.
  prefillIntake(request);
  injectAmendBanner();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function cancelAmend() {
  AMEND = null;
  renderIntake();
  toast("Amendment cancelled");
}

function injectAmendBanner() {
  const v = document.getElementById("view-intake");
  if (!v || document.getElementById("amend-banner")) return;
  const b = el("div", { class: "card", id: "amend-banner" });
  b.style.borderColor = "var(--lava)";
  b.innerHTML = `
    <div class="flex"><h3 style="margin:0">✎ Amending an existing project</h3>
      <button class="btn ghost small right" id="amend-cancel">Cancel amendment</button></div>
    <p class="muted" style="font-size:12px;margin:6px 0">
      Change request against <b>${esc(AMEND.label)}</b> <span class="mono">${esc(AMEND.project_id)}</span>.
      On submit, the amended metadata re-tags the project's existing assets and any net-new
      resource is provisioned. Requires an <b>approver</b> persona + e-signature.</p>
    <label class="field">E-signature (approver full name)</label>
    <input id="amend-esig" placeholder="Type your full name to sign this change" />`;
  v.insertBefore(b, v.children[2] || null);   // under the title/sub, above the co-pilot
  b.querySelector("#amend-cancel").onclick = cancelAmend;
}

async function submitAmendment(errBox) {
  const esig = (document.getElementById("amend-esig") || {}).value || "";
  if (!esig.trim()) { toast("An e-signature is required to submit an amendment", false);
    document.getElementById("amend-esig")?.focus(); return; }
  const payload = Object.assign(collectPayload(), { esignature: esig.trim() });
  try {
    const r = await api(`/api/requests/${AMEND.request_id}/amend`,
      { method: "POST", body: JSON.stringify(payload) });
    const parts = [];
    const nNew = (r.provisioned_new || []).length;
    if (nNew) parts.push(`+${nNew} new resource(s) queued`);
    if ((r.retagged || []).length) parts.push(`re-tagged ${r.retagged.length} asset(s)`);
    toast(`Amended ${r.project_id} — ${parts.join(" · ") || "recorded"} · track in Requests`);
    AMEND = null;
    switchView("requests");
  } catch (e) {
    const errs = (e.body && e.body.details && e.body.details.errors) || [(e.body && e.body.error) || "amendment failed"];
    errBox.innerHTML = `<div class="errors"><b>Amendment blocked (${esc(e.status)})</b><ul>${errs.map(x => `<li>${esc(x)}</li>`).join("")}</ul></div>`;
    showStep(0);
  }
}

// Prefill every intake field from a request record (used by amend). Extends applyDraft's
// subset to the full governed field-set + resource selection.
function prefillIntake(rq) {
  const set = (id, val) => { const e = document.getElementById(id); if (e != null && val != null && val !== "") e.value = val; };
  const check = (id, val) => { const e = document.getElementById(id); if (e) e.checked = !!val; };
  set("f-project_name", rq.project_name); set("f-use_case_name", rq.use_case_name);
  set("f-description", rq.description); set("f-justification", rq.justification);
  set("f-business_domain", rq.business_domain);
  refreshTaxonomy("lob"); set("f-business_function", rq.business_function);
  refreshTaxonomy("function"); set("f-business_sub_function", rq.business_sub_function);
  set("f-business_owner", rq.business_owner); set("f-owner_group", rq.owner_group);
  set("f-technical_lead", rq.technical_lead); set("f-backup_owner", rq.backup_owner);
  set("f-support_contact", rq.support_contact); set("f-department", rq.department);
  set("f-cost_center", rq.cost_center); set("f-cost_type", rq.cost_type);
  set("f-budget_monthly_cap", rq.budget_monthly_cap); set("f-wbs_code", rq.wbs_code);
  set("f-data_classification", rq.data_classification); set("f-environment", rq.environment);
  set("f-medallion_layer", rq.medallion_layer); set("f-region", rq.region);
  set("f-data_retention", rq.data_retention); set("f-lifecycle_stage", rq.lifecycle_stage);
  set("f-sla_tier", rq.sla_tier); set("f-rto_hours", rq.rto_hours); set("f-rpo_hours", rq.rpo_hours);
  set("f-go_live_date", rq.go_live_date); set("f-sunset_date", rq.sunset_date);
  set("f-dpia_ref", rq.dpia_ref); set("f-security_review_status", rq.security_review_status);
  set("f-intended_use", rq.intended_use); set("f-out_of_scope_uses", rq.out_of_scope_uses);
  set("f-model_card_ref", rq.model_card_ref); set("f-ai_risk_tier", rq.ai_risk_tier);
  set("f-change_ref", rq.change_ref); set("f-servicenow_ref", rq.servicenow_ref);
  set("f-jira_epic", rq.jira_epic); set("f-confluence_url", rq.confluence_url);
  const setList = (id, arr) => set(id, Array.isArray(arr) ? arr.join(", ") : arr);
  setList("f-depends_on", rq.depends_on); setList("f-source_systems", rq.source_systems);
  setList("f-consumed_by", rq.consumed_by);
  check("f-gxp_relevant", rq.gxp_relevant); check("f-contains_phi", rq.contains_phi);
  check("f-validated_system", rq.validated_system); check("f-human_oversight", rq.human_oversight);
  document.querySelectorAll("#f-compliance input").forEach(cb => {
    cb.checked = (rq.compliance_scope || []).includes(cb.value);
  });
  selectResources(new Set((rq.resources || []).map(r => r.type)));
  refreshHints(); updateCompletion();
}

// ================================================================== REQUESTS
// Request-centric lifecycle view: the home a request has AFTER submit. Serves the
// requester (track my submissions + amend), the approver (full ledger of decisions), and
// compliance/audit (signatures + audit trail on every request).
const STATUS_PILL = { ACTIVE: "real", APPROVED: "real", PROVISIONING: "", PARTIAL: "degraded",
                      FAILED: "tier2", REJECTED: "tier2", DECOMMISSIONED: "" };
const statusPillClass = (s) => STATUS_PILL[s] || "";

async function renderRequests() {
  const v = document.getElementById("view-requests");
  const mineDefault = persona === "requester";
  v.innerHTML = `<h1>Requests &amp; change history</h1>
    <p class="sub">Every request submitted — track status, open full detail (metadata, resources, approvals, signatures, audit), and amend an existing project as a new change request.</p>`;
  const fc = el("div", { class: "card" });
  fc.innerHTML = `
    <div class="row" style="align-items:center">
      <label class="check"><input type="checkbox" id="rq-mine" ${mineDefault ? "checked" : ""}/> Only mine (${esc(PERSONAS[persona].email)})</label>
      <div style="flex:1"></div>
      <label class="field" style="margin:0;margin-right:8px">Status:</label>
      <select id="rq-status" style="max-width:200px">
        <option value="">All statuses</option>
        ${["PENDING_APPROVAL", "APPROVED", "PROVISIONING", "ACTIVE", "PARTIAL", "FAILED", "REJECTED", "DECOMMISSIONED"]
          .map(s => `<option value="${s}">${s}</option>`).join("")}
      </select>
    </div>
    <div class="row" style="align-items:center; margin-top:8px">
      <input id="rq-search" placeholder="Filter by project, use case, request id, requester, or resource…" style="flex:1" />
      <label class="field" style="margin:0 8px">Kind:</label>
      <select id="rq-kind" style="max-width:170px">
        <option value="">All kinds</option>
        <option value="create">create</option>
        <option value="amendment">amendment</option>
        <option value="add_resources">add_resources</option>
      </select>
    </div>`;
  v.appendChild(fc);
  const tableCard = el("div", { class: "card" });
  tableCard.innerHTML = `<p class="muted">Loading…</p>`;
  v.appendChild(tableCard);

  let allRows = [];
  const render = () => {
    const q = (document.getElementById("rq-search").value || "").trim().toLowerCase();
    const kindF = document.getElementById("rq-kind").value;
    let rows = allRows.slice();
    if (kindF) rows = rows.filter(r => (((r.metadata || {}).change_kind || r.change_kind || "create") === kindF));
    if (q) rows = rows.filter(r => {
      const hay = [r.use_case_name, r.project_name, r.project_id, r.requester, r.risk_tier, r.status,
                   ...(r.resources || []).map(x => x.type)].filter(Boolean).join(" ").toLowerCase();
      return hay.includes(q);
    });
    if (!rows.length) { tableCard.innerHTML = `<p class="muted">No requests match these filters.</p>`; return; }
    rows.sort((a, b) => ((toDate(b.created_at) || 0) - (toDate(a.created_at) || 0)));
    const t = el("table");
    t.innerHTML = `<thead><tr><th>Use case / project</th><th>Tier</th><th>Status</th><th>Approvals</th><th>Resources</th><th>Requester</th><th>Created</th></tr></thead>`;
    const tb = el("tbody");
    rows.forEach(r => {
      const tr = el("tr", { class: "click" });
      const d = toDate(r.created_at);
      const created = d ? d.toLocaleDateString() : "—";
      const kind = (r.metadata || {}).change_kind || r.change_kind;
      const isChange = kind && kind !== "create";
      // Approvals summary from the enriched list. "Pending" is shown ONLY while the request
      // is actually in the approval queue; approved/active states show ✓ / auto / e-signed
      // (change requests are e-signed at creation, so they carry no queue approvals).
      const req = r.required_approvals || 0, got = r.approvals_count || 0;
      let appCell = "—";
      if (r.status === "REJECTED" || r.rejected) appCell = `<span class="kv tier2">rejected</span>`;
      else if (r.status === "PENDING_APPROVAL") appCell = `<span class="kv">⏳ ${got}/${req || 1}</span>`;
      else if (r.auto_approved && !got) appCell = `<span class="kv ok" title="pre-authorized standard change">auto</span>`;
      else if (isChange && !got) appCell = `<span class="kv ok" title="approver e-signed the change">✓ e-signed</span>`;
      else appCell = `<span class="kv ok">✓ ${got || req}/${req || got}</span>`;
      tr.innerHTML = `
        <td><b>${esc(r.use_case_name || r.project_name || "—")}</b>${isChange ? ` <span class="kv">${esc(kind)}</span>` : ""}
          <div class="mono muted" style="font-size:11px">${esc(r.project_id)}</div></td>
        <td>${tierPill(r.risk_tier)}</td>
        <td><span class="pill ${statusPillClass(r.status)}">${esc(r.status)}</span></td>
        <td style="font-size:12px">${appCell}</td>
        <td>${(r.resources || []).map(x => `<span class="kv">${esc(x.type)}</span>`).join("") || "—"}</td>
        <td class="muted" style="font-size:12px">${esc(r.requester)}</td>
        <td class="muted" style="font-size:12px">${esc(created)}</td>`;
      tr.onclick = () => showRequestDetail(r.id);
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    tableCard.innerHTML = ""; tableCard.appendChild(t);
  };
  const load = async () => {
    const qs = new URLSearchParams();
    if (document.getElementById("rq-mine").checked) qs.set("mine", "true");
    const status = document.getElementById("rq-status").value;
    if (status) qs.set("status", status);
    try { allRows = await api("/api/requests" + (qs.toString() ? "?" + qs : "")); }
    catch (e) { tableCard.innerHTML = `<p class="muted">Could not load requests (${esc(e.status)}).</p>`; return; }
    render();
  };
  document.getElementById("rq-mine").onchange = load;
  document.getElementById("rq-status").onchange = load;
  document.getElementById("rq-search").oninput = render;
  document.getElementById("rq-kind").onchange = render;
  load();
}

// ================================================================== APPROVALS
async function renderApprovals() {
  const v = document.getElementById("view-approvals");
  v.innerHTML = `<h1>Approval queue</h1><p class="sub">Risk-tiered gates. Tier-2 (restricted/GxP/PHI/prod) needs two distinct approvers + compliance.</p>
    <div id="provision-status"></div>
    <p class="muted">Loading queue…</p>`;
  let queue;
  try { queue = await api("/api/approvals/queue"); }
  catch (e) {
    v.appendChild(el("div", { class: "errors" }, `Switch persona to <b>Platform approver</b> or <b>Security &amp; compliance</b> to review (status ${e.status}).`));
    return;
  }
  if (!queue.length) { v.appendChild(el("p", { class: "muted" }, "Queue is empty.")); return; }
  let highlightCard = null;
  queue.forEach((r) => {
    const c = el("div", { class: "card" });
    if (_deepLinkRequestId && String(r.id) === _deepLinkRequestId) {
      c.classList.add("highlight");   // came from an approval-email deep-link
      highlightCard = c;
    }
    const approves = (r.approvals || []).filter(a => a.decision === "approve").length;
    // The server decides gate entitlement; the card just reflects it, so an approver sees
    // "compliance must sign this" instead of discovering it as a rejected POST.
    const signable = r.signable_gates || [];
    // Per-gate status: who has signed each required gate, and which are still pending —
    // so a Tier-2 request shows "✓ platform — Alex Rivera · ⏳ security-compliance — pending".
    const byGate = {};
    (r.approvals || []).filter(a => a.decision === "approve").forEach(a => { byGate[a.gate] = a.approver; });
    const gates = (r.required_gates || []).length
      ? `<div class="gate-status" style="font-size:12px; margin:6px 0">
          <span class="muted">Approvals:</span> ${(r.required_gates || []).map(g => byGate[g]
            ? `<span class="kv ok" title="signed">✓ ${esc(g)} — ${esc(byGate[g])}</span>`
            : `<span class="kv" title="pending">⏳ ${esc(g)} — pending</span>`).join(" ")}</div>`
      : "";
    c.innerHTML = `
      <div class="flex">
        <h3>${esc(r.use_case_name || r.project_name)} <span class="mono muted">${esc(r.project_id)}</span></h3>
        <div class="right">${tierPill(r.risk_tier)} <span class="pill">${esc(approves)}/${esc(r.required_approvals)} approvals</span></div>
      </div>
      <div class="muted" style="font-size:12px">${esc(r.requester)} · ${esc([r.business_domain, r.business_function, r.business_sub_function].filter(Boolean).join(" › "))} · ${esc(r.data_classification)} · ${esc(r.environment)} · CC ${esc(r.cost_center)}</div>
      ${r.business_owner ? `<div class="muted" style="font-size:12px">Business owner: ${esc(r.business_owner)}${r.project_name && r.use_case_name ? ` · asset: ${esc(r.project_name)}` : ""}</div>` : ""}
      ${gates}
      <p style="font-size:13px">${esc(r.justification)}</p>
      <div class="tagset">${(r.resources || []).map(x => `<span class="kv">${esc(x.type)}</span>`).join("")}</div>
      ${wafPanel((r.metadata || {}).waf)}
      <div style="margin:8px 0"><button class="btn ghost small" id="detail-${esc(r.id)}">View details</button></div>
      ${signable.length
        ? `<div style="margin-top:10px">
             <textarea class="acomment" rows="2" placeholder="Decision comment / rationale — stored on the e-signature (required to reject)"></textarea>
             <div class="row" style="margin-top:8px">
               <input class="esig" placeholder="Type your full name (e-signature)" />
               ${signable.length > 1
                 ? `<select class="gate">${signable.map(g => `<option value="${esc(g)}">${esc(g)}</option>`).join("")}</select>`
                 : ""}
               <button class="btn small approve">Approve &amp; sign${signable.length === 1 ? ` (${esc(signable[0])})` : ""}</button>
               <button class="btn ghost small reject">Reject</button>
             </div>
           </div>`
        : `<div class="muted" style="margin-top:10px;font-size:12px">
             <span class="pill tier2">cannot sign</span> ${esc(r.blocked_reason || "no outstanding gate you are entitled to sign")}
           </div>`}`;
    if (signable.length) {
      const sig = () => c.querySelector(".esig").value.trim();
      const gate = () => (c.querySelector(".gate") || {}).value || signable[0];
      const comment = () => c.querySelector(".acomment").value.trim();
      c.querySelector(".approve").onclick = () => decide(r.id, "approve", sig(), gate(), comment(), c);
      c.querySelector(".reject").onclick = () => decide(r.id, "reject", sig(), gate(), comment(), c);
    }
    c.querySelector(`#detail-${esc(r.id)}`).onclick = () => showRequestDetail(r.id);
    v.appendChild(c);
  });
  if (highlightCard) {
    highlightCard.scrollIntoView({ behavior: "smooth", block: "center" });
    _deepLinkRequestId = "";   // consume once so a later manual visit doesn't re-highlight
  }
}

// Lock a card (grey out + disable every control) while its decision is in flight / the
// request provisions, so the approver can't double-act; `label` shows what's happening.
function setCardBusy(card, label) {
  if (!card) return;
  card.querySelectorAll("button, input, select, textarea").forEach(el => (el.disabled = true));
  card.style.opacity = "0.6"; card.style.pointerEvents = "none";
  let b = card.querySelector(".card-busy");
  if (!b) { b = el("div", { class: "card-busy", style: "margin-top:10px" }); card.appendChild(b); }
  b.innerHTML = `<span class="pill">⏳ ${esc(label)}</span>`;
}
function clearCardBusy(card) {
  if (!card) return;
  card.querySelectorAll("button, input, select, textarea").forEach(el => (el.disabled = false));
  card.style.opacity = ""; card.style.pointerEvents = "";
  const b = card.querySelector(".card-busy"); if (b) b.remove();
}

async function decide(rid, decision, esignature, gate, comment, card) {
  if (!esignature) { toast("An e-signature is required", false); return; }
  if (decision === "reject" && !comment) { toast("A comment is required to reject", false); return; }
  // The comment is the stored rationale (bound into the signed manifest); fall back to the
  // decision word only when an approval is signed without a note.
  const reason = comment || (decision === "approve" ? "approved" : "rejected");
  setCardBusy(card, decision === "approve" ? "Submitting approval…" : "Submitting rejection…");
  try {
    const r = await api(`/api/approvals/${rid}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision, reason, esignature, gate: gate || null,
                             meaning: decision === "approve" ? "approved" : "rejected" }),
    });
    if (r.outstanding_gates && r.outstanding_gates.length) {
      toast(`Signed ${r.signed_gate} — ${r.outstanding_gates.length} gate${r.outstanding_gates.length !== 1 ? 's' : ''} remaining`);
      setTimeout(renderApprovals, 500);
      return;
    }
    toast(`${decision}: ${r.status}` + (r.provisioning ? " — provisioning started" : ""));
    if (r.provisioning) {
      // Keep the card locked and show live progress; watchProvisioning polls to a terminal
      // state, refreshes the queue, and the final status lands on the Requests page.
      setCardBusy(card, `Provisioning ${String(rid).slice(0, 8)}… — final status shows in Requests`);
      watchProvisioning(rid);
      return;
    }
    setTimeout(renderApprovals, 600);
  } catch (e) {
    clearCardBusy(card);   // let the approver retry
    toast((e.body && e.body.error) || "decision failed", false);
  }
}

// Approving used to end the story on the client: the toast said "provisioning triggered"
// and nothing ever reported whether it worked. Poll to a terminal state, and when it
// fails, say why and offer the re-drive instead of leaving a dead end.
async function watchProvisioning(rid) {
  const TERMINAL = ["ACTIVE", "PARTIAL", "FAILED", "REJECTED"];
  // Re-query every time: the queue re-renders underneath this and would otherwise leave
  // us writing into a detached node.
  const show = (html) => {
    const box = document.getElementById("provision-status");
    if (box) box.innerHTML = html;
  };
  for (let i = 0; i < 40; i++) {
    let r;
    try { r = await api(`/api/requests/${rid}`); }
    catch (e) { show(`<div class="errors">Lost track of ${esc(rid)}: ${esc((e.body && e.body.error) || e.status)}</div>`); return; }
    const st = r.status;
    if (!TERMINAL.includes(st)) {
      show(`<div class="card"><span class="pill">${esc(st)}</span> provisioning <span class="mono">${esc(rid)}</span>…</div>`);
      await new Promise(res => setTimeout(res, 1500));
      continue;
    }
    const assets = r.assets || [];
    const failed = (r.metadata || {}).failed || [];
    const rows = assets.map(a => `<span class="kv">${esc(a.type)} ${modePill(a.mode)}${
      a.degraded ? ` <span class="pill degraded">modelled: ${esc(reasonLabel(a.mode_reason))}</span>` : ""}</span>`).join(" ");
    const problems = failed.map(f => `<div class="audit-ev"><span class="pill tier2">${esc(f.type || "resource")}</span> ${esc(f.error || "failed")}</div>`).join("");
    await renderApprovals();   // request has left the queue; refresh it before reporting
    show(`<div class="card"><div class="flex"><h3>${esc(st)}</h3>
        <div class="right">${st === "PARTIAL" || st === "FAILED"
          ? `<button class="btn small" id="pv-retry">Retry failed resources</button>` : ""}</div></div>
      <div class="tagset">${rows}</div>${problems}</div>`);
    const btn = document.getElementById("pv-retry");
    if (btn) btn.onclick = async () => {
      try { await api(`/api/requests/${rid}/retry`, { method: "POST" }); toast("Retrying…"); watchProvisioning(rid); }
      catch (e) { toast((e.body && e.body.error) || "retry failed", false); }
    };
    // Final verdict — surfaces "succeeded/failed"; the Requests page shows the same status.
    toast(`${esc(r.project_id || rid)} — ${st}${failed.length ? ` (${failed.length} failed)` : ""}`,
          st === "ACTIVE");
    return;
  }
  show(`<div class="errors">Still provisioning <span class="mono">${esc(rid)}</span> after 60s — check the registry.</div>`);
}

// =================================================================== REGISTRY
const REASON_LABELS = {
  kill_switch_off: "real provisioning disabled",
  no_permission: "no permission",
  unsupported_here: "unsupported in this workspace",
  missing_prerequisite: "missing prerequisite",
  sdk_unavailable: "SDK does not support it",
  sdk_error: "create call failed",
  provider_unavailable: "no real provider",
};
const reasonLabel = (r) => REASON_LABELS[r] || r || "unknown";

async function renderRegistry() {
  const v = document.getElementById("view-registry");
  v.innerHTML = `<h1>Asset &amp; ownership registry</h1><p class="sub">The CMDB of vended resources. Ownership is by reference — reassign and tags follow.</p>`;
  const assets = await api("/api/assets");
  const currentPersonaEmail = PERSONAS[persona].email;
  const filterCard = el("div", { class: "card" });
  filterCard.innerHTML = `
    <div class="row" style="align-items:center; margin-bottom:10px">
      <label class="check"><input type="checkbox" id="filter-mine"/> Show only my assets</label>
      <div style="flex:1"></div>
      <label class="field" style="margin:0; margin-right:10px">Status:</label>
      <select id="filter-status" style="max-width:180px">
        <option value="">All statuses</option>
        <option value="ACTIVE">ACTIVE</option>
        <option value="PROVISIONING">PROVISIONING</option>
        <option value="PARTIAL">PARTIAL</option>
        <option value="FAILED">FAILED</option>
        <option value="DECOMMISSIONED">DECOMMISSIONED</option>
      </select>
    </div>`;
  v.appendChild(filterCard);

  const t = el("table");
  t.innerHTML = `<thead><tr><th>Type</th><th>Mode</th><th>Handle</th><th>Owner</th><th>Project</th><th>Request</th><th>Status</th><th>Tags</th></tr></thead>`;
  const tb = el("tbody");

  const applyFilters = () => {
    const mineOnly = document.getElementById("filter-mine").checked;
    const statusFilter = document.getElementById("filter-status").value;
    const filtered = assets.filter(a => {
      if (mineOnly && a.owner_id !== currentPersonaEmail) return false;
      if (statusFilter && a.status !== statusFilter) return false;
      return true;
    });

    tb.innerHTML = "";
    if (!filtered.length) {
      tb.innerHTML = `<tr><td colspan="8" class="muted">No assets match filters.</td></tr>`;
      return;
    }
    filtered.forEach(a => {
      const tr = el("tr");
      const why = a.degraded
        ? ` <span class="pill degraded" title="${esc(a.mode_reason)}">modelled: ${esc(reasonLabel(a.mode_reason))}</span>`
        : "";
      // The full governed tag set is ~18 chips — inline it would blow the row height apart
      // (as reported). Collapse to a "N tags" disclosure that expands in place on click.
      const nTags = Object.keys(a.applied_tags || {}).length;
      const tagsCell = nTags
        ? `<details class="tags-cell"><summary>${nTags} tags</summary>${tagsHtml(a.applied_tags)}</details>`
        : "—";
      tr.innerHTML = `<td><b>${esc(a.type)}</b></td><td>${modePill(a.mode)}${why}</td>
        <td class="mono">${esc(a.external_id)}</td><td>${esc(a.owner_id)}</td>
        <td class="mono">${esc(a.project_id)}</td><td class="mono">${esc(a.request_id)}</td>
        <td><span class="pill ${a.status === 'ACTIVE' || a.status === 'real' ? 'real' : a.status === 'FAILED' || a.status === 'degraded' ? 'tier2' : ''}">${esc(a.status || 'ACTIVE')}</span></td>
        <td>${tagsCell}</td>`;
      tb.appendChild(tr);
    });
  };

  if (!assets.length) {
    tb.innerHTML = `<tr><td colspan="8" class="muted">No assets vended yet.</td></tr>`;
  } else {
    applyFilters();
  }

  t.appendChild(tb);
  const card = el("div", { class: "card" }); card.appendChild(t);
  v.appendChild(card);

  document.getElementById("filter-mine").onchange = applyFilters;
  document.getElementById("filter-status").onchange = applyFilters;

  // reassignment
  v.appendChild(el("div", { class: "section-title" }, "<h2>Ownership reassignment</h2>"));
  const rc = el("div", { class: "card" });
  rc.innerHTML = `
    <p class="muted" style="font-size:12px">Reassign a project's assets to a new owner. Requires approver/compliance persona + e-signature.</p>
    <div class="row">
      <div><label class="field">Project ID</label><input id="ra-project" placeholder="proj-clinical-…" /></div>
      <div><label class="field">New owner email</label><input id="ra-new-email" placeholder="new.owner@pave.test" /></div>
    </div>
    <div class="row">
      <div><label class="field">New owner group</label><input id="ra-new-group" placeholder="platform" /></div>
      <div><label class="field">New cost center</label><input id="ra-new-cc" placeholder="CC-9100" /></div>
    </div>
    <label class="field">E-signature</label><input id="ra-sig" placeholder="Type your full name" />
    <div style="margin-top:10px"><button class="btn" id="ra-go">Reassign &amp; re-tag</button></div>`;
  rc.querySelector("#ra-go").onclick = async () => {
    try {
      const r = await api("/api/ownership/reassign", { method: "POST", body: JSON.stringify({
        project_id: document.getElementById("ra-project").value.trim() || null,
        new_owner_email: document.getElementById("ra-new-email").value.trim(),
        new_owner_group: document.getElementById("ra-new-group").value.trim(),
        new_cost_center: document.getElementById("ra-new-cc").value.trim(),
        esignature: document.getElementById("ra-sig").value.trim(),
      }) });
      toast(`Reassigned ${r.count} asset(s) to ${r.new_owner}`);
      renderRegistry();
    } catch (e) { toast((e.body && e.body.error) || "reassign failed", false); }
  };
  v.appendChild(rc);

  // Lifecycle: as-code spec + decommission (keyed by request_id)
  v.appendChild(el("div", { class: "section-title" }, "<h2>Lifecycle</h2>"));
  const lc = el("div", { class: "card" });
  lc.innerHTML = `
    <p class="muted" style="font-size:12px">View the declarative as-code record, or decommission a request's footprint (approver + e-signature; restricted/GxP held for controlled change).</p>
    <div class="row">
      <input id="lc-req" placeholder="request id (from an asset above)" />
      <button class="btn ghost small" id="lc-spec">View as-code spec</button>
    </div>
    <div class="row" style="margin-top:8px">
      <input id="lc-sig" placeholder="e-signature" />
      <label class="check"><input type="checkbox" id="lc-ctrl"/> controlled change done</label>
      <button class="btn danger small" id="lc-dc">Decommission</button>
    </div>
    <hr class="sep" />
    <p class="muted" style="font-size:12px">Add new resources to an existing project (approver + e-signature). Only the new resources are provisioned; they inherit the project's governance context.</p>
    <div class="row">
      <div style="flex:1">${OPTS.resource_types.map(rt =>
        `<label class="check" style="margin-right:10px"><input type="checkbox" class="lc-add-rtype" value="${rt}"/> ${rt}</label>`).join("")}</div>
    </div>
    <div class="row" style="margin-top:8px">
      <input id="lc-add-sig" placeholder="e-signature" />
      <button class="btn small" id="lc-add">Add resources to project</button>
    </div>`;
  lc.querySelector("#lc-spec").onclick = async () => {
    const rid = document.getElementById("lc-req").value.trim();
    if (!rid) { toast("enter a request id", false); return; }
    try { const r = await api(`/api/requests/${rid}/spec`); showModal(`As-code spec · ${rid}`, r.yaml); }
    catch (e) { toast((e.body && e.body.error) || "spec failed", false); }
  };
  lc.querySelector("#lc-dc").onclick = async () => {
    const rid = document.getElementById("lc-req").value.trim();
    try {
      const r = await api(`/api/requests/${rid}/decommission`, { method: "POST", body: JSON.stringify({
        esignature: document.getElementById("lc-sig").value.trim(),
        controlled: document.getElementById("lc-ctrl").checked,
      }) });
      toast(`Decommissioned ${r.decommissioned.length}; held ${r.held_for_controlled_change.length}`);
      renderRegistry();
    } catch (e) { toast((e.body && e.body.error) || "decommission failed", false); }
  };
  lc.querySelector("#lc-add").onclick = async () => {
    const rid = document.getElementById("lc-req").value.trim();
    const types = [...lc.querySelectorAll(".lc-add-rtype:checked")].map(c => c.value);
    if (!rid) { toast("enter a request id", false); return; }
    if (!types.length) { toast("pick at least one resource type", false); return; }
    try {
      const r = await api(`/api/requests/${rid}/resources`, { method: "POST", body: JSON.stringify({
        resources: types.map(t => ({ type: t, config: {} })),
        esignature: document.getElementById("lc-add-sig").value.trim(),
      }) });
      const cr = String(r.change_request_id || "").slice(0, 8);
      toast(`Queued ${(r.resources || types).length} resource(s) as change request ${cr} — track in Requests`);
      renderRegistry();
    } catch (e) { toast((e.body && e.body.error) || "add resources failed", false); }
  };
  v.appendChild(lc);

  renderAccess(v);
}

// Access requests: the highest-volume ticket on a real platform team ("give my group read
// on this schema"). Vending a resource and then sending people back to ServiceNow for a
// grant would undercut the whole premise.
async function renderAccess(v) {
  let g;
  try { g = await api("/api/access/grantable"); } catch (e) { return; }
  v.appendChild(el("div", { class: "section-title" }, "<h2>Access requests</h2>"));
  const c = el("div", { class: "card" });
  // Levels differ per securable (a catalog has no write); offer the union and let the
  // server refuse the combination that does not apply to the chosen asset.
  const byLevel = {};
  Object.entries(g.grantable || {}).forEach(([securable, levels]) =>
    Object.entries(levels).forEach(([lvl, meta]) => {
      byLevel[lvl] = byLevel[lvl] || { securables: [], risk: meta.risk };
      byLevel[lvl].securables.push(securable);
    }));
  const levels = Object.entries(byLevel);
  c.innerHTML = `
    <p class="muted" style="font-size:12px">Grant a group on an already-vended asset. Read is self-service;
      write and manage on restricted or GxP data need an approver and an e-signature. Every grant is audited
      and shows up on the asset.</p>
    <div class="row">
      <div><label class="field">Asset id</label><input id="ac-asset" placeholder="asset id from the table above" /></div>
      <div><label class="field">Principal (group)</label><input id="ac-principal" placeholder="dbx-clinical-analysts" /></div>
    </div>
    <div class="row">
      <div><label class="field">Level</label><select id="ac-level">${levels.map(([k, meta]) =>
        `<option value="${esc(k)}">${esc(k)} — ${esc(meta.securables.join("/"))}${meta.risk === "high" ? " (needs approval)" : ""}</option>`).join("")}</select></div>
      <div><label class="field">Duration (days, blank = permanent)</label><input id="ac-days" type="number" min="1" max="365" /></div>
    </div>
    <label class="field">Justification</label><input id="ac-why" placeholder="why this group needs it" />
    <label class="field">E-signature (only if approval is required)</label><input id="ac-sig" placeholder="Type your full name" />
    <div style="margin-top:10px"><button class="btn" id="ac-go">Request access</button></div>
    <div id="ac-out" style="margin-top:10px"></div>`;
  v.appendChild(c);
  c.querySelector("#ac-go").onclick = async () => {
    const days = parseInt(document.getElementById("ac-days").value, 10);
    try {
      const r = await api("/api/access/request", { method: "POST", body: JSON.stringify({
        asset_id: document.getElementById("ac-asset").value.trim(),
        principal: document.getElementById("ac-principal").value.trim(),
        level: document.getElementById("ac-level").value,
        justification: document.getElementById("ac-why").value.trim(),
        esignature: document.getElementById("ac-sig").value.trim(),
        duration_days: Number.isFinite(days) ? days : null,
      }) });
      const applied = r.applied || {};
      c.querySelector("#ac-out").innerHTML =
        `<div class="audit-ev">Granted <span class="mono">${esc(r.privileges.join(", "))}</span> to
         <b>${esc(r.principal)}</b> on <span class="mono">${esc(r.asset_id)}</span>
         ${modePill(applied.mode)}${applied.mode === "simulated"
            ? ` <span class="pill degraded">modelled: ${esc(reasonLabel(applied.mode_reason))}</span>` : ""}
         · approval: ${esc(r.approval)}</div>`;
      toast(`Access granted to ${r.principal}`);
    } catch (e) {
      c.querySelector("#ac-out").innerHTML = `<div class="errors">${esc((e.body && e.body.error) || "access request failed")}</div>`;
    }
  };
}

// ================================================================ GOVERNANCE
async function renderGovernance() {
  const v = document.getElementById("view-governance");
  v.innerHTML = `<h1>Day-2 governance</h1><p class="sub">Sunset autopilot, tag-drift &amp; orphan sweep, and owner recertification — keeping vended resources healthy after provisioning.</p>`;

  let sw, rc;
  try { sw = await api("/api/governance/sweep"); }
  catch (e) {
    v.appendChild(el("div", { class: "card" },
      `<b>Governance sweep unavailable</b> (${e.status}). Check back later.`));
    sw = { clean: 0, past_sunset: [], tag_drift: [] };
  }
  try { rc = await api("/api/governance/recertification"); }
  catch (e) {
    v.appendChild(el("div", { class: "card" },
      `<b>Recertification data unavailable</b> (${e.status}). Check back later.`));
    rc = { due_count: 0, due: [], recert_age_days: 0 };
  }

  const kpis = el("div", { class: "grid cols-4" });
  kpis.innerHTML = `
    <div class="card"><div class="muted">Clean assets</div><div class="kpi good">${sw.clean}</div></div>
    <div class="card"><div class="muted">Past sunset</div><div class="kpi ${sw.past_sunset.length ? 'warn' : 'good'}">${sw.past_sunset.length}</div></div>
    <div class="card"><div class="muted">Tag drift</div><div class="kpi ${sw.tag_drift.length ? 'warn' : 'good'}">${sw.tag_drift.length}</div></div>
    <div class="card"><div class="muted">Recert due</div><div class="kpi ${rc.due_count ? 'warn' : 'good'}">${rc.due_count}</div></div>`;
  v.appendChild(kpis);

  // Past sunset -> reclaim
  v.appendChild(el("div", { class: "section-title" }, "<h2>Sunset autopilot</h2>"));
  const sc = el("div", { class: "card" });
  if (!sw.past_sunset.length) { sc.innerHTML = `<p class="muted">No expired assets. 🎉</p>`; }
  else {
    const t = el("table");
    t.innerHTML = `<thead><tr><th>Asset</th><th>Type</th><th>Sunset</th><th>Class</th><th></th></tr></thead>`;
    const tb = el("tbody");
    sw.past_sunset.forEach(a => {
      const tr = el("tr");
      const restricted = a.classification === "restricted";
      tr.innerHTML = `<td class="mono">${esc(a.asset_id)}</td><td>${esc(a.type)}</td><td>${esc(a.sunset_date)}</td>
        <td>${esc(a.classification || "")}</td>
        <td>${restricted ? '<span class="pill tier2">controlled change</span>'
                         : `<button class="btn small reclaim" data-id="${esc(a.asset_id)}">Reclaim</button>
                            <button class="btn ghost small extend" data-id="${esc(a.asset_id)}">Extend</button>`}</td>`;
      tb.appendChild(tr);
    });
    t.appendChild(tb); sc.appendChild(t);
  }
  v.appendChild(sc);
  sc.querySelectorAll(".reclaim").forEach(b => b.onclick = async () => {
    try { const r = await api(`/api/governance/reclaim/${b.dataset.id}`, { method: "POST" });
      toast(`Reclaimed ${r.asset_id} -> ${r.status}`); renderGovernance(); }
    catch (e) { toast((e.body && e.body.error) || "reclaim failed", false); }
  });
  // Extension is the honest alternative to reclaim: still needed, so say so on the record
  // with a new date and a justification rather than letting the sweep nag forever.
  sc.querySelectorAll(".extend").forEach(b => b.onclick = async () => {
    const date = prompt("New sunset date (YYYY-MM-DD):");
    if (!date) return;
    const why = prompt("Why is this still needed?") || "";
    try {
      const r = await api(`/api/governance/extend/${b.dataset.id}`, {
        method: "POST", body: JSON.stringify({ sunset_date: date, justification: why }) });
      toast(`Extended ${r.asset_id} to ${r.sunset_date}`); renderGovernance();
    } catch (e) { toast((e.body && e.body.error) || "extend failed", false); }
  });

  // Tag drift
  v.appendChild(el("div", { class: "section-title" }, "<h2>Tag drift</h2>"));
  const dc = el("div", { class: "card" });
  dc.innerHTML = sw.tag_drift.length ? sw.tag_drift.map(d =>
    `<div class="audit-ev"><span class="mono">${esc(d.asset_id)}</span> · coverage ${Math.round(d.coverage*100)}% · missing: ${esc(d.missing.join(", "))}</div>`).join("")
    : `<p class="muted">All active assets at 100% required-tag coverage.</p>`;
  v.appendChild(dc);

  renderReconcile(v);

  // Recertification
  v.appendChild(el("div", { class: "section-title" }, `<h2>Recertification</h2><span class="pill">> ${rc.recert_age_days} days</span>`));
  const rcc = el("div", { class: "card" });
  if (!rc.due.length) { rcc.innerHTML = `<p class="muted">Nothing due for recertification.</p>`; }
  else {
    const t = el("table");
    t.innerHTML = `<thead><tr><th>Asset</th><th>Type</th><th>Owner</th><th>Age</th><th></th></tr></thead>`;
    const tb = el("tbody");
    rc.due.forEach(a => {
      const tr = el("tr");
      tr.innerHTML = `<td class="mono">${esc(a.asset_id)}</td><td>${esc(a.type)}</td><td>${esc(a.owner_id||"")}</td>
        <td>${esc(a.age_days)}d</td><td><button class="btn ghost small recert" data-id="${esc(a.asset_id)}">Attest still needed</button></td>`;
      tb.appendChild(tr);
    });
    t.appendChild(tb); rcc.appendChild(t);
  }
  v.appendChild(rcc);
  rcc.querySelectorAll(".recert").forEach(b => b.onclick = async () => {
    try { await api(`/api/governance/recertify/${b.dataset.id}`, { method: "POST" });
      toast(`Recertified ${b.dataset.id}`); renderGovernance(); }
    catch (e) { toast("recert failed", false); }
  });
}

// Reconcile: registry (desired state) vs the workspace (observed state). This is the
// reconcile loop that replaces a Terraform state file, so it has to be visible, not just
// an endpoint. Read-only against the workspace, and it demos with no access at all
// because simulated assets support injected drift.
function renderReconcile(v) {
  v.appendChild(el("div", { class: "section-title" },
    "<h2>Reconcile (desired vs actual)</h2><span class=\"pill\">read-only</span>"));
  const c = el("div", { class: "card" });
  c.innerHTML = `
    <p class="muted" style="font-size:12px">PAVE's registry is the desired state and this sweep is the reconcile loop —
      the reason there is no per-request Terraform state file. It reads live tags back and reports drift,
      assets that vanished, and PAVE-tagged resources nobody vended.</p>
    <div class="row"><button class="btn small" id="rc-run">Run reconcile</button>
      <button class="btn ghost small" id="rc-drift">Simulate drift on an asset</button>
      <button class="btn ghost small" id="rc-clear">Clear simulated drift</button></div>
    <div id="rc-out" style="margin-top:10px"></div>`;
  v.appendChild(c);
  const out = c.querySelector("#rc-out");
  const run = async () => {
    out.innerHTML = `<p class="muted">Reading live state…</p>`;
    try {
      const r = await api("/api/governance/reconcile", { method: "POST" });
      const rows = [
        ["In sync", r.in_sync, "good"],
        ["Tag drift", (r.drifted || []).length, "warn"],
        ["Tracked but gone", (r.missing || []).length, "warn"],
        ["Untracked (shadow IT)", (r.untracked || []).length, "warn"],
        ["Unreadable", (r.unreadable || []).length, ""],
      ];
      out.innerHTML = `<div class="grid cols-4">${rows.map(([k, n, cls]) =>
        `<div class="card"><div class="muted">${esc(k)}</div><div class="kpi ${n ? cls : 'good'}">${esc(n)}</div></div>`).join("")}</div>`
        + (r.drifted || []).map(d => `<div class="audit-ev"><span class="mono">${esc(d.asset_id)}</span> ${esc(d.type)} · ` +
            (d.tag_drift || []).map(t => `<span class="pill tier2">${esc(t.key)}: expected ${esc(t.expected)}, found ${esc(t.actual || "(missing)")}</span>`).join(" ") +
          `</div>`).join("")
        + (r.missing || []).map(m => `<div class="audit-ev"><span class="mono">${esc(m.asset_id)}</span> ${esc(m.type)} · <span class="pill tier2">gone from the workspace</span></div>`).join("")
        + (r.untracked || []).map(u => `<div class="audit-ev"><span class="mono">${esc(u.name)}</span> · <span class="pill tier2">PAVE-tagged but not in the registry</span></div>`).join("")
        + (r.unreadable || []).map(u => `<div class="audit-ev"><span class="mono">${esc(u.asset_id)}</span> · <span class="pill">could not read: ${esc(reasonLabel(u.reason))}</span></div>`).join("");
    } catch (e) { out.innerHTML = `<div class="errors">${esc((e.body && e.body.error) || "reconcile failed")}</div>`; }
  };
  c.querySelector("#rc-run").onclick = run;
  c.querySelector("#rc-drift").onclick = async () => {
    const id = prompt("Asset id to drift (from the registry):");
    if (!id) return;
    const key = prompt("Tag key to strip (e.g. cost_center):", "cost_center");
    if (!key) return;
    try { await api(`/api/governance/drift/simulate/${id}?untag=${encodeURIComponent(key)}`, { method: "POST" });
      toast(`Drift injected on ${id}`); run(); }
    catch (e) { toast((e.body && e.body.error) || "could not inject drift", false); }
  };
  c.querySelector("#rc-clear").onclick = async () => {
    try { await api("/api/governance/drift/simulate", { method: "DELETE" }); toast("Simulated drift cleared"); run(); }
    catch (e) { toast("clear failed", false); }
  };
}

// ===================================================================== FINOPS
async function renderFinops() {
  const v = document.getElementById("view-finops");
  v.innerHTML = `<h1>FinOps &amp; Well-Architected</h1><p class="sub">Cost attribution from tags + ROI + live Well-Architected Lakehouse scorecard.</p>`;

  let s, sc, imp, ai;
  try { s = await api("/api/finops/summary"); }
  catch (e) {
    v.appendChild(el("div", { class: "card" },
      `<b>FinOps summary unavailable</b> (${e.status})`));
    s = { tag_coverage_pct: 0, total_estimated_monthly: 0, active_assets: 0, untagged_cost: 0, by_cost_center: {}, by_project: {}, by_business_domain: {} };
  }
  try { sc = await api("/api/finops/scorecard"); }
  catch (e) {
    v.appendChild(el("div", { class: "card" },
      `<b>Well-Architected scorecard unavailable</b> (${e.status})`));
    sc = { overall_score: 0, pillars: [] };
  }
  try { imp = await api("/api/finops/impact"); }
  catch (e) {
    v.appendChild(el("div", { class: "card" },
      `<b>Business impact metrics unavailable</b> (${e.status})`));
    imp = { tickets_eliminated: 0, manual_baseline_days: 0, engineer_days_saved: 0, dollars_saved: 0, speedup_x: 0, pave_minutes: 0, measured: {} };
  }
  try { ai = await api("/api/finops/ai"); }
  catch (e) {
    v.appendChild(el("div", { class: "card" },
      `<b>AI governance metrics unavailable</b> (${e.status})`));
    ai = {};
  }

  // ROI banner — the days->minutes story
  const roi = el("div", { class: "grid cols-4" });
  roi.innerHTML = `
    <div class="card"><div class="muted">Tickets eliminated</div><div class="kpi good">${imp.tickets_eliminated}</div>
      <div class="muted" style="font-size:11px">vs ${imp.manual_baseline_days}-day ServiceNow baseline</div></div>
    <div class="card"><div class="muted">Engineer-days saved</div><div class="kpi good">${imp.engineer_days_saved}</div></div>
    <div class="card"><div class="muted">Cost avoided</div><div class="kpi good">$${imp.dollars_saved.toLocaleString()}</div></div>
    <div class="card"><div class="muted">Speed-up</div><div class="kpi">${imp.speedup_x.toLocaleString()}×</div>
      <div class="muted" style="font-size:11px">days → ~${imp.pave_minutes} min</div></div>`;
  // Labelled as modelled, and kept away from the system-table panels below: real numbers
  // sitting next to unlabelled assumptions make the real ones look invented too.
  v.appendChild(el("div", { class: "section-title" },
    `<h2>Business impact (days → minutes)</h2><span class="pill">modelled</span>`));
  v.appendChild(el("p", { class: "muted", style: "font-size:12px;margin:-2px 0 8px" },
    `Modelled from ${esc((imp.measured || {}).requests_provisioned ?? 0)} provisioned request(s) using house assumptions: ` +
    `${esc(imp.manual_baseline_days)}-day manual baseline, $${esc((imp.assumptions || {}).engineer_day_cost_usd)}/engineer-day. ` +
    `Replace with the customer's own cycle time before quoting.`));
  v.appendChild(roi);

  const kpis = el("div", { class: "grid cols-4" });
  const covClass = s.tag_coverage_pct >= 95 ? "good" : "warn";
  kpis.innerHTML = `
    <div class="card"><div class="muted">Est. monthly cost</div><div class="kpi">$${esc(s.total_estimated_monthly)}</div>
      <div class="muted" style="font-size:11px">rate-card estimate, not billed spend</div></div>
    <div class="card"><div class="muted">Active assets</div><div class="kpi">${esc(s.active_assets)}</div></div>
    <div class="card"><div class="muted">Required tags on PAVE assets</div><div class="kpi ${covClass}">${esc(s.tag_coverage_pct)}%</div>
      <div class="bar"><span style="width:${Number(s.tag_coverage_pct) || 0}%"></span></div>
      <div class="muted" style="font-size:11px">~100% by construction — see attribution below</div></div>
    <div class="card"><div class="muted">Untagged cost</div><div class="kpi ${s.untagged_cost ? 'warn' : 'good'}">$${esc(s.untagged_cost)}</div></div>`;
  v.appendChild(kpis);

  await renderAttribution(v);

  v.appendChild(el("div", { class: "section-title" }, "<h2>Estimated cost by tag (PAVE assets)</h2>"));
  v.appendChild(el("p", { class: "muted", style: "font-size:12px;margin:-2px 0 8px" },
    "PAVE's lens sits ABOVE Databricks FinOps: it guarantees every $ is attributable. Cost reporting itself lives in the native AI/BI Usage Dashboard."));
  const cc = el("div", { class: "grid cols-3" });
  cc.appendChild(costCard("Attributed by cost center", s.by_cost_center));
  cc.appendChild(costCard("Attributed by PAVE project", s.by_project));
  cc.appendChild(costCard("Attributed by business domain", s.by_business_domain));
  v.appendChild(cc);

  // GenAI governance & spend (multi-team, AI Gateway)
  if (ai.ai_assets) {
    v.appendChild(el("div", { class: "section-title" }, "<h2>GenAI governance &amp; spend (by team)</h2>"));
    const ak = el("div", { class: "grid cols-4" });
    ak.innerHTML = `
      <div class="card"><div class="muted">AI assets</div><div class="kpi">${ai.ai_assets}</div></div>
      <div class="card"><div class="muted">LLM gateway endpoints</div><div class="kpi">${ai.llm_endpoints}</div></div>
      <div class="card"><div class="muted">Guardrail coverage</div><div class="kpi ${ai.guardrail_coverage_pct>=100?'good':'warn'}">${ai.guardrail_coverage_pct}%</div></div>
      <div class="card"><div class="muted">Inference logging</div><div class="kpi ${ai.logging_coverage_pct>=100?'good':'warn'}">${ai.logging_coverage_pct}%</div></div>`;
    v.appendChild(ak);
    const tcard = el("div", { class: "card" });
    const teams = Object.entries(ai.by_team || {});
    tcard.innerHTML = `<h3>Per-team AI spend vs budget</h3>` + (teams.length ? `<table>
      <thead><tr><th>Team / domain</th><th>Endpoints</th><th>Est. $/mo</th><th>Budget</th><th>Status</th></tr></thead>
      <tbody>${teams.map(([t, r]) => `<tr><td>${esc(t)}</td><td>${esc(r.endpoints)}</td><td>$${esc(r.est_spend)}</td>
        <td>${r.budget ? '$' + esc(r.budget) : '—'}</td>
        <td>${r.over_budget ? '<span class="pill tier2">over budget</span>' : '<span class="pill real">ok</span>'}</td></tr>`).join("")}</tbody></table>`
      : `<p class="muted">No AI assets yet.</p>`);
    v.appendChild(tcard);
  }

  // Native Databricks FinOps — complement, don't duplicate
  const nat = el("div", { class: "card" });
  nat.innerHTML = `<h3>Spend reporting → native Databricks FinOps</h3>
    <p class="muted" style="font-size:12px">PAVE feeds <span class="mono">system.billing.usage.custom_tags</span>; reporting lives where it belongs:</p>
    <div class="tagset">
      <a class="kv" href="https://docs.databricks.com/aws/en/admin/account-settings/usage" target="_blank">AI/BI Usage Dashboard ↗</a>
      <a class="kv" href="https://github.com/mohanab89/databricks-dashboard-suite" target="_blank">Dashboard Suite (Cost/Jobs/DBSQL/Lineage) ↗</a>
      <span class="kv">join key: project_id / cost_center / business_domain</span>
    </div>`;
  v.appendChild(nat);

  const overallCls = sc.overall_score >= 90 ? "good" : sc.overall_score >= 70 ? "" : "warn";
  v.appendChild(el("div", { class: "section-title" },
    `<h2>Well-Architected scorecard</h2><span class="pill ${overallCls === 'warn' ? 'tier2' : 'real'}">overall ${sc.overall_score}/100</span>`));
  v.appendChild(el("p", { class: "muted", style: "font-size:12px;margin:-2px 0 8px" },
    "A real per-pillar score computed from the controls PAVE enforces at provisioning time (born-compliant defaults + gates). Each control cites its Well-Architected Lakehouse identifier."));
  const pg = el("div", { class: "grid cols-2" });
  sc.pillars.forEach(p => {
    const c = el("div", { class: "card" });
    const cls = p.score >= 90 ? "good" : p.score >= 70 ? "" : "warn";
    const findings = p.open_findings
      ? `<span class="pill tier2">${p.open_findings} open finding${p.open_findings > 1 ? 's' : ''}</span>`
      : `<span class="pill real">clean</span>`;
    c.innerHTML = `<div class="flex"><h3>${esc(p.pillar)}</h3><span class="right kpi ${cls}" style="font-size:20px">${esc(p.score)}</span></div>
      <div class="bar"><span style="width:${Number(p.score) || 0}%"></span></div>
      <div class="tagset" style="margin-top:8px">${p.controls.map(x => `<span class="kv">${esc(x)}</span>`).join("")}</div>
      <p class="muted" style="font-size:12px;margin-top:8px">${findings}</p>`;
    pg.appendChild(c);
  });
  v.appendChild(pg);
}
// The honest tag-coverage metric: PAVE-attributed spend over TOTAL spend. Measuring
// coverage over PAVE's own assets answers a question nobody asked (they are tagged by
// construction); the denominator has to be the whole estate for the number to move.
async function renderAttribution(v) {
  v.appendChild(el("div", { class: "section-title" },
    `<h2>Attribution completeness</h2><span class="pill">system.billing.usage</span>`));
  const c = el("div", { class: "card" });
  c.innerHTML = `<p class="muted">Reading billing system tables…</p>`;
  v.appendChild(c);
  let a;
  try { a = await api("/api/finops/attribution"); }
  catch (e) { a = { source: "unavailable", reason: (e.body && e.body.error) || "request failed" }; }
  if (a.source !== "system.billing.usage") {
    c.innerHTML = `<div class="flex"><h3>Not available here</h3><span class="right pill">no billing access</span></div>
      <p class="muted" style="font-size:12px">${esc(a.reason || "")}. This metric is deliberately NOT estimated from
        PAVE's own registry — doing so would beg the question, since PAVE tags everything it vends.
        ${esc(a.note || "")}</p>`;
    return;
  }
  const pct = a.attribution_completeness_pct;
  c.innerHTML = `
    <div class="flex"><h3>${esc(pct)}% of ${esc(a.window_days)}-day spend carries a PAVE project_id</h3>
      <span class="right kpi ${pct >= 80 ? "good" : "warn"}">$${esc(a.unattributed_cost)} unattributed</span></div>
    <div class="bar"><span style="width:${Number(pct) || 0}%"></span></div>
    <p class="muted" style="font-size:12px">${esc(a.interpretation)}</p>
    ${(a.top_unattributed || []).length ? `<table>
      <thead><tr><th>SKU</th><th>Workspace</th><th>Unattributed $</th></tr></thead>
      <tbody>${a.top_unattributed.map(r =>
        `<tr><td>${esc(r.sku_name)}</td><td class="mono">${esc(r.workspace_id)}</td><td>$${esc(r.list_cost)}</td></tr>`).join("")}</tbody>
      </table>` : ""}`;
}

function costCard(title, obj) {
  const entries = Object.entries(obj || {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(e => e[1]));
  const c = el("div", { class: "card" });
  // Keys here are requester-supplied (cost_center, project_id, business_domain).
  c.innerHTML = `<h3>${esc(title)}</h3>` + (entries.length ? entries.map(([k, val]) =>
    `<div style="margin:6px 0"><div class="flex"><span class="mono">${esc(k)}</span><span class="right">$${esc(val)}</span></div>
     <div class="bar"><span style="width:${Number(100 * val / max) || 0}%"></span></div></div>`).join("") : `<p class="muted">No data yet.</p>`);
  return c;
}

// Well-Architected control summary for a request (enforced defaults + findings + waivers).
function wafPanel(waf) {
  if (!waf) return "";
  const def = waf.enforced_defaults || [], find = waf.findings || [], waived = waf.waived || [];
  if (!def.length && !find.length && !waived.length) return "";
  let html = `<div class="card" style="margin-top:10px;background:rgba(255,255,255,.02)">
    <div class="muted" style="font-size:11px;text-transform:uppercase;letter-spacing:.5px">Well-Architected controls</div>`;
  if (def.length) html += `<div class="tagset" style="margin-top:6px">${def.map(d =>
    `<span class="kv" title="${esc(d.pillar)}">✓ ${esc(d.key)} = ${esc(JSON.stringify(d.value))}</span>`).join("")}</div>`;
  if (find.length) html += `<div class="tagset" style="margin-top:6px">${find.map(f =>
    `<span class="pill tier2" title="${esc(f.remediation || '')}">⚠ ${esc(f.rule_id)}: ${esc(f.title)}</span>`).join("")}</div>`;
  // Waiver justification is free text typed by the requester and read by the approver.
  if (waived.length) html += `<div class="tagset" style="margin-top:6px">${waived.map(w =>
    `<span class="pill" title="${esc(w.justification || '')}">waived: ${esc(w.rule_id)}</span>`).join("")}</div>`;
  return html + `</div>`;
}

// ====================================================================== SHELL
function switchView(name) {
  document.querySelectorAll(".view").forEach(v => v.classList.add("hidden"));
  document.getElementById(`view-${name}`).classList.remove("hidden");
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.view === name));
  ({ intake: renderIntake, requests: renderRequests, approvals: renderApprovals,
     registry: renderRegistry, governance: renderGovernance, finops: renderFinops }[name])();
}

async function boot() {
  document.querySelectorAll(".tab").forEach(t => (t.onclick = () => switchView(t.dataset.view)));
  const personaSel = document.getElementById("persona");
  personaSel.value = persona;   // restore persisted persona on load (survives refresh)
  personaSel.onchange = (e) => {
    persona = e.target.value;
    localStorage.setItem("pave_persona", persona);
    toast(`Acting as ${persona}`);
    switchView(document.querySelector(".tab.active")?.dataset.view || "intake");  // re-render current view with new identity
  };
  const modal = document.getElementById("modal");
  document.getElementById("modal-close").onclick = () => modal.classList.add("hidden");
  modal.onclick = (e) => { if (e.target === modal) modal.classList.add("hidden"); };
  try {
    [OPTS, TEMPLATES] = await Promise.all([api("/api/meta/form-options"), api("/api/meta/templates")]);
    try { WORKSPACES = (await api("/api/meta/workspaces")).workspaces || WORKSPACES; } catch (e) { /* keep default */ }
    renderPosture();    // state up front what this deployment really does
    renderIntake();
    handleDeepLink();   // honor #approvals/{id} from an approval email
  } catch (e) {
    document.getElementById("view-intake").innerHTML =
      `<div class="errors">Failed to load PAVE metadata. <button class="btn ghost small" onclick="location.reload()">Retry</button></div>`;
  }
}

// Deep-link support: an approval email links to #approvals/{request_id}. On load (and on
// hash change) jump to the approvals view and highlight that request.
let _deepLinkRequestId = "";
function handleDeepLink() {
  const m = (location.hash || "").match(/^#approvals\/(.+)$/);
  if (m) { _deepLinkRequestId = decodeURIComponent(m[1]); switchView("approvals"); }
}
window.addEventListener("hashchange", handleDeepLink);
boot();
