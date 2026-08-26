# All Things Agentic Hackathon — Build Blueprint

**Category:** Fortified Enterprise Fleet (locked)
**Project (working title):** CloudCap — a governed multi-agent Cloud Cost & Security Governance fleet
**Target prize:** Fortified Enterprise Fleet category ($20K + $2K credits) — safe win; positioned to reach for Best Architectural Design ($5K) and Grand Prize.
**Deadline:** Aug 31, 2026, 5:00pm PDT. **Credits deadline: Aug 28, 12:00pm PT (hard).**

---

## 1. Why this wins
- **Stage One is a pass/fail gate** requiring ALL seven GEAP pillars. Most entrants fail it → small scoreable field. Our edge (Cloud/DevOps/architecture) clears it cleanly.
- Category rubric = the Gemini Enterprise Agent Platform feature list. We build ON the platform, so mandatory stack (Gemini 3.5+, Google agent framework, Google Cloud infra) is satisfied for free.
- Business-ROI narrative ("found $X of waste + N misconfigs, live") matches what past Google hackathon winners had.

## 2. Fleet roster → mapped 1:1 to the seven mandatory pillars
| Agent | Job | Real data source |
|---|---|---|
| Orchestrator/Supervisor | Schedules scans, routes work, aggregates | — |
| Cost Scanner | Idle/rightsizing/waste | Cloud Billing API + Recommender/Active Assist |
| Security Posture | Public buckets, open firewalls, misconfig | Security Command Center / Asset Inventory |
| IAM/Access | Over-privilege, unused permissions | IAM Recommender + Policy Analyzer |
| Compliance & Sovereignty | Region/residency policy, PII/secret exposure | Asset Inventory + log scan (Model Armor DLP) |
| Remediation (gated write) | Proposes guarded fixes, opens PRs/tickets | — |
| Reporter | Exec summary + audit report | — |

### Seven pillars — each load-bearing
1. **Agent Registry (GEAP):** all agents published + versioned, discoverable cross-department (FinOps / SecOps / dept engineers).
2. **Agent Runtime (Agent Engine):** agents deployed as long-running async background scanners.
3. **Memory Bank (Vertex AI):** per-project finding history, dismissed items, remediation state over weeks (memory scoped to project/dept identity).
4. **Agent Identity (IAM):** each agent a least-privilege service account — scanners read-only; remediation gated write.
5. **Agent Gateway (GEAP):** routes every agent→data / agent→agent call; enforces policy (read-only, in-region/data-sovereignty).
6. **Model Armor:** blocks tool-poisoning via malicious resource tags/metadata, PII leakage, and Memory Bank poisoning.
7. **Agent Observability (OpenTelemetry + Cloud Trace):** reasoning-chain traces + audit logs for every decision.

## 3. Data & correctness strategy
- **Terraform "chaos" project:** seed a small real GCP project with known planted issues (idle VM, unattached disk, public bucket, over-privileged SA, unused static IPs, oversized Cloud SQL) → **ground truth**.
- Fleet reads **real GCP APIs** (Billing, Recommender, Asset Inventory, IAM Recommender, SCC) through Agent Gateway with scoped identity.
- **Eval harness:** compare fleet findings vs. planted ground truth → precision/recall + "$ waste identified". Turns the demo into a quantified, verifiable claim (nails Demo & Production Readiness 30%).
- "Weeks of async context": pre-seed Memory Bank with prior weeks of findings; demo shows live *recall* (time-compressed).

