# 16. Well-Architected Enforcement (How born-compliant defaults + scoring work)

How `well_architected.py` turns the 7 Well-Architected Lakehouse pillars from an after-the-fact
scorecard into **provisioning-time enforcement** — injecting safe defaults, blocking violations, and
scoring the rest.

```mermaid
flowchart TB
    req(["request + resource"]) --> rules["WAF rule set (data)<br/><i>each rule: pillar + severity + check/default</i>"]:::gov

    rules --> d["DEFAULT rules → apply_defaults()<br/><i>inject born-compliant config</i>"]:::ours
    rules --> h["HARD rules → checks<br/><i>violation = block</i>"]:::gov
    rules --> s["SOFT rules → checks<br/><i>violation = finding + score, waivable</i>"]:::ours

    d --> inj["Injected into provider config:<br/><i>autotermination · single-user (restricted) ·<br/>worker cap · LLM guardrails + budget · sunset</i>"]:::dbx
    h --> block{"blocking findings?"}
    block -->|yes| stop["reject before provisioning"]:::gov
    block -->|no| ok["proceed"]:::ours
    s --> score["per-pillar score<br/><i>7 pillars, waivers logged</i>"]:::ours

    inj --> saga["→ saga provisions with defaults baked in"]:::ours
    ok --> saga
    score --> card["FinOps/WAF scorecard<br/><i>/api/finops · SPA</i>"]:::ours

    classDef ours fill:#bfdbfe,stroke:#1d4ed8,color:#1f2937
    classDef dbx fill:#bbf7d0,stroke:#15803d,color:#1f2937
    classDef gov fill:#fecaca,stroke:#b91c1c,color:#1f2937
```

## How to read it

- **Rules are data.** Each `WafRule` carries a **pillar** (Governance, Cost, Security, Operational
  Excellence, Reliability, Performance, Interoperability), a **severity**, and either a `default`
  (inject) or a `check` (evaluate). The three severities do three different jobs:
  - **default** — `apply_defaults()` injects born-compliant config into the resource *before* the
    provider sees it: autotermination on compute, `single-user` access for restricted data, a worker
    cap, LLM PII/safety guardrails + budget cap, a sunset date.
  - **hard** — a violation that **blocks** (e.g. restricted data on shared compute). The request does
    not provision.
  - **soft** — a violation that **lowers a pillar score** and can be **waived** with a logged reason
    (e.g. no autotermination, missing backup owner, no budget cap).
- The scorecard is computed from these same rules against the real request — so the number a customer
  sees is genuine enforcement evidence, not a self-assessment.

## Key points

- **This is the "teaching tool" pillar of PAVE:** every resource is born already satisfying the
  defaults, and any gap is either blocked or an auditable, waived finding.
- The scope is deliberately **provisioning-time** controls only — runtime/consumer WAF items are out
  of scope for PAVE (it enforces what it can guarantee at birth).
- Waivers are recorded, so "we knowingly accepted this" is itself part of the audit trail.
