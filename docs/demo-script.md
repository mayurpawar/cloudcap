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

> "CloudCap is a governed multi-agent fleet on Google Cloud that finds cloud cost, security,
> and compliance risk on **real** infrastructure, proves it, and **fixes it by Pull Request** —
> with Gemini reasoning, Model Armor guardrails, and a full audit trail."

---

## 1. Before you record (setup — do NOT film this part live)

The hub is already deployed to Cloud Run, so the video shows the **running app**. The repo +
`INSTALL.md` provide the reproducible setup the rules require. Do this prep first:

1. **Clone + skim the repo on a camera-ready screen** (used briefly in the intro):
   `git clone https://github.com/mayurpawar/cloudcap && cd cloudcap`
   Have `README.md`, `INSTALL.md`, and `docs/architecture-system.png` open in tabs.
2. **Confirm the live hub is healthy** (v24): open `https://cloudcap-lu3jp4b2ba-uc.a.run.app`
   and sign in with Google (admin: mayurpawar1@gmail.com).
3. **Confirm the chaos-env is running** (the audited infra with planted issues):
   VMs + Cloud SQL started; `cc-chaos-fffbba` is the scan target.
4. **[Recommended] Wire the GitHub token** so "Run scan" opens the fix PR *live* (see §4).
   Without it, do NOT click "Run scan" on camera (it would replace the real PR link) — instead
   navigate the already-populated board and show the existing PR as CloudCap's output.
5. **Open a second browser tab on the Google Cloud Console** (Cloud Run → cloudcap service),
   ready for the deployment-proof shot. Also have Vertex AI + Firestore + Model Armor tabs.
6. **Reset for a clean take:** run one scan so the board is populated and current, then don't
   touch it until you film.
7. **Record 1080p+ , one continuous take, no cuts.** Rehearse once end-to-end.

**Prereqs the video implicitly proves (call them out in voiceover):** Gemini 3.7-flash on
**Vertex AI**, the **GenAI SDK** agent framework, and **Cloud Run + Firestore** (all mandatory).

---

## 2. The screenplay (≤ 4:00, shot-by-shot)

| Time | On screen / action | Say (narration) | Pillars · GCP proof |
|---|---|---|---|
| **0:00–0:18** | Title card "CloudCap — governed agent fleet for GCP", then the repo README + architecture diagram. | "Every cloud org quietly leaks money and risk — idle machines, public buckets, over-privileged accounts, secrets committed to code. CloudCap is a background agent fleet that finds them, proves them, and fixes them by Pull Request." | — |
| **0:18–0:38** | Terminal: `git clone …/cloudcap`; scroll `terraform/fleet`. Then cut to the live hub URL (`.run.app` visible) + Google sign-in. | "The whole fleet deploys with one Terraform apply to Cloud Run. It self-onboards, then runs read-only against your projects." | ID · **Cloud Run URL** |
| **0:38–1:05** | **Hub → Components** page. Point to the 6-agent **Agent Registry** table (identity-verified badges) and the pillar rows all showing **LIVE**. | "This is the fleet: six agents, each running as its own least-privilege service account — verified live against Google Cloud IAM. Registry, Gateway, Model Armor, Observability, Ownership — every pillar is live, not mocked." | REG, ID, GW, MA, OBS, OWN |
| **1:05–1:35** | Click **Run scan** (live). Board paints: findings across security / IAM / compliance, severity chips, and the **Gemini executive summary** up top. | "One read-only scan of a real GCP project. Gemini 3.7 on Vertex AI ranks and narrates on top of deterministic findings — it explains the evidence, it never invents it." | GW, REG · **Vertex AI** |
| **1:35–2:00** | Open the **"Tool-poisoning attempt blocked by Model Armor"** finding. | "One object in a public bucket was *named* as a prompt injection — bait to hijack the agent. Model Armor caught it and turned the attack into a finding instead of executing it. The guardrail working on live input." | MA, OBS |
| **2:00–2:40** | Open the **"Hardcoded secret in Terraform"** finding (SOC 2 CC6.1) → click **View Pull Request** → real GitHub **PR #1**: `password = "…"` → `password = var.db_password` + a sensitive variable. | "CloudCap scanned the repo's Terraform, found a plaintext database password, mapped it to SOC 2, and opened a **real Pull Request** with the fix — the secret moved to a sensitive variable. Nothing is auto-merged. A human approves. That's how it's safe for production." | GW, OWN |
| **2:40–3:05** | Show a finding's **ownership**: "Managed by `mayurpawar/cc-chaos-infra` → `google_storage_bucket.public_bucket`". Contrast the ClickOps VM (unmanaged). | "Ownership comes from **real Terraform state** in GCS — so every fix routes to the repo that owns it. And the one resource in no state at all — created by hand — is flagged as ClickOps." | OWN · **GCS state** |
| **3:05–3:30** | **Compliance** page: SOC 2 / CIS / ISO 27001 / PCI posture; failing controls linked to findings. Then the audit trail / **Cloud Logging**. | "Every finding maps to real controls across four frameworks. And every scan and action is an immutable Cloud Logging trace — evidence for an auditor." | OBS · **Cloud Logging** |
| **3:30–3:52** | **Google Cloud Console**: Cloud Run `cloudcap` service (green, the `.run` URL) → glance at Vertex AI, Firestore, the Model Armor template. | "All of it on Google Cloud: Cloud Run for the fleet, Vertex AI for Gemini, Firestore for state, Model Armor for the guardrail." | **GCP deployment proof (required)** |
| **3:52–4:00** | Zoom the board headline (findings + severities) → tagline card. | "CloudCap — find the waste and risk, prove it, and fix it by Pull Request. A governed agent fleet, safe enough to point at production." | — |

