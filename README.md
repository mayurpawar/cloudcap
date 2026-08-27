# CloudCap

A **governed multi-agent Cloud Cost & Security Governance fleet** for the
*All Things Agentic Hackathon* — **Fortified Enterprise Fleet** category.

A FinOps/Security lead discovers CloudCap in their org's **Agent Registry**,
then runs a continuous, multi-week governance cycle: the fleet securely queries
production billing/asset/IAM data (**Agent Identity**, read-only), coordinates
scanner sub-agents (**Agent Gateway**), remembers prior findings and remediation
state (**Memory Bank**), screens untrusted resource metadata and redacts PII
(**Model Armor**), and emits an auditable reasoning-chain trace for every decision
(**Agent Observability**). Remediation is always **human-gated**.

## Architecture
Hub-and-spoke control plane: deployed once into a dedicated **hub project**, granted
**read-only** access across the org/folder scope. Full diagrams + pillar map in
[`docs/architecture.md`](docs/architecture.md).

![CloudCap system architecture](docs/architecture-system.png)

## Seven pillars → Google products
| Pillar | Product | Where |
|---|---|---|
| Agent Registry | GEAP Agent Registry | `terraform/fleet` + `adapters/google_geap.py` |
| Agent Runtime | Agent Engine | `terraform/fleet` |
| Memory Bank | Vertex AI Memory Bank | `adapters/google_geap.py` |
| Agent Identity | IAM / Workload Identity | `terraform/fleet` (per-agent SA) |
| Agent Gateway | GEAP Agent Gateway | `adapters/google_geap.py` |
| Model Armor | Model Armor | `adapters/google_geap.py` |
| Observability | OpenTelemetry → Cloud Trace | `adapters/google_geap.py` |

## Architecture: hexagonal (ports & adapters)
Agent logic (`agents/orchestrator`, `agents/scanners`) depends only on the ports
in `agents/ports/interfaces.py`. The single shipped implementation is Google GEAP
(`agents/adapters/google_geap.py`). Swapping the governance layer to another
platform means writing new adapters — no agent-logic changes.

## Install (customer side)
```bash
# 1. Seed a test environment with known ground-truth issues (OUR eval target)
cd terraform/chaos-env
terraform init && terraform apply -var project_id=YOUR_TEST_PROJECT

# 2. Install the fleet into the target GCP project
cd ../fleet
terraform init && terraform apply -var project_id=YOUR_PROJECT

# 3. Run agents locally against real data during development
pip install -e .
python -m agents.run --project YOUR_TEST_PROJECT   # (entrypoint added D5)
```

## How users interact
- **Discovery:** agents appear in the org **Agent Registry** for cross-dept use.
- **Dashboard** (Cloud Run): fleet status, findings, eval scorecard, Memory Bank
  recall, live OTel traces.
- **Alerts:** findings via email/Chat; **remediation via approve/reject** (gated).
- **Reports:** Reporter agent emails the weekly exec summary.

## Repo layout
See `BLUEPRINT.md` §9. Strategy, 13-day plan, and demo script live in `BLUEPRINT.md`.

