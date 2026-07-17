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
const tierPill = (t) => `<span class="pill ${String(t || "").toLowerCase()}">${t || "-"}</span>`;
const modePill = (m) => `<span class="pill ${m}">${m}</span>`;
const tagsHtml = (tags) => `<div class="tagset">${Object.entries(tags || {})
  .map(([k, v]) => `<span class="kv">${k}=${v}</span>`).join("")}</div>`;

let OPTS = null, TEMPLATES = [];
let WORKSPACES = [{ host: "", label: "This workspace (default)", self: true }];

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
  // live validation + completion %
  wrap.addEventListener("input", onIntakeInput);
  wrap.addEventListener("change", onIntakeInput);
  refreshAI();
  showStep(0);
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
      ${L("Owning AD group", "SCIM-synced AD group that owns this. You must be a member.", true)}
      ${inp("f-owner_group", { placeholder: "rwe-clinical", maxlength: 80 })}
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
        <div>${sel("f-region", opt("regions"), true, "Data residency region (if multi-region).")}</div>
        <div>${sel("f-data_retention", opt("data_retention_classes"), true, "Retention class. Required for restricted / GxP.")}</div>
      </div>
      ${L("Sunset date", "Auto-decommission reminder. Required for dev/test sandboxes.")}
      ${inp("f-sunset_date", { type: "date" })}
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
        <div>${L("Monthly budget cap ($)", "Spend cap; drives alerts. Over $2000 escalates approval.")}${inp("f-budget_monthly_cap", { type: "number", min: 0, placeholder: "2000" })}</div>
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
      ${L("Target go-live date", "Planned production go-live.")}${inp("f-go_live_date", { type: "date" })}
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
    <div id="resource-picker" class="grid cols-3" style="margin-top:8px">${opt("resource_types").filter(rt => rt !== "workspace").map(rt =>
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

// (Re)render one config panel per selected resource into #resource-configs.
function renderResourceConfigs() {
  const host = document.getElementById("resource-configs");
  if (!host) return;
  const types = selectedResourceTypes();
  host.innerHTML = types.length
    ? types.map(rt => `<div class="card rcfg" data-rtype="${rt}">
        <div class="flex"><b>${rt}</b><span class="right muted" style="font-size:11px">configuration</span></div>
        ${L("Name (optional)", "Override the auto-generated name.")}<input class="rname" maxlength="60" placeholder="auto" />
        ${resourceConfigHtml(rt)}
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
function resourceConfigHtml(rt) {
  const locs = opt("pre_approved_locations") || [];
  const locOptions = ["", ...locs];

  if (rt === "catalog") {
    return `
      ${L("Catalog kind", "Managed = Databricks-governed storage (default). External = a pre-approved external location.")}
      <div class="row" style="gap:16px">
        <label class="check"><input type="radio" name="cat-kind" class="cat-kind" value="managed" checked/> managed</label>
        <label class="check"><input type="radio" name="cat-kind" class="cat-kind" value="external"/> external</label>
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
    return computeConfigHtml(true);
  }
  if (rt === "job_cluster") {
    return computeConfigHtml(false);
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
        <label class="check"><input type="radio" name="lb-offer" class="lb-offer" value="provisioned" checked/> provisioned</label>
        <label class="check"><input type="radio" name="lb-offer" class="lb-offer" value="autoscaling"/> autoscaling</label>
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
  return "";
}

// Shared cluster / job_cluster config. `interactive` -> all-purpose (has autotermination).
function computeConfigHtml(interactive) {
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
         <label class="check"><input type="radio" name="cl-sizemode" class="cl-sizemode" value="autoscale" checked/> autoscale</label>
         <label class="check"><input type="radio" name="cl-sizemode" class="cl-sizemode" value="fixed"/> fixed</label>
       </div>
       <div class="cl-autoscale"><div class="row">
         <div>${L("Min workers", "")}<input type="number" min="0" class="cl-min" value="1" /></div>
         <div>${L("Max workers", "Autoscale ceiling; capped by policy.")}<input type="number" min="1" class="cl-max" value="4" /></div>
       </div></div>
       <div class="cl-fixed hidden">${L("Workers", "0 = single node.")}<input type="number" min="0" class="cl-workers" value="2" /></div>`
    : `${L("Workers", "Fixed worker count for the job cluster (0 = single node).")}<input type="number" min="0" class="cl-workers" value="2" />`;
  return `
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

async function previewCost() {
  const out = document.getElementById("cost-out");
  try {
    const r = await api("/api/finops/estimate", { method: "POST",
      body: JSON.stringify({ resources: collectResources() }) });
    out.innerHTML = `Est. <b>$${r.estimated_monthly}/mo</b>` +
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
      (d._rationale || []).map(x => `<span class="kv">${x.replace(/\*\*/g, "")}</span>`).join("");
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
    errBox.innerHTML = `<div class="errors"><b>Request blocked (${e.status})</b><ul>${errs.map(x => `<li>${x}</li>`).join("")}</ul></div>`;
    showStep(0);
  }
}

// ================================================================== APPROVALS
async function renderApprovals() {
  const v = document.getElementById("view-approvals");
  v.innerHTML = `<h1>Approval queue</h1><p class="sub">Risk-tiered gates. Tier-2 (restricted/GxP/PHI/prod) needs two distinct approvers + compliance.</p>`;
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
    c.innerHTML = `
      <div class="flex">
        <h3>${r.use_case_name || r.project_name} <span class="mono muted">${r.project_id}</span></h3>
        <div class="right">${tierPill(r.risk_tier)} <span class="pill">${approves}/${r.required_approvals} approvals</span></div>
      </div>
      <div class="muted" style="font-size:12px">${r.requester} · ${[r.business_domain, r.business_function, r.business_sub_function].filter(Boolean).join(" › ")} · ${r.data_classification} · ${r.environment} · CC ${r.cost_center}</div>
      ${r.business_owner ? `<div class="muted" style="font-size:12px">Business owner: ${r.business_owner}${r.project_name && r.use_case_name ? ` · asset: ${r.project_name}` : ""}</div>` : ""}
      <p style="font-size:13px">${r.justification || ""}</p>
      <div class="tagset">${(r.resources || []).map(x => `<span class="kv">${x.type}</span>`).join("")}</div>
      ${wafPanel((r.metadata || {}).waf)}
      <div class="row" style="margin-top:10px">
        <input class="esig" placeholder="Type your full name (e-signature)" />
        <button class="btn small approve">Approve &amp; sign</button>
        <button class="btn ghost small reject">Reject</button>
      </div>`;
    const sig = () => c.querySelector(".esig").value.trim();
    c.querySelector(".approve").onclick = () => decide(r.id, "approve", sig());
    c.querySelector(".reject").onclick = () => decide(r.id, "reject", sig());
    v.appendChild(c);
  });
  if (highlightCard) {
    highlightCard.scrollIntoView({ behavior: "smooth", block: "center" });
    _deepLinkRequestId = "";   // consume once so a later manual visit doesn't re-highlight
  }
}

async function decide(rid, decision, esignature) {
  if (!esignature) { toast("An e-signature is required", false); return; }
  try {
    const r = await api(`/api/approvals/${rid}/decision`, {
      method: "POST", body: JSON.stringify({ decision, reason: decision, esignature }),
    });
    toast(`${decision}: ${r.status}` + (r.provisioning ? " — provisioning triggered" : ""));
    setTimeout(renderApprovals, 600);
  } catch (e) { toast((e.body && e.body.error) || "decision failed", false); }
}

// =================================================================== REGISTRY
async function renderRegistry() {
  const v = document.getElementById("view-registry");
  v.innerHTML = `<h1>Asset &amp; ownership registry</h1><p class="sub">The CMDB of vended resources. Ownership is by reference — reassign and tags follow.</p>`;
  const assets = await api("/api/assets");
  const t = el("table");
  t.innerHTML = `<thead><tr><th>Type</th><th>Mode</th><th>Handle</th><th>Owner</th><th>Project</th><th>Request</th><th>Tags</th></tr></thead>`;
  const tb = el("tbody");
  assets.forEach(a => {
    const tr = el("tr");
    tr.innerHTML = `<td><b>${a.type}</b></td><td>${modePill(a.mode)}</td>
      <td class="mono">${a.external_id || ""}</td><td>${a.owner_id || ""}</td>
      <td class="mono">${a.project_id || ""}</td><td class="mono">${a.request_id || ""}</td>
      <td>${tagsHtml(a.applied_tags)}</td>`;
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  const card = el("div", { class: "card" }); card.appendChild(t);
  v.appendChild(card);

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
      toast(`Added ${r.created.length} resource(s) to ${rid}` + (r.failed.length ? `, ${r.failed.length} failed` : ""));
      renderRegistry();
    } catch (e) { toast((e.body && e.body.error) || "add resources failed", false); }
  };
  v.appendChild(lc);
}

// ================================================================ GOVERNANCE
async function renderGovernance() {
  const v = document.getElementById("view-governance");
  v.innerHTML = `<h1>Day-2 governance</h1><p class="sub">Sunset autopilot, tag-drift &amp; orphan sweep, and owner recertification — keeping vended resources healthy after provisioning.</p>`;
  const [sw, rc] = await Promise.all([api("/api/governance/sweep"), api("/api/governance/recertification")]);

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
      tr.innerHTML = `<td class="mono">${a.asset_id}</td><td>${a.type}</td><td>${a.sunset_date}</td>
        <td>${a.classification || ""}</td>
        <td>${restricted ? '<span class="pill tier2">controlled change</span>'
                         : `<button class="btn small reclaim" data-id="${a.asset_id}">Reclaim</button>`}</td>`;
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

  // Tag drift
  v.appendChild(el("div", { class: "section-title" }, "<h2>Tag drift</h2>"));
  const dc = el("div", { class: "card" });
  dc.innerHTML = sw.tag_drift.length ? sw.tag_drift.map(d =>
    `<div class="audit-ev"><span class="mono">${d.asset_id}</span> · coverage ${Math.round(d.coverage*100)}% · missing: ${d.missing.join(", ")}</div>`).join("")
    : `<p class="muted">All active assets at 100% required-tag coverage.</p>`;
  v.appendChild(dc);

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
      tr.innerHTML = `<td class="mono">${a.asset_id}</td><td>${a.type}</td><td>${a.owner_id||""}</td>
        <td>${a.age_days}d</td><td><button class="btn ghost small recert" data-id="${a.asset_id}">Attest still needed</button></td>`;
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

// ===================================================================== FINOPS
async function renderFinops() {
  const v = document.getElementById("view-finops");
  v.innerHTML = `<h1>FinOps &amp; Well-Architected</h1><p class="sub">Cost attribution from tags + ROI + live Well-Architected Lakehouse scorecard.</p>`;
  const [s, sc, imp, ai] = await Promise.all([
    api("/api/finops/summary"), api("/api/finops/scorecard"),
    api("/api/finops/impact"), api("/api/finops/ai")]);

  // ROI banner — the days->minutes story
  const roi = el("div", { class: "grid cols-4" });
  roi.innerHTML = `
    <div class="card"><div class="muted">Tickets eliminated</div><div class="kpi good">${imp.tickets_eliminated}</div>
      <div class="muted" style="font-size:11px">vs ${imp.manual_baseline_days}-day ServiceNow baseline</div></div>
    <div class="card"><div class="muted">Engineer-days saved</div><div class="kpi good">${imp.engineer_days_saved}</div></div>
    <div class="card"><div class="muted">Cost avoided</div><div class="kpi good">$${imp.dollars_saved.toLocaleString()}</div></div>
    <div class="card"><div class="muted">Speed-up</div><div class="kpi">${imp.speedup_x.toLocaleString()}×</div>
      <div class="muted" style="font-size:11px">days → ~${imp.pave_minutes} min</div></div>`;
  v.appendChild(el("div", { class: "section-title" }, "<h2>Business impact (days → minutes)</h2>"));
  v.appendChild(roi);

  const kpis = el("div", { class: "grid cols-4" });
  const covClass = s.tag_coverage_pct >= 95 ? "good" : "warn";
  kpis.innerHTML = `
    <div class="card"><div class="muted">Est. monthly cost</div><div class="kpi">$${s.total_estimated_monthly}</div></div>
    <div class="card"><div class="muted">Active assets</div><div class="kpi">${s.active_assets}</div></div>
    <div class="card"><div class="muted">Tag coverage</div><div class="kpi ${covClass}">${s.tag_coverage_pct}%</div>
      <div class="bar"><span style="width:${s.tag_coverage_pct}%"></span></div></div>
    <div class="card"><div class="muted">Untagged cost</div><div class="kpi ${s.untagged_cost ? 'warn' : 'good'}">$${s.untagged_cost}</div></div>`;
  v.appendChild(kpis);

  v.appendChild(el("div", { class: "section-title" }, "<h2>Attribution completeness</h2>"));
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
      <tbody>${teams.map(([t, r]) => `<tr><td>${t}</td><td>${r.endpoints}</td><td>$${r.est_spend}</td>
        <td>${r.budget ? '$' + r.budget : '—'}</td>
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
    c.innerHTML = `<div class="flex"><h3>${p.pillar}</h3><span class="right kpi ${cls}" style="font-size:20px">${p.score}</span></div>
      <div class="bar"><span style="width:${p.score}%"></span></div>
      <div class="tagset" style="margin-top:8px">${p.controls.map(x => `<span class="kv">${x}</span>`).join("")}</div>
      <p class="muted" style="font-size:12px;margin-top:8px">${findings}</p>`;
    pg.appendChild(c);
  });
  v.appendChild(pg);
}
function costCard(title, obj) {
  const entries = Object.entries(obj || {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(e => e[1]));
  const c = el("div", { class: "card" });
  c.innerHTML = `<h3>${title}</h3>` + (entries.length ? entries.map(([k, val]) =>
    `<div style="margin:6px 0"><div class="flex"><span class="mono">${k}</span><span class="right">$${val}</span></div>
     <div class="bar"><span style="width:${100 * val / max}%"></span></div></div>`).join("") : `<p class="muted">No data yet.</p>`);
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
    `<span class="kv" title="${d.pillar}">✓ ${d.key} = ${JSON.stringify(d.value)}</span>`).join("")}</div>`;
  if (find.length) html += `<div class="tagset" style="margin-top:6px">${find.map(f =>
    `<span class="pill tier2" title="${(f.remediation || '').replace(/"/g, "'")}">⚠ ${f.rule_id}: ${f.title}</span>`).join("")}</div>`;
  if (waived.length) html += `<div class="tagset" style="margin-top:6px">${waived.map(w =>
    `<span class="pill" title="${(w.justification || '').replace(/"/g, "'")}">waived: ${w.rule_id}</span>`).join("")}</div>`;
  return html + `</div>`;
}

// ====================================================================== SHELL
function switchView(name) {
  document.querySelectorAll(".view").forEach(v => v.classList.add("hidden"));
  document.getElementById(`view-${name}`).classList.remove("hidden");
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.view === name));
  ({ intake: renderIntake, approvals: renderApprovals, registry: renderRegistry,
     governance: renderGovernance, finops: renderFinops }[name])();
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
    renderIntake();
    handleDeepLink();   // honor #approvals/{id} from an approval email
  } catch (e) {
    document.getElementById("view-intake").innerHTML = `<div class="errors">Failed to load PAVE metadata.</div>`;
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