---

## 3. Pillar & requirement coverage (all visible on camera)

- **REG** 0:38 registry table · **ID** 0:18 deploy, 0:38 SAs · **GW** 0:38 allowlist, 1:05 scan ·
  **MA** 1:35 block · **OBS** 0:38 status, 3:05 Cloud Logging · **OWN** 2:00 PR routing, 2:40 state ·
  **MEM** finding lifecycle (say "known issues don't re-nag across scans").
- **Mandatory tech shown:** Gemini 3.7-flash / Vertex AI (1:05), GenAI SDK (say it), Cloud Run +
  Firestore (0:18, 3:30). **Backend-on-GCP proof:** 3:30 Console shot. ✅

## 4. [Recommended] Wire the live-PR path before recording

For the 2:00 hero beat to open the PR *live* on "Run scan", the hub needs a GitHub token:
create a **fine-grained PAT scoped to `cc-chaos-infra` only** (Contents + Pull requests: RW),
store it in Secret Manager as `github-token`, re-enable the `GITHUB_TOKEN` env block in
`terraform/fleet/main.tf` (search `TODO(github-pr)`), then `terraform apply`. The scan then
opens/reuses PR #1 idempotently. **Without the token, don't click Run scan live** — show the
already-open PR as the artifact CloudCap produced (still 100% real; CloudCap authored it).

## 5. Honesty rules (never fake — judges check the repo)

- **Live in the video:** real scan (Recommender / Asset Inventory / IAM), real Gemini 3.7 on
  Vertex AI, real Model Armor block, real GitHub PR, real Cloud Logging audit, real TF-state
  ownership from GCS. The Console shot is real.
- **Be precise:** the findings are on a **purpose-built chaos project** (`cc-chaos-fffbba`) with
  planted issues — say so; it's a controlled ground-truth environment, not a customer.
- Don't claim creator-attribution or dollar-precision the tool doesn't compute. Stick to what's
  on screen.

## 6. Post-production & submission checklist

- [ ] One clean take, ≤ 4:00, English/subtitled, uploaded **public** to YouTube/Vimeo.
- [ ] The Google Cloud Console / `.run` URL shot is unmistakable.
- [ ] Devpost: category **Fortified Enterprise Fleet**; text description (features, tech, data
      sources, learnings); **repo** `github.com/mayurpawar/cloudcap`; **README** spin-up;
      **architecture diagram** (`docs/architecture-system.png`); demo video link.
- [ ] Confirm eligibility: project built within the submission window.
- [ ] **Bonus (+0.4):** publish a build blog/video (+0.2) and one post with
      **#AllThingsAgenticHackathon** (+0.2). Optional +0.2 each (max +0.6) for Gemma/Veo/Lyria.

---

## Appendix — extended walkthrough (for a blog or a longer cut, not the 4-min video)

If you also make a longer "how it's built" video/blog for the bonus points, show the full setup:
`git clone` → `pip install -r requirements.txt` → `gcloud builds submit` → edit
`terraform/fleet/terraform.tfvars` → `terraform apply` (deploys Cloud Run + SAs + Firestore) →
open the hub → onboarding → first scan. Then walk the hexagonal architecture (`agents/ports`,
`agents/adapters/google_geap.py`, and `agents/context.py` as the single mock/live switch) — this
is the "Architectural Discipline" story judges reward.
