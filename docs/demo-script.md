# CloudCap — Demo Video Script, Screenplay & Recording Guide

**Hackathon:** All Things Agentic (Google · Devpost) · **Category:** The Fortified Enterprise Fleet
**Video rules:** ≤ 4:00, publicly on YouTube/Vimeo, English (or subtitled), **live/unedited
execution**, and it **must show the backend running on Google Cloud** (Cloud Run dashboard /
Vertex logs / `.run` URL). Only the first 4 minutes are judged. Keep total **≤ 3:55**.

**What the judges score:** Innovation & Operational Utility 40% · Architectural Discipline &
Tech Stack 30% · Demo & Production Readiness 30%. This script is built to hit all three.

**GEAP pillar legend:** REG Registry · MEM Memory · ID Identity · GW Gateway ·
MA Model Armor · OBS Observability · OWN Ownership.

---

## 0. The one-line pitch (memorize it)

> "CloudCap is a governed multi-agent fleet on Google Cloud that scans your **live cloud** for
> cost, security, and compliance risk — catching the drift and ClickOps that code-only IaC
> scanners miss — proves each finding, and **fixes it by Pull Request back into your Terraform**.
> Gemini reasoning, Model Armor guardrails, full audit trail."

**The wedge (say it once, clearly):** *"IaC scanners read your code. But people change things
directly in the cloud console — and those changes are invisible to a code audit. CloudCap watches
the live cloud, so it catches them, then reconciles the fix back into IaC."*

---

## 1. Positioning — what CloudCap is (and is not)

- **A cloud scanner.** Detection is 100% from the **running cloud**: live bucket IAM, IAM
  policies, Recommender, Asset Inventory, real Terraform *state* in GCS. It never greps source
  code to *find* a problem.
- **Fixes flow to IaC.** Once a live finding is detected, CloudCap resolves the Terraform repo
  that *owns* the resource (from state) and opens a **Pull Request** there with the fix. Reading
  the repo happens only to *write the fix*, never to detect.
- **Not** an application/code (SAST) scanner. That's a deliberate boundary — the cloud is the
  source of truth for what's actually running and exposed.

---

## 2. The recorded flow (this is the take)

You film the whole thing **live**, starting from a clean install. The hub infra is proven
working; you reset app state to a fresh onboarding so the "first scan" populates on camera.

1. **Install from the repo (MacBook terminal).** Copy the exact commands from the README:
   clone → `terraform apply` in `terraform/fleet`. Terraform prints the **Cloud Run URL**.
   *(The image is already built + pushed, and the infra already exists, so `apply` completes in
   ~30–60s — no waiting for a from-scratch build on camera.)*
2. **Open the Cloud Run URL.** "This is a real `.run.app` URL — you'd map it to your own domain."
3. **Sign in — Firebase (Google).** "Auth is Firebase; I'm the admin. Add a teammate's email to
   the admin allowlist and they're in." *(No passwords — Google sign-in.)*
4. **Onboard → discover projects.** The wizard lists your real GCP projects; select the two we
   govern: `cc-chaos-fffbba` (a sandbox, bound to a Git repo for fixes) and `kitearc-prod` (real
   production — **cost-only, and under a change-freeze**).