## 4. 13-day plan (Aug 18 → Aug 31)
- **D1–2:** GCP project; **request $150 credits (before Aug 28)**; enable Agent Engine / GEAP / Model Armor; Terraform chaos env.
- **D3–5:** Build core scanners in ADK + Gemini 3.5; wire to real GCP APIs; verify findings vs ground truth.
- **D6–7:** Deploy on Agent Runtime; wire Memory Bank; publish agents to Registry (versioned).
- **D8–9:** Governance layer — Agent Gateway routing + policy, per-agent Identity, Model Armor guardrails (incl. poisoned-tag attack).
- **D10:** Observability — OTel traces + audit dashboard.
- **D11:** Eval harness + ground-truth scorecard; polish remediation flow.
- **D12:** Record 4-min unedited demo; architecture diagram; README with spin-up steps.
- **D13:** Buffer + bonus (blog post, #AllThingsAgenticHackathon social, optional Gemma model = up to +0.6). Submit early.

## 5. Demo script (≤4 min, must visibly hit all seven pillars)
- 0:00–0:30 — Problem + show **Registry** catalog (cross-dept agents, versioned).
- 0:30–1:30 — Trigger fleet on the real project via **Gateway**; show per-agent **Identity** scoping; agents scan live on real data (**Runtime**).
- 1:30–2:15 — **Model Armor** blocks a poisoned resource tag + redacts PII, live.
- 2:15–3:00 — **Memory Bank** recall: "here's what we found last week" (weeks of context, compressed).
- 3:00–3:30 — Remediation agent opens a **GitOps Pull Request** against the IaC repo with a **deterministic proof graph** attached + **eval scorecard** (e.g., found 11/12 planted issues, $340/mo waste).
- 3:30–4:00 — **Observability**: OTel reasoning-chain trace + audit log.

## 6. Open risks to manage
- Over-scoping seven pillars in 13 days → mitigate by using managed products (don't build from scratch) and demoing narrow-but-complete.
- Model Armor/PII fit → strengthened via poisoned-tag tool-poisoning demo + log PII scan.
- SCC/premium tiers may exceed credits → fall back to free Recommender + Asset Inventory + IAM Recommender.
- Credits deadline Aug 28 — request Day 1.

---

## 7. Architecture principles (portability + real-vs-concept)

**These are REAL cloud-native agents, not a concept.** Stage One requires a backend running on Google Cloud; the demo must show live, unedited execution. Agents actually deploy to Agent Runtime, call real GCP APIs, persist to Memory Bank, and are guarded live by Model Armor. The only compression is time: "weeks of async context" is demonstrated with *real, pre-seeded* Memory Bank history recalled live — genuine persistence, not a faked deployment.

**Hexagonal (ports & adapters) design — portability as a byproduct of the winning architecture.**
- **Agent logic layer** (orchestration, scanners, reasoning) is written in **ADK** — open-source, model-flexible, deployable to Agent Engine / Cloud Run / GKE / local. Fully portable.
- **Platform/governance layer** (Memory, Guardrails, Gateway, Registry, Identity) is reached only through thin **ports** (interfaces). Hackathon ships ONE implementation — Google GEAP. Each port has an off-Google analogue (generic memory store, NeMo/Llama-Guard, Kong/Apigee, generic registry) that could be swapped behind the same interface.
- Why it wins: Architecture is 30% and rewards "system decoupling + state management." The seams give a senior-architect answer to "isn't this Google-locked?" → *governance moat is Google by design; agent logic runs anywhere.*
- Guardrail: build only the Google adapters now. Do NOT build multiple backends — structure for swappability, implement once.

## 8. Distribution, install & UX

**Install = Terraform into the customer's own GCP org** (`terraform/fleet/`): enables APIs, creates per-agent least-privilege SAs (Identity), deploys agents to Runtime, publishes to Registry, wires Memory Bank + Gateway policies + Model Armor, sets up OTel. One `terraform apply`. Data stays in the customer cloud → data-sovereignty story.

**Three surfaces:**
1. **Install** — Terraform / IaC (above).
2. **Discovery** — agents appear in the org's **Agent Registry** (cross-department "app store"); business persona finds and runs them.
3. **Interaction** — thin **dashboard** (Cloud Run web app): fleet status, findings, eval scorecard, Memory Bank recall, live OTel traces (also the Best Multimodal UX play). **Alerts** via email/Chat; **remediation gated by human approve/reject** (Gateway policy + Identity). Reporter agent emails weekly exec summary.

Two separate Terraform modules: `chaos-env/` (test data with planted ground-truth) vs. `fleet/` (product installer).

## 9. Repo layout
```
AllAgent/
  BLUEPRINT.md
  README.md                     # install + spin-up (submission requirement)
  terraform/
    chaos-env/                  # seeds planted-issue test env (OUR ground truth)
    fleet/                      # product installer (customer-side deploy)
  agents/
    ports/interfaces.py         # hexagonal ports (portability seams)
    adapters/google_geap.py     # Google GEAP implementations
    orchestrator/agent.py       # ADK supervisor + sub-agent wiring
    scanners/cost_scanner.py    # first scanner (Billing + Recommender)
    remediation/agent.py        # GitOps: opens PRs with proof (no cloud write)
```

---

## 10. Trust-Gap strategy (market-validated) — GitOps remediation + deterministic proof

The 2026 market is "trust-starved": FinOps agents (Vantage, Amnic, AWS FinOps Agent)
stay read-only because security teams won't grant write access; security-remediation
tools (Dazz, Torq) ignore architectural efficiency. The winner closes the **Trust Gap**.
Our wedge:

1. **Read-only analytics** — the fleet only ingests billing, utilization metrics, IAM.
2. **Deterministic proof** — a recommendation attaches hard evidence (e.g., 90-day
   utilization time-series), NOT LLM prose. The model explains the proof; it does not
   assert the conclusion. (Demo caveat: a 13-day env lacks 90-day history → attach
   Recommender's deterministic evidence + available metrics; state the prod window
   honestly. Never fake a 90-day graph.)
3. **GitOps approval** — remediation agent has **zero cloud write access**. It branches
   the IaC repo, edits the Terraform/K8s manifest, and opens a **Pull Request**. The
   engineer reviews a diff, not a black box. GitHub/GitLab PR UI = the approval surface.
4. **Semantic policy guardrail** — a policy-as-code check (availability, compliance,
   blast-radius) evaluates the PR before a human sees it (→ Agent Gateway pillar).

Demo loop (self-contained + verifiable): chaos-env Terraform *creates* problems →
fleet *detects* on live resources → remediation opens a PR *against that Terraform*
to fix them → merge → `terraform apply` → fleet confirms resolution from Memory Bank.

**GTM framing:** lead with the **Cloud Waste / efficiency wedge** (pays for itself;
VP Eng / CFO buyer); present **security/IAM drift as a secondary flag** enabled by the
same read access (CISO buyer). Do not pitch a confusing do-everything platform.

**Identity impact:** remediation agent gets git credentials only — the strongest
zero-trust posture (no infra mutation is even possible by an agent).

### 10.1 Remediation delivery — graceful degradation (brownfield reality)

We must NOT assume clean Terraform we can see. Remediation is a **pluggable channel**
chosen after classifying each resource's management source (Infrastructure Manager /
Config Connector annotations, `managed-by` labels, Terraform state if access granted,
else unmanaged/drift). Channels, best → fallback (all human-gated, proof-attached,
never mutate cloud directly):

| Situation | Channel |
|---|---|
| Terraform repo + access | Open PR editing the `.tf` (primary; matches demo) |
| GKE / Kubernetes | PR against manifests/Helm (Argo/Flux) — GitOps strongest here |
| Non-TF IaC (Pulumi/CFN/CDK/Bicep) | PR via language adapter |
| IaC but no repo access / unknown tool | Ticket (Jira/ServiceNow) or patch/script artifact |
| ClickOps / not codified | **Codify-then-PR**: reverse to IaC (`gcloud export`/`terraform import`) + fix — turns drift into IaC adoption |

**Hard floor:** with no IaC and no access, degrade to **read-only insights + proof**
(what Vantage/Amnic/AWS FinOps Agent sell today). The product is never broken by
missing IaC; remediation is the upsell.

**Scope:** ship Terraform-PR + one fallback (ticket/artifact) to prove graceful
degradation on camera; Pulumi/CFN/codify-ClickOps are roadmap adapters.

**Install input (not assumption):** repo + state access and channel preference are
configured at install time. Access → PRs; no access → tickets/artifacts.

Architecture: it's another port (`RemediationChannelPort`) with adapters — same
hexagonal pattern, demonstrates failure-tolerant decoupled design (Architecture 30%).

### 10.2 Production hardening (stress-test fixes)

Three real-world edge cases and the decisions we bake in:

1. **`terraform import` into nested/Terragrunt repos is brutal.** Do NOT reverse-engineer
   the customer's module structure. The remediation agent writes a FLAT
   `cloudcap_clickops_recovery.tf` at repo ROOT, runs the state import, opens the PR;
   humans refactor into modules post-merge. (`agents/remediation/agent.py:codify_clickops_resource`)
2. **Audit-log masking / service-account problem.** If a CI/CD pipeline or Workload
   Identity SA created the resource, `principalEmail` is a generic SA, not a human.
   The classifier must **traverse the assumption chain** — correlate the creation
   timestamp with WIF federation logs + CI trigger logs (build id → commit → author)
   to find the real actor. `Attribution` now carries `principal_type`,
   `triggering_entity`, `attribution_confidence`. Never stop at the SA.
   (`adapters/google_geap.py:AuditLogClassifierAdapter._resolve_assumption_chain`)
3. **Quarantine physics differ per resource type.** "Stop + snapshot" is compute-only.
   Per-type reversible-first primitives (`QUARANTINE_PRIMITIVES`): DB → final backup then
   terminate; **bucket → strip public IAM + attach deny-all, NEVER delete the data**;
   SA → disable + remove bindings; disk → snapshot then delete; static IP → release.
   (`agents/remediation/agent.py`)

### 10.3 GTM / sales motion
- **Spearhead with the Cost Agent** (CFO / VP Eng): waste is cash-in-hand and pays for
  the product day one; security risk is insurance (secondary).
- **The Planted-Issues demo IS the sales motion.** Hand the prospect a Terraform sandbox
  with ~12 obfuscated "sins" (unattached disk, over-provisioned node pool, ClickOps VM),
  unleash CloudCap, watch it open PRs with deterministic proofs. Undeniable PoV —
  same harness as our hackathon eval (`terraform/chaos-env` + eval scorecard).

## 11. Deployment model & multi-cloud roadmap
- **GCP-native, always** — control plane deploys on GCP (leverages GEAP); other clouds are audited, never the deploy target.
- **ICP:** orgs that have GCP (GCP-native or multi-cloud incl. GCP). Lead with the **multi-cloud unified-governance** wedge (native single-cloud tools each see only their own cloud).
- **Not near-term ICP:** AWS-only / Azure-only shops. Future: small dedicated GCP "governance project" as the brain, or hosted SaaS.
- **What's next:** AWS + Azure as adapter packs behind the same ports (~70–80% cloud-agnostic; IaC-ownership + GitOps remediation already cross-cloud via Terraform state). See docs/architecture.md §5.
- **UI:** show AWS/Azure only as a clearly-labeled greyed "coming soon" indicator (never as working functionality — nothing faked).
