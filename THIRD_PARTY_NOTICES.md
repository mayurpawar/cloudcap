# Third-Party Notices

CloudCap is **self-hosted** software: it is distributed as source + Terraform
scripts and deployed by the operator into their own Google Cloud org. CloudCap is
not operated as a multi-tenant service (not SaaS).

All CloudCap fleet, lifecycle, GitOps-remediation, classification, and governance
logic is the original work of the CloudCap Authors, licensed under Apache-2.0
(see `LICENSE`). This file discloses the third-party components CloudCap builds
upon, in compliance with their licenses and with the hackathon's IP requirements.

> Licenses can change between versions (as Terraform's did). Verify each project's
> LICENSE at the exact version pinned before release.

## 1. Bundled dependencies

**Mock mode** (`--mode mock`, the runnable slice) uses the **Python standard library
only** — no third-party runtime dependencies.

**Live mode** (`pip install .[live]`) declares the following, all permissive:

| Component | License | Use in CloudCap |
|---|---|---|
| Google ADK — `google-adk` | Apache-2.0 | Agent framework: orchestrator + scanner + remediation agents (hackathon req #2) |
| Vertex AI / Gemini SDK — `google-cloud-aiplatform` | Apache-2.0 | Gemini 3.5 model access; Agent Engine runtime; Memory Bank |
| `google-cloud-recommender` | Apache-2.0 | Cost / IAM recommendations (findings source) |
| `google-cloud-asset` | Apache-2.0 | Cloud Asset Inventory (resource classification) |
| `google-cloud-logging` | Apache-2.0 | Cloud Audit Logs (attribution / assumption chain) |
| `google-cloud-monitoring` | Apache-2.0 | Utilization metrics (deterministic proof) |
| `google-cloud-storage` | Apache-2.0 | Bucket posture checks |
| `google-cloud-secret-manager` | Apache-2.0 | Integration-token storage (set up by the app) |
| OpenTelemetry — `opentelemetry-*` | Apache-2.0 | Observability pillar: traces + audit log export |

## 2. Planned integrations (not yet bundled)

Recommended additions to strengthen scored features; all permissive. Listed for
transparency — they are wired in during the live phase, each behind an adapter.

| Component | License | Planned use |
|---|---|---|
| Open Policy Agent / Conftest | Apache-2.0 | Semantic policy gate on remediation PRs (Rego) |
| Terraformer (`GoogleCloudPlatform/terraformer`) | Apache-2.0 | Codify-then-PR: reverse live ClickOps resources → HCL |
| Infracost | Apache-2.0 | Deterministic cost proof attached to remediation PRs |
| `ghapi` (or `gh` CLI) | Apache-2.0 (gh CLI: MIT) | GitHub PR automation |
| `python-hcl2` | MIT | Parse/edit HCL for managed-diff remediation |
| NeMo Guardrails | Apache-2.0 | Off-Google guardrail adapter (portability seam; primary is Model Armor) |

## 3. Customer-provided tools (NOT distributed by CloudCap)

These are executed by the operator using their own installations and licenses.
CloudCap ships configuration/scripts only and is not a redistributor of any of them.

| Tool | Note |
|---|---|
| Terraform (BUSL-1.1) **or** OpenTofu (MPL-2.0) | Operator runs `terraform apply` on CloudCap's `.tf` files with their own binary. CloudCap ships only the `.tf` configuration (its own original work). Requires Terraform ≥ 1.5 or OpenTofu. |
| Google Cloud SDK (`gcloud`) | Invoked as an external CLI where needed (e.g., `terraform import`). Not bundled. |
| Google Cloud Platform services (Gemini Enterprise Agent Platform, Cloud Run, IAM, Secret Manager, etc.) | Consumed as the customer's cloud services under their Google Cloud agreement. |
| GitHub / GitLab / Bitbucket, Jira, Slack | Reached via the customer's own accounts/tokens configured in-app. |

## 4. Attribution & compliance statement

- CloudCap complies with the applicable open-source licenses of the components above.
- Apache-2.0 / MIT / MPL-2.0 obligations are satisfied by retaining upstream
  `LICENSE`/`NOTICE` files and this disclosure; no copyleft (GPL/AGPL) components are
  bundled into CloudCap.
- CloudCap's own contributions are original and solely owned by the CloudCap Authors.
