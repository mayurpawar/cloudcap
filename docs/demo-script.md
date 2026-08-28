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

| Time | On screen / action | Say (narration) | Pillars · GCP proof |
|---|---|---|---|
| **0:00–0:16** | Title card "CloudCap — governed agent fleet for GCP", then the repo README. | "Every cloud quietly drifts — a bucket goes public in the console, an account gets over-privileged, a machine sits idle. Code scanners never see it, because it never touched the code. CloudCap watches the **live cloud**, proves what it finds, and fixes it by Pull Request." | — |
| **0:16–0:44** | **Terminal:** `git clone …/cloudcap`, `cd terraform/fleet`, `terraform apply`. Terraform prints `dashboard_url = https://…run.app`. Open it. | "The whole fleet is one Terraform apply to Cloud Run. It outputs its own URL — map it to any domain — and comes up self-onboarding, read-only against your projects." | ID · **Cloud Run URL** |
| **0:44–1:05** | **Firebase Google sign-in** → **Onboarding**: discover projects, select `cc-chaos-fffbba` + `kitearc-prod`. | "Sign in with Firebase — I'm the admin. It discovers my real GCP projects. I'll govern two: a sandbox wired to a Git repo, and my production project — which I'm putting under a change-freeze." | ID |
| **1:05–1:28** | **Hub → Components.** 6-agent **Agent Registry** table (identity-verified badges); pillar rows all **LIVE**. | "The fleet: six agents, each its own least-privilege service account, verified live against Google Cloud IAM. Registry, Gateway, Model Armor, Observability, Ownership — all live, not mocked." | REG, ID, GW, MA, OBS, OWN |
| **1:28–1:50** | Click **Run scan**. *(Freeze frame; resume on the green "Scan complete" banner.)* Board paints findings across both projects; **Gemini executive summary** on top. | "One read-only scan of two real projects. Gemini 3.7 on Vertex AI ranks and narrates on top of deterministic evidence — it explains the findings, it never invents them." | GW, REG · **Vertex AI** |
| **1:50–2:35** | Open **"Publicly accessible Bucket (allUsers)"** (critical, `cc-chaos-fffbba`). Show **Ownership**: "Managed by `mayurpawar/cc-chaos-infra` → `google_storage_bucket.public_bucket`". Click **View Pull Request** → real GitHub PR removing the `allUsers` binding from `buckets.tf`. | "This bucket is public to the entire internet — detected on the **live cloud**, right now. An IaC scanner would miss it if someone made it public by hand. CloudCap traces the resource to the Terraform that owns it — from real state in GCS — and opens a **Pull Request** that removes the public binding at the source. Nothing auto-merges; a human approves. That's the whole idea: detect on the cloud, fix in the code." | OWN, GW · **GCS state + GitHub PR** |
| **2:35–2:55** | Open a **`kitearc-prod`** finding → show the **change-freeze** banner (detect-only, no PR). Then the **cost** finding: ~$133/mo idle spend. | "My production project is under a change-freeze — CloudCap still detects, but proposes **no** automated changes. And it found real waste: about $133 a month in idle spend, ready to reclaim." | GW, MEM · **Recommender** |
| **2:55–3:15** | Open **"Tool-poisoning attempt blocked by Model Armor"**. | "One object in a public bucket was *named* as a prompt injection — bait to hijack the agent. Model Armor caught it and turned the attack into a finding instead of executing it." | MA, OBS |
| **3:15–3:35** | Contrast the **ClickOps** (unmanaged) resource vs the managed bucket in the Ownership column. Then **Compliance**: SOC 2 / CIS / ISO / PCI, failing controls linked to findings. | "Every resource is traced to its owner — or flagged as ClickOps when it's in no state at all. And every finding maps to real controls across four frameworks." | OWN, OBS |
| **3:35–3:52** | **Google Cloud Console:** Cloud Run `cloudcap` (green, `.run` URL) → glance Vertex AI, Firestore, Model Armor template, the **Cloud Logging** audit trail. | "All on Google Cloud: Cloud Run for the fleet, Vertex AI for Gemini, Firestore for state, Model Armor for the guardrail, and an immutable Cloud Logging trace of every action." | **GCP deployment proof (required)** · OBS |
| **3:52–4:00** | Board headline (findings + severities) → tagline card. | "CloudCap — watch the live cloud, prove the risk, fix it by Pull Request. A governed agent fleet, safe enough to point at production." | — |

---

## 3b. Continuous VO script (paste into ElevenLabs — pure narration, no cues)