5. **Hub → Components.** Six agents, each its own least-privilege SA, every GEAP pillar **LIVE**.
6. **Run scan (live).** One read-only pass across both projects. **You will see a green
   "Scan complete" banner** on the board when it finishes (~2–2.5 min — freeze the frame and
   resume on the banner; there's no other thing to wait on).
7. **Walk the findings** (below): the cloud→IaC PR, the change-freeze, Model Armor,
   ClickOps/ownership, the kitearc cost win, compliance, and the audit trail.
8. **Google Cloud Console** proof shot.

---

## 3. The screenplay (≤ 4:00, shot-by-shot)

The **Say** column is the exact narration from the VO script (§3b / `VO_script`) — pair each line
with the on-screen action beside it. Times are intentionally omitted; pace to the VO + the pause
points in §3b, not to a clock.

| On screen / action (what to show + where to point) | Say (narration — verbatim VO) | Pillars · GCP proof |
|---|---|---|
| Title card "CloudCap — governed agent fleet for GCP". Then the repo `README.md` and **`docs/architecture-system.png`** (the architecture diagram — a submission requirement). Optionally flash a real Console tab on the public bucket to ground "a bucket goes public." | "Every cloud quietly drifts — a bucket goes public in the console, an account gets over-privileged, a machine sits idle. Code scanners never see it, because it never touched the code. CloudCap watches the live cloud, proves what it finds, and fixes it by Pull Request." | — |
| **Terminal (MacBook):** `git clone …/cloudcap`, `cd terraform/fleet`, `terraform apply`. It prints `dashboard_url = https://…run.app` — open it. **[ID]** the apply creates the six per-agent service accounts + `cc-runtime`; **[Cloud Run + Firestore]** it stands up the hub. The live **`.run.app` URL is your backend-on-GCP proof**. | "The whole fleet is one Terraform apply to Cloud Run. It outputs its own URL — map it to any domain — and comes up self-onboarding, read-only against your projects." | ID · **Cloud Run URL** |
| **Firebase Google sign-in** as admin **[ID — Identity pillar / zero-trust]**. **Onboarding → Discover** lists your real GCP projects (Cloud Resource Manager). Select `cc-chaos-fffbba` + `kitearc-prod`; on the cc-chaos card confirm its **repo** and paste the **Slack webhook**; optionally set kitearc's **change-freeze**. | "Sign in with Firebase — I'm the admin. It discovers my real GCP projects. I'll govern two: a sandbox wired to a Git repo, and my production project — which I'm putting under a change-freeze." | ID |
| **Hub → Components** — the pillar showcase; point to each: the **Agent Registry** table, 6 agents each with an **identity-verified badge** (REG + ID); then the pillar status rows all reading **LIVE** — **Gateway**, **Model Armor**, **Observability (OTel→Cloud Trace)**, **Memory**, **Ownership**. One page proves Registry, Identity, Gateway, Model Armor, Observability, Memory, and Ownership are live — not mocked. | "The fleet: six agents — an orchestrator and five scanners — each with its own least-privilege identity, verified live against Google Cloud IAM. This is the full enterprise pattern: an Agent Registry that publishes and versions the approved fleet; an asynchronous Runtime that scans unattended on a schedule — no chat, no prompting; a Memory Bank, so known issues don't re-nag across scans; an Agent Gateway that policy-gates every action; Model Armor guarding the inputs; and OpenTelemetry observability tracing every decision. All live — not mocked." | REG, ID, GW, MA, OBS, OWN |
| Click **Run scan** **[GW — the Gateway brokers the read-only API calls]**. *(Freeze frame; resume on the green "Scan complete" banner.)* The board paints findings across both projects with the **Gemini executive summary** on top **[Gemini 3.7-flash on Vertex AI via the GenAI SDK — say "GenAI SDK"]**. | "One read-only scan of two real projects. Gemini 3.7 on Vertex AI ranks and narrates on top of deterministic evidence — it explains the findings, it never invents them." | GW, REG · **Vertex AI** |
| Open **"Publicly accessible Bucket (allUsers)"** (critical, `cc-chaos-fffbba`). In the finding, point to the **Ownership** row **[OWN — resolved from real GCS Terraform state]** → `mayurpawar/cc-chaos-infra` → `google_storage_bucket.public_bucket`. Click **View Pull Request** → the real GitHub PR removing the `allUsers` binding from `buckets.tf` **[GW brokers the fix; OWN routes it to the owning repo]**. | "This bucket is public to the entire internet — detected on the live cloud, right now. An IaC scanner would miss it if someone made it public by hand. CloudCap traces the resource to the Terraform that owns it — from real state in GCS — and opens a Pull Request that removes the public binding at the source. Nothing auto-merges; a human approves. That's the whole idea: detect on the cloud, fix in the code." | OWN, GW · **GCS state + GitHub PR** |
| Cut to **Slack** — a card per cc-chaos finding (the bucket card carries the PR link). Then the **JIRA / DEVPOST** board — the tickets filed. ~5s each **[GW — the per-project action policy fans delivery across channels]**. | "And detection is only half of it — the fleet acts. For every finding, across the channels your team already uses: a Pull Request for the fix, a Slack alert for the on-call, a JIRA ticket for the backlog — each governed by per-project policy. No one prompts it; it just acts." | GW · **Slack + JIRA** |
| Open a **`kitearc-prod`** finding: the **change-freeze** banner (if set) = detect-only, no PR **[MEM/GW — the freeze gates action]**. Then the **cost** finding (~$133/mo) — expand its detail to show the **<1% CPU utilization** evidence (Recommender + Cloud Monitoring) as you say "under one percent CPU." | "My production project is under a change-freeze — CloudCap still detects, but proposes no automated changes. And it found real waste: about $133 a month in idle spend, ready to reclaim. And it proves it — those Cloud Run instances are held always-on but running at under one percent CPU. No real traffic is reaching them; you're just paying to keep them warm." | GW, MEM · **Recommender** |
| Open **"Tool-poisoning attempt blocked by Model Armor"** **[MA]**. Show the detail — a bucket object *named* as a prompt injection was screened by the real Model Armor template (`cloudcap-guard`) and turned into a finding instead of executing **[MA + OBS: the block is audited]**. | "One object in a public bucket was named as a prompt injection — bait to hijack the agent. Model Armor caught it and turned the attack into a finding instead of executing it." | MA, OBS |
| In the Ownership column, contrast the **ClickOps** (unmanaged — in no Terraform state) resource vs the managed bucket **[OWN]**. Then **Compliance** — SOC 2 / CIS GCP / ISO 27001 / PCI DSS posture, with failing controls **linked back to the findings** **[OBS — the evidence trail]**. | "Every resource is traced to its owner — or flagged as ClickOps when it's in no state at all. And every finding maps to real controls across four frameworks." | OWN, OBS |
| **Google Cloud Console (the required backend-on-GCP proof), tab by tab:** **Cloud Run** `cloudcap` (green, `.run` URL) → **Vertex AI** (Gemini) → **Firestore** (state/memory) → **Model Armor** template `cloudcap-guard` → **Cloud Scheduler** `cloudcap-daily-scan` (the unattended background runtime) → **IAM → Service Accounts** (the six agents) → **Cloud Logging** `cloudcap-audit` (hash-chained, **OBS**). | "All on Google Cloud: Cloud Run for the fleet, Vertex AI for Gemini, Firestore for state, Model Armor for the guardrail, and an immutable Cloud Logging trace of every action." | **GCP deployment proof (required)** · OBS |
| Board headline (findings + severities) → tagline card. | "CloudCap — watch the live cloud, prove the risk, fix it by Pull Request. A governed agent fleet, safe enough to point at production." | — |

---

## 3b. Continuous VO script (paste into ElevenLabs — pure narration, no cues)

This is the **exact narration** from the shot table above (§3), assembled as one block for
text-to-speech (identical to `AA_Vid/VO_script`). Paste it into ElevenLabs as one piece. The VO
does **not** need to fill the full 4:00 — hold **deliberate pauses** so viewers absorb the screen.

**Pause points (stop talking, let the screen breathe):** ① while the **scan runs** → hold on the
green "Scan complete" banner; ② right after the **Pull Request opens** — linger on the diff
removing `allUsers`; ③ during the **Slack + JIRA** reveal; ④ on the **Google Cloud Console** shot.

> Every cloud quietly drifts — a bucket goes public in the console, an account gets
> over-privileged, a machine sits idle. Code scanners never see it, because it never touched the
> code. CloudCap watches the live cloud, proves what it finds, and fixes it by Pull Request.
>
> The whole fleet is one Terraform apply to Cloud Run. It outputs its own URL — map it to any
> domain — and comes up self-onboarding, read-only against your projects.
>
> Sign in with Firebase — I'm the admin. It discovers my real GCP projects. I'll govern two: a
> sandbox wired to a Git repo, and my production project — which I'm putting under a change-freeze.
>
> The fleet: six agents — an orchestrator and five scanners — each with its own least-privilege
> identity, verified live against Google Cloud IAM. This is the full enterprise pattern: an Agent
> Registry that publishes and versions the approved fleet; an asynchronous Runtime that scans
> unattended on a schedule — no chat, no prompting; a Memory Bank, so known issues don't re-nag
> across scans; an Agent Gateway that policy-gates every action; Model Armor guarding the inputs;
> and OpenTelemetry observability tracing every decision. All live — not mocked.
>
> One read-only scan of two real projects. Gemini 3.7 on Vertex AI ranks and narrates on top of
> deterministic evidence — it explains the findings, it never invents them.
>
> This bucket is public to the entire internet — detected on the live cloud, right now. An IaC
> scanner would miss it if someone made it public by hand. CloudCap traces the resource to the
> Terraform that owns it — from real state in GCS — and opens a Pull Request that removes the
> public binding at the source. Nothing auto-merges; a human approves. That's the whole idea:
> detect on the cloud, fix in the code.
>
> And detection is only half of it — the fleet acts. For every finding, across the channels your
> team already uses: a Pull Request for the fix, a Slack alert for the on-call, a JIRA ticket for
> the backlog — each governed by per-project policy. No one prompts it; it just acts.
>
> My production project is under a change-freeze — CloudCap still detects, but proposes no
> automated changes. And it found real waste: about $133 a month in idle spend, ready to reclaim.
> And it proves it — those Cloud Run instances are held always-on but running at under one percent
> CPU. No real traffic is reaching them; you're just paying to keep them warm.
>
> One object in a public bucket was named as a prompt injection — bait to hijack the agent. Model
> Armor caught it and turned the attack into a finding instead of executing it.
>
> Every resource is traced to its owner — or flagged as ClickOps when it's in no state at all.
> And every finding maps to real controls across four frameworks.
>
> All on Google Cloud: Cloud Run for the fleet, Vertex AI for Gemini, Firestore for state, Model
> Armor for the guardrail, and an immutable Cloud Logging trace of every action.
>
> CloudCap — watch the live cloud, prove the risk, fix it by Pull Request. A governed agent fleet,
> safe enough to point at production.

---

## 3c. Agentic fit — how CloudCap maps to the judging criteria

Use this in the Devpost write-up and to sanity-check the video hits every rubric line.

**Innovation & Operational Utility — 40% (the big one; theme = autonomous background action):**
- **Background & unattended.** CloudCap runs as an **unattended fleet**, not a chatbot — no one
  prompts it; it scans and acts on its own. A **Cloud Scheduler job** (`cloudcap-daily-scan`,
  `0 6 * * *`) triggers `agents/scan.py` daily via an OIDC-authed endpoint — so "runs daily in
  the background, autonomously" is literally true and shown in the Console proof shot.
- **Autonomous decisions, no hand-holding.** Per finding it decides severity, ranks priority,
  resolves the owning IaC repo from Terraform state, drafts the exact fix, and routes it
  (PR vs Issue vs report vs suppress-under-freeze) — entirely on its own. Human touch = **one**
  merge approval.
- **Massive dataset / heavy lifting.** It sweeps the **entire cloud estate** — Cloud Asset
  Inventory + Recommender + IAM across every resource in every project — surfacing the handful
  that matter. That's the "heavy lifting of massive datasets" line, literally.
- **Real friction removed.** Reclaims idle spend, closes public-exposure, generates audit
  evidence — quantified value, not a demo toy.

**Architectural Discipline & Tech Stack — 30%:**
- **Decoupled** — hexagonal ports/adapters; one mock/live switch (`agents/context.py`).
- **State & memory** — Firestore-backed finding lifecycle (known issues don't re-nag) + hash-
  chained Cloud Logging audit.
- **Secured credentials** — per-agent least-privilege SAs; GitHub token in Secret Manager; the
  remediation agent has **no cloud write access** and can only propose.
- **Failure handling** — Model Armor fails *closed* (deterministic backstop); PRs never
  auto-merge; change-freeze + policy gates before any action.

**Demo & Production Readiness — 30%:**
- **Live, unedited** run from `terraform apply` → URL → login → scan → real PR.
- **Repro setup** (`INSTALL.md` Cloud Run section) · **architecture diagram**
  (`docs/architecture-system.png`) · **GCP proof** shot (Cloud Run / Vertex / Firestore).

---

## 4. Pillar & requirement coverage (all visible on camera)

Each pillar is now called out inline in the **On screen** column above. Quick checklist:
- **REG** Components registry table · **ID** deploy SAs + Firebase sign-in · **GW** Run scan +
  multi-channel delivery · **MA** Model Armor finding + Console template · **OBS** Components
  status rows + Cloud Logging audit · **OWN** bucket ownership (GCS state) + ClickOps contrast ·
  **MEM** change-freeze + finding lifecycle ("known issues don't re-nag") + the Memory status row.
- **Mandatory tech shown:** Gemini 3.7-flash / Vertex AI (scan summary), GenAI SDK (say it),
  Cloud Run + Firestore (deploy + Console). **Backend-on-GCP proof:** the Console shot. ✅

---

## 5. Live-PR path — already wired (v32)

The hub already has a `github-token` in Secret Manager and the `GITHUB_TOKEN` env enabled, so
**"Run scan" opens the public-bucket PR live**. Safety is enforced by three independent guards:
(1) `kitearc-prod` has **no repo bound** and every action disabled; (2) the ownership resolver
maps only to `cc-chaos-infra` (the sole tfstate source); (3) the PR channel opens PRs **only**
against a finding's explicitly-resolved `owner_repo` with concrete file changes. So a PR can only
ever land on `cc-chaos-infra`.

**Hardening (recommended before the public recording):** replace the broad token in
`github-token` with a **fine-grained PAT scoped to `cc-chaos-infra` only** (Contents +
Pull requests: Read/Write). Then it is *physically* impossible to touch any other repo. Update
the secret with: `gcloud secrets versions add github-token --data-file=-` and re-run the scan.

Re-scans are idempotent: the same fix branch reuses the existing open PR rather than opening
duplicates.

---

## 6. Honesty rules (never fake — judges check the repo)

- **Live in the video:** real scan (Recommender / Asset Inventory / live bucket IAM / IAM), real
  Gemini 3.7 on Vertex AI, real Model Armor block, a real GitHub PR, real Cloud Logging audit,
  real Terraform-state ownership from GCS. The Console shot is real.
- **Be precise:** `cc-chaos-fffbba` is a **purpose-built sandbox** with intentionally-planted
  issues — say so; it's controlled ground truth, not a customer. `kitearc-prod` is a **real**
  project shown **cost-only** and under a change-freeze (no changes are proposed to it).
- Detection is cloud-side; the PR only *writes* the fix into the owning repo. Don't describe it
  as "scanning the code for bugs."
- Don't claim dollar-precision or creator-attribution the tool doesn't compute. Stick to screen.

---

## 7. Post-production & submission checklist

- [ ] One clean take, ≤ 4:00, English/subtitled, uploaded **public** to YouTube/Vimeo.
- [ ] The Google Cloud Console / `.run` URL shot is unmistakable.
- [ ] Devpost: category **Fortified Enterprise Fleet**; text description (features, tech, data
      sources, learnings); **repo** `github.com/mayurpawar/cloudcap`; **README** spin-up;
      **architecture diagram** (`docs/architecture-system.png`); demo video link.
- [ ] Confirm eligibility: project built within the submission window.
- [ ] **Bonus (+0.4):** publish a build blog/video (+0.2) and one post with
      **#AllThingsAgenticHackathon** (+0.2). Optional +0.2 each (max +0.6) for Gemma/Veo/Lyria.

---

## Appendix — pre-flight checklist (do NOT film this)

1. **App state is fresh:** onboarding reset so the wizard shows; board empty until the on-camera
   scan. (Config — repo binding, policy, kitearc freeze + cost-only scope — is preserved.)
2. **Hub healthy (v32):** `https://cloudcap-lu3jp4b2ba-uc.a.run.app` responds; Google sign-in works.
3. **Chaos-env is live:** the public bucket, SAs, and VMs exist in `cc-chaos-fffbba` (the audited
   infra). The bucket is public *right now* (that's the live-detection point).
4. **kitearc-prod is locked:** no repo bound, all actions off, governance = cost only, freeze set.
5. **Second browser tab on Cloud Console** (Cloud Run → cloudcap), plus Vertex AI / Firestore /
   Model Armor / Cloud Logging tabs ready for the proof shot.
6. **Rehearse once** end-to-end; record 1080p+, one continuous take.
