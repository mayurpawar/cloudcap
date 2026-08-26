# CloudCap — Architecture

CloudCap is a **hub-and-spoke governance control plane** built on the Gemini Enterprise
Agent Platform. It deploys once into a dedicated **hub project**, is granted **read-only**
access across the org/folder scope, detects cost + security + IAM issues (including
untracked ClickOps resources), and delivers every fix as a **human-approved GitOps PR** —
the agents hold **zero cloud write access**.

## 1. System & deployment topology (hub-and-spoke + the seven pillars)

```mermaid
flowchart TB
  user["FinOps / Security lead"]
  repo["IaC repo<br/>(GitHub / GitLab)"]

  subgraph ORG["Customer GCP Organization — data never leaves the tenant"]
    direction TB

    subgraph HUB["CloudCap Hub Project — control plane (ONE per org)"]
      direction TB
      reg["Agent Registry<br/>discover / version"]
      subgraph FLEET["Agent Fleet (ADK on Agent Runtime)"]
        direction LR
        orch["Orchestrator"]
        cost["Cost scanner"]
        sec["Security scanner"]
        iam["IAM scanner"]
        comp["Compliance scanner"]
        rem["Remediation<br/>(no cloud write)"]
        rpt["Reporter"]
      end
      gw["Agent Gateway<br/>routing + policy<br/>(read-only, region pin)"]
      ma["Model Armor<br/>prompt-injection / PII guardrails"]
      mem["Memory Bank<br/>cross-scan continuity"]
      obs["Observability<br/>OTel traces + audit log"]
      idn["Agent Identity<br/>least-privilege SAs"]
      dash["Dashboard<br/>(Cloud Run)"]
    end

    subgraph SCOPE["Scan scope — org / folder / projects (READ-ONLY)"]
      direction LR
      d1["Billing +<br/>Recommender"]
      d2["Cloud Asset<br/>Inventory"]
      d3["Security<br/>Command Center"]
      d4["IAM Recommender /<br/>Policy Analyzer"]
    end
  end

  user -->|discovers agent| reg --> orch
  orch --> cost & sec & iam & comp
  cost & sec & iam & comp -->|tool calls| gw
  gw -->|screened by| ma
  gw ==>|read-only, policy-checked| SCOPE
  orch <-->|recall / remember| mem
  idn -.scopes.-> FLEET
  FLEET -.emits.-> obs
  orch -->|confirmed findings| rem
  rem ==>|proof-backed PR| repo
  repo -->|human merge + terraform apply| SCOPE
  user --> dash
  dash --- obs
  rpt -->|weekly summary| user
```

## 2. Scan → remediation lifecycle

```mermaid
flowchart LR
  A["1. Discover<br/>agent in Registry"] --> B["2. Scan read-only<br/>via Gateway → GCP APIs"]
  B --> C["3. Guardrail<br/>Model Armor screens<br/>untrusted metadata"]
  C --> D["4. Classify<br/>mgmt source + attribution<br/>ClickOps → real actor"]
  D --> E["5. Dedup<br/>Memory Bank recall<br/>suppress already-known"]
  E --> F["6. Prove<br/>deterministic evidence<br/>not AI prose"]
  F --> G["7. Remediate<br/>GitOps PR: managed diff /<br/>codify-then-PR for ClickOps"]
  G --> H["8. Policy gate +<br/>human approve / merge"]
  H --> I["9. Observe<br/>OTel trace + audit log"]
```

## 3. Pillar → product → code map

| GEAP pillar | Google product | Port (interface) | Adapter |
|---|---|---|---|
| Agent Registry | GEAP Agent Registry | `RegistryPort` | `AgentRegistryAdapter` |
| Agent Runtime | Agent Engine | (deploy) | `terraform/fleet` |
| Memory Bank | Vertex AI Memory Bank | `MemoryPort` | `MemoryBankAdapter` / `FileBackedMemory` |
| Agent Identity | IAM / Workload Identity | `IdentityPort` | `WorkloadIdentityAdapter` |
| Agent Gateway | GEAP Agent Gateway | `GatewayPort` | `AgentGatewayAdapter` |
| Model Armor | Model Armor | `GuardrailPort` | `ModelArmorAdapter` |
| Observability | OpenTelemetry → Cloud Trace | `ObservabilityPort` | `OtelObservabilityAdapter` |
| — Remediation | GitHub/GitLab PR | `RemediationChannelPort` | `PullRequestChannel` |
| — Classification | Asset Inventory + Audit Logs | `ResourceClassifierPort` | `AuditLogClassifierAdapter` |

Agent logic depends only on the ports (`agents/ports/interfaces.py`); Google is one
implementation set (`agents/adapters/google_geap.py`), the runnable mock is another
(`agents/adapters/local_mock.py`) — the hexagonal seam that keeps the fleet portable.

## 4. Trust model (why it's safe for production)
- **Read-only scanners** — least-privilege SAs granted at the org/folder node.
- **No cloud write, ever** — remediation is a GitOps PR; merging (not the AI) changes infra.
- **Deterministic proof** attached to every PR; the LLM explains evidence, never asserts it.
- **Semantic policy gate** evaluates each PR before a human sees it.
- **Quarantine-first** decommission (reversible: stop+snapshot / strip-public-IAM), never raw delete.
- **Model Armor** blocks prompt-injection / tool-poisoning from untrusted resource metadata.
- **Full OTel audit trail** for every reasoning step.

## 5. Deployment model, ICP & multi-cloud roadmap

**GCP-native, always.** The control plane deploys into a GCP hub project and leverages
the Gemini Enterprise Agent Platform (Registry, Runtime, Memory Bank, Gateway, Model
Armor). This is the moat and does not change — other clouds are *audited*, never the
deployment target.

**Ideal customer profile:** organizations that have GCP — GCP-native, or multi-cloud
that includes GCP. The strongest wedge is **multi-cloud unified governance**: native
single-cloud tools each see only their own cloud, so nobody gives a multi-cloud org one
governance + compliance posture across GCP + AWS + Azure. CloudCap-on-GCP is that unified
control plane (one board, one SOC2/PCI evidence pack, one remediation workflow).

**Not the near-term ICP:** AWS-only / Azure-only shops (native tools suffice; they won't
stand up GCP). Optional future paths: a small dedicated GCP "governance project" as the
brain (~$37/mo, ROI-positive), or a hosted SaaS.

**What's next — AWS & Azure (roadmap):** new clouds are *adapter packs behind the same
ports*, not a rewrite. ~70–80% is cloud-agnostic and reused (findings, lifecycle,
suppression/exceptions, **IaC ownership via Terraform state**, GitOps remediation,
compliance mapping, dashboard, RBAC). Per-cloud swaps: data-source scanners
(AWS Cost Explorer/Config/Security Hub/CloudTrail; Azure Cost Mgmt/Resource Graph/
Defender/Activity Log), attribution source, remediation templates, and CIS benchmark IDs.
The two differentiators — **IaC-ownership + GitOps remediation** — are already cross-cloud
because Terraform state is provider-agnostic.