Paste the block below **as one piece**. It's ~540 words ≈ **3:20–3:40** at a natural pace
(the shorter earlier version measured 158s; this leads with the **autonomous / background**
agentic story — criterion #1, 40% — plus the differentiator, the agent architecture, and an
explicit value/close). VO does **not** need to fill the full 4:00 — hold **deliberate pauses**
(see "Pause points" below) so viewers absorb what's on screen. Trim the *italicised* sentences
first if you need to shave time.

**Pause points (stop talking, let the screen breathe):** ① while the **scan runs** → freeze/hold
~a few seconds on the green "Scan complete" banner; ② right after the **Pull Request opens** —
linger on the diff removing `allUsers`; ③ on the **Google Cloud Console** proof shot. These
three pauses + the ~3:30 VO land you at a well-paced ~4:00.

> Every cloud quietly drifts. A bucket goes public in the console. An account gets
> over-privileged. A machine sits idle, burning money. The tools that scan your Terraform never
> catch it — because it never touched the code.
>
> CloudCap is different. It isn't a chatbot you prompt — it's a fleet of background agents that
> run on a schedule, unattended. They scan your live cloud, decide what matters, and open the
> fix as a Pull Request. Autonomously.
>
> The entire fleet deploys with one Terraform apply to Cloud Run. It prints its own URL — map
> it to any domain — and comes up self-onboarding, read-only against your projects.
>
> I sign in with Firebase — I'm the admin. CloudCap discovers my real GCP projects. I'll
> govern two: a sandbox wired to a Git repo, and my production project, which I'm placing under
> a change-freeze.
>
> This is the fleet — six specialized agents: cost, security, IAM, and compliance scanners, an
> orchestrator, and a remediation broker. Each runs as its own least-privilege service account,
> verified live against Google Cloud IAM. And it's governed by design: *the remediation agent
> has no cloud write access at all — it can only propose.*
>
> Now one read-only scan across both projects. Gemini 3.7 on Vertex AI ranks and narrates on
> top of deterministic evidence — it explains the findings, it never invents them.
>
> Here's the difference. This bucket is public to the entire internet — detected on the live
> cloud, right now. A code scanner would miss it if someone made it public by hand. CloudCap
> traces the resource to the Terraform that owns it, from real state in GCS, and opens a Pull
> Request that removes the public access at the source. Detection to remediation — a closed
> loop. And nothing auto-merges; a human approves.
>
> My production project is under a change-freeze, so CloudCap still detects but proposes no
> automated changes to it. And it found real waste — about a hundred and thirty dollars a month
> in idle spend, ready to reclaim.
>
> One object in that public bucket was named as a prompt injection — bait to hijack the agent.
> Model Armor caught it and turned the attack into a finding, instead of executing it. The
> guardrail, working on live input.
>
> Every resource is traced to its owner — or flagged as ClickOps when it's in no state at all.
> *And every finding maps to real controls across SOC 2, CIS, ISO, and PCI — audit evidence,
> generated automatically.*
>
> All of it on Google Cloud: Cloud Run for the fleet, Vertex AI for Gemini, Firestore for
> state, Model Armor for the guardrail, and an immutable Cloud Logging trace of every action.
>
> Everything you just saw — detect, rank, trace the owner, draft the fix — the agents did on
> their own, with no prompting. Other tools stop at a dashboard full of alerts; CloudCap closes
> the loop. A governed agent fleet that runs in the background — finding risk, proving it, and
> handing you the fix while you sleep. Safe enough to point at production.

---

## 3c. Agentic fit — how CloudCap maps to the judging criteria

Use this in the Devpost write-up and to sanity-check the video hits every rubric line.

**Innovation & Operational Utility — 40% (the big one; theme = autonomous background action):**
- **Background & unattended.** CloudCap runs as an **unattended fleet**, not a chatbot — no one
  prompts it; it scans and acts on its own. `agents/scan.py` is built to be driven by a schedule.
  ⚠️ **To say "runs daily on a schedule" truthfully, wire the Cloud Scheduler job** (not yet
  wired — see below). Until then, phrase as "runs unattended when triggered."
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

- **REG** 1:05 registry table · **ID** 0:16 deploy, 0:44 SAs/auth · **GW** 1:28 scan, 2:55 policy ·
  **MA** 2:55 block · **OBS** 1:05 status, 3:35 Cloud Logging · **OWN** 1:50 PR routing + state,
  3:15 ClickOps · **MEM** change-freeze + finding lifecycle ("known issues don't re-nag").
- **Mandatory tech shown:** Gemini 3.7-flash / Vertex AI (1:28), GenAI SDK (say it), Cloud Run +
  Firestore (0:16, 3:35). **Backend-on-GCP proof:** 3:35 Console shot. ✅

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
